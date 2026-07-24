from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .core import (
    discover,
    download_top,
    generate_review_index,
    inspect_url,
    list_candidates,
    load_config,
    produce_top,
)


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def doctor() -> int:
    checks = {name: shutil.which(name) for name in ("python3", "yt-dlp", "ffmpeg", "ffprobe")}
    tts = shutil.which("say") or shutil.which("espeak-ng") or shutil.which("espeak")
    checks["tts"] = tts
    checks["ptbr_voice"] = "Luciana (macOS)" if shutil.which("say") else ("espeak pt-br (fallback)" if tts else None)
    checks["ready"] = all(checks[name] for name in ("python3", "yt-dlp", "ffmpeg", "ffprobe")) and bool(tts)
    print_json(checks)
    return 0 if checks["ready"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaguartv", description="Standalone JaguarTV content factory")
    parser.add_argument("--config", default="config/pipeline.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--platform", action="append", choices=["youtube", "bilibili", "douyin"])
    discover_parser.add_argument("--limit", type=int)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("url")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status")
    list_parser.add_argument("--limit", type=int, default=50)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--limit", type=int, default=5)
    download_parser.add_argument("--candidate")

    produce_parser = subparsers.add_parser("produce")
    produce_parser.add_argument("--limit", type=int, default=1)
    produce_parser.add_argument("--candidate")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--discover", type=int, default=5)
    run_parser.add_argument("--download", type=int, default=1)
    run_parser.add_argument("--produce", type=int, default=1)

    subparsers.add_parser("review")

    ui_parser = subparsers.add_parser("ui")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor()
    config = load_config(Path(args.config))
    if args.command == "discover":
        print_json(discover(config, platforms=args.platform, limit=args.limit))
    elif args.command == "ingest":
        print(inspect_url(config, args.url))
    elif args.command == "list":
        print_json([dict(row) for row in list_candidates(config, args.status, args.limit)])
    elif args.command == "download":
        print_json(download_top(config, args.limit, args.candidate))
    elif args.command == "produce":
        print_json(produce_top(config, args.limit, args.candidate))
    elif args.command == "run":
        print_json({
            "discover": discover(config, limit=args.discover),
            "download": download_top(config, args.download),
            "produce": produce_top(config, args.produce),
            "review": str(generate_review_index(config)),
        })
    elif args.command == "review":
        print(generate_review_index(config))
    elif args.command == "ui":
        from .dashboard import serve_dashboard

        serve_dashboard(config, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
