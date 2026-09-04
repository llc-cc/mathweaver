"""Upgrade the configured MySQL database to the latest Alembic revision."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    command.upgrade(config, "head")
    print("MySQL schema is at Alembic head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
