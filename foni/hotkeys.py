


# name: (keycode, left/right-specific flag, generic flag). Some external keyboards
# don't report the left/right flag, so we fall back to the generic one.
FN, OPT, CMD, CTRL, SHIFT = 0x800000, 0x80000, 0x100000, 0x40000, 0x20000
HOTKEYS = {
    "fn": (63, FN, FN),
    "right_option": (61, 0x40, OPT),
    "right_command": (54, 0x10, CMD),
    "right_control": (62, 0x2000, CTRL),
    "right_shift": (60, 0x04, SHIFT),
    "left_option": (58, 0x20, OPT),
    "left_command": (55, 0x08, CMD),
    "left_control": (59, 0x01, CTRL),
    "left_shift": (56, 0x02, SHIFT),
}
KEYCODE_TO_NAME = {kc: name for name, (kc, _, _) in HOTKEYS.items()}
MODIFIER_NAMES = {CTRL: "Control", OPT: "Option", SHIFT: "Shift", CMD: "Command"}


def command_modifier(hotkeys):
    """Key held together with the hotkey for voice commands: Control, unless a Control key is
    itself a hotkey (then Option, then Shift)."""
    used = {HOTKEYS[h][2] for h in hotkeys}
    return next(m for m in (CTRL, OPT, SHIFT, CMD) if m not in used)

def pretty(names):
    return " or ".join(n.replace("_", " ").title() for n in names)
