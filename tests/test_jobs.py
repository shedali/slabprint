"""The scheduled-print queue."""

import datetime as dt
import json

import pytest

from nemonic import jobs

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Never touch the real queue in ~/.local/state."""
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(jobs, "DONE_DIR", tmp_path / "done")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("+30m", dt.datetime(2026, 9, 15, 12, 30)),
        ("+2h", dt.datetime(2026, 9, 15, 14, 0)),
        ("+3d", dt.datetime(2026, 9, 18, 12, 0)),
        ("+1w", dt.datetime(2026, 9, 22, 12, 0)),
    ],
)
def test_parse_when_handles_relative_offsets(text, expected):
    assert jobs.parse_when(text, now=NOW) == expected


def test_parse_when_handles_an_absolute_datetime():
    assert jobs.parse_when("2026-12-25 08:30", now=NOW) == dt.datetime(2026, 12, 25, 8, 30)


def test_parse_when_handles_an_iso_datetime():
    assert jobs.parse_when("2026-12-25T08:30", now=NOW) == dt.datetime(2026, 12, 25, 8, 30)


def test_a_bare_time_later_today_stays_today():
    assert jobs.parse_when("18:00", now=NOW) == dt.datetime(2026, 9, 15, 18, 0)


def test_a_bare_time_already_past_rolls_to_tomorrow():
    assert jobs.parse_when("07:30", now=NOW) == dt.datetime(2026, 9, 16, 7, 30)


def test_a_bare_time_equal_to_now_rolls_to_tomorrow():
    assert jobs.parse_when("12:00", now=NOW) == dt.datetime(2026, 9, 16, 12, 0)


@pytest.mark.parametrize("text", ["tomorrow", "", "25/12/2026", "+", "+5x", "half past two"])
def test_parse_when_rejects_what_it_cannot_understand(text):
    with pytest.raises(ValueError):
        jobs.parse_when(text, now=NOW)


def test_add_then_pending_round_trips_the_command():
    jobs.add(NOW, ["print", "hello"], label="greeting")
    pending = jobs.pending()
    assert len(pending) == 1
    assert pending[0]["argv"] == ["print", "hello"]
    assert pending[0]["label"] == "greeting"


def test_pending_is_ordered_by_due_time():
    jobs.add(NOW + dt.timedelta(hours=2), ["print", "later"])
    jobs.add(NOW, ["print", "sooner"])
    assert [job["argv"][1] for job in jobs.pending()] == ["sooner", "later"]


def test_pending_is_empty_before_anything_is_queued():
    assert jobs.pending() == []


def test_pending_skips_a_corrupt_job_file(tmp_path):
    jobs.add(NOW, ["print", "good"])
    broken = tmp_path / "queue" / "9999999999-broken.json"
    broken.write_text("{ this is not json")
    assert [job["argv"][1] for job in jobs.pending()] == ["good"]


def test_clear_empties_the_queue():
    jobs.add(NOW, ["print", "a"])
    jobs.add(NOW, ["print", "b"])
    assert jobs.clear() == 2
    assert jobs.pending() == []


def test_run_due_runs_only_what_is_due():
    jobs.add(NOW - dt.timedelta(minutes=1), ["print", "due"])
    jobs.add(NOW + dt.timedelta(hours=1), ["print", "later"])
    finished = jobs.run_due(["true"], now=NOW)
    assert [job["argv"][1] for job in finished] == ["due"]
    assert [job["argv"][1] for job in jobs.pending()] == ["later"]


def test_run_due_archives_the_job_with_its_exit_code(tmp_path):
    jobs.add(NOW, ["print", "x"])
    finished = jobs.run_due(["true"], now=NOW)
    assert finished[0]["exit"] == 0
    archived = list((tmp_path / "done").glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())["exit"] == 0


def test_run_due_records_a_failure_rather_than_raising():
    jobs.add(NOW, ["print", "x"])
    finished = jobs.run_due(["false"], now=NOW)
    assert finished[0]["exit"] != 0
    # A failed job is still consumed, so a broken job cannot wedge the queue.
    assert jobs.pending() == []


def test_run_due_does_nothing_when_nothing_is_due():
    jobs.add(NOW + dt.timedelta(days=1), ["print", "x"])
    assert jobs.run_due(["true"], now=NOW) == []
    assert len(jobs.pending()) == 1
