#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from jaguartv_factory.original_uploader import upload_original


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a completed original schedule video into Original Factory review"
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--server-url", default=os.environ.get("JAGUARTV_DASHBOARD_URL", ""))
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    result = upload_original(
        args.video,
        metadata,
        base_url=args.server_url,
        upload_token=os.environ.get("JAGUARTV_UPLOAD_TOKEN", ""),
    )
    print(json.dumps({"id": result["id"], "status": result["status_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
