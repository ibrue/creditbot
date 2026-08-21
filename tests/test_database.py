"""Tests for the SQLite layer: credits, check-ins, streaks, votes, audits."""
import json
from datetime import date, datetime, timedelta

import pytest

import config
from conftest import expected_weekend_bonus


# ---------------------------------------------------------------- users

def test_get_or_create_user_is_idempotent(db):
    first = db.get_or_create_user("1", "alice")
    second = db.get_or_create_user("1", "alice")
    assert first["discord_id"] == second["discord_id"] == "1"
    assert len(db.get_all_users()) == 1


def test_new_user_starts_at_zero(db):
    user = db.get_or_create_user("1", "alice")
    assert user["total_credits"] == 0
    assert user["weekly_credits"] == 0
    assert user["current_streak"] == 0


def test_get_user_returns_none_when_absent(db):
    assert db.get_user("nobody") is None


def test_update_username(db):
    db.get_or_create_user("1", "alice")
    db.update_username("1", "alice_v2")
    assert db.get_user("1")["username"] == "alice_v2"


# -------------------------------------------------------------- credits

def test_add_credits_updates_totals_and_logs_transaction(db):
    db.add_credits("1", "alice", 5, "Being thanked")
    user = db.get_user("1")
    assert user["total_credits"] == 5
    assert user["weekly_credits"] == 5

    txs = db.get_transactions("1")
    assert len(txs) == 1
    assert txs[0]["amount"] == 5
    assert txs[0]["reason"] == "Being thanked"


def test_add_credits_accumulates(db):
    db.add_credits("1", "alice", 5, "a")
    db.add_credits("1", "alice", 3, "b")
    assert db.get_user("1")["total_credits"] == 8


def test_negative_credits_apply(db):
    db.add_credits("1", "alice", 10, "earned")
    db.add_credits("1", "alice", config.CREDITS["magic_smoke"], "Magic smoke")
    assert db.get_user("1")["total_credits"] == 10 + config.CREDITS["magic_smoke"]


def test_add_credits_autocreates_user(db):
    db.add_credits("99", "newbie", 3, "Documentation")
    assert db.get_user("99")["total_credits"] == 3


def test_get_transactions_respects_limit(db):
    for i in range(5):
        db.add_credits("1", "alice", i + 1, f"reason {i}")
    assert len(db.get_transactions("1", limit=3)) == 3


def test_get_transactions_is_newest_first(db):
    """Regression: same-second transactions used to come back oldest-first,
    because ORDER BY timestamp alone left the tie to rowid order."""
    for i in range(5):
        db.add_credits("1", "alice", i + 1, f"reason {i}")
    txs = db.get_transactions("1")
    assert [t["reason"] for t in txs] == [f"reason {i}" for i in range(4, -1, -1)]


# ------------------------------------------------------------- check-in

def test_checkin_creates_active_session(db):
    db.start_checkin("1", "alice", "msg1")
    active = db.get_active_checkin("1")
    assert active is not None
    assert db.get_all_checked_in()[0]["discord_id"] == "1"


def test_double_checkin_returns_same_session(db):
    first = db.start_checkin("1", "alice", "msg1")
    second = db.start_checkin("1", "alice", "msg2")
    assert first == second
    assert len(db.get_all_checked_in()) == 1


def test_checkout_without_checkin_returns_none(db):
    assert db.end_checkin("1") is None


def test_checkout_clears_active_session(db):
    db.start_checkin("1", "alice", "msg1")
    db.end_checkin("1")
    assert db.get_active_checkin("1") is None
    assert db.get_all_checked_in() == []


@pytest.mark.parametrize("minutes,expected_lab_credits", [
    (0, 0), (29, 0), (30, 1), (59, 1), (60, 2), (90, 3), (185, 6),
])
def test_lab_time_credits_are_one_per_30_minutes(db, backdate, minutes, expected_lab_credits):
    db.start_checkin("1", "alice", "msg1")
    backdate("1", minutes)
    bonus = expected_weekend_bonus(db, "1")

    result = db.end_checkin("1")

    assert result["duration_minutes"] == pytest.approx(minutes, abs=1)
    assert result["credits_earned"] == expected_lab_credits + bonus
    assert db.get_user("1")["total_credits"] == expected_lab_credits + bonus


def test_checkout_accumulates_lab_minutes(db, backdate):
    db.start_checkin("1", "alice", "msg1")
    backdate("1", 45)
    db.end_checkin("1")
    assert db.get_user("1")["total_lab_minutes"] == pytest.approx(45, abs=1)


def test_stale_checkins_are_detected(db, backdate):
    db.start_checkin("1", "alice", "msg1")
    backdate("1", 13 * 60)
    db.start_checkin("2", "bob", "msg1")

    stale = db.get_stale_checkins(hours=12)
    assert [row["discord_id"] for row in stale] == ["1"]


def test_force_checkout_no_points_leaves_credits_alone(db):
    checkin_id = db.start_checkin("1", "alice", "msg1")
    db.force_checkout_no_points(checkin_id)
    assert db.get_active_checkin("1") is None
    assert db.get_user("1")["total_credits"] == 0


def test_is_first_checkin_today(db):
    assert db.is_first_checkin_today() is True
    db.start_checkin("1", "alice", "msg1")
    assert db.is_first_checkin_today() is False


def test_checkin_history(db):
    db.start_checkin("1", "alice", "msg1")
    db.end_checkin("1")
    assert len(db.get_checkin_history("1")) == 1


# -------------------------------------------------------------- streaks

def test_first_streak_is_one(db):
    assert db.update_streak("1", "alice") == 1


def test_same_day_checkin_does_not_advance_streak(db):
    db.update_streak("1", "alice")
    assert db.update_streak("1", "alice") == 1


def _set_last_checkin(db, discord_id, day):
    conn = db.get_connection()
    conn.execute("UPDATE users SET last_checkin_date = ? WHERE discord_id = ?",
                 (day, discord_id))
    conn.commit()
    conn.close()


def test_consecutive_day_advances_streak(db):
    db.update_streak("1", "alice")
    _set_last_checkin(db, "1", date.today() - timedelta(days=1))
    assert db.update_streak("1", "alice") == 2


def test_gap_resets_streak(db):
    db.update_streak("1", "alice")
    _set_last_checkin(db, "1", date.today() - timedelta(days=3))
    assert db.update_streak("1", "alice") == 1


def test_longest_streak_survives_a_reset(db):
    db.update_streak("1", "alice")
    for _ in range(3):
        _set_last_checkin(db, "1", date.today() - timedelta(days=1))
        db.update_streak("1", "alice")
    _set_last_checkin(db, "1", date.today() - timedelta(days=5))
    db.update_streak("1", "alice")

    user = db.get_user("1")
    assert user["current_streak"] == 1
    assert user["longest_streak"] == 4


def test_streak_bonus_is_once_per_day(db):
    assert db.can_earn_streak_bonus("1") is True
    db.record_streak_bonus("1")
    assert db.can_earn_streak_bonus("1") is False


def test_weekend_bonus_is_once_per_day(db):
    assert db.can_earn_weekend_bonus("1") is True
    db.record_weekend_bonus("1")
    assert db.can_earn_weekend_bonus("1") is False


# ---------------------------------------------------------- magic smoke

def test_magic_smoke_vote_counts_up(db):
    assert db.add_magic_smoke_vote("1", "voter1") == 1
    assert db.add_magic_smoke_vote("1", "voter2") == 2
    assert db.add_magic_smoke_vote("1", "voter3") == 3


def test_duplicate_magic_smoke_vote_is_rejected(db):
    db.add_magic_smoke_vote("1", "voter1")
    assert db.add_magic_smoke_vote("1", "voter1") == -1
    assert db.has_voted_magic_smoke("1", "voter1") is True


def test_magic_smoke_voters_are_listed(db):
    db.add_magic_smoke_vote("1", "voter1")
    db.add_magic_smoke_vote("1", "voter2")
    assert sorted(db.get_magic_smoke_voters("1")) == ["voter1", "voter2"]


def test_applying_magic_smoke_clears_votes_and_allows_revote(db):
    db.add_magic_smoke_vote("1", "voter1")
    db.apply_magic_smoke("1")

    assert db.get_magic_smoke_voters("1") == []
    assert db.has_voted_magic_smoke("1", "voter1") is False
    assert db.add_magic_smoke_vote("1", "voter1") == 1


def test_votes_are_scoped_per_target(db):
    db.add_magic_smoke_vote("1", "voter1")
    assert db.add_magic_smoke_vote("2", "voter1") == 1


# ------------------------------------------------------ notebook voting

def test_notebook_submission_roundtrip(db):
    db.track_notebook_submission("msg1", "1")
    submission = db.get_notebook_submission("msg1")
    assert submission["discord_id"] == "1"
    assert submission["resolved"] == 0


def test_tracking_same_submission_twice_is_safe(db):
    db.track_notebook_submission("msg1", "1")
    db.track_notebook_submission("msg1", "1")
    assert db.get_notebook_submission("msg1")["discord_id"] == "1"


def test_only_one_caller_can_resolve_a_submission(db):
    db.track_notebook_submission("msg1", "1")
    assert db.resolve_notebook_submission("msg1", "approved") is True
    assert db.resolve_notebook_submission("msg1", "rejected") is False
    assert db.get_notebook_submission("msg1")["result"] == "approved"


def test_unresolved_submissions_are_swept(db):
    db.track_notebook_submission("msg1", "1")
    db.track_notebook_submission("msg2", "2")
    db.resolve_notebook_submission("msg2", "approved")

    unresolved = db.get_unresolved_notebook_submissions()
    assert [row["message_id"] for row in unresolved] == ["msg1"]


# ---------------------------------------------------- memes and roasting

def test_meme_credit_is_once_per_day(db):
    assert db.can_earn_meme_credit("1") is True
    db.record_meme_credit("1")
    assert db.can_earn_meme_credit("1") is False


def test_roasted_message_is_only_penalized_once(db):
    db.track_roasted_message("msg1", "1")
    assert db.is_message_roasted("msg1") is False
    db.mark_message_roasted("msg1")
    assert db.is_message_roasted("msg1") is True
    assert db.get_roasted_message_author("msg1") == "1"


# ---------------------------------------------------------- leaderboard

def test_weekly_leaderboard_is_ranked(db):
    db.add_credits("1", "alice", 10, "x")
    db.add_credits("2", "bob", 30, "x")
    db.add_credits("3", "carol", 20, "x")

    board = db.get_weekly_leaderboard()
    assert [row["username"] for row in board] == ["bob", "carol", "alice"]


def test_leaderboard_respects_limit(db):
    for i in range(5):
        db.add_credits(str(i), f"user{i}", i + 1, "x")
    assert len(db.get_weekly_leaderboard(limit=2)) == 2


def test_all_time_leaderboard_survives_weekly_reset(db):
    db.add_credits("1", "alice", 10, "x")
    db.reset_weekly_credits()

    assert db.get_user("1")["weekly_credits"] == 0
    assert db.get_all_time_leaderboard()[0]["total_credits"] == 10


def test_reset_files_scores_under_the_current_week(db):
    """reset_weekly_credits() runs Sunday 6 PM and files that week's scores
    under the current week's Monday."""
    db.add_credits("1", "alice", 10, "x")
    db.reset_weekly_credits()

    this_monday = date.today() - timedelta(days=date.today().weekday())
    conn = db.get_connection()
    row = conn.execute(
        "SELECT week_start, credits, rank FROM weekly_history WHERE discord_id = '1'"
    ).fetchone()
    conn.close()

    assert row["week_start"] == this_monday.isoformat()
    assert row["credits"] == 10
    assert row["rank"] == 1


def test_previous_week_credits_reads_last_weeks_row(db):
    """The following week, get_previous_week_credits() finds that row."""
    last_monday = date.today() - timedelta(days=date.today().weekday() + 7)
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO weekly_history (week_start, discord_id, credits, rank) "
        "VALUES (?, '1', 10, 1)", (last_monday,))
    conn.commit()
    conn.close()

    assert db.get_previous_week_credits("1") == 10


def test_previous_week_credits_defaults_to_zero(db):
    assert db.get_previous_week_credits("nobody") == 0


def test_most_lab_hours_this_week(db, backdate):
    db.start_checkin("1", "alice", "m")
    backdate("1", 30)
    db.end_checkin("1")
    db.start_checkin("2", "bob", "m")
    backdate("2", 120)
    db.end_checkin("2")

    assert db.get_most_lab_hours_this_week()["discord_id"] == "2"


def test_penalize_supreme_leader_sets_the_new_weeks_starting_score(db):
    """The handicap sets weekly credits to the penalty; all-time is untouched."""
    db.add_credits("1", "alice", 50, "a good week")
    db.reset_weekly_credits()
    db.penalize_supreme_leader("1", -15)

    user = db.get_user("1")
    assert user["weekly_credits"] == -15
    assert user["total_credits"] == 35  # the handicap is a real cost


def test_supreme_leader_handicap_does_not_create_audit_drift(db):
    """Regression: the handicap logged a transaction without touching
    total_credits, so /audit flagged the winner and its fix deducted the
    penalty a second time."""
    db.add_credits("1", "alice", 50, "a good week")
    db.reset_weekly_credits()
    db.penalize_supreme_leader("1", -15)

    assert db.audit_user_credits("1")["total_diff"] == 0
    assert db.audit_all_users() == []


def test_audit_fix_does_not_undo_the_weekly_reset(db):
    """Regression: the audit summed the whole calendar week, so a fix run
    after Sunday's reset restored everyone's pre-reset weekly score."""
    db.add_credits("1", "alice", 40, "the week's work")
    db.reset_weekly_credits()
    db.fix_user_credits("1")

    assert db.get_user("1")["weekly_credits"] == 0


# --------------------------------------------------------------- audits

def test_audit_reports_no_drift_for_clean_history(db):
    db.add_credits("1", "alice", 10, "x")
    audit = db.audit_user_credits("1")
    assert audit["total_diff"] == 0
    assert audit["calculated_total"] == 10


def test_audit_detects_drift(db):
    db.add_credits("1", "alice", 10, "x")
    conn = db.get_connection()
    conn.execute("UPDATE users SET total_credits = 999 WHERE discord_id = '1'")
    conn.commit()
    conn.close()

    audit = db.audit_user_credits("1")
    assert audit["total_diff"] == 989
    assert db.audit_all_users()[0]["discord_id"] == "1"


def test_fix_user_credits_restores_the_transaction_total(db):
    db.add_credits("1", "alice", 10, "x")
    conn = db.get_connection()
    conn.execute("UPDATE users SET total_credits = 999 WHERE discord_id = '1'")
    conn.commit()
    conn.close()

    db.fix_user_credits("1")
    assert db.get_user("1")["total_credits"] == 10
    assert db.audit_user_credits("1")["total_diff"] == 0


def test_audit_of_unknown_user_is_none(db):
    assert db.audit_user_credits("nobody") is None


def test_fix_all_credits(db):
    db.add_credits("1", "alice", 10, "x")
    db.add_credits("2", "bob", 20, "x")
    conn = db.get_connection()
    conn.execute("UPDATE users SET total_credits = 500")
    conn.commit()
    conn.close()

    db.fix_all_credits()
    assert db.get_user("1")["total_credits"] == 10
    assert db.get_user("2")["total_credits"] == 20


# ---------------------------------------------------------- kiosk/faces

def test_face_encoding_roundtrip_and_delete(db):
    embedding = [0.1] * 128
    row_id = db.save_face_encoding("1", "alice", json.dumps(embedding))
    assert row_id > 0

    faces = db.get_face_encodings()
    assert len(faces) == 1
    assert json.loads(faces[0]["embedding"]) == embedding

    assert db.delete_face_encodings("1") == 1
    assert db.get_face_encodings() == []


def test_multiple_encodings_per_person_are_kept(db):
    db.save_face_encoding("1", "alice", json.dumps([0.1] * 128))
    db.save_face_encoding("1", "alice", json.dumps([0.2] * 128))
    assert len(db.get_face_encodings()) == 2
    assert db.delete_face_encodings("1") == 2


def test_kiosk_photo_queue_marks_posted(db):
    db.add_kiosk_photo("1", "alice", "queued-photo.jpg",
                       bonuses=json.dumps(["+3 First arrival!"]))
    pending = db.get_unposted_kiosk_photos()
    assert len(pending) == 1

    db.mark_kiosk_photo_posted(pending[0]["id"])
    assert db.get_unposted_kiosk_photos() == []


# ---------------------------------------------------------------- prune

def test_prune_keeps_recent_data(db):
    db.add_credits("1", "alice", 5, "recent")
    db.prune_old_data(days=14)
    assert len(db.get_transactions("1")) == 1


def test_prune_never_touches_credits_or_transactions(db):
    """Credit history is permanent — prune only clears ephemeral tracking."""
    db.add_credits("1", "alice", 5, "old")
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET timestamp = ?",
                 (datetime.now() - timedelta(days=60),))
    conn.commit()
    conn.close()

    db.prune_old_data(days=14)
    assert len(db.get_transactions("1")) == 1
    assert db.get_user("1")["total_credits"] == 5


def test_prune_clears_old_tracking_rows(db):
    db.track_roasted_message("msg1", "1")
    db.record_meme_credit("1")
    db.add_magic_smoke_vote("1", "voter1")

    conn = db.get_connection()
    old = datetime.now() - timedelta(days=60)
    conn.execute("UPDATE roasted_messages SET created_at = ?", (old,))
    conn.execute("UPDATE magic_smoke_votes SET timestamp = ?", (old,))
    conn.execute("UPDATE meme_credits SET date = ?",
                 ((date.today() - timedelta(days=60)).isoformat(),))
    conn.commit()
    conn.close()

    db.prune_old_data(days=14)

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM roasted_messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM magic_smoke_votes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM meme_credits").fetchone()[0] == 0
    conn.close()


def test_prune_keeps_legacy_roast_rows_out(db):
    """Rows predating the created_at migration are old by definition."""
    db.track_roasted_message("msg1", "1")
    conn = db.get_connection()
    conn.execute("UPDATE roasted_messages SET created_at = NULL")
    conn.commit()
    conn.close()

    db.prune_old_data(days=14)
    # falsy, not False: is_message_roasted returns None for a missing row
    assert not db.is_message_roasted("msg1")
    assert db.get_roasted_message_author("msg1") is None


# ------------------------------------------------------------ schema/io

def test_init_database_is_idempotent(db):
    db.init_database()
    db.init_database()
    assert db.get_all_users() == []


def test_database_parent_directory_is_created(tmp_path, monkeypatch):
    import config
    import database

    nested = tmp_path / "Documents" / "CreditBot" / "social_credit.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(nested))
    database.init_database()

    assert nested.exists()


# ------------------------------------------------- weekly reset markers

def test_reset_records_a_marker_anchored_to_the_last_transaction(db):
    db.add_credits("1", "alice", 10, "x")
    db.reset_weekly_credits()

    marker = db.get_last_weekly_reset()
    assert marker is not None
    assert marker["reset_at"]

    conn = db.get_connection()
    last_id = conn.execute("SELECT MAX(id) FROM transactions").fetchone()[0]
    conn.close()
    assert marker["last_transaction_id"] == last_id


def test_no_marker_before_the_first_reset(db):
    assert db.get_last_weekly_reset() is None


def test_new_weeks_earnings_count_against_the_new_marker(db):
    db.add_credits("1", "alice", 40, "last week")
    db.reset_weekly_credits()
    db.add_credits("1", "alice", 6, "this week")

    audit = db.audit_user_credits("1")
    assert audit["calculated_weekly"] == 6
    assert audit["weekly_diff"] == 0
    assert audit["calculated_total"] == 46
    assert audit["total_diff"] == 0


def test_each_reset_supersedes_the_previous_marker(db):
    db.add_credits("1", "alice", 10, "week one")
    db.reset_weekly_credits()
    db.add_credits("1", "alice", 20, "week two")
    db.reset_weekly_credits()
    db.add_credits("1", "alice", 5, "week three")

    assert db.audit_user_credits("1")["calculated_weekly"] == 5


def test_supreme_leader_handicap_counts_toward_the_new_week(db):
    """The handicap is logged after the reset, so it lands in the new week."""
    db.add_credits("1", "alice", 50, "a good week")
    db.reset_weekly_credits()
    db.penalize_supreme_leader("1", -15)

    audit = db.audit_user_credits("1")
    assert audit["calculated_weekly"] == -15
    assert audit["weekly_diff"] == 0


# ------------------------------------------ upgrading an older database

def test_init_adds_the_reset_table_to_an_existing_database(db):
    """A database written before this change gains weekly_resets on startup
    without losing data."""
    conn = db.get_connection()
    conn.execute("DROP TABLE weekly_resets")
    conn.commit()
    conn.close()

    db.add_credits("1", "alice", 10, "x")
    db.init_database()

    assert db.get_last_weekly_reset() is None
    assert db.get_user("1")["total_credits"] == 10


def test_audit_falls_back_to_the_calendar_week_without_a_marker(db):
    """Until the first reset under the new code, the audit behaves as before."""
    db.add_credits("1", "alice", 10, "x")
    conn = db.get_connection()
    conn.execute("DELETE FROM weekly_resets")
    conn.commit()
    conn.close()

    audit = db.audit_user_credits("1")
    assert audit["calculated_weekly"] == 10
    assert audit["weekly_diff"] == 0


# ------------------------------------------- magic smoke regression tests

def _age_smoke_votes(db, hours):
    """Pretend every existing vote was cast `hours` ago."""
    conn = db.get_connection()
    conn.execute("UPDATE magic_smoke_votes SET timestamp = ?",
                 (datetime.now() - timedelta(hours=hours),))
    conn.commit()
    conn.close()


def test_a_stale_vote_does_not_block_voting_again(db):
    """Regression: a round that never reached the threshold left an unapplied
    row occupying UNIQUE(target, voter, applied), so after 24 hours the
    voter's next vote hit an IntegrityError and they could never vote
    against that person again."""
    db.add_magic_smoke_vote("bob", "alice")
    _age_smoke_votes(db, 25)

    assert db.has_voted_magic_smoke("bob", "alice") is False
    assert db.add_magic_smoke_vote("bob", "alice") == 1


def test_a_stale_vote_is_replaced_not_double_counted(db):
    db.add_magic_smoke_vote("bob", "alice")
    _age_smoke_votes(db, 25)
    db.add_magic_smoke_vote("bob", "alice")

    assert db.get_magic_smoke_voters("bob") == ["alice"]


def test_voting_twice_inside_the_window_is_still_refused(db):
    db.add_magic_smoke_vote("bob", "alice")
    assert db.add_magic_smoke_vote("bob", "alice") == -1


def test_the_same_person_can_be_smoked_more_than_once(db):
    """Regression: the second round's votes collided with the first round's
    applied rows, so nobody could ever be smoked twice."""
    for voter in ("alice", "dave", "erin"):
        db.add_magic_smoke_vote("bob", voter)
    db.apply_magic_smoke("bob")

    votes = [db.add_magic_smoke_vote("bob", v) for v in ("alice", "dave", "erin")]
    assert votes == [1, 2, 3]
    db.apply_magic_smoke("bob")

    assert db.get_magic_smoke_voters("bob") == []


def test_a_third_smoke_round_also_works(db):
    for _ in range(3):
        for voter in ("alice", "dave", "erin"):
            db.add_magic_smoke_vote("bob", voter)
        db.apply_magic_smoke("bob")

    assert db.get_magic_smoke_voters("bob") == []


def test_a_partial_round_then_a_full_one(db):
    """The realistic sequence: a vote that fizzles, then a real round later."""
    db.add_magic_smoke_vote("bob", "alice")
    _age_smoke_votes(db, 25)

    votes = [db.add_magic_smoke_vote("bob", v) for v in ("alice", "dave", "erin")]
    assert votes == [1, 2, 3]


def test_the_database_stays_writable_after_a_revote(db):
    """Regression: the failing insert escaped with the connection open,
    stranding a write transaction and locking the database for the whole
    bot — check-ins included — until the process restarted."""
    db.add_magic_smoke_vote("bob", "alice")
    _age_smoke_votes(db, 25)
    db.add_magic_smoke_vote("bob", "alice")

    db.start_checkin("9", "frank", "msg")
    db.add_credits("9", "frank", 5, "thanks")
    assert db.get_user("9")["total_credits"] == 5


def test_the_database_stays_writable_after_a_second_smoke(db):
    for voter in ("alice", "dave", "erin"):
        db.add_magic_smoke_vote("bob", voter)
    db.apply_magic_smoke("bob")
    for voter in ("alice", "dave", "erin"):
        db.add_magic_smoke_vote("bob", voter)
    db.apply_magic_smoke("bob")

    db.add_credits("9", "frank", 5, "thanks")
    assert db.get_user("9")["total_credits"] == 5


def test_votes_against_other_people_are_unaffected(db):
    db.add_magic_smoke_vote("bob", "alice")
    _age_smoke_votes(db, 25)
    db.add_magic_smoke_vote("bob", "alice")

    assert db.add_magic_smoke_vote("carol", "alice") == 1
    assert db.get_magic_smoke_voters("carol") == ["alice"]
