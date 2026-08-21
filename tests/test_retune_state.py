"""Tests for the automatic-retuning schedule.

retune_state decides *who* is worth retuning and when. The actual
retuning needs OpenCV; this decision deliberately does not, so it is
covered here and runs in CI without the vision stack.
"""
import json
from datetime import datetime

import pytest

import retune_state


@pytest.fixture
def log_dir(tmp_path):
    directory = tmp_path / "face_log"
    directory.mkdir()
    return directory


def person(log_dir, discord_id="1", name="alice", captures=0):
    directory = log_dir / f"{discord_id}_{name}"
    directory.mkdir(exist_ok=True)
    for i in range(captures):
        (directory / f"capture_{i:04d}.jpg").write_bytes(b"\xff\xd8fake\xff\xd9")
    return (discord_id, name, str(directory))


# ------------------------------------------------------------ persistence

def test_missing_state_reads_as_empty(log_dir):
    assert retune_state.load_state(str(log_dir)) == {}


def test_state_round_trips(log_dir):
    saved = retune_state.record_retune({}, "1", 40)
    assert retune_state.save_state(str(log_dir), saved) is True

    loaded = retune_state.load_state(str(log_dir))
    assert loaded["1"]["captures"] == 40
    assert loaded["1"]["last_retune"]


def test_the_state_file_sits_beside_the_captures(log_dir):
    """It must travel with the log when that is moved to Documents."""
    retune_state.save_state(str(log_dir), {"1": {"captures": 5}})
    assert (log_dir / retune_state.STATE_FILENAME).exists()


@pytest.mark.parametrize("content", ["not json at all", "", "[1, 2, 3]", "null"])
def test_a_damaged_state_file_reads_as_empty(log_dir, content):
    """A corrupt file must cost at most one extra retune, never a crash."""
    (log_dir / retune_state.STATE_FILENAME).write_text(content)
    assert retune_state.load_state(str(log_dir)) == {}


def test_saving_creates_a_missing_directory(tmp_path):
    target = tmp_path / "not-yet" / "face_log"
    assert retune_state.save_state(str(target), {"1": {"captures": 1}}) is True
    assert retune_state.load_state(str(target))["1"]["captures"] == 1


def test_record_retune_stamps_the_time(log_dir):
    when = datetime(2026, 8, 21, 3, 30, 0)
    state = retune_state.record_retune({}, "1", 30, when=when)
    assert state["1"]["last_retune"].startswith("2026-08-21T03:30")


# -------------------------------------------------------------- capturing

def test_captures_are_counted(log_dir):
    _, _, directory = person(log_dir, captures=7)
    assert retune_state.count_captures(directory) == 7


def test_only_images_are_counted(log_dir):
    _, _, directory = person(log_dir, captures=3)
    (log_dir / "1_alice" / "notes.txt").write_text("not a capture")
    assert retune_state.count_captures(directory) == 3


def test_a_missing_directory_counts_as_zero(tmp_path):
    assert retune_state.count_captures(str(tmp_path / "gone")) == 0


# ------------------------------------------------------------- the decision

def test_a_new_person_waits_for_enough_captures(log_dir):
    below = retune_state.MIN_CAPTURES_FOR_FIRST_RETUNE - 1
    assert retune_state.is_due("1", below, {}) is False


def test_a_new_person_becomes_due(log_dir):
    at_threshold = retune_state.MIN_CAPTURES_FOR_FIRST_RETUNE
    assert retune_state.is_due("1", at_threshold, {}) is True


def test_a_retuned_person_is_not_immediately_due_again(log_dir):
    state = retune_state.record_retune({}, "1", 40)
    assert retune_state.is_due("1", 40, state) is False


def test_a_few_new_captures_are_not_enough(log_dir):
    state = retune_state.record_retune({}, "1", 40)
    just_under = 40 + retune_state.MIN_NEW_CAPTURES - 1
    assert retune_state.is_due("1", just_under, state) is False


def test_enough_new_captures_make_them_due_again(log_dir):
    state = retune_state.record_retune({}, "1", 40)
    enough = 40 + retune_state.MIN_NEW_CAPTURES
    assert retune_state.is_due("1", enough, state) is True


def test_pruning_does_not_make_someone_permanently_ineligible(log_dir):
    """face_log caps captures per person, so the count can fall. A drop
    must read as 'no new material', not as a negative that never recovers."""
    state = retune_state.record_retune({}, "1", 500)
    assert retune_state.is_due("1", 480, state) is False
    assert retune_state.is_due("1", 500 + retune_state.MIN_NEW_CAPTURES, state) is True


@pytest.mark.parametrize("entry", ["nonsense", 42, None, {"captures": "lots"},
                                   {"captures": -5}, {}])
def test_a_malformed_entry_falls_back_to_first_retune_rules(log_dir, entry):
    state = {"1": entry}
    assert retune_state.is_due(
        "1", retune_state.MIN_CAPTURES_FOR_FIRST_RETUNE, state) is True


def test_people_are_independent(log_dir):
    state = retune_state.record_retune({}, "1", 40)
    assert retune_state.is_due("1", 40, state) is False
    assert retune_state.is_due("2", 40, state) is True


# ---------------------------------------------------------------- filtering

def test_due_people_selects_only_those_ready(log_dir):
    ready = person(log_dir, "1", "alice",
                   captures=retune_state.MIN_CAPTURES_FOR_FIRST_RETUNE)
    person(log_dir, "2", "bob", captures=2)

    due = retune_state.due_people([ready, person(log_dir, "2", "bob")], {})

    assert [p[0] for p in due] == ["1"]


def test_nobody_is_due_on_a_quiet_day(log_dir):
    alice = person(log_dir, "1", "alice", captures=40)
    state = retune_state.record_retune({}, "1", 40)

    assert retune_state.due_people([alice], state) == []


def test_due_people_on_an_empty_log(log_dir):
    assert retune_state.due_people([], {}) == []


# --------------------------------------------------------------- reporting

def test_describe_an_empty_run():
    assert "up to date" in retune_state.describe([])


def test_describe_names_who_improved():
    results = [{"status": "retuned", "name": "alice"},
               {"status": "retuned", "name": "bob"}]
    summary = retune_state.describe(results)
    assert "alice" in summary and "bob" in summary


def test_describe_abbreviates_a_long_list():
    results = [{"status": "retuned", "name": f"user{i}"} for i in range(6)]
    assert "+3 more" in retune_state.describe(results)


def test_describe_mentions_skips_and_failures():
    results = [{"status": "skipped", "name": "alice"},
               {"status": "failed", "name": "bob"}]
    summary = retune_state.describe(results)
    assert "1 skipped" in summary
    assert "1 failed" in summary
