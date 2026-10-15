"""Disposable browser-test server. Never points at a user's database."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="shift-ledger-e2e-") as directory:
    environment = {**os.environ, "POS_ADMIN_PASSWORD": "browser-test-password"}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "shift_ledger_pos",
            "--demo",
            "--database",
            str(Path(directory) / "test.sqlite3"),
            "--port",
            "5055",
        ],
        env=environment,
        check=True,
    )
