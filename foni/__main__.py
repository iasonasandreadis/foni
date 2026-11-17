"""Start Foni: `python -m foni` (the app bundle does the same)."""
import fcntl
import os

from .config import APP_DIR, LOG_PATH, migrate_old_data


def main():
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if migrate_old_data():
        print("[setup] brought over settings, dictionary and history from LocalFlow")
    APP_DIR.mkdir(exist_ok=True)
    try:  # keep only the latest ~1000 lines of the log
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 512_000:
            LOG_PATH.write_text("\n".join(LOG_PATH.read_text(errors="ignore").splitlines()[-1000:]) + "\n")
    except Exception:
        pass
    lock = open(APP_DIR / "foni.lock", "w")
    try:  # only one Foni at a time, or every dictation would be pasted twice
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[exit] Foni is already running")
        raise SystemExit(0)
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from .app import Foni
    # menu-bar only: no icon in the Dock or the Cmd-Tab switcher
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    Foni().run()
    lock.close()


if __name__ == "__main__":
    main()
