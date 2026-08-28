from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import shutil
import threading
from pathlib import Path
from typing import Any

from .binaries import require_binary
from .core import (
    connect_db,
    media_duration,
    now_iso,
    production_design_config,
    qa_video,
    render_video_remotion_generic,
    run_command,
    workspace_dir,
)
from .server_store import storage_root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset(config: dict[str, Any], asset_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from .dashboard import review_output_asset_by_id, review_package_metadata

    asset = review_output_asset_by_id(config, str(asset_id or ""))
    if not asset:
        raise ValueError("review output does not exist")
    if str(asset.get("variant") or "") != "通用版":
        raise ValueError("only the generic output can be edited")
    package_id = str(asset["id"]).split(":", 1)[0]
    metadata = review_package_metadata(config, package_id)
    candidate_ids = [package_id, str(metadata.get("source_job_id") or "").strip()]
    connection = connect_db(config)
    status = next(
        (
            str(row["status"])
            for candidate_id in candidate_ids
            if candidate_id
            if (row := connection.execute("SELECT status FROM candidates WHERE id=?", (candidate_id,)).fetchone())
        ),
        "",
    )
    if status != "READY_FOR_REVIEW":
        raise ValueError("manual editing is only available while the item is pending review")
    path = Path(str(asset.get("_path") or "")).resolve()
    allowed_roots = [
        (workspace_dir(config) / "ready_for_review").resolve(),
        (storage_root(config) / "review").resolve(),
    ]
    if not path.is_file() or not any(path.is_relative_to(root) for root in allowed_roots):
        raise ValueError("review output file is missing or outside managed storage")
    asset["_package_id"] = package_id
    asset["_path"] = str(path)
    return asset, metadata


def _output_metadata(metadata: dict[str, Any], filename: str) -> dict[str, Any]:
    outputs = metadata.get("output_variants") if isinstance(metadata.get("output_variants"), list) else []
    return next(
        (
            item for item in outputs
            if isinstance(item, dict) and Path(str(item.get("path") or item.get("filename") or "")).name == filename
        ),
        next((item for item in outputs if isinstance(item, dict) and item.get("variant") == "通用版"), {}),
    )


def _content_duration(metadata: dict[str, Any], filename: str, total_duration: float) -> float:
    output = _output_metadata(metadata, filename)
    layout = output.get("layout") if isinstance(output.get("layout"), dict) else {}
    segment = metadata.get("segment") if isinstance(metadata.get("segment"), dict) else {}
    value = float(layout.get("content_duration_sec") or segment.get("duration_sec") or total_duration)
    return max(0.01, min(total_duration, value))


def _package_paths(config: dict[str, Any], package_id: str, filename: str) -> list[Path]:
    paths: list[Path] = []
    for root in (workspace_dir(config) / "ready_for_review", storage_root(config) / "review"):
        package = root / package_id
        for name in (filename, "video.mp4"):
            path = package / name
            if path.is_file() and path.resolve() not in [item.resolve() for item in paths]:
                paths.append(path)
    return paths


def _update_metadata(
    config: dict[str, Any], package_id: str, filename: str, *, output_info: dict[str, Any],
    edit_event: dict[str, Any], content_duration: float,
) -> None:
    for root in (workspace_dir(config) / "ready_for_review", storage_root(config) / "review"):
        path = root / package_id / "metadata.json"
        if not path.is_file():
            continue
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata.setdefault("manual_edits", []).append(edit_event)
        segment = metadata.setdefault("segment", {})
        segment["duration_sec"] = content_duration
        segment["end_sec"] = float(segment.get("start_sec") or 0) + content_duration
        outputs = metadata.get("output_variants") if isinstance(metadata.get("output_variants"), list) else []
        for item in outputs:
            if not isinstance(item, dict) or item.get("variant") != "通用版":
                continue
            item.update({key: value for key, value in output_info.items() if key != "path"})
            item["path"] = str((root / package_id / filename).resolve())
        temporary = path.with_name(f".{path.name}.{os.getpid()}.updating")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _replace_package_files(
    config: dict[str, Any], package_id: str, filename: str, rendered: Path
) -> list[str]:
    updated = []
    for destination in _package_paths(config, package_id, filename):
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{threading.get_ident()}.replacing"
        )
        shutil.copy2(rendered, temporary)
        temporary.replace(destination)
        updated.append(str(destination))
    return updated


def _persist_output(
    config: dict[str, Any], asset: dict[str, Any], output_info: dict[str, Any], qa: dict[str, Any]
) -> None:
    connection = connect_db(config)
    filename = Path(str(asset["_path"])).name
    layout = output_info.get("layout") if isinstance(output_info.get("layout"), dict) else {}
    cta = layout.get("cta") if isinstance(layout.get("cta"), dict) else {}
    rows = connection.execute(
        "SELECT id,path FROM production_outputs WHERE candidate_id IN (?,?) AND variant='通用版'",
        (str(asset["_package_id"]), str(asset["_package_id"]).split("_part", 1)[0]),
    ).fetchall()
    matching = [row for row in rows if Path(str(row["path"])).name == filename]
    timestamp = now_iso()
    for row in matching:
        path = Path(str(row["path"]))
        current = path if path.is_file() else Path(str(asset["_path"]))
        connection.execute(
            """
            UPDATE production_outputs SET sha256=?,size_bytes=?,duration_sec=?,width=?,height=?,fps=?,
              has_video=?,has_audio=?,endcard_count=1,cta_asset_id=?,cta_media_type=?,cta_orientation=?,
              cta_duration_sec=?,layout_json=?,qa_json=?,updated_at=? WHERE id=?
            """,
            (
                _sha256(current), current.stat().st_size, float(qa.get("duration") or 0),
                int(qa.get("width") or 0), int(qa.get("height") or 0), float(qa.get("fps") or 0),
                int(bool(qa.get("has_video"))), int(bool(qa.get("has_audio"))),
                str(cta.get("asset_id") or output_info.get("cta_asset_id") or ""),
                str(cta.get("media_type") or output_info.get("cta_media_type") or ""),
                str(cta.get("orientation") or output_info.get("cta_orientation") or ""),
                float(cta.get("duration_sec") or output_info.get("cta_duration_sec") or 0),
                json.dumps(layout, ensure_ascii=False), json.dumps(qa, ensure_ascii=False), timestamp, row["id"],
            ),
        )
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (
            str(asset["_package_id"]), "REVIEW_OUTPUT_EDITED",
            json.dumps({"asset_id": asset["id"], "filename": filename}, ensure_ascii=False), timestamp,
        ),
    )
    connection.commit()


def manual_cut_review_output(
    config: dict[str, Any], asset_id: str, cut_start_sec: float, cut_end_sec: float, *, actor: str = "dashboard"
) -> dict[str, Any]:
    asset, metadata = _asset(config, asset_id)
    source = Path(str(asset["_path"]))
    total_duration = media_duration(source)
    content_duration = _content_duration(metadata, source.name, total_duration)
    start, end = float(cut_start_sec), float(cut_end_sec)
    if start <= 0.05 or end <= start or end >= content_duration - 0.05:
        raise ValueError("cut range must be a middle interval inside the original content, not the CTA")
    if end - start < 0.10:
        raise ValueError("cut range must be at least 0.10 seconds")
    remaining_content = content_duration - (end - start)
    if remaining_content < 3:
        raise ValueError("at least 3 seconds of original content must remain")
    lock_path = source.parent / ".review-edit.lock"
    rendered = source.with_name(f".{source.stem}.{random.randrange(1_000_000)}.cut.mp4")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        filters = (
            f"[0:v]trim=0:{start:.6f},setpts=PTS-STARTPTS[v0];"
            f"[0:a]atrim=0:{start:.6f},asetpts=PTS-STARTPTS[a0];"
            f"[0:v]trim={end:.6f}:{content_duration:.6f},setpts=PTS-STARTPTS[v1];"
            f"[0:a]atrim={end:.6f}:{content_duration:.6f},asetpts=PTS-STARTPTS[a1];"
            f"[0:v]trim={content_duration:.6f}:{total_duration:.6f},setpts=PTS-STARTPTS[v2];"
            f"[0:a]atrim={content_duration:.6f}:{total_duration:.6f},asetpts=PTS-STARTPTS[a2];"
            "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
        )
        result = run_command([
            "ffmpeg", "-y", "-i", str(source), "-filter_complex", filters,
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(rendered),
        ], check=False)
        if result.returncode != 0 or not rendered.is_file():
            rendered.unlink(missing_ok=True)
            raise RuntimeError("manual cut failed: " + (result.stderr or result.stdout)[-2000:])
        qa = qa_video(rendered, config, content_duration=remaining_content)
        if not qa.get("passed"):
            rendered.unlink(missing_ok=True)
            raise RuntimeError(f"manual cut did not pass media QA: {qa}")
        updated = _replace_package_files(config, asset["_package_id"], source.name, rendered)
        rendered.unlink(missing_ok=True)
    previous = _output_metadata(metadata, source.name)
    layout = dict(previous.get("layout") or {})
    layout["content_duration_sec"] = remaining_content
    edit = {
        "type": "middle_cut", "start_sec": start, "end_sec": end,
        "removed_sec": end - start, "actor": str(actor or "dashboard")[:120], "created_at": now_iso(),
    }
    output_info = {**previous, "duration": float(qa["duration"]), "size": Path(updated[0]).stat().st_size, "layout": layout, "qa": qa}
    _update_metadata(
        config, asset["_package_id"], source.name, output_info=output_info,
        edit_event=edit, content_duration=remaining_content,
    )
    _persist_output(config, asset, output_info, qa)
    return {"asset_id": asset_id, "content_duration_sec": remaining_content, "removed_sec": end - start, "updated": updated, "qa": qa}


def replace_review_output_design(
    config: dict[str, Any], asset_id: str, layers: list[dict[str, Any]], *, actor: str = "dashboard"
) -> dict[str, Any]:
    asset, metadata = _asset(config, asset_id)
    source = Path(str(asset["_path"]))
    total_duration = media_duration(source)
    content_duration = _content_duration(metadata, source.name, total_duration)
    patched = production_design_config(config, {"design": {"layers": layers}})
    rendered = source.with_name(f".{source.stem}.{random.randrange(1_000_000)}.design.mp4")
    lock_path = source.parent / ".review-edit.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = render_video_remotion_generic(
            patched, source, rendered, job_id=f"review-design-{asset['_package_id']}",
            candidate_id=str(metadata.get("source_job_id") or asset["_package_id"]),
            content_duration_override=content_duration,
        )
        qa = qa_video(rendered, patched, content_duration=content_duration)
        if not qa.get("passed"):
            rendered.unlink(missing_ok=True)
            raise RuntimeError(f"designed output did not pass media QA: {qa}")
        updated = _replace_package_files(config, asset["_package_id"], source.name, rendered)
        rendered.unlink(missing_ok=True)
    edit = {
        "type": "design_replace", "layer_count": len(layers),
        "scope": "content_only", "actor": str(actor or "dashboard")[:120], "created_at": now_iso(),
    }
    info.update({"qa": qa, "duration": float(qa["duration"]), "size": Path(updated[0]).stat().st_size})
    _update_metadata(
        config, asset["_package_id"], source.name, output_info=info,
        edit_event=edit, content_duration=content_duration,
    )
    _persist_output(config, asset, info, qa)
    return {"asset_id": asset_id, "replaced": True, "content_duration_sec": content_duration, "updated": updated, "qa": qa, "layout": info["layout"]}
