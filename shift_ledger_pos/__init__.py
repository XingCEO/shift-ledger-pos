"""Shift Ledger POS application factory."""
import csv
import io
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from functools import wraps
from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from .db import connect, initialize, transaction
from . import service


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DATABASE=os.environ.get('POS_DATABASE', '.local/pos.sqlite3'), MAX_CONTENT_LENGTH=256*1024,
                      SESSION_COOKIE_NAME='shift_ledger_session',SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('POS_HTTPS') == '1', PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    if config: app.config.update(config)
    initialize(app.config['DATABASE'])
    if not app.config.get('SECRET_KEY'):
        key_path = Path(app.config['DATABASE']).parent / 'session.key'
        try:
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            app.secret_key = key_path.read_text()
        else:
            with os.fdopen(fd, 'w') as stream:
                app.secret_key = secrets.token_hex(32); stream.write(app.secret_key)
    failures = {}; lock = threading.Lock()

    def user():
        uid = session.get('user_id')
        if not uid: return None
        db = connect(app.config['DATABASE'])
        try:
            row = db.execute('SELECT id,username,role FROM users WHERE id=?', (uid,)).fetchone()
            return dict(row) if row else None
        finally: db.close()

    def require_user(admin=False):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                account = user()
                if not account: raise service.DomainError('Sign in to continue', 401)
                if admin and account['role'] != 'admin': raise service.DomainError('Manager access required', 403)
                return fn(account, *args, **kwargs)
            return wrapped
        return decorate

    @app.before_request
    def protect_mutations():
        if request.method in ('POST','PUT','PATCH','DELETE'):
            if not request.is_json: raise service.DomainError('Use application/json', 415)
            supplied = request.headers.get('X-CSRF-Token', '')
            expected = session.get('csrf', '')
            if not expected or not secrets.compare_digest(supplied, expected):
                raise service.DomainError('Session expired; reload and try again', 403)
            body = request.get_json(silent=True)
            if not isinstance(body, dict): raise service.DomainError('A JSON object is required')

    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith('/api/') or request.path.startswith('/receipts/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(service.DomainError)
    def domain_error(error): return jsonify(error=str(error)), error.status

    @app.errorhandler(ValueError)
    def malformed_value(error): return jsonify(error='Invalid request value'), 400

    @app.errorhandler(413)
    def too_large(error): return jsonify(error='Request is too large'), 413

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.error('Database operation failed: %s', error)
        return jsonify(error='The register is busy. Retry using the same request.'), 503

    @app.get('/')
    def index(): return render_template('index.html')

    @app.get('/api/session')
    def current_session():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return jsonify(user=user(), csrf=session['csrf'])

    @app.post('/api/login')
    def login():
        payload = request.get_json(); ip = request.remote_addr or 'local'; now = time.monotonic()
        username = service.text(payload.get('username'), 'Username', 60)
        password = payload.get('password')
        if not isinstance(password, str) or len(password) > 1000: raise service.DomainError('Invalid credentials', 401)
        with lock:
            for address in list(failures):
                failures[address] = [t for t in failures[address] if now-t < 300]
                if not failures[address]: failures.pop(address)
            if len(failures.get(ip, [])) >= 5: raise service.DomainError('Too many attempts. Try again in five minutes.', 429)
        db = connect(app.config['DATABASE'])
        try: row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        finally: db.close()
        if not row or not check_password_hash(row['password_hash'], password):
            with lock: failures.setdefault(ip, []).append(now)
            raise service.DomainError('Invalid credentials', 401)
        with lock: failures.pop(ip, None)
        session.clear(); session.permanent = True; session['user_id'] = row['id']; session['csrf'] = secrets.token_urlsafe(32)
        return jsonify(user=user(), csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear(); return jsonify(ok=True)

    @app.get('/api/state')
    @require_user()
    def get_state(account): return jsonify(service.state(app.config['DATABASE']))

    @app.post('/api/products')
    @require_user(admin=True)
    def product_create(account): return jsonify(service.create_product(app.config['DATABASE'], request.get_json())), 201

    @app.post('/api/products/<int:product_id>')
    @require_user(admin=True)
    def product_update(account, product_id): return jsonify(service.update_product(app.config['DATABASE'], product_id, request.get_json()))

    @app.post('/api/products/<int:product_id>/stock')
    @require_user(admin=True)
    def stock_adjust(account, product_id): return jsonify(service.adjust_stock(app.config['DATABASE'], product_id, request.get_json()))

    @app.post('/api/shifts')
    @require_user()
    def shift_open(account): return jsonify(service.open_shift(app.config['DATABASE'], account['id'], request.get_json())), 201

    @app.post('/api/shifts/close')
    @require_user()
    def shift_close(account): return jsonify(service.close_shift(app.config['DATABASE'], request.get_json()))

    @app.post('/api/sales')
    @require_user()
    def sale_create(account): return jsonify(service.sell(app.config['DATABASE'], account['id'], request.get_json())), 201

    @app.post('/api/sales/<int:sale_id>/refunds')
    @require_user(admin=True)
    def sale_refund(account, sale_id): return jsonify(service.refund(app.config['DATABASE'], account['id'], sale_id, request.get_json())), 201

    @app.get('/receipts/<int:sale_id>')
    @require_user()
    def receipt_page(account, sale_id):
        db = connect(app.config['DATABASE'])
        try:
            sale = service.receipt(db, sale_id)
            currency = (db.execute("SELECT value FROM settings WHERE key='currency'").fetchone() or ['USD'])[0]
            return render_template('receipt.html', sale=sale, currency=currency)
        finally: db.close()

    @app.get('/api/export/sales.csv')
    @require_user(admin=True)
    def export(account):
        db = connect(app.config['DATABASE'])
        try:
            rows = db.execute('SELECT s.id,s.created_at,s.shift_id,s.payment,s.subtotal_cents,s.tax_cents,s.total_cents,COALESCE((SELECT sum(r.total_cents) FROM refunds r WHERE r.sale_id=s.id),0) AS refunded_cents FROM sales s ORDER BY s.id').fetchall()
            out = io.StringIO(newline=''); writer = csv.writer(out)
            writer.writerow(['receipt','created_at_utc','shift','payment','subtotal_minor','tax_minor','total_minor','refunded_minor'])
            writer.writerows(tuple(r) for r in rows)
            return Response(out.getvalue(), mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=sales.csv'})
        finally: db.close()

    return app


def create_user(path, username, password, role='admin'):
    if role not in ('admin','cashier'): raise ValueError('invalid role')
    service.text(username, 'Username', 60)
    if len(password) < 12: raise ValueError('Use a password of at least 12 characters')
    with transaction(path) as db:
        if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone(): raise ValueError('User already exists')
        db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)', (username,generate_password_hash(password),role))
