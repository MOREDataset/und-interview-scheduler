"""Command-line helpers.

    python manage.py init-db                       create tables (safe to re-run)
    python manage.py seed-demo                     load the fictional demo search
    python manage.py create-coordinator EMAIL "Full Name"   (asks for a password)
    python manage.py backup PATH                   copy the SQLite database to PATH
"""
from __future__ import annotations

import getpass
import shutil
import sys

from scheduler_app.core.auth import create_coordinator
from scheduler_app.core.config import get_settings
from scheduler_app.core.db import init_db, session_scope
from scheduler_app.core.seed import seed_demo


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "init-db":
        init_db()
        print("Database ready:", get_settings().database_url.split("@")[-1])
    elif cmd == "seed-demo":
        init_db()
        with session_scope() as s:
            seed_demo(s)
        print("Demo data loaded (fictional). Coordinator: coordinator@example.edu")
    elif cmd == "create-coordinator" and len(argv) >= 3:
        init_db()
        pw = getpass.getpass("Password (min. 8 characters): ")
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords don't match.")
            return 1
        with session_scope() as s:
            create_coordinator(s, argv[1], argv[2], pw)
        print("Coordinator created:", argv[1])
    elif cmd == "backup" and len(argv) >= 2:
        url = get_settings().database_url
        if not url.startswith("sqlite:///"):
            print("For PostgreSQL use pg_dump (see README).")
            return 1
        shutil.copy2(url.split(":///", 1)[1], argv[1])
        print("Backup written to", argv[1])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
