# HTTP contract

All mutations accept a JSON object and require `X-CSRF-Token`. Obtain a token and the current user from `GET /api/session`; sign in with `POST /api/login` containing `username` and `password`. Login rotates the token. Keep the session cookie. `POST /api/logout` clears it.

| Method and path | Payload / purpose |
| --- | --- |
| GET `/api/state` | Products, active/recent shifts, latest 50 receipts and refunds |
| POST `/api/products` | Manager: sku, name, category, price_cents, tax_bps, stock |
| POST `/api/products/{id}` | Manager: name, category, price_cents, tax_bps, active (0/1) |
| POST `/api/products/{id}/stock` | Manager: delta (signed integer), reason |
| POST `/api/shifts` | opening_cents |
| POST `/api/shifts/close` | shift_id, counted_cents |
| POST `/api/sales` | See below |
| POST `/api/sales/{id}/refunds` | Manager: see below |
| GET `/receipts/{id}` | Printable HTML receipt |
| GET `/api/export/sales.csv` | Manager: all sales, UTC timestamps and minor-unit amounts |

Sale example:

```json
{"idempotency_key":"client-generated-unique-key","shift_id":1,"payment":"cash","tendered_cents":1000,"items":[{"product_id":1,"quantity":2}]}
```

Refund example:

```json
{"idempotency_key":"another-unique-request-key","shift_id":1,"reason":"Customer return","items":[{"sale_item_id":1,"quantity":1}]}
```

Never trust browser totals. The server prices sales from the current catalog and refunds from the original receipt. Sale and refund keys are separate namespaces, 8–100 alphanumeric/underscore/hyphen characters. Use a UUID per operation and preserve the exact body during retries. Repeating a successful request returns its original result even after the shift closes. Mutations are not generally idempotent: product creation, stock adjustments and shift operations do not support retry keys.

Errors use `{"error":"message"}` with 400 (validation), 401 (authentication), 403 (role/CSRF), 404 (missing), 409 (state conflict), 413 (too large), 415 (content type), 429 (login limit) or 503 (database busy). Sales/refunds return 201 even for a successful replay. Prices and counts must be integers; booleans are rejected. Payment is `cash` or `card`, where card means payment was externally collected.
