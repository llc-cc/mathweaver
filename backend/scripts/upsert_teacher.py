"""Create or update a MySQL-backed teacher account without CLI password arguments."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        required=True,
        help="read the plaintext password from standard input",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    email = args.email.strip().lower()
    if not email or "@" not in email:
        raise SystemExit("--email must be a valid email-shaped identity")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise SystemExit("a non-empty password is required on stdin")
    password_hash = generate_password_hash(password)
    password = ""

    from backend.storage.database import connect_database

    created = False
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds")
    with connect_database() as db:
        row = db.execute("SELECT id FROM users WHERE email = ? FOR UPDATE", (email,)).fetchone()
        if row:
            user_id = int(row["id"])
            db.execute(
                "UPDATE users SET password_hash = ?, can_teach = 1 WHERE id = ?",
                (password_hash, user_id),
            )
        else:
            created = True
            db.execute(
                "INSERT INTO users (email, password_hash, created_at, can_teach) VALUES (?, ?, ?, 1)",
                (email, password_hash, now),
            )
            user_id = int(db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"])
    print(json.dumps({"ok": True, "created": created, "user_id": user_id, "email": email}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
