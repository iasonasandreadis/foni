"""Draws Foni's app icon (a white waveform on a dark rounded square) as an .icns file.

Usage: python make_icon.py OUTPUT.icns
"""
import os
import subprocess
import sys
import tempfile

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSMakeRect,
)

BARS = [0.24, 0.46, 0.74, 0.96, 0.60, 0.82, 0.44, 0.26]


def render(px, path):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, px, px, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    s = px / 1024
    # macOS icon grid: an 824 pt rounded square centered in a 1024 pt canvas
    body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(100 * s, 100 * s, 824 * s, 824 * s), 185 * s, 185 * s)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.24, 0.31, 1.0),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.06, 0.06, 0.09, 1.0),
    ).drawInBezierPath_angle_(body, -90)
    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).setStroke()
    body.setLineWidth_(max(1.0, 4 * s))
    body.stroke()

    bw, gap, max_h = 54 * s, 36 * s, 500 * s
    total = len(BARS) * bw + (len(BARS) - 1) * gap
    x0 = 512 * s - total / 2
    NSColor.whiteColor().setFill()
    for i, h in enumerate(BARS):
        bh = max(bw, h * max_h)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x0 + i * (bw + gap), 512 * s - bh / 2, bw, bh), bw / 2, bw / 2).fill()
    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None) \
        .writeToFile_atomically_(path, True)


def main(out):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "Foni.iconset")
        os.mkdir(iconset)
        for pt in (16, 32, 128, 256, 512):
            render(pt, os.path.join(iconset, f"icon_{pt}x{pt}.png"))
            render(pt * 2, os.path.join(iconset, f"icon_{pt}x{pt}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)


if __name__ == "__main__":
    main(sys.argv[1])
