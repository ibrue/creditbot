"""Tests for the auto-updater.

These drive real git repositories in a temp directory rather than mocking
git, because the property that matters — "fast-forward only, never clobber
local work" — is a property of git's actual behavior.
"""
import os
import subprocess

import pytest

import updater


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """An 'origin' repo plus a clone standing in for the lab PC."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main", "--bare")

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "--initial-branch=main")
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "bot.py").write_text("print('v1')\n")
    (seed / "requirements.txt").write_text("discord.py>=2.3.0\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "initial")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-u", "origin", "main")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(origin), str(clone))
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Test")

    monkeypatch.setattr(updater, "REPO_DIR", str(clone))
    monkeypatch.setattr(updater, "AUTO_UPDATE", True)
    monkeypatch.setattr(updater, "UPDATE_BRANCH", "main")

    return {"origin": origin, "seed": seed, "clone": clone}


@pytest.fixture
def no_pip(monkeypatch):
    """Record pip installs instead of actually running them."""
    calls = []
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if len(args) > 1 and args[1] == "-m" and "pip" in args:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    return calls


def push_upstream(repos, filename="bot.py", content="print('v2')\n", message="update"):
    seed = repos["seed"]
    (seed / filename).write_text(content)
    git(seed, "add", "-A")
    git(seed, "commit", "-m", message)
    git(seed, "push", "origin", "main")


# ------------------------------------------------------------ availability

def test_available_in_a_git_clone(repos):
    assert updater.is_available() is True


def test_not_available_without_a_git_directory(tmp_path, monkeypatch):
    plain = tmp_path / "zip-install"
    plain.mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", str(plain))
    assert updater.is_available() is False


def test_zip_install_reports_why_it_cannot_update(tmp_path, monkeypatch):
    plain = tmp_path / "zip-install"
    plain.mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", str(plain))
    monkeypatch.setattr(updater, "AUTO_UPDATE", True)

    result = updater.check_and_update()

    assert result["updated"] is False
    assert "not a git clone" in result["reason"]


def test_disabled_by_configuration(repos, monkeypatch):
    monkeypatch.setattr(updater, "AUTO_UPDATE", False)

    result = updater.check_and_update()

    assert result["updated"] is False
    assert "disabled" in result["reason"]


# ----------------------------------------------------------------- updating

def test_no_upstream_change_is_a_no_op(repos, no_pip):
    result = updater.check_and_update()

    assert result["updated"] is False
    assert result["reason"] == "already up to date"
    assert no_pip == []


def test_new_commit_is_pulled(repos, no_pip):
    before = git(repos["clone"], "rev-parse", "HEAD").stdout.strip()
    push_upstream(repos)

    result = updater.check_and_update()

    after = git(repos["clone"], "rev-parse", "HEAD").stdout.strip()
    assert result["updated"] is True
    assert "updated" in result["reason"]
    assert after != before
    assert after == result["new"]
    assert (repos["clone"] / "bot.py").read_text() == "print('v2')\n"


def test_update_is_idempotent(repos, no_pip):
    push_upstream(repos)
    updater.check_and_update()

    second = updater.check_and_update()
    assert second["updated"] is False
    assert second["reason"] == "already up to date"


# ------------------------------------------------------- safety properties

def test_local_edits_to_tracked_files_block_the_update(repos, no_pip):
    """The lab PC must never have someone's hand-edit clobbered."""
    (repos["clone"] / "bot.py").write_text("print('someone edited this by hand')\n")
    push_upstream(repos)

    result = updater.check_and_update()

    assert result["updated"] is False
    assert "local changes block fast-forward" in result["reason"]
    assert (repos["clone"] / "bot.py").read_text() == "print('someone edited this by hand')\n"


def test_local_commits_block_the_update(repos, no_pip):
    (repos["clone"] / "local_only.py").write_text("print('local work')\n")
    git(repos["clone"], "add", "-A")
    git(repos["clone"], "commit", "-m", "local work")
    local_head = git(repos["clone"], "rev-parse", "HEAD").stdout.strip()
    push_upstream(repos)

    result = updater.check_and_update()

    assert result["updated"] is False
    assert git(repos["clone"], "rev-parse", "HEAD").stdout.strip() == local_head
    assert (repos["clone"] / "local_only.py").exists()


def test_untracked_files_are_left_alone(repos, no_pip):
    """.env, databases and face logs are untracked — updates must not touch them."""
    (repos["clone"] / ".env").write_text("DISCORD_TOKEN=secret\n")
    (repos["clone"] / "social_credit.db").write_bytes(b"sqlite-ish bytes")
    push_upstream(repos)

    result = updater.check_and_update()

    assert result["updated"] is True
    assert (repos["clone"] / ".env").read_text() == "DISCORD_TOKEN=secret\n"
    assert (repos["clone"] / "social_credit.db").read_bytes() == b"sqlite-ish bytes"


def test_unreachable_remote_is_reported_not_raised(repos, no_pip):
    git(repos["clone"], "remote", "set-url", "origin",
        str(repos["clone"].parent / "does-not-exist"))

    result = updater.check_and_update()

    assert result["updated"] is False
    assert "fetch failed" in result["reason"]


def test_unknown_branch_is_reported_not_raised(repos, no_pip, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_BRANCH", "no-such-branch")

    result = updater.check_and_update()

    assert result["updated"] is False
    assert result["reason"]  # a reason, not an exception


# --------------------------------------------------------- dependency sync

def test_requirements_change_triggers_a_reinstall(repos, no_pip):
    push_upstream(repos, filename="requirements.txt",
                  content="discord.py>=2.3.0\nrequests>=2.31.0\n",
                  message="add requests")

    result = updater.check_and_update()

    assert result["updated"] is True
    assert len(no_pip) == 1
    assert no_pip[0][-1].endswith("requirements.txt")


def test_code_only_change_skips_the_reinstall(repos, no_pip):
    push_upstream(repos)

    result = updater.check_and_update()

    assert result["updated"] is True
    assert no_pip == []


def test_nested_requirements_are_also_reinstalled(repos, no_pip):
    seed = repos["seed"]
    (seed / "kiosk").mkdir(exist_ok=True)
    (seed / "kiosk" / "requirements.txt").write_text("opencv-python\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "kiosk requirements")
    git(seed, "push", "origin", "main")

    result = updater.check_and_update()

    assert result["updated"] is True
    assert len(no_pip) == 1
    installed = no_pip[0][-1].replace("\\", "/")
    assert installed.endswith("kiosk/requirements.txt")


# -------------------------------------------------------- background thread

def _no_threads(monkeypatch):
    def boom(*args, **kwargs):
        pytest.fail("start_background_updater started a thread when it should not have")
    monkeypatch.setattr(updater.threading, "Thread", boom)


def test_background_updater_is_skipped_when_disabled(repos, monkeypatch):
    monkeypatch.setattr(updater, "AUTO_UPDATE", False)
    _no_threads(monkeypatch)

    updater.start_background_updater("server")


def test_background_updater_is_skipped_for_a_zip_install(tmp_path, monkeypatch):
    plain = tmp_path / "zip-install"
    plain.mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", str(plain))
    monkeypatch.setattr(updater, "AUTO_UPDATE", True)
    _no_threads(monkeypatch)

    updater.start_background_updater("server")


def test_update_interval_has_a_floor():
    """A tiny interval would hammer GitHub; the module clamps it to 5 minutes."""
    assert updater.UPDATE_INTERVAL_MIN >= 5
