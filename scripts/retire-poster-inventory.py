#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jaguartv_factory.core import load_config
from jaguartv_factory.poster_retirement import backup_and_clear_poster_inventory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up and clear only the retired poster inventory"
    )
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.yaml"))
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument(
        "--confirm",
        required=True,
        help="must be exactly CLEAR-LEGACY-POSTERS",
    )
    args = parser.parse_args()
    if args.confirm != "CLEAR-LEGACY-POSTERS":
        raise SystemExit("refusing to clear: invalid --confirm value")
    report = backup_and_clear_poster_inventory(load_config(args.config), args.backup_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
