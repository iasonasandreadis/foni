"""Update checks, and installing an update (with a real signed test app when this Mac can)."""
import io
import json
import pathlib
import plistlib
import shutil
import subprocess

import pytest

from foni import updates


def test_versions():
    assert updates.is_newer("1.0.10", "1.0.9")
    assert updates.is_newer("v2.0.0", "1.9.9")
    assert not updates.is_newer("1.0.2", "1.0.2")
    assert not updates.is_newer("1.0.1", "1.0.2")


def test_latest_release_reads_github(monkeypatch):
    body = {"tag_name": "v1.2.0", "body": "Faster.\n\nMore.", "html_url": "https://x/r",
            "assets": [{"name": "Foni.dmg", "browser_download_url": "https://x/Foni.dmg"}]}

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    info = updates.latest_release(opener=lambda req, timeout: R(json.dumps(body).encode()))
    assert info == ("1.2.0", "Faster.\n\nMore.", "https://x/Foni.dmg", "https://x/r")


def test_same_signer_needs_a_developer_certificate(monkeypatch):
    reqs = {"a": 'identifier "x" and certificate leaf = H"1"', "b": 'identifier "x" and certificate leaf = H"1"',
            "c": 'cdhash H"abc"', "d": 'cdhash H"abc"', "e": 'identifier "x" and certificate leaf = H"2"'}
    monkeypatch.setattr(updates, "designated_requirement", lambda app: reqs[app])
    assert updates.same_signer("a", "b")
    assert not updates.same_signer("c", "d")   # ad-hoc: changes every build, can't prove anything
    assert not updates.same_signer("a", "e")   # someone else's certificate


def test_trusts_foni_s_apple_developer_team_once_it_is_set(monkeypatch):
    reqs = {"old": 'identifier "x" and certificate leaf = H"1"', "new": 'identifier "x" and anchor apple generic'}
    monkeypatch.setattr(updates, "designated_requirement", lambda app: reqs[app])
    checked = []
    monkeypatch.setattr(updates, "signed_by_team", lambda app, team: checked.append((app, team)) or team == "ABCDE12345")
    assert not updates.same_signer("old", "new")           # no team yet: refused, as before
    monkeypatch.setattr(updates, "DEVELOPER_TEAM", "ABCDE12345")
    assert updates.same_signer("old", "new") and checked[-1] == ("new", "ABCDE12345")
    monkeypatch.setattr(updates, "DEVELOPER_TEAM", "SOMEONE999")
    assert not updates.same_signer("old", "new")           # another team: refused


def test_a_self_signed_app_is_not_taken_for_the_team(tmp_path):
    # the real codesign check: Foni's current self-signed build can't pass as an Apple-vouched team
    import glob
    app = next(iter(glob.glob("/Applications/Foni.app") + glob.glob("build/Foni.app")), None)
    if not app:
        pytest.skip("no built Foni to check")
    assert not updates.signed_by_team(app, "ABCDE12345")


HAS_ID = b'"Foni Developer"' in subprocess.run(["security", "find-identity", "-p", "codesigning"],
                                               capture_output=True).stdout


def _fake_app(folder, version, sign):
    app = pathlib.Path(folder) / "Foni.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    shutil.copy("/bin/echo", app / "Contents/MacOS/Foni")
    plistlib.dump({"CFBundleIdentifier": "com.jayandreadis.foni", "CFBundleExecutable": "Foni",
                   "CFBundleShortVersionString": version, "CFBundlePackageType": "APPL"},
                  open(app / "Contents/Info.plist", "wb"))
    subprocess.run(["codesign", "--force", "--sign", sign, "--identifier", "com.jayandreadis.foni",
                    str(app)], check=True, capture_output=True)
    return app


def _dmg(tmp, version, sign):
    src = tmp / f"src-{version}"
    src.mkdir()
    _fake_app(src, version, sign)
    dmg = tmp / f"Foni-{version}.dmg"
    subprocess.run(["hdiutil", "create", "-quiet", "-volname", "Foni", "-srcfolder", str(src),
                    "-format", "UDZO", str(dmg)], check=True)
    return dmg.as_uri()


@pytest.mark.skipif(not HAS_ID, reason="needs the Foni Developer signing identity")
def test_install_swaps_in_a_signed_update(tmp_path):
    installed = _fake_app(tmp_path / "Applications", "1.0.0", "Foni Developer")
    trash = tmp_path / "Trash"
    trash.mkdir()
    err = updates.install(_dmg(tmp_path, "1.0.1", "Foni Developer"), str(installed), trash=str(trash))
    assert err is None
    info = plistlib.load(open(installed / "Contents/Info.plist", "rb"))
    assert info["CFBundleShortVersionString"] == "1.0.1"
    assert len(list(trash.iterdir())) == 1     # the old version went to the Trash


@pytest.mark.skipif(not HAS_ID, reason="needs the Foni Developer signing identity")
def test_install_refuses_a_differently_signed_download(tmp_path):
    installed = _fake_app(tmp_path / "Applications", "1.0.0", "Foni Developer")
    err = updates.install(_dmg(tmp_path, "9.9.9", "-"), str(installed), trash=str(tmp_path))
    assert err == "the download isn't signed by Foni's developer"
    info = plistlib.load(open(installed / "Contents/Info.plist", "rb"))
    assert info["CFBundleShortVersionString"] == "1.0.0"   # untouched
    assert not list(installed.parent.glob(".Foni-update*.app"))


def test_only_one_install_at_a_time(tmp_path, monkeypatch):
    # clicking Update five times must not start five installs that delete each other's files
    import threading
    started, finish = threading.Event(), threading.Event()

    def slow_install(*args):
        started.set()
        finish.wait(5)
    monkeypatch.setattr(updates, "_install", slow_install)
    first = threading.Thread(target=updates.install, args=("url", str(tmp_path / "Foni.app")))
    first.start()
    started.wait(5)
    assert updates.install("url", str(tmp_path / "Foni.app")) == updates.BUSY
    finish.set()
    first.join(5)
    assert updates.install("url", str(tmp_path / "Foni.app")) is None  # free again afterwards


def test_install_clears_half_copied_leftovers(tmp_path):
    (tmp_path / ".Foni-update.app/Contents").mkdir(parents=True)   # left by an older Foni
    err = updates.install((tmp_path / "missing.dmg").as_uri(), str(tmp_path / "Foni.app"))
    assert err and not list(tmp_path.glob(".Foni-update*.app"))
