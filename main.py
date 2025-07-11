import os
import sys
import subprocess
import time
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
import io
import csv
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    print("Error: sqlcipher3-wheels is not installed.", file=sys.stderr)
    sys.exit(1)

DATABASE = 'operations_dashboard.db'
app = Flask(__name__)
app.secret_key = os.urandom(24)
scheduler = BackgroundScheduler()

# --- Custom Jinja2 Filter ---
def format_with_commas(value):
    """Formats a number with commas for thousands separation."""
    if isinstance(value, (int, float)):
        return "{:,.2f}".format(value)
    return value

app.jinja_env.filters['commas'] = format_with_commas
# -----------------------------

def get_db_connection(password):
    if not password: raise ValueError("A database password is required.")
    try:
        con = sqlite3.connect(DATABASE, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA key = '{password}';")
        con.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;")
        return con
    except sqlite3.Error as e:
        raise ConnectionError(f"Failed to connect or unlock database '{DATABASE}'. Is the password correct? Error: {e}")

def add_sieve_test(db_conn, header_data: dict, detail_lines: list):
    cursor = db_conn.cursor()
    try:
        cursor.execute("INSERT INTO SieveTest (SieveTestDate, CarorTruckNumber, BillofLading, SampleID) VALUES (?, ?, ?, ?)",
                       (header_data['date'], header_data['car_truck'], header_data['bol'], header_data['sample_id']))
        new_sieve_test_id = cursor.lastrowid
        if not new_sieve_test_id: raise Exception("Failed to retrieve new SieveTestID.")
        detail_params = [(new_sieve_test_id, line['sieve'], line['weight']) for line in detail_lines]
        cursor.executemany("INSERT INTO SieveTestDetail (SieveTestID, USSieve, Weight) VALUES (?, ?, ?)", detail_params)
        db_conn.commit()
        return new_sieve_test_id
    except sqlite3.Error as e:
        db_conn.rollback(); return 0

def get_sales_report_data(db_conn, start_date, end_date):
    # This query now selects all columns from the joined tables
    sql = """
        SELECT h.*, d.LineKey, d.ItemCode, d.ItemCodeDesc, d.QuantityOrdered, d.QuantityShipped,
               d.UnitPrice, d.ExtensionAmt, d.CommentText, c.CustomerName
        FROM SalesOrderHeader AS h
        INNER JOIN SalesOrderDetail AS d ON h.SalesOrderNo = d.SalesOrderNo
        LEFT JOIN Customer AS c ON h.CustomerNo = c.CustomerNo
        WHERE h.OrderDate BETWEEN ? AND ? ORDER BY h.OrderDate DESC, h.SalesOrderNo DESC;
    """
    return db_conn.execute(sql, (start_date, end_date)).fetchall()

def get_recent_sieve_tests(db_conn):
    sql = """
        SELECT st.SieveTestID, s.Name AS Product, st.SieveTestDate, st.CarorTruckNumber
        FROM SieveTest AS st INNER JOIN Sample AS s ON st.SampleID = s.SampleID
        ORDER BY st.SieveTestDate DESC, st.SieveTestID DESC LIMIT 20
    """
    return db_conn.execute(sql).fetchall()

def get_sieve_test_details(db_conn, test_id):
    header = db_conn.execute("SELECT st.*, s.Name as ProductName FROM SieveTest as st INNER JOIN Sample as s ON st.SampleID = s.SampleID WHERE st.SieveTestID = ?", (test_id,)).fetchone()
    if not header: return None
    details = db_conn.execute("SELECT USSieve, Weight FROM SieveTestDetail WHERE SieveTestID = ? ORDER BY USSieve", (test_id,)).fetchall()
    return {"header": header, "details": details}

def run_job(job_id, script_path, password):
    print(f"[{datetime.now()}] SCHEDULER: Running job '{job_id}': {script_path}")
    log_output, status = "", "Failure"
    try:
        env = os.environ.copy(); env['DB_MASTER_PASSWORD'] = password
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, check=False, timeout=1800, encoding='utf-8', errors='replace', env=env)
        log_output = f"--- STDOUT ---\n{result.stdout}\n\n--- STDERR ---\n{result.stderr}"
        if result.returncode == 0: status = "Success"
    except Exception as e: log_output = f"Scheduler failed to run script: {e}"
    finally:
        print(f"[{datetime.now()}] SCHEDULER: Finished job '{job_id}' with status: {status}")
        try:
            with get_db_connection(password) as con:
                con.execute("UPDATE scheduler_jobs SET last_run = ?, last_status = ?, last_run_log = ? WHERE id = ?",
                            (datetime.now().isoformat(timespec='seconds'), status, log_output, job_id))
                con.commit()
        except Exception as e:
            print(f"[{datetime.now()}] SCHEDULER: Failed to log job result to DB: {e}", file=sys.stderr)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        try:
            with get_db_connection(password) as con:
                if not scheduler.running:
                    print("--- First login, starting scheduler. ---")
                    jobs = con.execute("SELECT id, script_path, interval_minutes FROM scheduler_jobs WHERE enabled = 1").fetchall()
                    for job in jobs:
                        scheduler.add_job(run_job, 'interval', minutes=job['interval_minutes'], args=[job['id'], job['script_path'], password], id=str(job['id']), next_run_time=datetime.now() + timedelta(seconds=10))
                    scheduler.start()
            session['db_password'] = password
            flash('Database unlocked successfully!', 'success')
            return redirect(url_for('dashboard'))
        except (ValueError, ConnectionError) as e:
            flash(f"Login failed: {e}", 'error')
    return render_template('login.html')

@app.route('/')
def dashboard(): return redirect(url_for('ops_dashboard', dashboard_name='east'))

@app.route('/ops/<string:dashboard_name>')
def ops_dashboard(dashboard_name):
    if 'db_password' not in session: return redirect(url_for('login'))
    dashboards = {
        'east': {'title': 'East Operations', 'data': [{'key': 'Sample Reading', 'value': 123.45}]},
        'west_dry': {'title': 'West Dry Operations', 'data': []},
        'west_wet': {'title': 'West Wet Operations', 'data': []},
        'west_ball': {'title': 'West Ball Mill', 'data': []},
    }
    dash = dashboards.get(dashboard_name)
    if not dash: return "Dashboard not found", 404
    return render_template('ops_dashboard.html', title=dash['title'], data=dash['data'], active_page=dashboard_name)

@app.route('/sieve', methods=['GET', 'POST'])
def sieve_search():
    if 'db_password' not in session: return redirect(url_for('login'))
    if request.method == 'POST' and request.form.get('id_to_search'):
        return redirect(url_for('sieve_detail', test_id=request.form.get('id_to_search')))
    try:
        with get_db_connection(session['db_password']) as con:
            return render_template('sieve_search.html', recent_tests=get_recent_sieve_tests(con))
    except (ValueError, ConnectionError) as e:
        flash(f"Database Error: {e}", 'error'); return redirect(url_for('login'))

@app.route('/sieve/<int:test_id>')
def sieve_detail(test_id):
    if 'db_password' not in session: return redirect(url_for('login'))
    try:
        with get_db_connection(session['db_password']) as con: data = get_sieve_test_details(con, test_id)
        if not data: return "Sieve Test not found", 404
        return render_template('sieve_detail.html', data=data)
    except (ValueError, ConnectionError) as e:
        flash(f"Database Error: {e}", 'error'); return redirect(url_for('login'))

@app.route('/sieve/new', methods=['GET', 'POST'])
def new_sieve_test():
    if 'db_password' not in session: return redirect(url_for('login'))
    try:
        with get_db_connection(session['db_password']) as con:
            if request.method == 'POST':
                header = {'date': request.form.get('test_date'), 'sample_id': int(request.form.get('sample_id')), 'car_truck': request.form.get('car_truck'), 'bol': request.form.get('bol')}
                details = [{'sieve': int(s), 'weight': float(w)} for s, w in zip(request.form.getlist('sieve'), request.form.getlist('weight')) if s and w]
                if details:
                    new_id = add_sieve_test(con, header, details)
                    if new_id:
                        flash(f'Sieve Test #{new_id} added successfully!', 'success')
                        return redirect(url_for('sieve_detail', test_id=new_id))
                    else: flash('Error saving Sieve Test.', 'error')
                else: flash('You must add at least one sieve detail line.', 'error')
                return redirect(request.url)

            samples = con.execute("SELECT SampleID, Name FROM Sample ORDER BY Name").fetchall()
            sieve_defaults_rows = con.execute("SELECT USSieve FROM SieveDefaults ORDER BY USSieve").fetchall()
            sieve_defaults = [row['USSieve'] for row in sieve_defaults_rows]
            return render_template('sieve_entry.html', samples=samples, today_str=datetime.now().strftime('%Y-%m-%d'), default_sieves_json=json.dumps(sieve_defaults))
    except (ValueError, ConnectionError, TypeError) as e:
        flash(f'An error occurred: {e}', 'error'); return redirect(url_for('dashboard'))

@app.route('/sales-report', methods=['GET', 'POST'])
def sales_report():
    if 'db_password' not in session: return redirect(url_for('login'))
    # Default date range changed to 1 week
    end_date, start_date = datetime.now(), datetime.now() - timedelta(days=7)
    if request.method == 'POST':
        try:
            start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d')
            end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d')
        except (ValueError, TypeError): flash("Invalid date format.", "error")
    try:
        with get_db_connection(session['db_password']) as con:
            report_data = get_sales_report_data(con, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))

        # --- Aggregation Logic ---
        summary_by_item = defaultdict(lambda: {'revenue': 0.0, 'tons_sold': 0.0})
        summary_by_year = defaultdict(lambda: {'revenue': 0.0, 'tons_sold': 0.0})

        for row in report_data:
            revenue = row['ExtensionAmt'] or 0.0
            # Use QuantityShipped for tons calculation, fall back to QuantityOrdered
            quantity = row['QuantityShipped'] if row['QuantityShipped'] is not None else (row['QuantityOrdered'] or 0.0)

            item_desc = row['ItemCodeDesc'] if (row['ItemCodeDesc'] and row['ItemCodeDesc'].strip()) else '(Not Specified)'
            summary_by_item[item_desc]['revenue'] += revenue

            excluded_keywords = ['freight', 'pallet', 'lease', 'dunnage', 'shipping', 'charge', 'fee', 'misc', 'covers', 'shrinkwrap']
            if not any(keyword in item_desc.lower() for keyword in excluded_keywords):
                summary_by_item[item_desc]['tons_sold'] += quantity

            try:
                order_date = datetime.strptime(row['OrderDate'], '%Y-%m-%d')
                year = order_date.year
                summary_by_year[year]['revenue'] += revenue
                if not any(keyword in item_desc.lower() for keyword in excluded_keywords):
                     summary_by_year[year]['tons_sold'] += quantity
            except (TypeError, ValueError):
                continue

        sorted_summary_by_item = sorted(summary_by_item.items(), key=lambda item: item[1]['revenue'], reverse=True)
        sorted_summary_by_year = sorted(summary_by_year.items(), key=lambda item: item[0])
        # --- End Aggregation ---

        return render_template(
            'sales_report.html',
            data=report_data,
            summary_by_item=sorted_summary_by_item,
            summary_by_year=sorted_summary_by_year,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )
    except (ValueError, ConnectionError) as e:
        flash(f"Database Error: {e}. Please log in again.", 'error'); return redirect(url_for('login'))

@app.route('/export/sales')
def export_sales_report():
    if 'db_password' not in session: return redirect(url_for('login'))
    start, end = request.args.get('start_date'), request.args.get('end_date')
    try:
        with get_db_connection(session['db_password']) as con: report_data = get_sales_report_data(con, start, end)
        output, writer = io.StringIO(), csv.writer(output)
        if report_data: writer.writerow(report_data[0].keys())
        for row in report_data: writer.writerow(row)
        output.seek(0)
        return Response(output, mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=sales_report_{start}_to_{end}.csv"})
    except (ValueError, ConnectionError) as e: return f"Error exporting data: {e}", 500

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'db_password' not in session: return redirect(url_for('login'))
    try:
        with get_db_connection(session['db_password']) as con:
            if request.method == 'POST':
                job_id = request.form.get('job_id')
                is_enabled = 1 if 'enabled' in request.form else 0
                interval = int(request.form.get('interval_minutes', 1))
                con.execute("UPDATE scheduler_jobs SET enabled = ?, interval_minutes = ? WHERE id = ?", (is_enabled, interval, job_id))
                con.commit()
                flash(f"Job settings updated. Restart the application for changes to take effect.", 'success')
                return redirect(url_for('settings'))
            jobs = con.execute("SELECT * FROM scheduler_jobs ORDER BY id").fetchall()
            return render_template('settings.html', scheduler_jobs=jobs)
    except (ValueError, ConnectionError) as e:
        flash(f"Database Error: {e}. Please log in again.", 'error'); return redirect(url_for('login'))

@app.route('/scheduler/run_now/<int:job_id>', methods=['POST'])
def run_now(job_id):
    if 'db_password' not in session: return redirect(url_for('login'))
    try:
        with get_db_connection(session['db_password']) as con:
            job = con.execute("SELECT script_path FROM scheduler_jobs WHERE id = ?", (job_id,)).fetchone()
        if job:
            scheduler.add_job(run_job, args=[job_id, job['script_path'], session['db_password']], id=f"manual_run_{job_id}_{time.time()}")
            flash(f"Job '{job['script_path']}' triggered.", 'success')
        else: flash(f"Job ID {job_id} not found.", 'error')
    except Exception as e: flash(f"Failed to trigger job: {e}", 'error')
    return redirect(url_for('settings'))

@app.route('/scheduler/log/<int:job_id>')
def get_log(job_id):
    if 'db_password' not in session: return jsonify({'log': 'Authentication required.'}), 401
    try:
        with get_db_connection(session['db_password']) as con:
            log = con.execute("SELECT last_run_log FROM scheduler_jobs WHERE id = ?", (job_id,)).fetchone()
        return jsonify({'log': log['last_run_log'] if log and log['last_run_log'] else 'No log found.'})
    except (ValueError, ConnectionError):
        return jsonify({'log': 'Error: Could not access database.'}), 500

if __name__ == '__main__':
    if not (os.path.exists('cert.pem') and os.path.exists('key.pem')):
        sys.exit("Error: SSL certificate not found. Run 'python generate_cert.py' first.")
    if not os.path.exists(DATABASE):
        sys.exit(f"Error: Database '{DATABASE}' not found. Run 'python init_db.py' first.")
    print("--- Starting Operations Dashboard Web Server ---")
    app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=('cert.pem', 'key.pem'))
