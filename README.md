# Operations Dashboard

This project is a modern, secure, and self-contained web application designed to replace legacy data entry and reporting systems. It provides a central platform for viewing operational data, entering new test results (like Sieve Tests), and analyzing information synced from an ERP system like Sage 100.

The entire system is built with Python and Flask and operates from a single, fully-encrypted local database file, eliminating the need for a dedicated database server and plaintext credential files.

## System Architecture

The architecture is designed for simplicity, security, and maintainability.

**Encrypted SQLite Database (operations_dashboard.db)**: The heart of the system. This single file acts as the repository for all operational data, synced ERP data, and all system configuration (including ERP credentials). It is encrypted with SQLCipher and protected by a master password.

**Python Sync Script (pull_sage.py)**: A background script responsible for connecting to an ERP system (pre-configured for Sage 100), pulling the latest data from multiple tables (Customers, Item Master, Sales History), and loading it into the local encrypted database.

**Flask Web Application (main.py)**: A modern web interface that allows users to:

- View operational dashboards.
- Enter and view Sieve Test data.
- Generate powerful, multi-view sales reports.
- Manage the automated data sync schedule.

## Data Flow

The system has two primary data flows that both feed into the central encrypted database:

**Automated Data Sync from Sage 100:**
```
Sage 100 DB → pull_sage.py → operations_dashboard.db
```

**Manual Data Entry via Web Interface:**
```
Web Browser → main.py → operations_dashboard.db
```

The web application then reads from the operations_dashboard.db file to display all combined information.

## Features

- **Secure, Zero-Configuration Database**: No database server to manage. All data is stored in operations_dashboard.db, encrypted with a master password.
- **Web-Based UI**: A clean and modern user interface accessible from any web browser on the local network.
- **Advanced Sales Reporting**: A dedicated sales report page with a tabbed interface to view data summarized by Item, by Year, or in a detailed, sortable table.
- **Contextual CSV Exports**: Each report view has its own export button, allowing users to download a CSV of the exact data they are viewing.
- **SSL Encryption**: All web traffic between the browser and the server is encrypted using a self-generated SSL certificate.
- **Automated Background Syncing**: A built-in scheduler automatically runs the sync script to keep data fresh.
- **Configurable Scheduler**: Enable, disable, change the sync interval, and view run logs directly from the "Settings" page in the web UI.

## Component Files

- **main.py**: The main Flask web application. It serves all HTML pages, handles user input, and manages the background scheduler. You run this script to start the application.
- **init_db.py**: A one-time setup script. This must be run first to create the encrypted database, set up the schema, and securely store all system and ERP credentials.
- **pull_sage.py**: The ETL (Extract, Transform, Load) script that syncs data from Sage 100 to the local SQLite database. It is run automatically by the scheduler.
- **generate_cert.py**: A utility to generate the cert.pem and key.pem files required for running the web server over HTTPS.
- **operations_dashboard.db**: The encrypted SQLite database file. This is the single source of truth for the application. This file is created by init_db.py.
- **templates/**: A folder containing all the HTML templates used by the Flask application.

## Prerequisites

Before you begin, ensure you have the following installed:

- Python 3.x
- pip (Python package installer)
- An ODBC DSN (Data Source Name) for your ERP system (e.g., Sage 100)

## Setup

Follow these steps to get the project up and running from scratch.

### 1. Install Python Dependencies

This project requires several Python packages. Install them using pip:

```bash
pip install Flask pyodbc sqlcipher3-wheels APScheduler cryptography
```

### 2. Generate SSL Certificate

The web application uses SSL to encrypt all traffic. Run the provided script to generate a self-signed certificate. This only needs to be done once.

```bash
python generate_cert.py
```

This will create two files in your project directory: `cert.pem` and `key.pem`.

### 3. Initialize the Encrypted Database

This is the most important setup step. This script will create the operations_dashboard.db file and prompt you for all the necessary configuration. This only needs to be run once.

```bash
python init_db.py
```

You will be prompted for:

- A master password for the new database. You must remember this password, as it will be used to log in to the web interface.
- Your Sage 100 DSN, Company Code, Server IP, and Database Name.
- Your Sage 100 Username and Password.

All these details will be stored securely inside the encrypted database, eliminating the need for any plaintext password or configuration files.

## Usage

### 1. Run the Application

Start the entire application (web server and background scheduler) with a single command:

```bash
python main.py
```

The application will be running on `https://<your-server-ip>:5000/`.

### 2. Access and Unlock the Web UI

Open a web browser and navigate to `https://localhost:5000` (or the server's IP address if running on a different machine).

**Browser Warning**: Your browser will likely display a security warning (e.g., "Your connection is not private"). This is expected because we are using a self-signed certificate. You must click "Advanced" and then "Proceed to..." to continue.

**Login**: You will be greeted by a login page. Enter the master password you created during the init_db.py setup.

This single action will unlock the UI for your browser session and start the background data sync scheduler.

### 3. Using the Dashboard

Use the top navigation bar to switch between the operational dashboards, Sieve Test entry, the Sales Report, and the Settings page. On the "Settings" page, you can view the status of the automated sync job, see its last run logs, change its schedule, or trigger it to run immediately.
