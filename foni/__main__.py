"""Start Foni: `python -m foni` (the app bundle does the same)."""
import fcntl
import os
import signal
import sys
import time

from .config import APP_DIR, LOG_PATH, migrate_old_data


class _Stamped:
    """Puts the date and time in front of every log line, so a problem can be matched to the
    moment it happened."""

    def __init__(self, stream):
        self.stream, self.at_start = stream, True

    def write(self, s):
        if not s:
            return 0
        out = []
        for line in s.splitlines(keepends=True):
            if self.at_start:
                out.append(time.strftime("%m-%d %H:%M:%S "))
            out.append(line)
            self.at_start = line.endswith("\n")
        self.stream.write("".join(out))
        return len(s)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _take_lock(lock, wait=10.0):
    """Only one Foni at a time, or every dictation would be typed twice. A restart opens the
    new Foni while the old one is still closing, so wait a little for it before giving up."""
    end = time.time() + wait
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() > end:
                return False
            time.sleep(0.25)


def main():
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    sys.stdout, sys.stderr = _Stamped(sys.stdout), _Stamped(sys.stderr)
    if migrate_old_data():
        print("[setup] brought over settings, dictionary and history from LocalFlow")
    APP_DIR.mkdir(exist_ok=True)
    try:  # keep only the latest ~1000 lines of the log
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 512_000:
            LOG_PATH.write_text("\n".join(LOG_PATH.read_text(errors="ignore").splitlines()[-1000:]) + "\n")
    except Exception:
        pass
    lock = open(APP_DIR / "foni.lock", "w")
    if not _take_lock(lock):
        print("[exit] Foni is already running")
        raise SystemExit(0)
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from .app import Foni
    # menu-bar only: no icon in the Dock or the Cmd-Tab switcher
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    print(f"[start] Foni {sys.modules['foni'].__version__}")
    app = Foni()

    def stopped(signum, frame):  # e.g. a force quit: don't leave your sound muted
        print(f"[quit] stopped by macOS ({signal.Signals(signum).name})")
        app.muter.restore()
        os._exit(0)
    signal.signal(signal.SIGTERM, stopped)
    app.run()
    lock.close()


if __name__ == "__main__":
    main()
