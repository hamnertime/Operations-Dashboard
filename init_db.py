import sys
import os
import getpass

# This is provided by the sqlcipher3-wheels package
try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    print("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels", file=sys.stderr)
    sys.exit(1)


DB_FILE = "operations_dashboard.db"

def create_database():
    """
    Initializes a new encrypted SQLite database for the Operations Dashboard.
    It prompts for all necessary site-specific configurations (passwords,
    Sage details, operational defaults) and creates the database schema.
    """
    if os.path.exists(DB_FILE):
        print(f"Error: Database file '{DB_FILE}' already exists.", file=sys.stderr)
        print("Please remove it manually to re-create the database from scratch.", file=sys.stderr)
        sys.exit(1)

    print("--- Operations Dashboard: Encrypted Database Setup ---")
    master_password = getpass.getpass("Enter a master password for the new encrypted database: ")
    if not master_password:
        print("Error: Master password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    print("\nEnter your Sage 100 Connection Details:")
    sage_dsn = input("  - Sage 100 DSN (e.g., SOTAMAS90): ")
    sage_company = input("  - Sage 100 Company Code (e.g., ABC): ")
    sage_server_ip = input("  - Sage 100 Server IP or Hostname: ")
    sage_database_name = input("  - Sage 100 Database Name: ")
    sage_user = input("  - Sage 100 Username: ")
    sage_password = getpass.getpass("  - Sage 100 Password: ")

    if not all([sage_dsn, sage_company, sage_server_ip, sage_database_name, sage_user, sage_password]):
        print("Error: All Sage connection details are required.", file=sys.stderr)
        sys.exit(1)

    con = None
    try:
        # Connect to the new database file and set the master password
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute(f"PRAGMA key = '{master_password}';")
        cur.execute("PRAGMA foreign_keys = ON;")

        print("\nCreating database schema...")

        # Create table to hold generic key-value config
        cur.execute("""
            CREATE TABLE config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Create table to store encrypted credentials
        cur.execute("""
            CREATE TABLE credentials (
                system TEXT PRIMARY KEY,
                server TEXT,
                database TEXT,
                username TEXT,
                password TEXT
            )
        """)

        # Create the application tables
        cur.execute("CREATE TABLE Customer (CustomerNo TEXT PRIMARY KEY, CustomerName TEXT)")
        cur.execute("""
            CREATE TABLE SalesOrderHeader (
                SalesOrderNo TEXT PRIMARY KEY, OrderDate TEXT, OrderStatus TEXT, CustomerNo TEXT,
                CustomerPONo TEXT, ShipToName TEXT, ShipToAddress1 TEXT, ShipToCity TEXT,
                ShipToState TEXT, ShipToZipCode TEXT, ShipVia TEXT,
                BillToName TEXT, BillToAddress1 TEXT, BillToCity TEXT, BillToState TEXT, BillToZipCode TEXT,
                FOREIGN KEY (CustomerNo) REFERENCES Customer (CustomerNo)
            )
        """)
        cur.execute("""
            CREATE TABLE SalesOrderDetail (
                SalesOrderNo TEXT, LineKey TEXT, ItemCode TEXT, ItemCodeDesc TEXT,
                QuantityOrdered REAL, QuantityShipped REAL, UnitPrice REAL, ExtensionAmt REAL, CommentText TEXT,
                PRIMARY KEY (SalesOrderNo, LineKey),
                FOREIGN KEY (SalesOrderNo) REFERENCES SalesOrderHeader (SalesOrderNo)
            )
        """)
        cur.execute("CREATE TABLE Sample (SampleID INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT UNIQUE NOT NULL)")
        cur.execute("""
            CREATE TABLE SieveTest (
                SieveTestID INTEGER PRIMARY KEY AUTOINCREMENT, SieveTestDate TEXT NOT NULL,
                CarorTruckNumber TEXT, BillofLading TEXT, SampleID INTEGER,
                InternalTest INTEGER DEFAULT 0, Selected4Avg INTEGER DEFAULT 1, StoredAFS REAL,
                FOREIGN KEY (SampleID) REFERENCES Sample (SampleID)
            )
        """)
        cur.execute("""
            CREATE TABLE SieveTestDetail (
                SieveTestID INTEGER, USSieve INTEGER, Weight REAL, SpecLow REAL, SpecHigh REAL,
                PRIMARY KEY (SieveTestID, USSieve),
                FOREIGN KEY (SieveTestID) REFERENCES SieveTest (SieveTestID)
            )
        """)
        cur.execute("CREATE TABLE SieveDefaults (USSieve INTEGER PRIMARY KEY NOT NULL)")
        cur.execute("""
            CREATE TABLE OPEvent (
                OpEventID INTEGER PRIMARY KEY AUTOINCREMENT, OPEventItemID INTEGER,
                OpEventItemOperationID INTEGER NOT NULL, OPEventTime TEXT NOT NULL,
                OpEventNumber REAL NOT NULL, OPEventDesc TEXT, OPEventCodeID INTEGER NOT NULL,
                OpEventPlantUpTime TEXT, EmployeeID INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE scheduler_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT NOT NULL UNIQUE, script_path TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL, enabled BOOLEAN NOT NULL CHECK (enabled IN (0, 1)),
                last_run TEXT, last_status TEXT, last_run_log TEXT
            )
        """)

        print("Storing configuration in the encrypted database...")
        cur.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("sage_dsn", sage_dsn))
        cur.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("sage_company_code", sage_company))
        cur.execute(
            "INSERT INTO credentials (system, server, database, username, password) VALUES (?, ?, ?, ?, ?)",
            ("sage100", sage_server_ip, sage_database_name, sage_user, sage_password)
        )
        cur.execute(
            "INSERT INTO scheduler_jobs (job_name, script_path, interval_minutes, enabled) VALUES (?, ?, ?, ?)",
            ('Sync Sage 100 Data', 'pull_sage.py', 1440, 1)
        )

        print("\n--- Optional Initial Data ---")
        sieve_defaults_str = input("Enter a comma-separated list of default sieve sizes (e.g., 40,50,70,100,140,200,999): ")
        if sieve_defaults_str:
            try:
                sieve_defaults = [(int(s.strip()),) for s in sieve_defaults_str.split(',') if s.strip()]
                cur.executemany("INSERT INTO SieveDefaults (USSieve) VALUES (?)", sieve_defaults)
                print(f"  > Added {len(sieve_defaults)} default sieve sizes.")
            except ValueError:
                print("  > Warning: Could not parse sieve sizes. Must be numbers. Skipping.", file=sys.stderr)

        sample_products_str = input("Enter a comma-separated list of initial sample product names (e.g., Product A, Product B): ")
        if sample_products_str:
            sample_products = [(s.strip(),) for s in sample_products_str.split(',') if s.strip()]
            cur.executemany("INSERT INTO Sample (Name) VALUES (?)", sample_products)
            print(f"  > Added {len(sample_products)} sample products.")


        con.commit()
        print(f"\n✅ Success! Encrypted database '{DB_FILE}' created and configured.")
        print("You can now run the main application.")

    except sqlite3.Error as e:
        print(f"\n❌ An error occurred: {e}", file=sys.stderr)
        if con: con.close()
        if os.path.exists(DB_FILE): os.remove(DB_FILE)
        sys.exit(1)
    finally:
        if con: con.close()

if __name__ == "__main__":
    create_database()
