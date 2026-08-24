"""Compatibility imports for the canonical factory download service.

All callers must use the core implementation so candidate state, managed
sessions, yt-dlp runtime arguments, media validation, and events stay unified.
"""

from ..core import download_candidate, download_top

__all__ = ["download_candidate", "download_top"]
