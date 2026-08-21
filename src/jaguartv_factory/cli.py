from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .pyvideotrans_adapter import pyvideotrans_available
from .core import (
    analyze_candidate,
    connect_db,
    discover,
    download_top,
    generate_review_index,
    inspect_url,
    list_candidates,
    load_config,
    produce_top,
    require_binary,
)
from .mediacrawler import ingest_mediacrawler_jsonl
from .publisher import dry_run_approved_queue, enqueue_approved_publication
from .publish_worker import publish_due_once, run_publish_worker
from .youtube_analytics import backfill_report, run_analytics_worker, set_backfill_status
from .reaction import REACTION_MODES
from .server_store import save_upload
from .strategy import AUDIO_POLICIES, CONTENT_TYPES, SEGMENT_STRATEGIES


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def doctor() -> int:
    environment_bin = Path(sys.executable).parent
    checks = {"python3": shutil.which("python3") or str(sys.executable)}
    for name in ("yt-dlp", "ffmpeg", "ffprobe"):
        try:
            checks[name] = require_binary(name)
        except Exception:
            checks[name] = shutil.which(name) or (str(environment_bin / name) if (environment_bin / name).is_file() else None)
    checks["tesseract"] = shutil.which("tesseract")
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        checks["paddleocr"] = True
    except ImportError:
        checks["paddleocr"] = False
    try:
        checks["pyvideotrans"] = pyvideotrans_available(load_config(Path("config/pipeline.yaml")))[1]
    except Exception as error:
        checks["pyvideotrans"] = f"unavailable:{error}"
    try:
        import edge_tts  # noqa: F401
        checks["edge_tts"] = True
    except ImportError:
        checks["edge_tts"] = False
    tts = shutil.which("say") or shutil.which("espeak-ng") or shutil.which("espeak")
    checks["tts"] = tts
    checks["ptbr_voice"] = "Luciana (macOS)" if shutil.which("say") else ("espeak pt-br (fallback)" if tts else None)
    codex_home = Path.home() / ".codex"
    skill_names = (
        "douyin-downloader", "tiktok-crawling", "agent-reach", "dlazy-merge",
        "bilibili-video-crawler", "bilibili-downloader-plus", "yt-dlp-downloader",
        "eye-yt-dlp", "bilibili-video-parser", "all-translate", "nologo-open-api",
        "tencentcloud-tts", "apify-ultimate-scraper", "openclaw-video-editor",
        "wavespeed-watermark-remover", "tencent-mps", "google-trends", "speech-recognition",
    )
    checks["skillhub_skills"] = {
        name: (codex_home / "skills" / name / "SKILL.md").exists() for name in skill_names
    }
    checks["mediacrawler_repo"] = next(
        (
            str(path) for path in (
                Path.cwd().parent / "MediaCrawler",
                Path.cwd() / "MediaCrawler",
                Path.home() / "MediaCrawler",
                Path("/opt/MediaCrawler"),
            )
            if (path / ".git").exists()
        ),
        "",
    )
    checks["ready"] = all(checks[name] for name in ("python3", "yt-dlp", "ffmpeg", "ffprobe")) and (
        bool(checks["edge_tts"]) or bool(tts)
    )
    print_json(checks)
    return 0 if checks["ready"] else 1


def add_strategy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--content-type", default="auto", choices=("auto", *CONTENT_TYPES))
    parser.add_argument("--segment-strategy", default="auto", choices=("auto", *SEGMENT_STRATEGIES))
    parser.add_argument("--audio-policy", default="auto", choices=("auto", *AUDIO_POLICIES))
    parser.add_argument("--max-segments", type=int)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--reaction-mode", default="none", choices=REACTION_MODES)
    parser.add_argument("--reaction-source")
    parser.add_argument("--source-volume", type=float, default=0.72)
    parser.add_argument("--reaction-volume", type=float, default=1.0)
    parser.add_argument("--reaction-position", default="bottom_right", choices=("top_left", "top_right", "bottom_left", "bottom_right"))
    parser.add_argument("--batch-label")
    parser.add_argument(
        "--rights-status",
        choices=("MANUAL_REVIEW", "OWNED", "LICENSED", "PUBLIC_DOMAIN", "CC_BY", "VERIFIED"),
    )


def strategy_options(args: argparse.Namespace) -> dict[str, object]:
    fields = (
        "content_type", "segment_strategy", "audio_policy", "max_segments", "max_duration",
        "reaction_mode", "reaction_source", "source_volume", "reaction_volume", "reaction_position",
        "batch_label", "rights_status",
    )
    return {field: getattr(args, field) for field in fields if getattr(args, field, None) is not None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaguartv", description="Standalone JaguarTV content factory")
    parser.add_argument("--config", default="config/pipeline.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--platform", action="append", choices=["youtube", "bilibili", "douyin", "xiaohongshu", "tiktok", "facebook"])
    discover_parser.add_argument("--limit", type=int)
    discover_parser.add_argument("--keyword", action="append", help="Override configured keyword file for a focused discovery run.")

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("url")

    crawler_parser = subparsers.add_parser("ingest-mediacrawler")
    crawler_parser.add_argument("path", type=Path)
    crawler_parser.add_argument("--platform", choices=("douyin", "bilibili", "xiaohongshu", "tiktok"))
    crawler_parser.add_argument("--min-likes", type=int, default=0)
    crawler_parser.add_argument("--min-views", type=int, default=0)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status")
    list_parser.add_argument("--limit", type=int, default=50)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--limit", type=int, default=5)
    download_parser.add_argument("--candidate")

    produce_parser = subparsers.add_parser("produce")
    produce_parser.add_argument("--limit", type=int, default=1)
    produce_parser.add_argument("--candidate")
    add_strategy_arguments(produce_parser)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--candidate", required=True)
    add_strategy_arguments(analyze_parser)

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("path", type=Path)
    upload_parser.add_argument("--kind", choices=("source", "reaction"), required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--discover", type=int, default=5)
    run_parser.add_argument("--download", type=int, default=1)
    run_parser.add_argument("--produce", type=int, default=1)

    subparsers.add_parser("review")

    ui_parser = subparsers.add_parser("ui")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8787)

    publish_queue_parser = subparsers.add_parser("publish-queue")
    publish_queue_parser.add_argument("--candidate")
    publish_queue_parser.add_argument("--dry-run", action="store_true")

    publish_worker_parser = subparsers.add_parser("publish-worker")
    publish_worker_parser.add_argument("--once", action="store_true")
    publish_worker_parser.add_argument("--dry-run", action="store_true")
    publish_worker_parser.add_argument("--sleep", type=int, default=60)
    publish_worker_parser.add_argument("--limit", type=int, default=3)

    analytics_worker_parser = subparsers.add_parser("youtube-analytics-worker")
    analytics_worker_parser.add_argument("--once", action="store_true")
    analytics_worker_parser.add_argument("--sleep", type=int, default=60)
    analytics_worker_parser.add_argument("--limit", type=int, default=50)

    analytics_backfill_parser = subparsers.add_parser("youtube-analytics-backfill")
    analytics_backfill_parser.add_argument("--execute", action="store_true")
    analytics_backfill_parser.add_argument("--rate-limit-per-minute", type=int, default=6)
    analytics_backfill_parser.add_argument("--run-id", type=int)
    analytics_backfill_parser.add_argument("--action", choices=("pause", "resume", "cancel"))

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--candidate")
    publish_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("publish-status")
    return parser


def publication_status_rows(config: dict[str, object]) -> list[dict[str, object]]:
    connection = connect_db(config)
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT id,candidate_id,platform,account,account_label,scheduled_at,status,
                   youtube_video_id,youtube_url,error,created_at,updated_at
            FROM publications
            ORDER BY COALESCE(scheduled_at,created_at) DESC,id DESC
            LIMIT 200
            """
        )
    ]


def enqueue_approved_rows(config: dict[str, object], candidate_id: str = "") -> list[dict[str, object]]:
    connection = connect_db(config)
    if candidate_id:
        rows = connection.execute(
            "SELECT id FROM candidates WHERE id=? AND status='APPROVED'", (candidate_id,)
        ).fetchall()
    else:
        rows = connection.execute("SELECT id FROM candidates WHERE status='APPROVED' ORDER BY updated_at DESC").fetchall()
    return [enqueue_approved_publication(config, str(row["id"])) for row in rows]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor()
    config = load_config(Path(args.config))
    if args.command == "discover":
        print_json(discover(config, platforms=args.platform, limit=args.limit, keyword_overrides=args.keyword))
    elif args.command == "ingest":
        print(inspect_url(config, args.url))
    elif args.command == "ingest-mediacrawler":
        print_json(ingest_mediacrawler_jsonl(
            config,
            args.path,
            platform=args.platform,
            min_likes=args.min_likes,
            min_views=args.min_views,
        ))
    elif args.command == "list":
        print_json([dict(row) for row in list_candidates(config, args.status, args.limit)])
    elif args.command == "download":
        print_json(download_top(config, args.limit, args.candidate))
    elif args.command == "produce":
        print_json(produce_top(config, args.limit, args.candidate, options=strategy_options(args)))
    elif args.command == "analyze":
        print_json(analyze_candidate(config, args.candidate, strategy_options(args)))
    elif args.command == "upload":
        path = args.path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open("rb") as handle:
            print_json(save_upload(config, handle, filename=path.name, kind=args.kind, content_length=path.stat().st_size))
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
    elif args.command == "publish-queue":
        if args.dry_run:
            print_json(dry_run_approved_queue(config, candidate_id=args.candidate or ""))
        else:
            print_json(enqueue_approved_rows(config, args.candidate or ""))
    elif args.command == "publish-worker":
        if args.dry_run:
            print_json(publish_due_once(config, limit=args.limit, dry_run=True))
        else:
            run_publish_worker(config, once=args.once, sleep_sec=args.sleep, limit=args.limit)
    elif args.command == "youtube-analytics-worker":
        run_analytics_worker(config, once=args.once, sleep_sec=args.sleep, limit=args.limit)
    elif args.command == "youtube-analytics-backfill":
        if args.run_id or args.action:
            if not args.run_id or not args.action:
                raise ValueError("--run-id and --action must be provided together")
            print_json(set_backfill_status(config, args.run_id, args.action))
        else:
            print_json(backfill_report(
                config,
                dry_run=not args.execute,
                rate_limit_per_minute=args.rate_limit_per_minute,
            ))
    elif args.command == "publish":
        if not args.candidate:
            raise ValueError("--candidate is required")
        if args.dry_run:
            print_json(dry_run_approved_queue(config, candidate_id=args.candidate))
        else:
            print_json(enqueue_approved_publication(config, args.candidate))
    elif args.command == "publish-status":
        print_json(publication_status_rows(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
