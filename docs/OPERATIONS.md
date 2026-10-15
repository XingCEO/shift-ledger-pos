# Operating Shift Ledger

## Data and users

The default database is `.local/pos.sqlite3`; the session signing key is `.local/session.key` (created with mode 0600). Both are ignored by Git. Keep the database, its WAL files and signing key private. Use `--database /absolute/path/shop.sqlite3` to choose another location.

Create a cashier interactively:

```bash
python -m shift_ledger_pos --database .local/pos.sqlite3 --add-user alex --role cashier
```

The command prompts for a password and exits. Managers (`admin`) can create/edit products, adjust stock, refund and export. Cashiers can view products and receipts, sell and open/close shifts. All employees share the single register and can see its transaction history.

To change the installation-wide currency before trading, update the `settings` row with key `currency` using a trusted SQLite administration tool. Use an ISO 4217 currency with two decimal minor units. Never relabel a database that already contains transactions in another currency.

## Deployment

For a private network deployment, install a production WSGI server (for example Gunicorn on Unix) and use one worker with threads:

```bash
python -m pip install gunicorn
POS_DATABASE=/absolute/path/shop.sqlite3 POS_HTTPS=1 \
  gunicorn --workers 1 --threads 4 --bind 127.0.0.1:5055 'shift_ledger_pos:create_app()'
```

Initialize the database and manager account with the CLI first. Put a configured HTTPS reverse proxy in front of this loopback listener. `POS_HTTPS=1` enables secure session cookies and therefore requires HTTPS at the browser. No reverse-proxy headers are implicitly trusted. Login limiting uses the server-observed address; a proxy may cause all clients to share a limit.

Do not expose the Flask development server or use debug mode for real operations. Restrict filesystem access and network reachability. Keep dependencies patched. The UI has no external scripts, fonts or analytics.

## Backup and restore

Use SQLite's backup API or the sqlite3 `.backup` command while the app is running. Copying only the main file during WAL activity can lose committed data. Verify a backup with `PRAGMA integrity_check` on a separate copy, and test a restore in a separate directory. Stop the application before replacing its live database. Preserve the signing key if existing sessions should remain valid; remove it while stopped to force all users to sign in again.

## Recovery rules

- Network interruption after checkout: retry the preserved request. Do not create a new request until its outcome is known.
- Browser tab lost: check the latest receipts before recreating a sale.
- Incorrect sale: record a return; do not edit the database to erase it.
- Cash mismatch: enter the counted amount. The difference is recorded.
- Stock mismatch: use an adjustment with a reason. Negative resulting stock is rejected.
- A return from an earlier shift affects the currently open drawer.

No scheduled tasks, external emails or payment transfers are performed by the application.
