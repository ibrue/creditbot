"""Auto-updater: pull new versions from the git repo and restart.

When changes are merged to the update branch (main by default) on GitHub,
machines running the server or the kiosk pick them up automatically:
every UPDATE_INTERVAL_MIN minutes the updater fetches, fast-forwards, and
reinstalls dependencies if requirements changed — then the process exits
and the start script's restart loop brings it back up on the new code.

Safety properties:
- Fast-forward only: local commits or edited tracked files block the
  update (logged, skipped) instead of being clobbered.
- .env, databases, face logs, and models are untracked, so updates never
  touch them.
- Requires a git clone + git on PATH; ZIP installs just log that
  auto-update is off.

Config (environment):
  AUTO_UPDATE          1 (default) to enable, 0 to disable
  UPDATE_BRANCH        branch to follow (default: main)
  UPDATE_INTERVAL_MIN  minutes between checks (default: 30)
"""
import os
import subprocess
import sys
import threading

AUTO_UPDATE = os.getenv("AUTO_UPDATE", "1") == "1"
UPDATE_BRANCH = os.getenv("UPDATE_BRANCH", "main")
UPDATE_INTERVAL_MIN = max(5, int(os.getenv("UPDATE_INTERVAL_MIN", "30")))

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
# Exit code that means "restart me on the new code" — the .bat/.sh restart
# loops bring the process back up regardless of code, so any exit works,
# but a distinctive one keeps logs readable
RESTART_EXIT_CODE = 75


def _git(*args, timeout: int = 120):
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=True, text=True,
        timeout=timeout,
    )


def is_available() -> bool:
    """Auto-update needs a git clone and git on PATH."""
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return False
    try:
        return _git("--version", timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_and_update() -> dict:
    """Fetch the update branch and fast-forward onto it if it moved.

    Returns {"updated": bool, "reason": str, "old": sha, "new": sha}.
    Never raises — any git problem is reported in "reason".
    """
    result = {"updated": False, "reason": "", "old": None, "new": None}
    try:
        if not AUTO_UPDATE:
            result["reason"] = "disabled (AUTO_UPDATE=0)"
            return result
        if not is_available():
            result["reason"] = "not a git clone (or git not installed) — auto-update off"
            return result

        fetch = _git("fetch", "origin", UPDATE_BRANCH)
        if fetch.returncode != 0:
            result["reason"] = f"fetch failed: {fetch.stderr.strip()[:200]}"
            return result

        head = _git("rev-parse", "HEAD").stdout.strip()
        remote = _git("rev-parse", f"origin/{UPDATE_BRANCH}").stdout.strip()
        result["old"], result["new"] = head, remote
        if not head or not remote:
            result["reason"] = "could not resolve revisions"
            return result
        if head == remote:
            result["reason"] = "already up to date"
            return result

        # Did requirements change? (checked before moving HEAD)
        diff = _git("diff", "--name-only", head, remote)
        changed = diff.stdout.split()

        merge = _git("merge", "--ff-only", f"origin/{UPDATE_BRANCH}")
        if merge.returncode != 0:
            result["reason"] = (
                "local changes block fast-forward — resolve manually "
                f"({merge.stderr.strip()[:200]})"
            )
            return result

        # Reinstall dependencies if any requirements file changed
        req_files = [f for f in changed if f.endswith("requirements.txt")]
        for req in req_files:
            req_path = os.path.join(REPO_DIR, req)
            if os.path.exists(req_path):
                print(f"🔄 Updating dependencies from {req}...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", req_path],
                    cwd=REPO_DIR, timeout=600,
                )

        result["updated"] = True
        result["reason"] = f"updated {head[:8]} -> {remote[:8]}"
        return result
    except Exception as e:
        result["reason"] = f"updater error: {e}"
        return result


def start_background_updater(name: str = "server"):
    """Check for updates periodically; exit the process when one lands.

    The start script's restart loop (start_server.bat / start_kiosk.bat /
    run_kiosk.sh / systemd) relaunches on the new code.
    """
    if not AUTO_UPDATE:
        return
    if not is_available():
        print("ℹ️ Auto-update off (not a git clone, or git not installed). "
              "Clone the repo with git to get automatic updates.")
        return

    def loop():
        import time
        while True:
            time.sleep(UPDATE_INTERVAL_MIN * 60)
            result = check_and_update()
            if result["updated"]:
                print(f"🔄 {name}: {result['reason']} — restarting to apply.")
                os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=loop, daemon=True, name="auto-updater").start()
    print(f"🔄 Auto-update on: following origin/{UPDATE_BRANCH}, "
          f"checking every {UPDATE_INTERVAL_MIN} min")


if __name__ == "__main__":
    # Manual one-shot update:  python updater.py
    outcome = check_and_update()
    print(("✅ " if outcome["updated"] else "ℹ️ ") + outcome["reason"])
    sys.exit(0 if outcome["updated"] or "up to date" in outcome["reason"] else 1)
