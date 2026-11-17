"""Updates: check GitHub for a newer Foni, and install it in place.

The check is one small request to GitHub's public API, every 6 hours (menu → Update to the latest
version does it on demand; "check_updates": false in the settings turns it off). Installing downloads
the release's Foni.dmg, makes sure it's signed by the same developer as the running Foni (so a
tampered download is refused, and macOS keeps your permissions), swaps it in and restarts."""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

REPO = "iasonasandreadis/foni"
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def is_newer(latest, current):
    return parse_version(latest) > parse_version(current)


def latest_release(timeout=10, opener=urllib.request.urlopen):
    """(version, notes, dmg_url, page_url) of the newest release, or None."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "Foni-update-check"})
    with opener(req, timeout=timeout) as r:
        data = json.loads(r.read())
    dmg = next((a["browser_download_url"] for a in data.get("assets", [])
                if a.get("name") == "Foni.dmg"), None)
    if not dmg:
        return None
    return data["tag_name"].lstrip("v"), (data.get("body") or "").strip(), dmg, data.get("html_url")


def designated_requirement(app):
    r = subprocess.run(["codesign", "-dr", "-", app], capture_output=True, text=True)
    m = re.search(r"designated => (.+)", r.stdout + r.stderr)
    return m.group(1).strip() if m else None


def same_signer(installed, new):
    """Both apps carry the same stable signature (identifier + developer certificate)."""
    a, b = designated_requirement(installed), designated_requirement(new)
    return bool(a and a == b and "certificate leaf" in a)


_installing = threading.Lock()  # one update at a time, however many times you click
BUSY = "an update is already being installed"


def install(dmg_url, app_path, on_progress=None, trash="~/.Trash"):
    """Download, verify and swap in the new Foni.app. Returns None on success or a reason."""
    if not _installing.acquire(blocking=False):
        return BUSY
    try:
        return _install(dmg_url, app_path, on_progress, trash)
    finally:
        _installing.release()


def _install(dmg_url, app_path, on_progress, trash):
    folder = os.path.dirname(app_path)
    for old in glob.glob(os.path.join(folder, ".Foni-update*.app")):
        shutil.rmtree(old, ignore_errors=True)  # half-copied leftovers from an earlier try
    tmp = tempfile.mkdtemp(prefix="foni-update-")
    mount = os.path.join(tmp, "mnt")
    dmg = os.path.join(tmp, "Foni.dmg")
    staged = os.path.join(folder, f".Foni-update-{os.getpid()}.app")
    try:
        req = urllib.request.Request(dmg_url, headers={"User-Agent": "Foni-updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dmg, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total or None)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, dmg],
                       check=True, capture_output=True)
        try:
            new_app = os.path.join(mount, "Foni.app")
            if not os.path.isdir(new_app):
                return "the download didn't contain Foni.app"
            if subprocess.run(["codesign", "--verify", "--deep", "--strict", new_app],
                              capture_output=True).returncode != 0:
                return "the download's signature is broken"
            if not same_signer(app_path, new_app):
                return "the download isn't signed by Foni's developer"
            shutil.rmtree(staged, ignore_errors=True)
            subprocess.run(["ditto", new_app, staged], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", mount, "-quiet"], capture_output=True)
        old = os.path.join(os.path.expanduser(trash), f"Foni-{time.strftime('%Y%m%d-%H%M%S')}.app")
        os.rename(app_path, old)          # the running copy keeps working from the Trash
        try:
            os.rename(staged, app_path)
        except Exception:
            os.rename(old, app_path)      # put the old one back rather than leave no Foni
            raise
        return None
    except Exception as e:
        shutil.rmtree(staged, ignore_errors=True)
        return f"{type(e).__name__}: {e}"[:120]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
