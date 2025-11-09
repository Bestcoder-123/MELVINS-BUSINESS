from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3
import os
import pandas as pd
from datetime import datetime, timedelta
from flask import send_file
import io
import csv
import json
import time   # <-- added
from datetime import datetime, date
from flask import render_template

# Initialize Flask app
app = Flask(__name__)
app.secret_key = "supersecretkey"

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), 'Database', 'shop.db')

# --- Ensure expiry_data table exists ---
def init_expiry_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS expiry_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT UNIQUE,
            expiry_date TEXT,
            expiry_status TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Initialize expiry_data table
init_expiry_table()


# Allowed file types
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------------ CONNECTION HELPER (FIX #1) ------------------
def get_connection(retries: int = 5, retry_delay: float = 0.15):
    """
    Return a new sqlite3 connection with retries on 'database is locked'.
    Uses check_same_thread=False so connections can be used in different threads
    (safe here because we create short-lived connections).
    """
    last_exc = None
    for attempt in range(retries):
        try:
            # timeout gives SQLite a little more time to acquire locks
            conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
            return conn
        except sqlite3.OperationalError as e:
            last_exc = e
            if 'locked' in str(e).lower():
                time.sleep(retry_delay)
                continue
            raise
    # If we exhaust retries, raise the last exception
    raise sqlite3.OperationalError(f"Could not get DB connection after {retries} retries: {last_exc}")

# ------------------ DATABASE INITIALIZATION ------------------
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    c = conn.cursor()

    # Items Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            description TEXT,
            price_per_pc_or_kg REAL NOT NULL,
            total_quantity_available REAL DEFAULT 0,
            total_stock_amount REAL DEFAULT 0,
            date_added TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Price Variation Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS price_variations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            old_price REAL,
            new_price REAL,
            change_date TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        )
    ''')

    # Sales Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            quantity_sold REAL,
            total_amount REAL,
            date TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        )
    ''')

    # Expiry Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS expiry (
            item TEXT PRIMARY KEY,
            expiry_date TEXT,
            expiry_status TEXT
        )
    ''')

    # Activities (Event Log) Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            date TEXT
        )
    ''')

    conn.commit()
    conn.close()

# Logging helper (now uses get_connection)
def log_activity(action, details):
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("INSERT INTO activities (action, details, date) VALUES (?, ?, ?)",
                  (action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception as e:
        # Don't crash the main operation if logging fails — print for debugging
        print("⚠️ log_activity failed:", e)
    finally:
        if conn:
            conn.close()

# ------------------ DASHBOARD ------------------
@app.route('/')
def index():
    conn = get_connection()
    c = conn.cursor()

    # Fetch items
    c.execute('SELECT id, item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount FROM items')
    items = c.fetchall()

    # --- Dashboard Stats ---
    # Total items in stock
    c.execute('SELECT COUNT(*) FROM items')
    total_items = c.fetchone()[0]

    # Total sales today
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('SELECT SUM(total_amount) FROM sales WHERE date(date) = ?', (today,))
    total_sales_today = c.fetchone()[0] or 0

    # New stock added today
    c.execute('SELECT COUNT(*) FROM items WHERE date(date_added) = ?', (today,))
    new_stock_today = c.fetchone()[0]

    # Expired products
    c.execute('SELECT COUNT(*) FROM expiry WHERE expiry_status = "Expired"')
    expired_row = c.fetchone()
    expired_products = expired_row[0] if expired_row and expired_row[0] is not None else 0

    # Total stock value (sum of all total_stock_amount)
    c.execute('SELECT SUM(total_stock_amount) FROM items')
    total_stock_value_raw = c.fetchone()[0] or 0
    # Format with commas and Ksh prefix
    total_stock_value = f"Ksh {int(total_stock_value_raw):,}"

    # Recent logs (latest 10)
    c.execute('SELECT date, action, details FROM activities ORDER BY id DESC LIMIT 10')
    recent_logs_rows = c.fetchall()
    recent_logs = [{'date': r[0], 'action': r[1], 'details': r[2]} for r in recent_logs_rows]

    # Sales trend data for the chart: last 7 days totals
    sales_dates = []
    sales_values = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        c.execute('SELECT SUM(total_amount) FROM sales WHERE date(date) = ?', (day,))
        val = c.fetchone()[0] or 0
        sales_dates.append(day)
        sales_values.append(val)

    conn.close()

    return render_template(
        'index.html',
        items=items,
        total_items=total_items,
        total_sales_today=total_sales_today,
        new_stock_today=new_stock_today,
        expired_products=expired_products,
        total_stock_value=total_stock_value,  # already formatted
        recent_logs=recent_logs,
        sales_dates=json.dumps(sales_dates),
        sales_values=json.dumps(sales_values)
    )


@app.route('/api/items')
def api_items():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT id, item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount FROM items')
    rows = c.fetchall()
    conn.close()
    data = [
        {
            'id': row[0],
            'item': row[1],
            'description': row[2],
            'price_per_pc_or_kg': row[3],
            'total_quantity_available': row[4],
            'total_stock_amount': row[5]
        } for row in rows
    ]
    return jsonify(data)

# ------------------ ITEM MANAGEMENT ------------------
@app.route('/add', methods=['POST'])
def add_item():
    item = request.form['item']
    description = request.form.get('description', '')
    try:
        price = float(request.form['price_per_pc_or_kg'])
    except (ValueError, KeyError):
        flash('Invalid price value')
        return redirect(url_for('index'))

    try:
        quantity = float(request.form['total_quantity_available'])
    except (ValueError, KeyError):
        flash('Invalid quantity value')
        return redirect(url_for('index'))

    total_amount = price * quantity

    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO items (item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount)
        VALUES (?, ?, ?, ?, ?)
    ''', (item, description, price, quantity, total_amount))
    conn.commit()
    conn.close()

    log_activity("ADD ITEM", f'Item "{item}" added — qty: {quantity}, price: {price:.2f}, total: {total_amount:.2f}')
    flash(f'Item "{item}" added successfully!')
    return redirect(url_for('index'))

@app.route('/delete/<int:item_id>')
def delete_item(item_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT item FROM items WHERE id = ?', (item_id,))
    row = c.fetchone()
    item_name = row[0] if row else f'ID {item_id}'
    c.execute('DELETE FROM items WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    log_activity("DELETE ITEM", f'Item "{item_name}" (id:{item_id}) deleted.')
    flash('Item deleted successfully!')
    return redirect(url_for('index'))

@app.route('/edit/<int:item_id>')
def edit_item(item_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM items WHERE id = ?', (item_id,))
    item = c.fetchone()
    conn.close()
    return render_template('edit.html', item=item)

@app.route('/update/<int:item_id>', methods=['POST'])
def update_item(item_id):
    item_name = request.form.get('item', '').strip()
    description = request.form.get('description', '').strip()

    # Validate price
    try:
        new_price = float(request.form['price_per_pc_or_kg'])
    except (ValueError, KeyError):
        flash('⚠️ Invalid price value')
        return redirect(url_for('index'))

    # Validate quantity
    try:
        quantity = float(request.form['total_quantity_available'])
    except (ValueError, KeyError):
        flash('⚠️ Invalid quantity value')
        return redirect(url_for('index'))

    total_amount = new_price * quantity

    # Use get_connection() here (was with sqlite3.connect(...))
    conn = get_connection()
    try:
        c = conn.cursor()

        # Check if the item exists
        c.execute('SELECT price_per_pc_or_kg, item FROM items WHERE id=?', (item_id,))
        row = c.fetchone()
        if not row:
            flash('❌ Item not found.')
            return redirect(url_for('index'))

        old_price = float(row[0])
        old_item_name = row[1]

        # Record the price change if different
        if old_price != new_price:
            c.execute('''
                INSERT INTO price_variations (item_id, old_price, new_price, change_date)
                VALUES (?, ?, ?, datetime('now'))
            ''', (item_id, old_price, new_price))
            # Logging uses its own connection (get_connection)
            log_activity("PRICE CHANGE", f'{old_item_name} (id:{item_id}) changed price {old_price:.2f} → {new_price:.2f}')

        # Update the main item
        c.execute('''
            UPDATE items
            SET item=?, description=?, price_per_pc_or_kg=?, total_quantity_available=?, total_stock_amount=?
            WHERE id=?
        ''', (item_name, description, new_price, quantity, total_amount, item_id))

        conn.commit()
    finally:
        conn.close()

    log_activity("UPDATE ITEM", f'Item "{item_name}" (id:{item_id}) updated — qty: {quantity}, price: {new_price:.2f}')
    flash(f'✅ Item "{item_name}" updated successfully{" (price variation recorded)" if old_price != new_price else ""}!')
    return redirect(url_for('index'))

# ------------------ PRICE LIST ------------------
@app.route('/price-list')
def price_list():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT id, item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount FROM items')
    items = c.fetchall()
    conn.close()
    return render_template('price_list.html', items=items)

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('No file selected')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            try:
                # Read file into dataframe
                if file.filename.lower().endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    df = pd.read_excel(file)

                required_columns = ['ITEM', 'DESCRIPTION', 'PRICE_PER_PC_OR_KG', 'TOTAL_QUANTITY_AVAILABLE']
                if not all(col in df.columns for col in required_columns):
                    flash(f'Missing required columns. Required: {required_columns}')
                    return redirect(request.url)

                df['TOTAL_STOCK_AMOUNT'] = df['PRICE_PER_PC_OR_KG'] * df['TOTAL_QUANTITY_AVAILABLE']

                inserted = 0
                updated = 0

                # Use single connection for all operations
                with get_connection() as conn:
                    c = conn.cursor()

                    def log_conn_activity(action, details):
                        """Log activity using the existing connection to prevent locking."""
                        try:
                            c.execute(
                                "INSERT INTO activities (action, details, date) VALUES (?, ?, ?)",
                                (action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            )
                        except Exception as e:
                            print("⚠️ log_activity failed:", e)

                    for _, row in df.iterrows():
                        item_name = str(row['ITEM']).strip()
                        description = str(row.get('DESCRIPTION', '')).strip()
                        new_price = float(row['PRICE_PER_PC_OR_KG'])
                        qty = float(row['TOTAL_QUANTITY_AVAILABLE'])
                        total_amt = float(row['TOTAL_STOCK_AMOUNT'])

                        # Check if item exists
                        c.execute('SELECT id, price_per_pc_or_kg FROM items WHERE item = ?', (item_name,))
                        existing = c.fetchone()

                        if existing:
                            item_id, old_price = existing
                            if old_price != new_price:
                                c.execute('''
                                    INSERT INTO price_variations (item_id, old_price, new_price)
                                    VALUES (?, ?, ?)
                                ''', (item_id, old_price, new_price))
                                log_conn_activity("PRICE CHANGE", f'{item_name} (id:{item_id}) changed price {old_price:.2f} → {new_price:.2f}')

                            # Update item
                            c.execute('''
                                UPDATE items
                                SET description=?, price_per_pc_or_kg=?, total_quantity_available=?, total_stock_amount=?, date_added=CURRENT_TIMESTAMP
                                WHERE id=?
                            ''', (description, new_price, qty, total_amt, item_id))
                            updated += 1
                            log_conn_activity("UPDATE ITEM (UPLOAD)", f'Updated "{item_name}" — qty: {qty}, price: {new_price:.2f}')
                        else:
                            # Insert new item
                            c.execute('''
                                INSERT INTO items (item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (item_name, description, new_price, qty, total_amt))
                            inserted += 1
                            log_conn_activity("ADD ITEM (UPLOAD)", f'Inserted "{item_name}" — qty: {qty}, price: {new_price:.2f}')

                    conn.commit()

                flash(f'File uploaded successfully! {inserted} new, {updated} updated.')
                return redirect(url_for('index'))

            except Exception as e:
                flash(f'Error processing file: {e}')
                print("⚠️ Upload failed:", e)
                return redirect(request.url)

    return render_template('upload.html')

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()  # Get the search input
    if not query:
        flash("Please enter a search term!")
        return redirect(url_for('sales'))  # Or wherever you want to redirect if empty

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount
        FROM items
        WHERE item LIKE ? OR description LIKE ?
    """, (f'%{query}%', f'%{query}%'))
    results = c.fetchall()
    conn.close()

    return render_template('sales.html', items=results)

@app.route('/api/search-items')
def search_items():
    query = request.args.get('q', '').strip()
    conn = get_connection()
    c = conn.cursor()
    if query:
        c.execute("SELECT id, item, description FROM items WHERE item LIKE ? LIMIT 10", (f"%{query}%",))
    else:
        c.execute("SELECT id, item, description FROM items LIMIT 20")
    items = c.fetchall()
    conn.close()
    data = [{'id': r[0], 'item': r[1], 'description': r[2]} for r in items]
    return jsonify(data)


# ------------------ SALES ------------------
@app.route('/sales')
def sales():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT id, item, description, price_per_pc_or_kg, total_quantity_available, total_stock_amount FROM items')
    items = c.fetchall()
    conn.close()
    return render_template('sales.html', items=items)

@app.route('/sell/<int:item_id>', methods=['POST'])
def sell_item(item_id):
    try:
        quantity_sold = float(request.form['quantity_sold'])
    except (ValueError, KeyError):
        flash('Invalid quantity entered')
        return redirect(url_for('sales'))

    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT item, total_quantity_available, price_per_pc_or_kg FROM items WHERE id = ?', (item_id,))
    item = c.fetchone()

    if item and quantity_sold <= item[1]:
        remaining = item[1] - quantity_sold
        total_amount = quantity_sold * item[2]
        c.execute('UPDATE items SET total_quantity_available=?, total_stock_amount=? WHERE id=?',
                  (remaining, remaining * item[2], item_id))
        c.execute('INSERT INTO sales (item_id, quantity_sold, total_amount) VALUES (?, ?, ?)',
                  (item_id, quantity_sold, total_amount))
        conn.commit()
        sold_item_name = item[0]
        conn.close()
        log_activity("SALE", f'Sold {quantity_sold} units of "{sold_item_name}" (id:{item_id}) for {total_amount:.2f} KSH')
        flash(f'Sold {quantity_sold} units of "{sold_item_name}" successfully!')
    else:
        conn.close()
        flash('Insufficient stock!')
    return redirect(url_for('sales'))

@app.route('/sales-today')
def sales_today():
    conn = get_connection()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''
        SELECT i.item, s.quantity_sold, i.price_per_pc_or_kg, s.total_amount
        FROM sales s
        JOIN items i ON s.item_id = i.id
        WHERE date(s.date) = ?
    ''', (today,))
    sales = c.fetchall()
    total_sales = sum(s[3] for s in sales)
    conn.close()
    return render_template('sales_today.html', sales=sales, total_sales=total_sales)

# ------------------ ADDED STOCK ------------------
@app.route('/added-stock')
def added_stock():
    conn = get_connection()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('SELECT item, description, price_per_pc_or_kg, total_quantity_available FROM items WHERE date(date_added) = ?', (today,))
    items = c.fetchall()
    conn.close()
    return render_template('added_stock.html', items=items)

# ------------------ STATISTICS ------------------
@app.route('/statistics')
def statistics():
    conn = get_connection()
    c = conn.cursor()

    # Activity logs (latest 200)
    c.execute("SELECT date, action, details FROM activities ORDER BY id DESC LIMIT 200")
    activities_rows = c.fetchall()
    activities = [{'date': r[0], 'action': r[1], 'details': r[2]} for r in activities_rows]

    # Action counts (for bar chart)
    c.execute("SELECT action, COUNT(*) FROM activities GROUP BY action ORDER BY COUNT(*) DESC")
    action_counts_rows = c.fetchall()
    action_labels = [r[0] for r in action_counts_rows]
    action_counts = [r[1] for r in action_counts_rows]

    # Sales last 14 days (for line chart)
    sales_dates = []
    sales_values = []
    for i in range(13, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        c.execute('SELECT SUM(total_amount) FROM sales WHERE date(date) = ?', (day,))
        val = c.fetchone()[0] or 0
        sales_dates.append(day)
        sales_values.append(val)

    # Top selling items (sum by item)
    c.execute('''
        SELECT i.item, SUM(s.quantity_sold) as total_qty, SUM(s.total_amount) as total_sales
        FROM sales s
        JOIN items i ON s.item_id = i.id
        GROUP BY i.item
        ORDER BY total_sales DESC
        LIMIT 10
    ''')
    top_rows = c.fetchall()
    top_labels = [r[0] for r in top_rows]
    top_sales_values = [r[2] for r in top_rows]  # total_sales

    conn.close()

    return render_template(
        'statistics.html',
        activities=activities,
        action_labels=json.dumps(action_labels),
        action_counts=json.dumps(action_counts),
        sales_dates=json.dumps(sales_dates),
        sales_values=json.dumps(sales_values),
        top_labels=json.dumps(top_labels),
        top_sales_values=json.dumps(top_sales_values)
    )

    # Ensure expiry table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expiry (
            item TEXT PRIMARY KEY,
            expiry_date TEXT,
            expiry_status TEXT
        )
    """)

    today_str = datetime.today().strftime('%Y-%m-%d')

    # Save each item's expiry info
    for key, value in request.form.items():
        if key.startswith("expiry_date_"):
            item_name = key.replace("expiry_date_", "").replace("_", " ")
            expiry_date = value.strip()
            
            # Determine status server-side
            if not expiry_date:
                expiry_status = 'N/A'
            elif expiry_date < today_str:
                expiry_status = 'Expired'
            else:
                expiry_status = 'Valid'

            # Save to DB
            cursor.execute("""
                INSERT INTO expiry (item, expiry_date, expiry_status)
                VALUES (?, ?, ?)
                ON CONFLICT(item) DO UPDATE SET
                    expiry_date = excluded.expiry_date,
                    expiry_status = excluded.expiry_status
            """, (item_name, expiry_date, expiry_status))

    conn.commit()
    conn.close()

    flash("Expiry data saved successfully!", "success")
    return redirect(url_for('expiry_status'))

@app.route('/expiry-status')
def expiry_status():
    conn = get_connection()
    c = conn.cursor()

    # Ensure expiry table exists
    c.execute("""
        CREATE TABLE IF NOT EXISTS expiry (
            item TEXT PRIMARY KEY,
            expiry_date TEXT,
            expiry_status TEXT
        )
    """)

    # Fetch all items from items table
    c.execute("SELECT item FROM items")
    all_items = [row[0] for row in c.fetchall()]

    # Fetch existing expiry info
    c.execute("SELECT item, expiry_date, expiry_status FROM expiry")
    expiry_rows = {row[0]: {'expiry_date': row[1], 'expiry_status': row[2]} for row in c.fetchall()}

    # Prepare items for template
    items = []
    for item_name in all_items:
        items.append({
            'item': item_name,
            'expiry_date': expiry_rows.get(item_name, {}).get('expiry_date', ''),
            'expiry_status': expiry_rows.get(item_name, {}).get('expiry_status', 'N/A')
        })

    conn.close()
    return render_template('expiry_status.html', items=items)

from datetime import datetime

@app.route('/update-expiry', methods=['POST'])
def update_expiry():
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure expiry table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expiry (
            item TEXT PRIMARY KEY,
            expiry_date TEXT,
            expiry_status TEXT
        )
    """)

    today = datetime.today().date()

    # Loop through all expiry_date fields from the form
    for key, value in request.form.items():
        if key.startswith("expiry_date_"):
            # Convert back underscores to spaces if needed
            item_name = key.replace("expiry_date_", "").replace("_", " ")
            expiry_date = value.strip()

            if expiry_date:
                exp_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                expiry_status = "Expired" if exp_date <= today else "Valid"

                cursor.execute("""
                    INSERT INTO expiry (item, expiry_date, expiry_status)
                    VALUES (?, ?, ?)
                    ON CONFLICT(item) DO UPDATE SET
                        expiry_date = excluded.expiry_date,
                        expiry_status = excluded.expiry_status
                """, (item_name, expiry_date, expiry_status))
            else:
                # If no date provided, set status to N/A
                cursor.execute("""
                    INSERT INTO expiry (item, expiry_date, expiry_status)
                    VALUES (?, ?, ?)
                    ON CONFLICT(item) DO UPDATE SET
                        expiry_date = excluded.expiry_date,
                        expiry_status = excluded.expiry_status
                """, (item_name, '', 'N/A'))

    conn.commit()
    conn.close()

    log_activity("UPDATE EXPIRY", "Auto-updated expiry information via form")
    flash("Expiry data updated successfully!", "success")
    return redirect(url_for('expiry_status'))

@app.route('/update-item-price', methods=['POST'])
def update_item_price():
    conn = get_connection()
    cursor = conn.cursor()

    item_id = request.form['item_id']
    new_price = float(request.form['new_price'])

    # Get old price
    cursor.execute('SELECT price_per_pc_or_kg, item FROM items WHERE id = ?', (item_id,))
    row = cursor.fetchone()
    if row:
        old_price = row[0]
        item_name = row[1]
        if old_price != new_price:
            # Record price variation
            cursor.execute('''
                INSERT INTO price_variations (item_id, old_price, new_price)
                VALUES (?, ?, ?)
            ''', (item_id, old_price, new_price))

            # Update item table
            cursor.execute('UPDATE items SET price_per_pc_or_kg = ? WHERE id = ?', (new_price, item_id))
            log_activity("PRICE CHANGE", f'{item_name} (id:{item_id}) changed price {old_price:.2f} → {new_price:.2f}')

    conn.commit()
    conn.close()

    flash("Price updated and variation recorded!", "success")
    return redirect(url_for('index'))

@app.route('/price-variation')
def price_variation():
    conn = get_connection()
    c = conn.cursor()

    # Fetch joined data including price_variation ID
    c.execute('''
        SELECT pv.id, i.item, i.description, pv.old_price, pv.new_price, pv.change_date
        FROM price_variations pv
        JOIN items i ON pv.item_id = i.id
        ORDER BY i.item, pv.change_date
    ''')
    price_rows = c.fetchall()
    conn.close()

    # Convert to dictionary by item for grouped display
    data = {}
    for row in price_rows:
        pv_id, item, desc, old_price, new_price, date = row
        if item not in data:
            data[item] = {
                'description': desc,
                'variations': []
            }
        data[item]['variations'].append({
            'id': pv_id,
            'old_price': old_price,
            'new_price': new_price,
            'change_date': date
        })

    return render_template('price_variation.html', data=data)

@app.route('/download-price-variation')
def download_price_variation():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT i.item, i.description, pv.old_price, pv.new_price, pv.change_date
        FROM price_variations pv
        JOIN items i ON pv.item_id = i.id
        ORDER BY i.item, pv.change_date
    ''')
    rows = c.fetchall()
    conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ITEM', 'DESCRIPTION', 'OLD PRICE', 'NEW PRICE', 'CHANGE DATE'])
    writer.writerows(rows)
    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='price_variation_report.csv'
    )

@app.route('/substitutes')
def substitutes():
    conn = get_connection()
    c = conn.cursor()

    # Fetch all items
    c.execute('SELECT item, total_quantity_available FROM items')
    rows = c.fetchall()
    conn.close()

    from collections import defaultdict
    import re

    substitutes_data = defaultdict(lambda: {'frequency': 0, 'total_quantity': 0})

    for item, qty in rows:
        # Convert to uppercase for consistency
        item_upper = item.upper().strip()

        # Remove color/variant words: take first word(s) before common separators
        # Adjust this regex to capture "TOSS" from "Toss yellow", "Toss Blue 500g", etc.
        base_name_match = re.match(r'^([A-Z]+)', re.sub(r'[^A-Za-z\s]', '', item_upper))
        base_name = base_name_match.group(1) if base_name_match else item_upper

        substitutes_data[base_name]['frequency'] += 1
        substitutes_data[base_name]['total_quantity'] += qty

    return render_template('substitutes.html', data=substitutes_data)


@app.route('/delete-price-variation/<int:variation_id>', methods=['POST'])
def delete_price_variation(variation_id):
    conn = get_connection()
    c = conn.cursor()

    # Get deleted entry details before deleting (join to get item name)
    c.execute('''
        SELECT i.item, pv.old_price, pv.new_price
        FROM price_variations pv
        JOIN items i ON pv.item_id = i.id
        WHERE pv.id = ?
    ''', (variation_id,))
    deleted_entry = c.fetchone()

    # Delete the entry
    c.execute('DELETE FROM price_variations WHERE id = ?', (variation_id,))
    conn.commit()

    if deleted_entry:
        item_name, old_price, new_price = deleted_entry
        activity_desc = f"Deleted price variation for {item_name}: {old_price} → {new_price}"
        # Use helper for consistent logging
        log_activity("PRICE VARIATION DELETED", activity_desc)

    conn.close()
    flash('Price variation entry deleted successfully!')
    return redirect(url_for('price_variation'))

# ------------------ RUN APP ------------------
app = Flask(__name__)
# … your routes …
if __name__ == "__main__":
    app.run(debug=True)

