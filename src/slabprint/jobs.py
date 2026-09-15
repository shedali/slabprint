"""A tiny file-backed queue of prints scheduled for the future.

Each job is one JSON file named "<epoch>-<id>.json", so the directory sorts by
due time and any job can be inspected or deleted with ordinary file tools. A
runner (launchd, cron, systemd timer) calls run_due() periodically.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

STATE_DIR = Path(os.environ.get("SLABPRINT_STATE", "~/.local/state/slabprint")).expanduser()
QUEUE_DIR = STATE_DIR / "queue"
DONE_DIR = STATE_DIR / "done"

# Lower case only, and deliberately no "M": minutes vs months is an ambiguity
# that would silently schedule a reminder months out, or minutes out, at random.
RELATIVE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
MAX_RELATIVE_AMOUNT = 10_000  # beyond this, timedelta overflows rather than helps
ABSOLUTE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%H:%M")


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime:
    """Parse '2026-09-15 07:30', '07:30', '+90m', '+2h', '+3d'.

    A bare time that has already passed today means tomorrow.
    """
    now = now or dt.datetime.now()
    text = text.strip()

    if text.startswith("+") and len(text) > 2 and text[-1] in RELATIVE_UNITS:
        body = text[1:-1]
        if not body.isdigit():  # rejects "+-5m" and "+abcm"
            raise ValueError(f"cannot parse time {text!r}: expected digits, got {body!r}")
        amount = int(body)
        if amount > MAX_RELATIVE_AMOUNT:
            raise ValueError(f"cannot parse time {text!r}: {amount} is too far ahead")
        return now + dt.timedelta(**{RELATIVE_UNITS[text[-1]]: amount})

    for fmt in ABSOLUTE_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            parsed = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if parsed <= now:
                parsed += dt.timedelta(days=1)
        return parsed

    raise ValueError(f"cannot parse time {text!r}: try '2026-09-15 07:30', '07:30', or '+90m'")


def add(when: dt.datetime, argv: list[str], label: str | None = None) -> dict:
    """Queue a command to run at `when`. argv is passed to the nemonic CLI."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    job = {
        "id": uuid.uuid4().hex[:10],
        "at": when.isoformat(timespec="minutes"),
        "at_epoch": when.timestamp(),
        "argv": argv,
        "label": label,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    path = QUEUE_DIR / f"{int(when.timestamp())}-{job['id']}.json"
    path.write_text(json.dumps(job, indent=2))
    job["path"] = path
    return job


def is_runnable(job: object) -> bool:
    """Whether a decoded job has the fields run_due needs.

    Guarding only against malformed JSON is not enough: a file that parses but
    lacks `argv` used to raise KeyError out of run_due on every pass. Because it
    sorts by due time and was never removed, one such file stopped every later
    job from ever printing — silently, since the runner's output goes nowhere.
    """
    return (
        isinstance(job, dict)
        and isinstance(job.get("id"), str)
        and isinstance(job.get("at_epoch"), (int, float))
        and isinstance(job.get("argv"), list)
        and all(isinstance(argument, str) for argument in job["argv"])
    )


def pending() -> list[dict]:
    """Every valid queued job, earliest first. Unusable files are reported and skipped."""
    if not QUEUE_DIR.is_dir():
        return []
    jobs = []
    for path in sorted(QUEUE_DIR.glob("*.json")):
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            print(f"slabprint: ignoring unreadable job {path.name}", file=sys.stderr)
            continue
        if not is_runnable(job):
            print(f"slabprint: ignoring malformed job {path.name}", file=sys.stderr)
            continue
        job["path"] = path
        jobs.append(job)
    return jobs


def clear() -> int:
    removed = 0
    for job in pending():
        job["path"].unlink()
        removed += 1
    return removed


def run_due(command: list[str], now: dt.datetime | None = None) -> list[dict]:
    """Run every job that is due, archiving each to the done directory.

    `command` is the nemonic entry point, e.g. ["/path/to/nemonic"].
    """
    now = (now or dt.datetime.now()).timestamp()
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    finished = []

    for job in pending():
        if job.get("at_epoch", 0) > now:
            continue

        # Claim the job by renaming it. os.rename is atomic within a directory,
        # so if two runners overlap — cron will happily start a second while the
        # first is still printing — exactly one wins and the loser moves on,
        # instead of both printing the job and one then crashing on unlink.
        claimed = job["path"].with_suffix(".running")
        archive_name = job["path"].name  # keep the .json name for the archive
        try:
            job["path"].rename(claimed)  # atomic within a directory
        except OSError:
            continue
        job["path"] = claimed

        result = subprocess.run(command + job["argv"], capture_output=True, text=True)
        job["ran_at"] = dt.datetime.now().isoformat(timespec="seconds")
        job["exit"] = result.returncode
        job["output"] = (result.stdout + result.stderr).strip()[:2000]

        path = job.pop("path")
        (DONE_DIR / archive_name).write_text(json.dumps(job, indent=2))
        path.unlink()
        finished.append(job)

    return finished
