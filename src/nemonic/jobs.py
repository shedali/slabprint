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
import uuid

STATE_DIR = os.environ.get("NEMONIC_STATE",
                           os.path.expanduser("~/.local/state/nemonic"))
QUEUE_DIR = os.path.join(STATE_DIR, "queue")
DONE_DIR = os.path.join(STATE_DIR, "done")

RELATIVE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
ABSOLUTE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%H:%M")


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime:
    """Parse '2026-09-15 07:30', '07:30', '+90m', '+2h', '+3d'.

    A bare time that has already passed today means tomorrow.
    """
    now = now or dt.datetime.now()
    text = text.strip()

    if text.startswith("+") and len(text) > 2 and text[-1].lower() in RELATIVE_UNITS:
        amount = int(text[1:-1])
        return now + dt.timedelta(**{RELATIVE_UNITS[text[-1].lower()]: amount})

    for fmt in ABSOLUTE_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            parsed = now.replace(hour=parsed.hour, minute=parsed.minute,
                                 second=0, microsecond=0)
            if parsed <= now:
                parsed += dt.timedelta(days=1)
        return parsed

    raise ValueError(f"cannot parse time {text!r}: "
                     "try '2026-09-15 07:30', '07:30', or '+90m'")


def add(when: dt.datetime, argv: list[str], label: str | None = None) -> dict:
    """Queue a command to run at `when`. argv is passed to the nemonic CLI."""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    job = {"id": uuid.uuid4().hex[:10],
           "at": when.isoformat(timespec="minutes"),
           "at_epoch": when.timestamp(),
           "argv": argv,
           "label": label,
           "created": dt.datetime.now().isoformat(timespec="seconds")}
    path = os.path.join(QUEUE_DIR, f"{int(when.timestamp())}-{job['id']}.json")
    with open(path, "w") as handle:
        json.dump(job, handle, indent=2)
    job["path"] = path
    return job


def pending() -> list[dict]:
    """Every queued job, earliest first."""
    if not os.path.isdir(QUEUE_DIR):
        return []
    jobs = []
    for name in sorted(os.listdir(QUEUE_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(QUEUE_DIR, name)
        try:
            with open(path) as handle:
                job = json.load(handle)
        except (OSError, ValueError):
            continue  # ignore a partially written or corrupt job
        job["path"] = path
        jobs.append(job)
    return jobs


def clear() -> int:
    removed = 0
    for job in pending():
        os.remove(job["path"])
        removed += 1
    return removed


def run_due(command: list[str], now: dt.datetime | None = None) -> list[dict]:
    """Run every job that is due, archiving each to the done directory.

    `command` is the nemonic entry point, e.g. ["/path/to/nemonic"].
    """
    now = (now or dt.datetime.now()).timestamp()
    os.makedirs(DONE_DIR, exist_ok=True)
    finished = []

    for job in pending():
        if job.get("at_epoch", 0) > now:
            continue
        result = subprocess.run(command + job["argv"], capture_output=True, text=True)
        job["ran_at"] = dt.datetime.now().isoformat(timespec="seconds")
        job["exit"] = result.returncode
        job["output"] = (result.stdout + result.stderr).strip()[:2000]

        path = job.pop("path")
        with open(os.path.join(DONE_DIR, os.path.basename(path)), "w") as handle:
            json.dump(job, handle, indent=2)
        os.remove(path)
        finished.append(job)

    return finished
