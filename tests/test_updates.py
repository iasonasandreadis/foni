"""Update checks, and installing an update (with a real signed test app when this Mac can)."""
import io
import json
import os
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
    assert not os.path.exists(installed.parent / ".Foni-update.app")
