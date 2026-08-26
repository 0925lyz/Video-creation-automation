from __future__ import annotations

from datetime import datetime, timedelta, timezone


POST_PUBLISH_SYNC_SCHEDULE_VERSION = "post_publish_12h_24h_3d_7d_v1"
POST_PUBLISH_SYNC_CHECKPOINTS = (
    ("12h", timedelta(hours=12)),
    ("24h", timedelta(hours=24)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
)


def checkpoint_due_at(published: datetime, stage: str) -> datetime:
    try:
        delay = next(delay for name, delay in POST_PUBLISH_SYNC_CHECKPOINTS if name == stage)
    except StopIteration as error:
        raise ValueError("invalid sync stage") from error
    return published.astimezone(timezone.utc) + delay


def initial_checkpoint(published: datetime, current: datetime) -> tuple[str, datetime]:
    due = [
        (stage, published.astimezone(timezone.utc) + delay)
        for stage, delay in POST_PUBLISH_SYNC_CHECKPOINTS
    ]
    reached = [item for item in due if item[1] <= current.astimezone(timezone.utc)]
    return reached[-1] if reached else due[0]


def next_checkpoint(published: datetime, stage: str) -> tuple[str, datetime] | None:
    names = [name for name, _delay in POST_PUBLISH_SYNC_CHECKPOINTS]
    if stage not in names:
        raise ValueError("invalid sync stage")
    index = names.index(stage) + 1
    if index >= len(POST_PUBLISH_SYNC_CHECKPOINTS):
        return None
    next_stage, delay = POST_PUBLISH_SYNC_CHECKPOINTS[index]
    return next_stage, published.astimezone(timezone.utc) + delay
