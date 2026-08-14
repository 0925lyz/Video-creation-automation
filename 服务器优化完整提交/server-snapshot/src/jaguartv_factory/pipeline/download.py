import sqlite3
import subprocess
import json
from pathlib import Path
from typing import Any

from ..core import connect_db, now_iso

def download_candidate(config: dict[str, Any], row: sqlite3.Row) -> Path | str:
    """
    Triggers a remote download on the server directly to save bandwidth locally.
    Does not use local VPN.
    """
    storage = config.get("storage", {})
    host = storage.get("remote_review_host", "43.134.128.197")
    user = storage.get("remote_review_user", "ubuntu")
    key = Path(storage.get("remote_review_key", "~/.ssh/jarg_tencent.pem")).expanduser()
    remote_root = "/opt/jaguartv-content-factory-vnext/workspace/jobs"
    remote_job_dir = f"{remote_root}/{row['id']}"
    url = row['url']

    connection = connect_db(config)
    
    # We SSH into the server and use yt-dlp to download it directly.
    # The server has yt-dlp inside its virtual environment.
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-i", str(key), f"{user}@{host}",
        f"mkdir -p {remote_job_dir} && "
        f"/opt/jaguartv-content-factory-vnext/.venv/bin/yt-dlp --force-ipv4 -o '{remote_job_dir}/source.%(ext)s' '{url}'"
    ]
    print(f"Triggering remote download on server {host} for candidate {row['id']}...")
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        connection.execute("UPDATE candidates SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        connection.commit()
        raise RuntimeError(f"Remote download failed: {result.stderr}")
    else:
        connection.execute("UPDATE candidates SET status='DOWNLOADED',updated_at=? WHERE id=?", (now_iso(), row["id"]))
        connection.commit()
        print(f"Remote download successful for {row['id']}")
        return f"{remote_job_dir}/source.mp4"

def download_top(config: dict[str, Any], limit: int, candidate: str | None = None) -> dict[str, int]:
    connection = connect_db(config)
    minimum = float(config.get("selection", {}).get("min_score", 0))
    if candidate:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE id=? AND status IN ('DISCOVERED','DOWNLOAD_FAILED')", (candidate,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE status='DISCOVERED' AND score>=? ORDER BY score DESC LIMIT ?",
            (minimum, limit),
        ).fetchall()
    
    stats = {"selected": len(rows), "downloaded": 0, "failed": 0}
    for row in rows:
        try:
            download_candidate(config, row)
            stats["downloaded"] += 1
        except Exception as e:
            print(f"Failed to download {row['id']}: {e}")
            stats["failed"] += 1
    return stats
