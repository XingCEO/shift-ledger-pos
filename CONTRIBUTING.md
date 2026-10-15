# Contributing

Use synthetic shop data. Describe the operator workflow and the invariant a change should preserve. Add a failing regression test, implement the smallest coherent change, then run the Python suite. Run browser tests for changed UI behavior. Do not add real credentials, customer data or local databases to a pull request.

Money stays in integer minor units. Every inventory movement needs a ledger entry. Historical receipts are immutable; corrections are explicit returns. Avoid new dependencies unless they materially simplify a supported use case. Update API and operating documentation when behavior changes.
