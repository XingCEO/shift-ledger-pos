---
name: transaction-invariant-review
description: Review or extend Shift Ledger POS transaction behavior, especially checkout, stock, partial refunds, idempotency and cash reconciliation. Use when changing money or inventory paths in this repository.
---

# Transaction invariant review

Work inside the Shift Ledger checkout. Read `docs/API.md` and complete relevant functions in `shift_ledger_pos/service.py` before proposing changes. This Skill does not authorize production database access, refunds, deployment or external payment actions.

## Trace the mutation

1. Identify the HTTP role/CSRF gate in `shift_ledger_pos/__init__.py`.
2. Follow the service entry point to `transaction()` in `db.py`. Reads used for stock or refund decisions must occur after the write transaction begins.
3. List every touched table and the expected before/after relationship. Never infer business correctness from HTTP status alone.

## Challenge these invariants

- Two buyers race for the last unit: exactly one new sale succeeds.
- Two identical requests race: one receipt and one stock deduction.
- Same key, changed body: reject without mutation.
- Failure on a later cart line: no earlier stock or receipt writes survive.
- Product price changes after sale: the refund still uses the original receipt.
- Several partial returns: amounts sum exactly to the original line total.
- Return after the original shift closed: cash changes in the current shift only.
- Cashier attempts inventory writes or refunds: HTTP denial before business mutation.

Choose only scenarios relevant to the change, then add a regression test asserting database state, not just returned JSON. Use `TemporaryDirectory` and synthetic records. Never point tests at `.local/pos.sqlite3`.

## Run and report

Run `python -m unittest discover -v`. If the user workflow changes, run `npm run test:e2e` with the intended Python interpreter set in `PYTHON`. The browser harness creates its own disposable database.

Report the affected invariant, a minimal before/after example, the exact checks run and any untested assumptions. A green suite does not establish fiscal compliance, payment-provider correctness or safe production deployment.
