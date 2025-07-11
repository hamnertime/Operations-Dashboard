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
    print("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels", file=sys.stderr)
    sys.exit(1)

DATABASE = 'operations_dashboard.db'
app = Flask(__name__)
app.secret_key = os.urandom(24)
scheduler = BackgroundScheduler()

def format_with_commas(value):
    if isinstance(value, (int, float)):
        return "{:,.2f}".format(value)
    return value
app.jinja_env.filters['commas'] = format_with_commas

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

def get_sales_report_data(db_conn, start_date, end_date):
    # Query now JOINS the new CI_Item table to get product details
    sql = """
        SELECT h.*, d.LineKey, d.ItemCode, d.ItemCodeDesc, d.QuantityOrdered, d.QuantityShipped,
               d.UnitPrice, d.ExtensionAmt, d.CommentText AS DetailComment, c.CustomerName,
               i.ProductLine, i.ProductType, i.SalesUnitOfMeasure
        FROM SalesOrderHeader AS h
        INNER JOIN SalesOrderDetail AS d ON h.SalesOrderNo = d.SalesOrderNo
        LEFT JOIN Customer AS c ON h.CustomerNo = c.CustomerNo
        LEFT JOIN CI_Item AS i ON d.ItemCode = i.ItemCode
        WHERE h.OrderDate BETWEEN ? AND ? ORDER BY h.OrderDate DESC, h.SalesOrderNo DESC;
    """
    return db_conn.execute(sql, (start_date, end_date)).fetchall()

@app.route('/sales-report', methods=['GET', 'POST'])
def sales_report():
    if 'db_password' not in session: return redirect(url_for('login'))
    end_date, start_date = datetime.now(), datetime.now() - timedelta(days=7)
    if request.method == 'POST':
        try:
            start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d')
            end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d')
        except (ValueError, TypeError): flash("Invalid date format.", "error")
    try:
        with get_db_connection(session['db_password']) as con:
            report_data = get_sales_report_data(con, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))

        summary_by_item = defaultdict(lambda: {'revenue': 0.0, 'tons_sold': 0.0})
        summary_by_year = defaultdict(lambda: {'revenue': 0.0, 'tons_sold': 0.0})

        for row in report_data:
            revenue = row['ExtensionAmt'] or 0.0
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

        return render_template(
            'sales_report.html', data=report_data,
            summary_by_item=sorted_summary_by_item, summary_by_year=sorted_summary_by_year,
            start_date=start_date.strftime('%Y-%m-%d'), end_date=end_date.strftime('%Y-%m-%d')
        )
    except (ValueError, ConnectionError) as e:
        flash(f"Database Error: {e}. Please log in again.", 'error'); return redirect(url_for('login'))

# Keep other routes as they are...
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        try:
            with get_db_connection(password):
                if not scheduler.running:
                    jobs = con.execute("SELECT id, script_path, interval_minutes FROM scheduler_jobs WHERE enabled = 1").fetchall()
                    for job in jobs:
                        scheduler.add_job(run_job, 'interval', minutes=job['interval_minutes'], args=[job['id'], job['script_path'], password], id=str(job['id']), next_run_time=datetime.now() + timedelta(seconds=10))
                    scheduler.start()
            session['db_password'] = password
            return redirect(url_for('dashboard'))
        except (ValueError, ConnectionError) as e:
            flash(f"Login failed: {e}", 'error')
    return render_template('login.html')

@app.route('/')
def dashboard(): return redirect(url_for('ops_dashboard', dashboard_name='east'))

@app.route('/ops/<string:dashboard_name>')
def ops_dashboard(dashboard_name):
    if 'db_password' not in session: return redirect(url_for('login'))
    dashboards = {'east': {},'west_dry': {},'west_wet': {},'west_ball': {}}
    dash = dashboards.get(dashboard_name)
    if not dash: return "Dashboard not found", 404
    return render_template('ops_dashboard.html', title=f"{dashboard_name.replace('_',' ').title()} Operations", data=dash.get('data',[]), active_page=dashboard_name)

@app.route('/export/sales')
def export_sales_report():
    if 'db_password' not in session: return redirect(url_for('login'))
    start, end = request.args.get('start_date'), request.args.get('end_date')
    try:
        with get_db_connection(session['db_password']) as con: report_data = get_sales_report_data(con, start, end)
        output = io.StringIO()
        writer = csv.writer(output)
        if report_data:
            writer.writerow(report_data[0].keys())
            for row in report_data: writer.writerow(row)
        output.seek(0)
        return Response(output, mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename=sales_report_{start}_to_{end}.csv"})
    except (ValueError, ConnectionError) as e:
        return f"Error exporting data: {e}", 500
