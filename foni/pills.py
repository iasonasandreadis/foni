import math
import time
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSEvent,
    NSGradient,
    NSGraphicsContext,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Quartz import (
    CAEmitterCell,
    CAEmitterLayer,
    CAGradientLayer,
    CALayer,
    CATransaction,
    kCAEmitterLayerAdditive,
    kCAEmitterLayerLine,
    kCAEmitterLayerRectangle,
    kCAEmitterLayerUnordered,
)


# ---------------------------------------------------------------- floating pill

def screen_under_mouse():
    p = NSEvent.mouseLocation()
    for s in NSScreen.screens():
        f = s.frame()
        if f.origin.x <= p.x < f.origin.x + f.size.width and \
                f.origin.y <= p.y < f.origin.y + f.size.height:
            return s
    return NSScreen.mainScreen() or NSScreen.screens()[0]


def make_panel(w, h, clickable=False):
    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setIgnoresMouseEvents_(not clickable)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary)
    return panel


PILL_BARS = 18  # how many recent loudness readings the fire listens to

# Fire is drawn by macOS itself (Core Animation particles on the GPU): smooth, soft and
# nearly free on the CPU. Python only nudges how hard it burns, ~30 times a second.
#               flame base (hot)        flame tips (cooler)     sparks
FIRE_PALETTES = {
    "recording": ((1.00, 0.80, 0.34), (0.96, 0.22, 0.04), (1.00, 0.78, 0.40)),  # fire
    "latched": ((0.78, 0.93, 1.00), (0.10, 0.32, 0.98), (0.62, 0.86, 1.00)),    # blue: hands-free
    "command": ((0.96, 0.82, 1.00), (0.52, 0.16, 0.95), (0.90, 0.72, 1.00)),    # purple: command
    "working": ((1.00, 0.56, 0.22), (0.62, 0.12, 0.03), (1.00, 0.70, 0.35)),    # embers: thinking
}


def _cg(rgb, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a).CGColor()


def _particle_image(draw, px=64):
    """A small white picture for particles; each particle tints it with its own color."""
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, px, px, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    draw(px)
    NSGraphicsContext.restoreGraphicsState()
    return rep.CGImage()


def _glow(rect, peak=1.0):
    NSGradient.alloc().initWithColors_([
        NSColor.colorWithCalibratedWhite_alpha_(1.0, peak),
        NSColor.colorWithCalibratedWhite_alpha_(1.0, peak * 0.45),
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
    ]).drawInRect_relativeCenterPosition_(rect, (0, 0))


def _soft_dot(px=64):
    """A soft round glow: the particle every flame is built from. Same glow as filling a px×px
    square (bright centre, fading by the corners), but drawn on a canvas big enough for it to
    fade out completely in a circle, so overlapping flames never show square edges.
    The canvas is FIRE_DOT_GROW times bigger, so particles using it are scaled down to match."""
    size = int(round(px * FIRE_DOT_GROW))
    def draw(_):
        c = size / 2
        NSGradient.alloc().initWithColors_([
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 1.0),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.45),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
        ]).drawFromCenter_radius_toCenter_radius_options_((c, c), 0, (c, c), c, 0)
    return _particle_image(draw, size)


FIRE_DOT_GROW = 2 ** 0.5  # the old square glow reached its corners at √2 × half its width


def _round_dot(px=16):
    """A soft dot that fades out in a true circle (the fire's glow fades in a square, which
    its overlapping flames hide but single specks don't)."""
    def draw(px):
        c = px / 2
        NSGradient.alloc().initWithColors_([
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 1.0),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.4),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
        ]).drawFromCenter_radius_toCenter_radius_options_((c, c), 0, (c, c), c, 0)
    return _particle_image(draw, px)


EDGE = NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.32)  # a thin dark edge baked into pale
# particles, so they still show on white windows (and vanish into dark ones)


def _bubble_image(px=64):
    """A see-through soap bubble: thin bright ring, faint inside, a highlight top-left."""
    def draw(px):
        ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(4, 4, px - 8, px - 8))
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.14).setFill()
        ring.fill()
        ring.setLineWidth_(px * 0.06 + 2.5)
        EDGE.setStroke()
        ring.stroke()
        ring.setLineWidth_(px * 0.06)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95).setStroke()
        ring.stroke()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(px * 0.24, px * 0.58, px * 0.2, px * 0.14)).fill()
    return _particle_image(draw, px)


def _sparkle_image(px=64):
    """A four-pointed twinkle with a soft halo."""
    def draw(px):
        c = px / 2
        _glow(NSMakeRect(c - px * 0.22, c - px * 0.22, px * 0.44, px * 0.44), 0.8)
        star = NSBezierPath.bezierPath()
        tips = [(c, px - 1), (px - 1, c), (c, 1), (1, c)]
        pinch = px * 0.07  # how close the curved sides come to the middle
        star.moveToPoint_(tips[0])
        for a, b in zip(tips, tips[1:] + tips[:1]):
            mid = (c + (a[0] + b[0] - 2 * c) * 0.5 * pinch / c, c + (a[1] + b[1] - 2 * c) * 0.5 * pinch / c)
            star.curveToPoint_controlPoint1_controlPoint2_(b, mid, mid)
        star.closePath()
        star.setLineWidth_(2.0)
        EDGE.setStroke()
        star.stroke()
        NSColor.whiteColor().setFill()
        star.fill()
    return _particle_image(draw, px)


def _heart_image(px=64):
    def draw(px):
        s = px
        h = NSBezierPath.bezierPath()
        h.moveToPoint_((s * 0.5, s * 0.10))                                   # bottom point
        h.curveToPoint_controlPoint1_controlPoint2_((s * 0.04, s * 0.62), (s * 0.30, s * 0.30), (s * 0.04, s * 0.44))
        h.curveToPoint_controlPoint1_controlPoint2_((s * 0.5, s * 0.78), (s * 0.04, s * 0.94), (s * 0.42, s * 0.98))
        h.curveToPoint_controlPoint1_controlPoint2_((s * 0.96, s * 0.62), (s * 0.58, s * 0.98), (s * 0.96, s * 0.94))
        h.curveToPoint_controlPoint1_controlPoint2_((s * 0.5, s * 0.10), (s * 0.96, s * 0.44), (s * 0.70, s * 0.30))
        h.closePath()
        NSColor.whiteColor().setFill()
        h.fill()
    return _particle_image(draw, px)


def _cell(name, img, base, tip, alpha, birth, life, vel, scale, scale_speed, spread, alpha_speed):
    """One kind of particle. It's born with the `base` color and drifts to `tip` as it rises,
    like a flame that's yellow-white at the bottom and red at the tips."""
    c = CAEmitterCell.emitterCell()
    c.setName_(name)
    c.setContents_(img)
    c.setColor_(_cg(base, alpha))
    c.setRedSpeed_((tip[0] - base[0]) / life)
    c.setGreenSpeed_((tip[1] - base[1]) / life)
    c.setBlueSpeed_((tip[2] - base[2]) / life)
    c.setBirthRate_(birth)
    c.setLifetime_(life)
    c.setLifetimeRange_(life * 0.3)
    # A small random wobble in every direction, and hot air pulling everything up.
    # (Steering by emission angle sent flames sideways, so the rise comes from lift alone.)
    c.setVelocity_(spread)
    c.setVelocityRange_(spread * 0.6)
    c.setEmissionRange_(2 * math.pi)
    c.setYAcceleration_(vel)
    c.setScale_(scale)
    c.setScaleRange_(scale * 0.3)
    c.setScaleSpeed_(scale_speed)  # shrink while rising: tongues taper to a point
    c.setAlphaSpeed_(alpha_speed)
    c.setSpinRange_(1.2)
    return c


class _Stage:
    """The transparent window at the bottom-center of the screen that every pill style draws in.
    All styles share this size, position and interface, so they can be swapped live."""
    W, H = 84, 66          # transparent stage; flames, bubbles etc. rise inside it
    BOTTOM = 8             # px above the Dock / bottom of the usable screen

    def _make_stage(self):
        self.panel = make_panel(self.W, self.H)
        self.panel.setHasShadow_(False)
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.W, self.H))
        view.setWantsLayer_(True)
        self.panel.setContentView_(view)
        self.mode = None
        self.visible = False
        return view.layer()

    def _place(self):
        vf = screen_under_mouse().visibleFrame()
        self.panel.setFrameOrigin_((vf.origin.x + (vf.size.width - self.W) / 2, vf.origin.y))

    def _present(self, mode):
        if not self.visible:
            self._place()
            self.panel.orderFrontRegardless()
            self.visible = True
        elif mode != self.mode and mode not in ("idle", "none") and self.mode in ("idle", "none"):
            self._place()  # starting to talk: come to the screen you're working on

    def hide(self):
        if self.visible:
            self.panel.orderOut_(None)
            self.visible = False
            self.mode = None

    def anchor(self):
        """Where the pill sits on screen: (center x, bottom y) for the Copy button."""
        if not self.visible:
            self._place()
        f = self.panel.frame()
        return f.origin.x + f.size.width / 2, f.origin.y + self.BOTTOM


class Pill(_Stage):
    """Fire. Also the base for the other particle styles (bubbles, stardust, hearts), which
    swap in their own particles, colors and motion through the hooks below."""
    LOG = (40, 8)          # the dark "log" the fire burns on while you talk
    EMBER = (26, 6)        # the resting ember
    PALETTES = FIRE_PALETTES
    RIM = 1                              # which palette color tints the log's rim and glow
    EMBER_COLOR = (0.98, 0.42, 0.12)     # the resting ember...
    EMBER_GLOW = (1.0, 0.45, 0.1)        # ...and its glow
    LOG_COLOR = (0.07, 0.065, 0.07)
    ADDITIVE = True                      # overlapping particles add up to a brighter glow

    def __init__(self):
        root = self._make_stage()
        self._images()

        self.emitter = CAEmitterLayer.layer()
        self.emitter.setFrame_(((0, 0), (self.W, self.H)))
        self.emitter.setEmitterShape_(kCAEmitterLayerLine)
        self.emitter.setEmitterPosition_((self.W / 2, self.BOTTOM + self.LOG[1] - 2))
        self.emitter.setEmitterSize_((self.LOG[0] - 14, 1))
        self.emitter.setRenderMode_(kCAEmitterLayerAdditive if self.ADDITIVE
                                    else kCAEmitterLayerUnordered)
        self.emitter.setBirthRate_(0)
        root.addSublayer_(self.emitter)

        self.base = CALayer.layer()  # the log / ember (drawn over the flames' roots)
        root.addSublayer_(self.base)

        self.palette = None
        self._set_mode("idle")

    # ---- look (the hooks a theme overrides: _images, _cells, _drive and the class colors)
    def _images(self):
        self.dot = _soft_dot()

    def _cells(self, palette):
        base, tip, spark = palette
        hot = tuple(min(1.0, c * 0.35 + 0.72) for c in base)  # near-white heart of the flame
        g = 1  # same pixel scale as before: the glow keeps its size, it just fades out round
        return [
            #      name     img       from  to    alpha birth life  lift scale     shrink    wobble fade
            _cell("flame", self.dot, base, tip, 0.46, 200, 0.70, 95, 0.20 * g, -0.22 * g, 5, -0.62),
            _cell("core", self.dot, hot, base, 0.40, 110, 0.32, 70, 0.12 * g, -0.24 * g, 3, -1.2),
            _cell("spark", self.dot, spark, tip, 1.0, 0, 0.9, 120, 0.04 * g, -0.02 * g, 14, -0.9),
        ]

    def _set_palette(self, mode):
        key = "recording" if mode in ("idle", "none") else mode
        if key == self.palette:
            return
        self.palette = key
        self.emitter.setEmitterCells_(self._cells(self.PALETTES[key]))

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        self._set_palette(mode)
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.2)  # the ember grows into the log smoothly
        if mode == "none":
            self.base.setOpacity_(0.0)
        elif mode == "idle":
            w, h = self.EMBER
            self.base.setOpacity_(1.0)
            self.base.setFrame_(((self.W / 2 - w / 2, self.BOTTOM), (w, h)))
            self.base.setCornerRadius_(h / 2)
            self.base.setBackgroundColor_(_cg(self.EMBER_COLOR, 0.92))
            self.base.setBorderWidth_(0.0)
            self.base.setShadowColor_(_cg(self.EMBER_GLOW))
            self.base.setShadowOpacity_(0.55)
            self.base.setShadowRadius_(3.0)
            self.base.setShadowOffset_((0, 0))
        else:
            w, h = self.LOG
            body = self.PALETTES[mode][self.RIM]
            self.base.setOpacity_(1.0)
            self.base.setFrame_(((self.W / 2 - w / 2, self.BOTTOM), (w, h)))
            self.base.setCornerRadius_(h / 2)
            self.base.setBackgroundColor_(_cg(self.LOG_COLOR, 0.96))
            self.base.setBorderWidth_(0.6)
            self.base.setBorderColor_(_cg(body, 0.45))
            self.base.setShadowColor_(_cg(body))
            self.base.setShadowRadius_(4.0)
        CATransaction.commit()

    # ---- per-frame
    def show(self, mode, levels=None):
        self._present(mode)
        self._set_mode(mode)
        loud = 0.0
        if levels:
            recent = levels[-3:]
            loud = sum(recent) / len(recent)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)  # per-frame updates: no implicit animation
        if mode in ("idle", "none"):
            self.emitter.setBirthRate_(0)  # particles already in the air finish and fade out
        else:
            self._drive(mode, loud, time.time())
        CATransaction.commit()

    def _drive(self, mode, loud, t):
        """Per frame while active: how hard the fire burns for this loudness (0-1)."""
        if mode == "working":
            self.emitter.setBirthRate_(0.22 + 0.06 * math.sin(t * 6))
            self.emitter.setValue_forKeyPath_(45, "emitterCells.flame.yAcceleration")
            self.emitter.setValue_forKeyPath_(35, "emitterCells.core.yAcceleration")
            self.emitter.setLifetime_(0.7)
            self.emitter.setScale_(0.8)
            self.emitter.setValue_forKeyPath_(0, "emitterCells.spark.birthRate")
            self.base.setShadowOpacity_(0.35)
        else:
            self.emitter.setBirthRate_(0.35 + 1.3 * loud)
            # louder = stronger lift and longer life = taller flames
            self.emitter.setValue_forKeyPath_(60 + 110 * loud, "emitterCells.flame.yAcceleration")
            self.emitter.setValue_forKeyPath_(45 + 70 * loud, "emitterCells.core.yAcceleration")
            self.emitter.setLifetime_(0.75 + 0.6 * loud)
            self.emitter.setScale_(0.9 + 0.35 * loud)
            self.emitter.setValue_forKeyPath_(max(0.0, loud - 0.4) * 36,
                                              "emitterCells.spark.birthRate")
            self.base.setShadowOpacity_(0.15 + 0.35 * loud)

    def hide(self):
        if self.visible:
            self.emitter.setBirthRate_(0)
        super().hide()


class BubblesPill(Pill):
    """Bubbles: a little pool that fizzes while you talk; louder = more, bigger bubbles."""
    #              bubbles             fizz                pool rim
    PALETTES = {
        "recording": ((0.40, 0.82, 1.00), (0.80, 0.95, 1.00), (0.25, 0.70, 1.00)),  # aqua
        "latched": ((0.36, 0.95, 0.70), (0.80, 1.00, 0.90), (0.20, 0.85, 0.60)),    # mint: hands-free
        "command": ((0.82, 0.55, 1.00), (0.95, 0.85, 1.00), (0.70, 0.40, 1.00)),    # violet: command
        "working": ((0.42, 0.78, 1.00), (0.80, 0.93, 1.00), (0.30, 0.62, 0.95)),
    }
    RIM = 2
    EMBER_COLOR, EMBER_GLOW = (0.32, 0.78, 1.00), (0.25, 0.70, 1.00)
    LOG_COLOR = (0.03, 0.07, 0.11)
    ADDITIVE = False

    def _images(self):
        self.dot, self.bubble = _round_dot(), _bubble_image(32)

    def _cells(self, palette):
        bub, fizz, _ = palette
        #              name      img          from  to    alpha birth life lift scale grow wobble fade
        b = _cell("bubble", self.bubble, bub, fizz, 0.95, 7, 1.5, 30, 0.26, 0.09, 9, -0.5)
        b.setSpinRange_(0)
        f = _cell("fizz", self.dot, fizz, bub, 0.85, 26, 0.9, 45, 0.16, -0.04, 6, -0.9)
        return [b, f]

    def _drive(self, mode, loud, t):
        if mode == "working":  # a lazy simmer
            self.emitter.setBirthRate_(0.25 + 0.1 * math.sin(t * 5))
            self.emitter.setScale_(0.8)
            self.emitter.setLifetime_(0.9)
            self.base.setShadowOpacity_(0.3)
        else:
            self.emitter.setBirthRate_(0.3 + 1.6 * loud)
            self.emitter.setScale_(0.8 + 0.5 * loud)
            self.emitter.setLifetime_(0.85 + 0.4 * loud)
            self.emitter.setValue_forKeyPath_(22 + 40 * loud, "emitterCells.bubble.yAcceleration")
            self.base.setShadowOpacity_(0.15 + 0.35 * loud)


class StardustPill(Pill):
    """Stardust: twinkles pop out of the night sky above a deep-blue bar as you speak."""
    #              twinkles            dust                rim
    PALETTES = {
        "recording": ((1.00, 0.80, 0.30), (0.62, 0.46, 1.00), (0.58, 0.44, 1.00)),  # gold & violet
        "latched": ((0.55, 0.85, 1.00), (0.30, 0.58, 1.00), (0.34, 0.62, 1.00)),    # ice: hands-free
        "command": ((1.00, 0.60, 0.85), (0.95, 0.35, 0.72), (0.95, 0.38, 0.72)),    # pink: command
        "working": ((1.00, 0.82, 0.40), (0.56, 0.44, 1.00), (0.52, 0.42, 0.95)),
    }
    RIM = 2
    EMBER_COLOR, EMBER_GLOW = (0.62, 0.50, 1.00), (0.55, 0.42, 1.00)
    LOG_COLOR = (0.04, 0.04, 0.12)

    def __init__(self):
        super().__init__()
        self.emitter.setEmitterShape_(kCAEmitterLayerRectangle)  # anywhere in the sky, not a line
        self._sky(0.0)

    def _sky(self, loud):
        h = 8 + 30 * loud  # louder: the stars reach higher
        self.emitter.setEmitterSize_((self.LOG[0] + 14, h))
        self.emitter.setEmitterPosition_((self.W / 2, self.BOTTOM + self.LOG[1] + 2 + h / 2))

    def _images(self):
        self.dot, self.star = _round_dot(), _sparkle_image(32)

    def _cells(self, palette):
        star, dust, _ = palette
        hot = tuple(min(1.0, c * 0.4 + 0.65) for c in star)  # born bright, settles into its color
        s = _cell("star", self.star, hot, star, 1.0, 12, 0.8, 5, 0.30, -0.34, 3, -1.2)
        s.setSpinRange_(2.0)
        d = _cell("dust", self.dot, dust, star, 0.6, 30, 0.9, 18, 0.26, -0.2, 4, -0.8)
        return [s, d]

    def _drive(self, mode, loud, t):
        if mode == "working":  # a few slow twinkles
            self._sky(0.15)
            self.emitter.setBirthRate_(0.3 + 0.15 * math.sin(t * 4))
            self.emitter.setScale_(0.75)
            self.base.setShadowOpacity_(0.35)
        else:
            self._sky(loud)
            self.emitter.setBirthRate_(0.35 + 1.4 * loud)
            self.emitter.setScale_(0.85 + 0.4 * loud)
            self.base.setShadowOpacity_(0.2 + 0.4 * loud)


class HeartsPill(Pill):
    """Hearts: little hearts float up while you talk."""
    #              hearts              glints              rim
    PALETTES = {
        "recording": ((1.00, 0.36, 0.56), (1.00, 0.78, 0.86), (1.00, 0.34, 0.55)),  # pink
        "latched": ((0.36, 0.62, 1.00), (0.80, 0.90, 1.00), (0.36, 0.60, 1.00)),    # blue: hands-free
        "command": ((0.68, 0.40, 1.00), (0.90, 0.80, 1.00), (0.66, 0.40, 1.00)),    # purple: command
        "working": ((1.00, 0.45, 0.62), (1.00, 0.80, 0.88), (0.95, 0.40, 0.58)),
    }
    RIM = 2
    EMBER_COLOR, EMBER_GLOW = (1.00, 0.42, 0.62), (1.00, 0.35, 0.55)
    LOG_COLOR = (0.10, 0.04, 0.07)
    ADDITIVE = False

    def _images(self):
        self.dot, self.heart = _round_dot(), _heart_image()

    def _cells(self, palette):
        heart, glint, _ = palette
        h = _cell("heart", self.heart, heart, heart, 1.0, 5, 1.4, 24, 0.16, 0.03, 9, -0.6)
        h.setSpinRange_(0.8)
        g = _cell("glint", self.dot, glint, heart, 0.8, 10, 0.8, 35, 0.14, -0.08, 8, -1.0)
        return [h, g]

    def _drive(self, mode, loud, t):
        if mode == "working":  # a heartbeat: lub-dub, pause
            phase = (t * 1.1) % 1.0
            beat = max(math.exp(-((phase - 0.1) / 0.05) ** 2), 0.7 * math.exp(-((phase - 0.3) / 0.05) ** 2))
            self.emitter.setBirthRate_(0.08)
            self.emitter.setScale_(0.8)
            self.base.setShadowOpacity_(0.15 + 0.75 * beat)
        else:
            self.emitter.setBirthRate_(0.25 + 1.5 * loud)
            self.emitter.setScale_(0.85 + 0.45 * loud)
            self.emitter.setLifetime_(0.9 + 0.4 * loud)
            self.emitter.setValue_forKeyPath_(18 + 30 * loud, "emitterCells.heart.yAcceleration")
            self.base.setShadowOpacity_(0.15 + 0.4 * loud)


MINIMAL_BAR_COLORS = {
    "recording": (1.00, 1.00, 1.00),  # white: listening while you hold the key
    "latched": (1.00, 0.74, 0.32),    # amber: hands-free
    "command": (0.76, 0.64, 1.00),    # lavender: voice command
}


class MinimalPill(_Stage):
    """The quiet style: a small dark capsule with a white waveform that follows your voice."""
    ACTIVE = (60, 22)
    IDLE = (30, 6)
    BARS = 11

    def __init__(self):
        root = self._make_stage()

        self.base = CALayer.layer()
        self.base.setShadowColor_(_cg((0, 0, 0)))
        self.base.setShadowOffset_((0, -1))
        self.base.setShadowRadius_(3.0)
        root.addSublayer_(self.base)
        self.bars = []
        for _ in range(self.BARS):
            bar = CALayer.layer()
            bar.setCornerRadius_(1.0)
            root.addSublayer_(bar)
            self.bars.append(bar)
        self.dots = []
        for _ in range(3):
            dot = CALayer.layer()
            dot.setCornerRadius_(1.7)
            dot.setBackgroundColor_(_cg((1, 1, 1)))
            root.addSublayer_(dot)
            self.dots.append(dot)
        self.heights = [0.0] * self.BARS
        self._set_mode("idle")

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        active = mode not in ("idle", "none")
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.18)  # the resting bar grows into the capsule
        w, h = self.ACTIVE if active else self.IDLE
        self.base.setFrame_(((self.W / 2 - w / 2, self.BOTTOM), (w, h)))
        self.base.setCornerRadius_(h / 2)
        self.base.setOpacity_(0.0 if mode == "none" else 1.0)
        if active:
            self.base.setBackgroundColor_(_cg((0.05, 0.05, 0.06), 0.9))
            self.base.setBorderColor_(_cg((1, 1, 1), 0.14))
            self.base.setShadowOpacity_(0.35)
        else:
            self.base.setBackgroundColor_(_cg((0.12, 0.12, 0.13), 0.55))
            self.base.setBorderColor_(_cg((1, 1, 1), 0.28))
            self.base.setShadowOpacity_(0.0)
        self.base.setBorderWidth_(0.8)
        show_bars = mode in MINIMAL_BAR_COLORS
        for bar in self.bars:
            bar.setHidden_(not show_bars)
            if show_bars:
                bar.setBackgroundColor_(_cg(MINIMAL_BAR_COLORS[mode], 0.95))
        for dot in self.dots:
            dot.setHidden_(mode != "working")
        CATransaction.commit()

    def show(self, mode, levels=None):
        self._present(mode)
        self._set_mode(mode)
        t = time.time()
        cy = self.BOTTOM + self.ACTIVE[1] / 2
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if mode in MINIMAL_BAR_COLORS:
            levels = list(levels or [0.0])
            recent = levels[-self.BARS:]
            if len(recent) < self.BARS:  # preview passes one loudness: spread it as a wave
                lv = recent[-1]
                recent = [lv * (0.55 + 0.45 * math.sin(t * 7 + i * 0.9)) for i in range(self.BARS)]
            bw, gap = 2.0, 2.4
            x0 = self.W / 2 - (self.BARS * bw + (self.BARS - 1) * gap) / 2
            for i, (bar, lv) in enumerate(zip(self.bars, recent)):
                # rise quickly, settle gently, so the waveform feels alive but not jittery
                self.heights[i] = lv if lv > self.heights[i] else self.heights[i] * 0.7 + lv * 0.3
                bh = max(2.0, self.heights[i] * (self.ACTIVE[1] - 9))
                bar.setFrame_(((x0 + i * (bw + gap), cy - bh / 2), (bw, bh)))
        elif mode == "working":
            for i, dot in enumerate(self.dots):
                dot.setFrame_(((self.W / 2 + (i - 1) * 6.5 - 1.7, cy - 1.7), (3.4, 3.4)))
                dot.setOpacity_(0.3 + 0.7 * (0.5 + 0.5 * math.sin(t * 7 - i * 0.9)))
        CATransaction.commit()


def _hsb(hue, sat=0.72, bright=1.0, alpha=1.0):
    return NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(hue % 1.0, sat, bright,
                                                                      alpha).CGColor()


class RainbowPill(MinimalPill):
    """Rainbow: the minimal waveform with every bar a different color, drifting along.
    At rest it's a tiny rainbow stripe. The rim says the mode: white, amber hands-free,
    lavender command."""
    RIMS = {"recording": (1.0, 1.0, 1.0), "latched": (1.0, 0.74, 0.32), "command": (0.76, 0.64, 1.0)}

    def __init__(self):
        super().__init__()
        self.stripe = CAGradientLayer.layer()
        self.stripe.setColors_([_hsb(i / 6, 0.7, 1.0, 0.95) for i in range(7)])
        self.stripe.setStartPoint_((0, 0.5))
        self.stripe.setEndPoint_((1, 0.5))
        w, h = self.IDLE
        self.stripe.setFrame_(((self.W / 2 - w / 2, self.BOTTOM), (w, h)))
        self.stripe.setCornerRadius_(h / 2)
        self.panel.contentView().layer().addSublayer_(self.stripe)
        self.mode = None
        self._set_mode("idle")

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        super()._set_mode(mode)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        stripe = getattr(self, "stripe", None)
        if stripe is not None:
            stripe.setHidden_(mode != "idle")
        if mode in self.RIMS:
            self.base.setBorderColor_(_cg(self.RIMS[mode], 0.4))
            self.base.removeAnimationForKey_("borderColor")
        for layer in self.bars + self.dots:
            layer.removeAnimationForKey_("backgroundColor")  # no white flash before the colors
        CATransaction.commit()

    def show(self, mode, levels=None):
        super().show(mode, levels)
        items = self.bars if mode in MINIMAL_BAR_COLORS else self.dots if mode == "working" else ()
        if items:
            t = time.time()
            CATransaction.begin()
            CATransaction.setDisableActions_(True)
            for i, layer in enumerate(items):
                layer.setBackgroundColor_(_hsb(i / len(items) * 0.85 + t * 0.12))
            CATransaction.commit()


# LED colors from the bottom row up: (lower rows, second-from-top, top)
EQ_PALETTES = {
    "recording": ((0.30, 0.95, 0.45), (1.00, 0.84, 0.25), (1.00, 0.32, 0.26)),  # green-yellow-red
    "latched": ((0.25, 0.75, 1.00), (0.50, 0.92, 1.00), (0.92, 0.98, 1.00)),    # blue: hands-free
    "command": ((0.70, 0.42, 1.00), (0.95, 0.48, 0.90), (1.00, 0.80, 0.95)),    # purple: command
    "working": ((0.30, 0.95, 0.45), (1.00, 0.84, 0.25), (1.00, 0.32, 0.26)),
}


class EqualizerPill(_Stage):
    """Retro: a tiny stereo LED meter. The newest sound lights the middle and spreads outward;
    peaks hang for a moment, then drop. At rest: a dark bar with a green power light."""
    COLS, ROWS = 9, 6
    SEG_W, SEG_H, GAP_X, GAP_Y = 4.0, 2.0, 1.4, 1.0
    PAD_X, PAD_Y = 6.0, 3.5
    IDLE = (30, 6)
    DIM = 0.1  # unlit LEDs stay faintly visible, like a real meter
    NOISE = 0.07     # loudness just above the room's background that still counts as silence
    MIN_SPAN = 0.26  # never stretch less than this, or room noise would fill the meter

    def __init__(self):
        root = self._make_stage()
        mw = self.COLS * self.SEG_W + (self.COLS - 1) * self.GAP_X
        mh = self.ROWS * self.SEG_H + (self.ROWS - 1) * self.GAP_Y
        self.ACTIVE = (mw + 2 * self.PAD_X, mh + 2 * self.PAD_Y)
        self.base = CALayer.layer()
        self.base.setShadowColor_(_cg((0, 0, 0)))
        self.base.setShadowOffset_((0, -1))
        self.base.setShadowRadius_(3.0)
        root.addSublayer_(self.base)
        x0, y0 = self.W / 2 - mw / 2, self.BOTTOM + self.PAD_Y
        self.segs = []
        for c in range(self.COLS):
            col = []
            for r in range(self.ROWS):
                seg = CALayer.layer()
                seg.setCornerRadius_(0.6)
                seg.setFrame_(((x0 + c * (self.SEG_W + self.GAP_X), y0 + r * (self.SEG_H + self.GAP_Y)),
                               (self.SEG_W, self.SEG_H)))
                root.addSublayer_(seg)
                col.append(seg)
            self.segs.append(col)
        self.led = CALayer.layer()  # the power light at rest
        w, h = self.IDLE
        self.led.setFrame_(((self.W / 2 + w / 2 - 6, self.BOTTOM + h / 2 - 1.5), (3, 3)))
        self.led.setCornerRadius_(1.5)
        self.led.setBackgroundColor_(_cg((0.35, 1.0, 0.5)))
        self.led.setShadowColor_(_cg((0.3, 1.0, 0.45)))
        self.led.setShadowOffset_((0, 0))
        self.led.setShadowRadius_(2.0)
        self.led.setShadowOpacity_(0.9)
        root.addSublayer_(self.led)
        self.heights = [0.0] * self.COLS
        self.peaks = [0.0] * self.COLS
        self.hold = [0.0] * self.COLS
        self.alpha = [[None] * self.ROWS for _ in range(self.COLS)]
        self.last_t = self.mode_t = time.time()
        self.floor, self.loud = 1.0, 0.3  # auto-level: room noise and your recent loudest
        self._set_mode("idle")

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        if mode in ("recording", "latched", "command") and self.mode not in ("recording", "latched", "command"):
            self.floor, self.loud = 1.0, 0.3  # new dictation: learn this room and voice afresh
        self.mode, self.mode_t = mode, time.time()
        active = mode not in ("idle", "none")
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.18)  # the resting bar grows into the meter
        w, h = self.ACTIVE if active else self.IDLE
        self.base.setFrame_(((self.W / 2 - w / 2, self.BOTTOM), (w, h)))
        self.base.setCornerRadius_(4.0 if active else h / 2)
        self.base.setOpacity_(0.0 if mode == "none" else 1.0)
        self.base.setBackgroundColor_(_cg((0.04, 0.045, 0.04), 0.94 if active else 0.78))
        self.base.setBorderWidth_(0.8)
        self.base.setBorderColor_(_cg((1, 1, 1), 0.12 if active else 0.24))
        self.base.setShadowOpacity_(0.35 if active else 0.0)
        CATransaction.commit()
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        palette = EQ_PALETTES.get(mode)
        for c in range(self.COLS):
            for r in range(self.ROWS):
                seg = self.segs[c][r]
                seg.setHidden_(not active)
                seg.setOpacity_(0.0)  # fades in once the bar has grown
                self.alpha[c][r] = 0.0
                if palette:
                    low, mid, top = palette
                    color = top if r == self.ROWS - 1 else mid if r == self.ROWS - 2 else low
                    seg.setBackgroundColor_(_cg(color))
        self.led.setHidden_(mode != "idle")
        CATransaction.commit()

    def _light(self, c, r, a):
        if self.alpha[c][r] != a:  # only touch LEDs that change
            self.alpha[c][r] = a
            self.segs[c][r].setOpacity_(a)

    def show(self, mode, levels=None):
        self._present(mode)
        self._set_mode(mode)
        t = time.time()
        dt, self.last_t = min(0.1, t - self.last_t), t
        if mode in ("idle", "none") or t - self.mode_t < 0.12:
            return
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if mode == "working":  # a light sweeping back and forth along the bottom
            pos = abs(((t * 1.4) % 2.0) - 1.0) * (self.COLS - 1)
            for c in range(self.COLS):
                glow = max(0.0, 1.0 - abs(c - pos) / 1.6)
                for r in range(self.ROWS):
                    self._light(c, r, round(self.DIM + (0.9 * glow if r < 2 else 0), 2))
        else:
            half = self.COLS // 2
            recent = list(levels or [0.0])[-(half + 1):]
            if len(recent) < half + 1:  # the preview passes one loudness: make it dance
                lv = recent[-1]
                recent = [lv * (0.6 + 0.4 * math.sin(t * 8 + i * 1.3)) for i in range(half + 1)]
            # Auto-level, so a quiet voice moves the meter as much as a loud one: the room's
            # noise floor (drops at once, creeps up slowly) and your recent loudest (jumps up
            # at once, relaxes over ~2 s) set the bottom and top of the meter.
            now = recent[-1]
            self.floor = now if now < self.floor else self.floor + (now - self.floor) * 0.01
            self.loud = max(now, self.loud * 0.985, self.floor + self.MIN_SPAN)
            quiet = self.floor + self.NOISE  # room noise flickers a little: that's not talking
            span = self.loud - quiet
            for c in range(self.COLS):
                # newest sound in the middle; each column jumps on its own, like frequency bands
                band = (0.78 + 0.22 * math.sin(t * (7.0 + 2.1 * c) + c * 2.1)
                        + 0.12 * math.sin(t * (13.7 + 3.3 * c) + c))
                level = max(0.0, (recent[-1 - abs(c - half)] - quiet) / span) ** 0.75
                lv = min(1.0, level * band)
                self.heights[c] = lv if lv > self.heights[c] else self.heights[c] * 0.72 + lv * 0.28
                n = max(1, round(self.heights[c] * self.ROWS))
                if n >= self.peaks[c]:
                    self.peaks[c], self.hold[c] = n, t + 0.35
                elif t > self.hold[c]:
                    self.peaks[c] = max(n, self.peaks[c] - dt * 9)
                peak = math.ceil(self.peaks[c]) - 1
                for r in range(self.ROWS):
                    self._light(c, r, 1.0 if r < n else 0.85 if r == peak else self.DIM)
        CATransaction.commit()


# Each style's colour, used for the Copy button's outline so it matches the pill.
STYLE_ACCENTS = {"fire": (1.0, 0.52, 0.18), "bubbles": (0.35, 0.78, 1.0), "stardust": (0.62, 0.48, 1.0),
                 "hearts": (1.0, 0.40, 0.60), "minimal": (1.0, 1.0, 1.0), "rainbow": "rainbow",
                 "equalizer": (0.30, 0.95, 0.45)}

PILL_STYLES = {"fire": Pill, "minimal": MinimalPill, "bubbles": BubblesPill,
               "stardust": StardustPill, "hearts": HeartsPill, "rainbow": RainbowPill,
               "equalizer": EqualizerPill}
