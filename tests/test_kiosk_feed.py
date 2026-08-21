"""Tests for the cog that posts kiosk check-in photos to Discord.

The cog's task loop is not started; `_post_kiosk_photos` is driven
directly so the queue-handling logic is what's under test.
"""
import asyncio
import json

import pytest

import config
from cogs.kiosk_feed import KioskFeedCog


class FakeChannel:
    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    async def send(self, embed=None, file=None):
        if file is not None:
            # discord.py closes the handle after upload; do the same so the
            # cog can delete the file (Windows won't unlink an open file).
            file.close()
        if self.fail_with:
            raise self.fail_with
        self.sent.append({"embed": embed, "file": file})


class FakeBot:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel


@pytest.fixture
def cog():
    """The cog without its task loop running."""
    instance = object.__new__(KioskFeedCog)
    return instance


@pytest.fixture(autouse=True)
def _feed_config(monkeypatch):
    monkeypatch.setattr(config, "KIOSK_POST_PHOTOS", True)
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 12345)


@pytest.fixture
def no_captions(monkeypatch):
    import cogs.kiosk_feed as feed
    monkeypatch.setattr(feed.caption_mod, "generate_caption", lambda path: None)


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "checkin.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9")
    return path


def run(cog, bot):
    cog.bot = bot
    asyncio.run(cog._post_kiosk_photos())


# ------------------------------------------------------------- happy path

def test_queued_photo_is_posted_and_dequeued(cog, db, photo, no_captions):
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert len(channel.sent) == 1
    assert db.get_unposted_kiosk_photos() == []


def test_posted_photo_is_deleted_from_disk(cog, db, photo, no_captions):
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))

    run(cog, FakeBot(FakeChannel()))

    assert not photo.exists()


def test_embed_mentions_the_member(cog, db, photo, no_captions):
    db.add_kiosk_photo("42", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert "<@42>" in channel.sent[0]["embed"].description


def test_bonuses_are_included(cog, db, photo, no_captions):
    bonuses = ["+3 First arrival!", "+2 Night owl!"]
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps(bonuses))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    description = channel.sent[0]["embed"].description
    assert "+3 First arrival!" in description
    assert "+2 Night owl!" in description


def test_several_photos_are_all_posted(cog, db, tmp_path, no_captions):
    for i in range(3):
        path = tmp_path / f"p{i}.jpg"
        path.write_bytes(b"\xff\xd8fake\xff\xd9")
        db.add_kiosk_photo(str(i), f"user{i}", str(path), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert len(channel.sent) == 3
    assert db.get_unposted_kiosk_photos() == []


# --------------------------------------------------------------- captions

def test_caption_is_added_when_available(cog, db, photo, monkeypatch):
    import cogs.kiosk_feed as feed
    monkeypatch.setattr(feed.caption_mod, "generate_caption",
                        lambda path: "Robot wrangler reporting for duty!")
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert "Robot wrangler reporting for duty!" in channel.sent[0]["embed"].description


def test_a_caption_failure_still_posts_the_photo(cog, db, photo, monkeypatch):
    import cogs.kiosk_feed as feed

    def boom(path):
        raise RuntimeError("ollama exploded")

    monkeypatch.setattr(feed.caption_mod, "generate_caption", boom)
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert len(channel.sent) == 1
    assert db.get_unposted_kiosk_photos() == []


# ----------------------------------------------------- malformed queue rows

def test_a_vanished_file_is_dropped_from_the_queue(cog, db, tmp_path, no_captions):
    db.add_kiosk_photo("1", "alice", str(tmp_path / "gone.jpg"), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert channel.sent == []
    assert db.get_unposted_kiosk_photos() == []


@pytest.mark.parametrize("bonuses", [None, "", "not json at all", "{}"])
def test_unreadable_bonuses_do_not_break_posting(cog, db, photo, no_captions, bonuses):
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=bonuses)
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert len(channel.sent) == 1


# --------------------------------------------------------------- retrying

def test_a_send_failure_leaves_the_photo_queued(cog, db, photo, no_captions):
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel(fail_with=RuntimeError("Discord is down"))

    run(cog, FakeBot(channel))

    assert db.get_unposted_kiosk_photos() != []
    assert photo.exists()  # not deleted, so the retry can still post it


def test_a_send_failure_stops_the_batch_rather_than_reordering_it(cog, db, tmp_path, no_captions):
    for i in range(3):
        path = tmp_path / f"p{i}.jpg"
        path.write_bytes(b"\xff\xd8fake\xff\xd9")
        db.add_kiosk_photo(str(i), f"user{i}", str(path), bonuses=json.dumps([]))
    channel = FakeChannel(fail_with=RuntimeError("Discord is down"))

    run(cog, FakeBot(channel))

    assert len(db.get_unposted_kiosk_photos()) == 3


def test_the_retry_succeeds_once_discord_recovers(cog, db, photo, no_captions):
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))

    run(cog, FakeBot(FakeChannel(fail_with=RuntimeError("Discord is down"))))
    assert len(db.get_unposted_kiosk_photos()) == 1

    channel = FakeChannel()
    run(cog, FakeBot(channel))

    assert len(channel.sent) == 1
    assert db.get_unposted_kiosk_photos() == []


# ----------------------------------------------------------- disabled paths

def test_nothing_posts_when_photo_posting_is_off(cog, db, photo, no_captions, monkeypatch):
    monkeypatch.setattr(config, "KIOSK_POST_PHOTOS", False)
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert channel.sent == []
    assert len(db.get_unposted_kiosk_photos()) == 1


def test_nothing_posts_without_a_checkin_channel(cog, db, photo, no_captions, monkeypatch):
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 0)
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert channel.sent == []
    assert len(db.get_unposted_kiosk_photos()) == 1


def test_an_unresolvable_channel_leaves_the_queue_intact(cog, db, photo, no_captions):
    """The bot may not have the channel cached yet — keep the photo for later."""
    db.add_kiosk_photo("1", "alice", str(photo), bonuses=json.dumps([]))

    run(cog, FakeBot(None))

    assert len(db.get_unposted_kiosk_photos()) == 1
    assert photo.exists()


def test_an_empty_queue_is_a_no_op(cog, db, no_captions):
    channel = FakeChannel()

    run(cog, FakeBot(channel))

    assert channel.sent == []
