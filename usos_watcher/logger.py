"""Logging setup with secret scrubbing.

SPEC.md §10: passwords must NEVER appear in logs.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Iterable


class _ScrubbingFilter(logging.Filter):
    """Replace any occurrence of a secret value in a log record with ****."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        # Only scrub non-empty secrets; an empty string would replace everything.
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Format the message early so we can scrub the rendered form.
        try:
            msg = record.getMessage()
        except Exception:
            return True
        scrubbed = msg
        for s in self._secrets:
            scrubbed = scrubbed.replace(s, "****")
        if scrubbed != msg:
            record.msg = scrubbed
            record.args = ()  # we've already substituted args into msg
        return True


def setup_logging(
    log_dir: Path,
    level: str,
    rotate_max_bytes: int,
    rotate_backups: int,
    secrets_to_scrub: Iterable[str],
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "watcher.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Wipe any pre-existing handlers (idempotent if called twice in tests).
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=rotate_max_bytes, backupCount=rotate_backups,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(fmt)

    scrubber = _ScrubbingFilter(secrets_to_scrub)
    file_handler.addFilter(scrubber)
    stream_handler.addFilter(scrubber)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    return logging.getLogger("usos_watcher")
