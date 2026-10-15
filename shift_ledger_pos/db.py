"""SQLite storage. Every business mutation acquires a write transaction first."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','cashier'))
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
 price_cents INTEGER NOT NULL CHECK(price_cents>=0), tax_bps INTEGER NOT NULL CHECK(tax_bps BETWEEN 0 AND 10000),
 stock INTEGER NOT NULL CHECK(stock>=0), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS shifts (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), opening_cents INTEGER NOT NULL,
 opened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), closed_at TEXT,
 counted_cents INTEGER, expected_cents INTEGER, difference_cents INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_shift ON shifts((1)) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS sales (
 id INTEGER PRIMARY KEY, shift_id INTEGER NOT NULL REFERENCES shifts(id), user_id INTEGER NOT NULL REFERENCES users(id),
 subtotal_cents INTEGER NOT NULL, tax_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL,
 payment TEXT NOT NULL CHECK(payment IN ('cash','card')), tendered_cents INTEGER NOT NULL,
 change_cents INTEGER NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS sale_items (
 id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id), product_id INTEGER NOT NULL REFERENCES products(id),
 sku TEXT NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
 unit_cents INTEGER NOT NULL, tax_bps INTEGER NOT NULL, total_cents INTEGER NOT NULL,
 returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK(returned_quantity BETWEEN 0 AND quantity)
);
CREATE TABLE IF NOT EXISTS refunds (
 id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id), shift_id INTEGER NOT NULL REFERENCES shifts(id),
 user_id INTEGER NOT NULL REFERENCES users(id), total_cents INTEGER NOT NULL, payment TEXT NOT NULL,
 reason TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS refund_items (
 id INTEGER PRIMARY KEY, refund_id INTEGER NOT NULL REFERENCES refunds(id), sale_item_id INTEGER NOT NULL REFERENCES sale_items(id),
 quantity INTEGER NOT NULL, total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_movements (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), delta INTEGER NOT NULL,
 reason TEXT NOT NULL, reference TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
'''


def connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
    finally:
        db.close()


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()
