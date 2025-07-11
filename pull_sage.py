import os
import sys
import getpass
import pyodbc
from datetime import datetime, date
from decimal import Decimal

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    print("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels", file=sys.stderr)
    sys.exit(1)

DB_FILE = "operations_dashboard.db"
TABLE_MAPPINGS = [
    ("AR_Customer", "Customer", {"CustomerNo": "CustomerNo", "CustomerName": "CustomerName"}, "CustomerNo"),
    (
        "AR_InvoiceHistoryHeader", "SalesOrderHeader",
        {
            # Corrected to use InvoiceNo as the source for the primary key, as it's the true key in Sage.
            "InvoiceNo": "SalesOrderNo",
            "OrderDate": "OrderDate", "ShipToName": "ShipToName",
            "ShipToAddress1": "ShipToAddress1", "ShipToCity": "ShipToCity", "ShipToState": "ShipToState",
            "ShipToZipCode": "ShipToZipCode", "CustomerPONo": "CustomerPONo", "ShipVia": "ShipVia",
            "CustomerNo": "CustomerNo"
        },
        "SalesOrderNo"
    ),
    (
        "AR_InvoiceHistoryDetail", "SalesOrderDetail",
        {
            # This mapping is now correct because the header sync uses InvoiceNo as the source for SalesOrderNo.
            "InvoiceNo": "SalesOrderNo",
            "DetailSeqNo": "LineKey", "ItemCode": "ItemCode",
            "ItemCodeDesc": "ItemCodeDesc", "QuantityOrdered": "QuantityOrdered",
            "UnitPrice": "UnitPrice", "ExtensionAmt": "ExtensionAmt"
        },
        ("SalesOrderNo", "LineKey")
    ),
]

def get_db_password():
    password = os.environ.get('DB_MASTER_PASSWORD')
    if password: return password
    try: return getpass.getpass("Please enter the database master password: ")
    except Exception as e: sys.exit(f"FATAL: Could not read password from prompt: {e}")

def get_local_db_connection(password):
    if not os.path.exists(DB_FILE):
        sys.exit(f"FATAL: Database file '{DB_FILE}' not found. Please run init_db.py first.")
    if not password: sys.exit("FATAL: A database master password is required to connect.")
    try:
        con = sqlite3.connect(DB_FILE, timeout=10)
        # Set row_factory here to make sure all cursor operations can access columns by name
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(f"PRAGMA key = '{password}';")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;")
        return con
    except sqlite3.Error as e:
        sys.exit(f"FATAL: Failed to connect to local database '{DB_FILE}'. Is the password correct? Error: {e}")

def get_sage_creds_and_config(local_db_con):
    """Retrieves Sage connection details from the local database."""
    creds = {}
    try:
        cur = local_db_con.cursor()
        cur.execute("SELECT server, database, username, password FROM credentials WHERE system = 'sage100'")
        cred_row = cur.fetchone()
        if not cred_row: sys.exit("FATAL: Sage 100 credentials not found in the local database.")
        creds.update(dict(cred_row))

        cur.execute("SELECT key, value FROM config WHERE key IN ('sage_dsn', 'sage_company_code')")
        for row in cur.fetchall(): creds[row['key']] = row['value']

        if 'sage_dsn' not in creds or 'sage_company_code' not in creds:
            sys.exit("FATAL: Sage DSN or Company Code not found in the database config table.")

        return creds
    except sqlite3.Error as e:
        sys.exit(f"FATAL: Could not read credentials/config from the local database. Error: {e}")

def get_sage_connection(config):
    print(f"DEBUG: Attempting to connect to Sage 100 (DSN: {config['sage_dsn']}, User: {config['username']})...")
    try:
        cnxn_str = f"DSN={config['sage_dsn']};UID={config['username']};PWD={config['password']};Company={config['sage_company_code']}"
        cnxn = pyodbc.connect(cnxn_str, autocommit=True, readonly=True)
        print("DEBUG: Successfully connected to Sage 100 in read-only mode.")
        return cnxn
    except pyodbc.Error as ex:
        sys.exit(f"FATAL: Sage 100 connection failed: {ex}")

def sync_table(sage_cursor, local_db_con, sage_table, local_table, column_map, pk_cols):
    print(f"\n--- Starting sync for {sage_table} -> {local_table} ---")
    sage_cols_str = ", ".join(f'"{k}"' for k in column_map.keys())
    query = f"SELECT {sage_cols_str} FROM {sage_table}"
    print(f"DEBUG: Executing Sage query: {query}")
    try:
        sage_cursor.execute(query)
        sage_rows = sage_cursor.fetchall()
        print(f"DEBUG: Found {len(sage_rows)} rows to process from {sage_table}.")
    except pyodbc.Error as e:
        print(f"ERROR: Could not fetch data from Sage table {sage_table}. Aborting. Error: {e}")
        return False

    if not sage_rows:
        print(f"INFO: No rows found in {sage_table} to sync.")
        return True

    data_to_upsert = []
    for row in sage_rows:
        new_row = []
        for value in row:
            if isinstance(value, Decimal): new_row.append(float(value))
            elif isinstance(value, (date, datetime)): new_row.append(value.strftime('%Y-%m-%d'))
            elif value is None: new_row.append(None)
            else: new_row.append(str(value).strip())
        data_to_upsert.append(tuple(new_row))

    local_cols_str = ", ".join(column_map.values())
    placeholders = ", ".join("?" for _ in column_map.values())
    sql_upsert = f"INSERT OR REPLACE INTO {local_table} ({local_cols_str}) VALUES ({placeholders})"

    print(f"DEBUG: Preparing to upsert {len(data_to_upsert)} rows into '{local_table}'.")
    local_cursor = local_db_con.cursor()
    try:
        local_cursor.executemany(sql_upsert, data_to_upsert)
        local_db_con.commit()
        print(f"SUCCESS: Committed {local_cursor.rowcount} changes to '{local_table}'.")
        return True
    except sqlite3.Error as e:
        print(f"ERROR: Failed to write to local table '{local_table}'. Rolling back. Error: {e}")
        local_db_con.rollback()
        return False

def main():
    print("--- Starting Sage 100 to Local DB Sync Process ---")
    start_time = datetime.now()

    master_password = get_db_password()
    local_conn = get_local_db_connection(master_password)

    sage_config = get_sage_creds_and_config(local_conn)
    sage_conn = get_sage_connection(sage_config)

    try:
        sage_cursor = sage_conn.cursor()
        for sage_tbl, local_tbl, col_map, pk in TABLE_MAPPINGS:
            if not sync_table(sage_cursor, local_conn, sage_tbl, local_tbl, col_map, pk):
                print(f"FATAL: Sync failed for table {sage_tbl}. Aborting further sync operations.")
                break
    finally:
        if local_conn: local_conn.close(); print("\nDEBUG: Local database connection closed.")
        if sage_conn: sage_conn.close(); print("DEBUG: Sage 100 connection closed.")

    end_time = datetime.now()
    print(f"\n--- Sync process finished in {end_time - start_time} ---")

if __name__ == "__main__":
    main()
