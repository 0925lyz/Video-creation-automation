from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from .audit import audit_review_inventory, repair_review_inventory, set_repair_run_status
from .cta import delete_cta_asset, import_cta_path, list_cta_assets
from .binaries import require_binary
from .krillinai_adapter import krillinai_available
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
)
from .mediacrawler import ingest_mediacrawler_jsonl
from .integrations import integration_status, load_integration_manifest, sync_integrations
from .publisher import dry_run_approved_queue, enqueue_approved_publication
from .publish_worker import publish_due_once, run_publish_worker
from .youtube_analytics import backfill_report, run_analytics_worker, set_backfill_status
from .reaction import REACTION_MODES
from .server_store import ALLOWED_IMAGE_EXTENSIONS, ALLOWED_MEDIA_EXTENSIONS, save_upload
from .strategy import AUDIO_POLICIES, CONTENT_TYPES, SEGMENT_STRATEGIES
from .sources import f2_runtime_status, yt_dlp_runtime_status
from .trends import run_trends_job


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def binary_check(name: str, *, required: bool = True) -> dict[str, object]:
    try:
        return {"name": name, "path": require_binary(name), "required": required, "ok": True}
    except Exception as error:
        return {
            "name": name,
            "path": None,
            "required": required,
            "ok": False,
            "reason": str(error),
        }


def doctor(config_path: Path = Path("config/pipeline.yaml")) -> int:
    config = load_config(config_path)
    environment_bin = Path(sys.executable).parent
    required_checks = {
        "python3": {
            "name": "python3",
            "path": shutil.which("python3") or str(sys.executable),
            "required": True,
            "ok": True,
        },
        "yt-dlp": binary_check("yt-dlp"),
        "ffmpeg": binary_check("ffmpeg"),
        "ffprobe": binary_check("ffprobe"),
        "tesseract": binary_check("tesseract"),
        "demucs": {
            "name": "demucs",
            "path": "python:demucs" if importlib.util.find_spec("demucs") else None,
            "required": True,
            "ok": importlib.util.find_spec("demucs") is not None,
        },
    }
    optional_checks = {
        "deno": binary_check("deno", required=False),
        "node": binary_check("node", required=False),
    }
    checks = {name: item["path"] for name, item in required_checks.items()}
    try:
        krillinai_ok, krillinai_reason = krillinai_available(config)
        required_checks["krillinai"] = {
            "name": "krillinai",
            "path": krillinai_reason if krillinai_ok else None,
            "required": True,
            "ok": krillinai_ok,
            "reason": "" if krillinai_ok else krillinai_reason,
        }
        checks["krillinai"] = krillinai_reason
    except Exception as error:
        required_checks["krillinai"] = {
            "name": "krillinai", "path": None, "required": True, "ok": False,
            "reason": str(error),
        }
        checks["krillinai"] = f"unavailable:{error}"
    project_root = Path(config.get("_root") or Path.cwd())
    skill_names = (
        "jaguartv-content-factory", "jaguartv-copywriter", "content-strategy",
        "copy-editing", "copywriting", "captions-overlay", "social", "analytics",
        "attribution",
    )
    checks["project_agent_skills"] = {
        name: (project_root / ".agents" / "skills" / name / "SKILL.md").is_file()
        for name in skill_names
    }
    checks["mediacrawler_repo"] = next(
        (
            str(path) for path in (
                Path.cwd().parent / "MediaCrawler",
                Path.cwd() / "MediaCrawler",
                project_root / "workspace" / "external_tools" / "MediaCrawler",
                Path.home() / "MediaCrawler",
                Path("/opt/MediaCrawler"),
            )
            if (path / ".git").exists()
        ),
        "",
    )
    required_ok = all(bool(item["ok"]) for item in required_checks.values())
    checks["required"] = required_checks
    checks["optional"] = optional_checks
    checks["yt_dlp"] = yt_dlp_runtime_status()
    f2_options = dict((((config.get("sources") or {}).get("adapters") or {}).get("douyin") or {}))
    f2_options["_root"] = str(project_root)
    checks["f2"] = f2_runtime_status(f2_options)
    checks["degraded"] = [
        name for name, item in optional_checks.items()
        if not item["ok"]
    ]
    checks["ready"] = required_ok
    if not checks["ready"]:
        checks["next_steps"] = (
            "Run scripts/bootstrap.sh, or scripts/server-install.sh on Ubuntu/Debian, then rerun doctor."
        )
    elif checks["degraded"]:
        checks["next_steps"] = (
            "Core pipeline is ready. Optional source-platform helpers are degraded."
        )
    else:
        checks["next_steps"] = "Core pipeline is ready."
    if str(environment_bin) not in str(checks["python3"]):
        checks["runtime_warning"] = (
            "doctor is not running from the project virtualenv; factory.sh will use .venv when available."
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
    parser.add_argument("--krillinai-voice", help="Override the KrillinAI TTS voice for this production run.")
    parser.add_argument(
        "--rights-status",
        choices=("MANUAL_REVIEW", "OWNED", "LICENSED", "PUBLIC_DOMAIN", "CC_BY", "VERIFIED"),
    )


def strategy_options(args: argparse.Namespace) -> dict[str, object]:
    fields = (
        "content_type", "segment_strategy", "audio_policy", "max_segments", "max_duration",
        "reaction_mode", "reaction_source", "source_volume", "reaction_volume", "reaction_position",
        "batch_label", "krillinai_voice", "rights_status", "trigger_source",
    )
    return {field: getattr(args, field) for field in fields if getattr(args, field, None) is not None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaguartv", description="Standalone JaguarTV content factory")
    parser.add_argument("--config", default="config/pipeline.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("ytdlp-status")
    subparsers.add_parser("f2-status")
    subparsers.add_parser("trends-run")

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--platform", action="append", choices=["youtube", "bilibili", "douyin", "xiaohongshu", "tiktok", "facebook", "x", "instagram", "kwai"])
    discover_parser.add_argument("--limit", type=int)
    discover_parser.add_argument("--keyword", action="append", help="Override configured keyword file for a focused discovery run.")

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("url")

    crawler_parser = subparsers.add_parser("ingest-mediacrawler")
    crawler_parser.add_argument("path", type=Path)
    crawler_parser.add_argument("--platform", choices=("douyin", "bilibili", "xiaohongshu", "tiktok", "youtube", "facebook", "x", "instagram", "kwai"))
    crawler_parser.add_argument("--min-likes", type=int, default=0)
    crawler_parser.add_argument("--min-views", type=int, default=0)

    reach_parser = subparsers.add_parser("ingest-agent-reach")
    reach_parser.add_argument("path", type=Path)
    reach_parser.add_argument("--platform", required=True, choices=("douyin", "bilibili", "xiaohongshu", "tiktok", "youtube", "facebook", "x", "instagram", "kwai"))
    reach_parser.add_argument("--min-likes", type=int, default=0)
    reach_parser.add_argument("--min-views", type=int, default=0)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status")
    list_parser.add_argument("--limit", type=int, default=50)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--limit", type=int, default=5)
    download_parser.add_argument("--candidate")

    produce_parser = subparsers.add_parser("produce")
    produce_parser.add_argument("--limit", type=int, default=1)
    produce_parser.add_argument("--candidate")
    produce_parser.add_argument(
        "--trigger-source",
        default="cli",
        choices=("cli", "ai_agent", "scheduled_worker", "history_repair"),
    )
    add_strategy_arguments(produce_parser)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--candidate", required=True)
    add_strategy_arguments(analyze_parser)

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("path", type=Path)
    upload_parser.add_argument("--kind", choices=("source", "reaction"), required=True)

    cta_import_parser = subparsers.add_parser("cta-import")
    cta_import_parser.add_argument("path", type=Path, help="CTA file or directory to import")
    subparsers.add_parser("cta-list")
    cta_delete_parser = subparsers.add_parser("cta-delete")
    cta_delete_parser.add_argument("asset_id")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--discover", type=int, default=5)
    run_parser.add_argument("--download", type=int, default=1)
    run_parser.add_argument("--produce", type=int, default=1)

    subparsers.add_parser("review")

    audit_parser = subparsers.add_parser("audit-review")
    audit_parser.add_argument("--status", default="READY_FOR_REVIEW")
    audit_parser.add_argument("--limit", type=int, default=0)

    repair_parser = subparsers.add_parser("repair-review")
    repair_parser.add_argument("--candidate", action="append")
    repair_parser.add_argument("--limit", type=int, default=1)
    repair_parser.add_argument("--execute", action="store_true")
    repair_parser.add_argument("--run-id")
    repair_parser.add_argument("--action", choices=("pause", "resume"))

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

    integrations_parser = subparsers.add_parser("integrations")
    integrations_parser.add_argument("--manifest", type=Path, default=Path("config/integrations.yaml"))
    integrations_parser.add_argument("--name", action="append")
    integrations_parser.add_argument("--sync", action="store_true")
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
        return doctor(Path(args.config))
    if args.command == "ytdlp-status":
        status = yt_dlp_runtime_status()
        print_json(status)
        return 0 if status["ok"] else 1
    if args.command == "f2-status":
        config = load_config(Path(args.config))
        options = dict((((config.get("sources") or {}).get("adapters") or {}).get("douyin") or {}))
        options["_root"] = str(config.get("_root") or Path.cwd())
        status = f2_runtime_status(options)
        print_json(status)
        return 0 if status.get("ok") else 1
    config = load_config(Path(args.config))
    if args.command == "trends-run":
        print_json(run_trends_job(config))
    elif args.command == "discover":
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
    elif args.command == "ingest-agent-reach":
        print_json(ingest_mediacrawler_jsonl(
            config,
            args.path,
            platform=args.platform,
            min_likes=args.min_likes,
            min_views=args.min_views,
            ingest_source="agent_reach_jsonl",
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
    elif args.command == "cta-import":
        path = args.path.expanduser().resolve()
        supported = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_MEDIA_EXTENSIONS
        sources = (
            sorted(
                item for item in path.iterdir()
                if item.is_file() and not item.name.startswith(".") and item.suffix.lower() in supported
            )
            if path.is_dir()
            else [path]
        )
        print_json([import_cta_path(config, item, actor="cli") for item in sources])
    elif args.command == "cta-list":
        print_json(list_cta_assets(config))
    elif args.command == "cta-delete":
        print_json(delete_cta_asset(config, args.asset_id, actor="cli"))
    elif args.command == "integrations":
        manifest = load_integration_manifest(args.manifest)
        project_root = Path(str(config.get("_root") or Path.cwd()))
        operation = sync_integrations if args.sync else integration_status
        print_json(operation(project_root, manifest, args.name))
    elif args.command == "run":
        print_json({
            "discover": discover(config, limit=args.discover),
            "download": download_top(config, args.download),
            "produce": produce_top(config, args.produce),
            "review": str(generate_review_index(config)),
        })
    elif args.command == "review":
        print(generate_review_index(config))
    elif args.command == "audit-review":
        print_json(audit_review_inventory(config, status=args.status, limit=args.limit, dry_run=True))
    elif args.command == "repair-review":
        if args.action:
            if not args.run_id:
                raise ValueError("--run-id is required with --action")
            result = set_repair_run_status(config, args.run_id, args.action)
            if args.action == "resume" and args.execute:
                result = repair_review_inventory(config, execute=True, run_id=args.run_id, limit=args.limit)
            print_json(result)
        else:
            print_json(repair_review_inventory(
                config,
                candidate_ids=args.candidate,
                limit=args.limit,
                execute=args.execute,
                run_id=args.run_id or "",
            ))
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
