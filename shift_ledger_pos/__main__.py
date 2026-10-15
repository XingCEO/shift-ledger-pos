import argparse
import getpass
import os
import secrets
from . import create_app, create_user
from .db import connect, transaction
from .service import create_product


def main():
    parser = argparse.ArgumentParser(description='Shift Ledger POS — one register, an accountable cash drawer')
    parser.add_argument('--database', default='.local/pos.sqlite3')
    parser.add_argument('--port', type=int, default=5055)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--demo', action='store_true', help='Seed a new database with sample products and a generated admin password')
    parser.add_argument('--add-user', metavar='USERNAME')
    parser.add_argument('--role', choices=['admin','cashier'], default='cashier')
    args = parser.parse_args()
    app = create_app({'DATABASE': args.database})
    if args.add_user:
        create_user(args.database, args.add_user, getpass.getpass('New password (12+ characters): '), args.role)
        print('User created.'); return
    db = connect(args.database)
    try: has_users = db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
    finally: db.close()
    if not has_users:
        password = os.environ.get('POS_ADMIN_PASSWORD')
        if not password:
            if not args.demo: parser.error('Set POS_ADMIN_PASSWORD (12+ characters), or use --demo for a generated password')
            password = secrets.token_urlsafe(15)
            print(f'Demo admin password: {password}', flush=True)
        create_user(args.database, 'admin', password)
        with transaction(args.database) as db: db.execute("INSERT OR IGNORE INTO settings VALUES('currency','USD')")
        if args.demo:
            for sku,name,category,price,stock in [('COF-01','House espresso','Coffee',350,40),('COF-02','Oat flat white','Coffee',520,35),('COF-03','Cold brew','Coffee',480,28),('BAK-01','Almond croissant','Bakery',450,18),('BAK-02','Lemon loaf','Bakery',380,16),('RET-01','Weekend blend · 250g','Retail',1800,12),('RET-02','Stoneware cup','Retail',2400,8),('BAK-03','Sea salt cookie','Bakery',280,24)]:
                create_product(args.database, dict(sku=sku,name=name,category=category,price_cents=price,stock=stock,tax_bps=0))
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__': main()
