import objc
import time
from AppKit import (
    NSBezierPath,
    NSColor,
    NSGradient,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPasteboard,
    NSPasteboardTypeString,
    NSString,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
)
from .pills import make_panel


class CopyView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(CopyView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.label = "Copy text"
        self.on_click = None
        self.accent = (1.0, 0.52, 0.18)  # the outline: matches the pill style (see STYLE_ACCENTS)
        return self

    def isOpaque(self):
        return False

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        if self.on_click:
            self.on_click()

    def drawRect_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, w - 1, h - 1), (h - 1) / 2, (h - 1) / 2)
        if self.accent == "rainbow":  # a rainbow rim: the gradient, then the dark body inset over it
            NSGradient.alloc().initWithColors_([
                NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(i / 6, 0.7, 1.0, 0.85)
                for i in range(7)]).drawInBezierPath_angle_(body, 0)
            inner = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(1.5, 1.5, w - 3, h - 3), (h - 3) / 2, (h - 3) / 2)
            NSColor.colorWithCalibratedWhite_alpha_(0.05, 1.0).setFill()
            inner.fill()
        else:
            NSColor.colorWithCalibratedWhite_alpha_(0.05, 0.92).setFill()
            body.fill()
            r, g, bl = self.accent
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, bl, 0.6).setStroke()
            body.setLineWidth_(0.8)
            body.stroke()
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, 0.3),
                 NSForegroundColorAttributeName: NSColor.whiteColor()}
        s = NSString.stringWithString_(self.label)
        size = s.sizeWithAttributes_(attrs)
        s.drawAtPoint_withAttributes_(((w - size.width) / 2, (h - size.height) / 2), attrs)


class CopyBubble:
    """Shown when there was no text box to paste into: one click copies the text."""
    H = 24
    SHOW_SECONDS = 8

    def __init__(self, anchor):
        self.anchor = anchor  # function -> (center x, bottom y) of the pill on screen
        self.visible = False
        self.panel = make_panel(90, self.H, clickable=True)
        self.view = CopyView.alloc().initWithFrame_(NSMakeRect(0, 0, 90, self.H))
        self.view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.view.on_click = self._clicked
        self.panel.setContentView_(self.view)
        self.text = ""
        self.hide_at = 0.0

    def set_accent(self, accent):
        """An (r, g, b) colour, or "rainbow", for the outline, to match the pill style."""
        self.view.accent = accent or (1.0, 0.52, 0.18)
        self.view.setNeedsDisplay_(True)

    def show(self, text):
        self.text = text
        self._set_label("Copy text")
        self.hide_at = time.time() + self.SHOW_SECONDS
        self.panel.orderFrontRegardless()
        self.visible = True

    def hide(self):
        self.panel.orderOut_(None)
        self.hide_at = 0.0
        self.visible = False

    def _set_label(self, label):
        self.view.label = label
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, 0.3)}
        w = NSString.stringWithString_(label).sizeWithAttributes_(attrs).width + 26
        cx, bottom = self.anchor()
        self.panel.setFrame_display_(NSMakeRect(cx - w / 2, bottom, w, self.H), True)
        self.view.setNeedsDisplay_(True)

    def toast(self, label, seconds=3.5):
        """A short note (not clickable), e.g. "Added RAM to dictionary"."""
        self.text = None
        self._set_label(label)
        self.hide_at = time.time() + seconds
        self.panel.orderFrontRegardless()
        self.visible = True

    def _clicked(self):
        if self.text is None:
            self.hide()
            return
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(self.text, NSPasteboardTypeString)
        self._set_label("Copied ✓")
        self.hide_at = time.time() + 0.9

    def tick(self):
        if self.hide_at and time.time() > self.hide_at:
            self.hide()
