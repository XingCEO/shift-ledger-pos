"""Money is integer minor units; stock and receipts commit atomically."""
import hashlib
import json
import re
from .db import connect, transaction


class DomainError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def integer(value, label, minimum=0, maximum=100_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f'{label} must be an integer from {minimum} to {maximum}')
    return value


def text(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise DomainError(f'{label} is required (maximum {limit} characters)')
    return value.strip()


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def key_and_hash(payload):
    key = text(payload.get('idempotency_key'), 'Request key', 100)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key):
        raise DomainError('Request key must contain 8–100 letters, digits, underscores or hyphens')
    return key, fingerprint(payload)


def require_shift(db, shift_id):
    integer(shift_id, 'Shift', 1)
    shift = db.execute('SELECT * FROM shifts WHERE id=? AND closed_at IS NULL', (shift_id,)).fetchone()
    if not shift:
        raise DomainError('Open a shift before continuing', 409)
    return shift


def receipt(db, sale_id):
    sale = db.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
    if not sale:
        raise DomainError('Receipt not found', 404)
    result = dict(sale)
    result.pop('request_hash'); result.pop('idempotency_key')
    result['items'] = [dict(row) for row in db.execute('SELECT * FROM sale_items WHERE sale_id=? ORDER BY id', (sale_id,))]
    result['refunded_cents'] = db.execute('SELECT COALESCE(sum(total_cents),0) FROM refunds WHERE sale_id=?', (sale_id,)).fetchone()[0]
    return result


def expected_cash(db, shift):
    sales = db.execute("SELECT COALESCE(sum(total_cents),0) FROM sales WHERE shift_id=? AND payment='cash'", (shift['id'],)).fetchone()[0]
    refunds = db.execute("SELECT COALESCE(sum(total_cents),0) FROM refunds WHERE shift_id=? AND payment='cash'", (shift['id'],)).fetchone()[0]
    return shift['opening_cents'] + sales - refunds


def state(path):
    db = connect(path)
    try:
        db.execute('BEGIN')
        shift = db.execute('SELECT * FROM shifts WHERE closed_at IS NULL').fetchone()
        current = dict(shift) if shift else None
        if current:
            current['expected_cents'] = expected_cash(db, shift)
        sales = [receipt(db, row[0]) for row in db.execute('SELECT id FROM sales ORDER BY id DESC LIMIT 50').fetchall()]
        return {
            'products': [dict(row) for row in db.execute('SELECT * FROM products ORDER BY category,name')],
            'shift': current,
            'shifts': [dict(row) for row in db.execute('SELECT * FROM shifts ORDER BY id DESC LIMIT 20')],
            'sales': sales,
            'refunds': [dict(row) for row in db.execute('SELECT id,sale_id,shift_id,total_cents,payment,reason,created_at FROM refunds ORDER BY id DESC LIMIT 50')],
            'currency': (db.execute("SELECT value FROM settings WHERE key='currency'").fetchone() or ['USD'])[0],
        }
    finally:
        db.close()


def open_shift(path, user_id, payload):
    opening = integer(payload.get('opening_cents'), 'Opening float')
    with transaction(path) as db:
        if db.execute('SELECT 1 FROM shifts WHERE closed_at IS NULL').fetchone():
            raise DomainError('A shift is already open', 409)
        cur = db.execute('INSERT INTO shifts(user_id,opening_cents) VALUES(?,?)', (user_id, opening))
        return {'id': cur.lastrowid, 'opening_cents': opening}


def close_shift(path, payload):
    counted = integer(payload.get('counted_cents'), 'Counted cash')
    with transaction(path) as db:
        shift = require_shift(db, payload.get('shift_id'))
        expected = expected_cash(db, shift)
        db.execute("UPDATE shifts SET closed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), counted_cents=?, expected_cents=?, difference_cents=? WHERE id=?", (counted, expected, counted-expected, shift['id']))
        return {'id': shift['id'], 'expected_cents': expected, 'counted_cents': counted, 'difference_cents': counted-expected}


def create_product(path, payload):
    sku = text(payload.get('sku'), 'SKU', 60)
    name = text(payload.get('name'), 'Name')
    category = text(payload.get('category', 'General'), 'Category', 50)
    price = integer(payload.get('price_cents'), 'Price')
    tax = integer(payload.get('tax_bps', 0), 'Tax basis points', 0, 10000)
    stock = integer(payload.get('stock', 0), 'Stock', 0, 1_000_000)
    with transaction(path) as db:
        if db.execute('SELECT 1 FROM products WHERE lower(sku)=lower(?)', (sku,)).fetchone():
            raise DomainError('SKU already exists', 409)
        cur = db.execute('INSERT INTO products(sku,name,category,price_cents,tax_bps,stock) VALUES(?,?,?,?,?,?)', (sku,name,category,price,tax,stock))
        db.execute('INSERT INTO stock_movements(product_id,delta,reason,reference) VALUES(?,?,?,?)', (cur.lastrowid,stock,'Opening stock','product-create'))
        return {'id': cur.lastrowid}


def adjust_stock(path, product_id, payload):
    delta = integer(payload.get('delta'), 'Adjustment', -1_000_000, 1_000_000)
    reason = text(payload.get('reason'), 'Reason', 200)
    with transaction(path) as db:
        cur = db.execute('UPDATE products SET stock=stock+? WHERE id=? AND stock+? BETWEEN 0 AND 1000000', (delta,product_id,delta))
        if cur.rowcount != 1:
            raise DomainError('Product missing or adjustment exceeds stock limits', 409)
        db.execute('INSERT INTO stock_movements(product_id,delta,reason,reference) VALUES(?,?,?,?)', (product_id,delta,reason,'manual'))
        return {'stock': db.execute('SELECT stock FROM products WHERE id=?', (product_id,)).fetchone()[0]}


def sell(path, user_id, payload):
    key, digest = key_and_hash(payload)
    payment = payload.get('payment')
    if payment not in ('cash', 'card'):
        raise DomainError('Choose cash or externally collected card payment')
    lines = payload.get('items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise DomainError('A sale needs 1–100 product lines')
    with transaction(path) as db:
        old = db.execute('SELECT id,request_hash FROM sales WHERE idempotency_key=?', (key,)).fetchone()
        if old:
            if old['request_hash'] != digest:
                raise DomainError('Request key was already used for a different sale', 409)
            return receipt(db, old['id'])
        shift = require_shift(db, payload.get('shift_id'))
        subtotal = tax_total = 0; priced = []; seen = set()
        for line in lines:
            if not isinstance(line, dict):
                raise DomainError('Each sale line must be an object')
            pid = integer(line.get('product_id'), 'Product', 1)
            qty = integer(line.get('quantity'), 'Quantity', 1, 10000)
            if pid in seen:
                raise DomainError('Combine duplicate product lines')
            seen.add(pid)
            product = db.execute('SELECT * FROM products WHERE id=? AND active=1', (pid,)).fetchone()
            if not product or product['stock'] < qty:
                raise DomainError('A product is unavailable or has insufficient stock', 409)
            base = product['price_cents'] * qty
            tax = (base * product['tax_bps'] + 5000) // 10000
            subtotal += base; tax_total += tax
            priced.append((product, qty, base + tax))
        total = subtotal + tax_total
        integer(total, 'Sale total')
        tendered = integer(payload.get('tendered_cents', total), 'Tendered amount')
        if payment == 'card':
            tendered = total
        if tendered < total:
            raise DomainError('Cash received is less than the total')
        cur = db.execute('INSERT INTO sales(shift_id,user_id,subtotal_cents,tax_cents,total_cents,payment,tendered_cents,change_cents,idempotency_key,request_hash) VALUES(?,?,?,?,?,?,?,?,?,?)', (shift['id'],user_id,subtotal,tax_total,total,payment,tendered,tendered-total,key,digest))
        sale_id = cur.lastrowid
        for product, qty, line_total in priced:
            db.execute('UPDATE products SET stock=stock-? WHERE id=?', (qty,product['id']))
            db.execute('INSERT INTO sale_items(sale_id,product_id,sku,name,quantity,unit_cents,tax_bps,total_cents) VALUES(?,?,?,?,?,?,?,?)', (sale_id,product['id'],product['sku'],product['name'],qty,product['price_cents'],product['tax_bps'],line_total))
            db.execute('INSERT INTO stock_movements(product_id,delta,reason,reference) VALUES(?,?,?,?)', (product['id'],-qty,'Sale',f'sale:{sale_id}'))
        return receipt(db, sale_id)


def refund(path, user_id, sale_id, payload):
    key, digest = key_and_hash({**payload, 'sale_id': sale_id})
    reason = text(payload.get('reason'), 'Refund reason', 200)
    lines = payload.get('items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise DomainError('Select 1–100 receipt lines to refund')
    with transaction(path) as db:
        old = db.execute('SELECT * FROM refunds WHERE idempotency_key=?', (key,)).fetchone()
        if old:
            if old['request_hash'] != digest:
                raise DomainError('Request key was already used for a different refund', 409)
            return {'id': old['id'], 'total_cents': old['total_cents']}
        shift = require_shift(db, payload.get('shift_id'))
        sale = receipt(db, sale_id)
        available = {item['id']: item for item in sale['items']}
        total = 0; selected = []; seen = set()
        for entry in lines:
            if not isinstance(entry, dict): raise DomainError('Refund lines must be objects')
            item_id = integer(entry.get('sale_item_id'), 'Receipt line', 1)
            quantity = integer(entry.get('quantity'), 'Refund quantity', 1, 10000)
            if item_id not in available or item_id in seen:
                raise DomainError('Unknown or duplicate receipt line')
            seen.add(item_id); item = available[item_id]; previous = item['returned_quantity']
            if previous + quantity > item['quantity']:
                raise DomainError('Refund quantity exceeds the unreturned quantity', 409)
            amount = item['total_cents'] * (previous + quantity) // item['quantity'] - item['total_cents'] * previous // item['quantity']
            total += amount; selected.append((item,quantity,amount))
        cur = db.execute('INSERT INTO refunds(sale_id,shift_id,user_id,total_cents,payment,reason,idempotency_key,request_hash) VALUES(?,?,?,?,?,?,?,?)', (sale_id,shift['id'],user_id,total,sale['payment'],reason,key,digest))
        rid = cur.lastrowid
        for item, quantity, amount in selected:
            db.execute('UPDATE sale_items SET returned_quantity=returned_quantity+? WHERE id=?', (quantity,item['id']))
            db.execute('UPDATE products SET stock=stock+? WHERE id=?', (quantity,item['product_id']))
            db.execute('INSERT INTO refund_items(refund_id,sale_item_id,quantity,total_cents) VALUES(?,?,?,?)', (rid,item['id'],quantity,amount))
            db.execute('INSERT INTO stock_movements(product_id,delta,reason,reference) VALUES(?,?,?,?)', (item['product_id'],quantity,'Refund',f'refund:{rid}'))
        return {'id': rid, 'total_cents': total}


def update_product(path, product_id, payload):
    """Edit the catalog without changing historical receipt snapshots."""
    name = text(payload.get('name'), 'Name')
    category = text(payload.get('category'), 'Category', 50)
    price = integer(payload.get('price_cents'), 'Price')
    tax = integer(payload.get('tax_bps'), 'Tax basis points', 0, 10000)
    active = integer(payload.get('active', 1), 'Active', 0, 1)
    with transaction(path) as db:
        cur = db.execute('UPDATE products SET name=?,category=?,price_cents=?,tax_bps=?,active=? WHERE id=?', (name,category,price,tax,active,product_id))
        if cur.rowcount != 1:
            raise DomainError('Product not found', 404)
        return {'id': product_id}
