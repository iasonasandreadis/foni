"""Mute the Mac's sound while you dictate, so the mic hears only you (not a FaceTime call,
a video or music), and put it back the moment you stop.

Talks to Core Audio directly: no extra permission, and it takes microseconds. If Foni is
stopped while your sound is muted, the next start puts it back (see `recover`)."""
import ctypes
import json
import threading
from ctypes import POINTER, byref, c_float, c_int32, c_ubyte, c_uint32, c_void_p

from .config import APP_DIR

MARK = APP_DIR / "muted.json"  # what Foni muted, until it's put back


def _code(s):
    return int.from_bytes(s.encode(), "big")


SYSTEM, MAIN = 1, 0
DEFAULT_OUTPUT, GLOBAL, OUTPUT = _code("dOut"), _code("glob"), _code("outp")
MUTE, MAIN_VOLUME = _code("mute"), _code("vmvc")


class _Address(ctypes.Structure):
    _fields_ = [("selector", c_uint32), ("scope", c_uint32), ("element", c_uint32)]


def _load(path, prefix):
    try:
        lib = ctypes.cdll.LoadLibrary(path)
        fns = [getattr(lib, prefix + n) for n in
               ("HasProperty", "IsPropertySettable", "GetPropertyData", "SetPropertyData")]
    except (OSError, AttributeError):
        return None
    has, settable, get, set_ = fns
    has.restype, has.argtypes = c_ubyte, [c_uint32, POINTER(_Address)]
    settable.restype, settable.argtypes = c_int32, [c_uint32, POINTER(_Address), POINTER(c_ubyte)]
    get.restype = c_int32
    get.argtypes = [c_uint32, POINTER(_Address), c_uint32, c_void_p, POINTER(c_uint32), c_void_p]
    set_.restype = c_int32
    set_.argtypes = [c_uint32, POINTER(_Address), c_uint32, c_void_p, c_uint32, c_void_p]
    return fns


_CORE = _load("/System/Library/Frameworks/CoreAudio.framework/CoreAudio", "AudioObject")
# the volume slider you see in Control Center (for speakers that can't simply be muted)
_SERVICE = _load("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox",
                 "AudioHardwareService")


def _can_set(api, device, addr):
    if api is None or not api[0](device, byref(addr)):
        return False
    ok = c_ubyte(0)
    return api[1](device, byref(addr), byref(ok)) == 0 and bool(ok.value)


def _get(api, device, addr, value):
    size = c_uint32(ctypes.sizeof(value))
    return api[2](device, byref(addr), 0, None, byref(size), byref(value)) == 0


def _set(api, device, addr, value):
    return api[3](device, byref(addr), 0, None, ctypes.sizeof(value), byref(value)) == 0


def default_output():
    """The device your sound plays on right now (speakers, AirPods, a display...)."""
    if _CORE is None:
        return None
    device = c_uint32(0)
    ok = _get(_CORE, SYSTEM, _Address(DEFAULT_OUTPUT, GLOBAL, MAIN), device)
    return device.value if ok and device.value else None


def silence(device):
    """Mute `device`. Returns how to undo it, or None (already silent, or it can't be muted)."""
    mute = _Address(MUTE, OUTPUT, MAIN)
    if _can_set(_CORE, device, mute):
        now = c_uint32(0)
        if _get(_CORE, device, mute, now) and now.value:
            return None  # you had muted it yourself: leave it, and don't unmute it later
        return {"device": device, "how": "mute"} if _set(_CORE, device, mute, c_uint32(1)) else None
    volume = _Address(MAIN_VOLUME, OUTPUT, MAIN)
    if _can_set(_SERVICE, device, volume):
        now = c_float(0)
        if not _get(_SERVICE, device, volume, now) or now.value <= 0:
            return None
        if _set(_SERVICE, device, volume, c_float(0)):
            return {"device": device, "how": "volume", "volume": now.value}
    return None


def unsilence(undo):
    device = undo["device"]
    if undo["how"] == "mute":
        return _set(_CORE, device, _Address(MUTE, OUTPUT, MAIN), c_uint32(0))
    return _set(_SERVICE, device, _Address(MAIN_VOLUME, OUTPUT, MAIN), c_float(undo["volume"]))


class SpeakerMuter:
    def __init__(self, backend=None):
        self.lock = threading.Lock()
        self.undo = None
        self.silence, self.unsilence, self.output = backend or (silence, unsilence, default_output)

    def mute(self, still_wanted=lambda: True):
        """Mute now, if `still_wanted()` (checked under the lock, so a recording that ended a
        moment ago can't leave your sound off)."""
        with self.lock:
            if self.undo is not None or not still_wanted():
                return
            device = self.output()
            undo = self.silence(device) if device else None
            if undo:
                self.undo = undo
                try:
                    MARK.write_text(json.dumps(undo))
                except OSError:
                    pass

    def restore(self):
        with self.lock:
            undo, self.undo = self.undo, None
            if undo is None:
                return
            try:
                self.unsilence(undo)
            except Exception as e:
                print(f"[sound] couldn't unmute: {e}")
            MARK.unlink(missing_ok=True)

    def recover(self):
        """Foni stopped while your sound was muted (e.g. it was force-quit): unmute it."""
        try:
            undo = json.loads(MARK.read_text())
        except (OSError, ValueError):
            return
        try:
            self.unsilence(undo)
            print("[sound] put your sound back on (Foni had stopped while it was muted)")
        except Exception as e:
            print(f"[sound] couldn't unmute: {e}")
        MARK.unlink(missing_ok=True)
