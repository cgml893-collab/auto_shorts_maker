# -*- coding: utf-8 -*-
"""ClipSpark AI 1024 아이콘과 Android mipmap ic_launcher를 생성한다."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ICON_1024 = ROOT / "assets" / "branding" / "app_icon.png"
ANDROID_RES = ROOT / "android" / "app" / "src" / "main" / "res"

SIZE = 1024
MIPMAPS = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}
IOS_ICONSET = ROOT / "ios" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"
IOS_SIZES = {
    "Icon-App-20x20@1x.png": 20,
    "Icon-App-20x20@2x.png": 40,
    "Icon-App-20x20@3x.png": 60,
    "Icon-App-29x29@1x.png": 29,
    "Icon-App-29x29@2x.png": 58,
    "Icon-App-29x29@3x.png": 87,
    "Icon-App-40x40@1x.png": 40,
    "Icon-App-40x40@2x.png": 80,
    "Icon-App-40x40@3x.png": 120,
    "Icon-App-60x60@2x.png": 120,
    "Icon-App-60x60@3x.png": 180,
    "Icon-App-76x76@1x.png": 76,
    "Icon-App-76x76@2x.png": 152,
    "Icon-App-83.5x83.5@2x.png": 167,
    "Icon-App-1024x1024@1x.png": 1024,
}


def _lerp(a, b, t):
    return int(round(a + (b - a) * t))


def _mix(c0, c1, t):
    t = max(0.0, min(1.0, t))
    return tuple(_lerp(c0[i], c1[i], t) for i in range(3))


def make_background(size):
    img = Image.new("RGB", (size, size))
    px = img.load()
    top = (14, 6, 36)
    mid = (92, 22, 148)
    hot = (236, 68, 158)
    cx = size * 0.52
    cy = size * 0.42
    for y in range(size):
        gy = y / float(size - 1)
        row = _mix(top, mid, gy ** 0.85)
        row = _mix(row, hot, max(0.0, (gy - 0.38) * 1.35))
        for x in range(size):
            dx = (x - cx) / size
            dy = (y - cy) / size
            radial = math.sqrt(dx * dx + dy * dy)
            glow = max(0.0, 1.0 - radial * 1.85)
            color = _mix(row, (255, 150, 210), glow * 0.42)
            vignette = 1.0 - 0.22 * ((x / size - 0.5) ** 2 + (y / size - 0.5) ** 2) * 4
            color = tuple(max(0, min(255, int(c * vignette))) for c in color)
            px[x, y] = color
    return img.convert("RGBA")


def _star(draw, cx, cy, outer, inner, points, fill):
    pts = []
    for i in range(points * 2):
        ang = -math.pi / 2 + i * math.pi / points
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def make_symbol_layer(size):
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    d = ImageDraw.Draw(layer)

    cx, cy = size * 0.48, size * 0.52
    reel_r = size * 0.236

    g.ellipse(
        (cx - reel_r * 1.55, cy - reel_r * 1.55, cx + reel_r * 1.55, cy + reel_r * 1.55),
        fill=(255, 90, 180, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.055))

    # Film strip behind the reel
    strip_w = size * 0.13
    left = cx + reel_r * 0.35
    top = cy - reel_r * 1.18
    d.rounded_rectangle(
        (left, top, left + strip_w * 2.15, top + reel_r * 2.36),
        radius=size * 0.035,
        fill=(255, 230, 245, 235),
    )
    d.rounded_rectangle(
        (left + size * 0.018, top + size * 0.018, left + strip_w * 2.15 - size * 0.018, top + reel_r * 2.36 - size * 0.018),
        radius=size * 0.028,
        fill=(48, 12, 78, 235),
    )
    hole_w = strip_w * 0.22
    for i in range(6):
        y0 = top + size * 0.07 + i * (reel_r * 0.36)
        d.rounded_rectangle(
            (left + size * 0.038, y0, left + size * 0.038 + hole_w, y0 + hole_w * 1.15),
            radius=hole_w * 0.25,
            fill=(255, 170, 210, 220),
        )
        d.rounded_rectangle(
            (
                left + strip_w * 2.15 - size * 0.038 - hole_w,
                y0,
                left + strip_w * 2.15 - size * 0.038,
                y0 + hole_w * 1.15,
            ),
            radius=hole_w * 0.25,
            fill=(255, 170, 210, 220),
        )

    # Reel body
    d.ellipse(
        (cx - reel_r, cy - reel_r, cx + reel_r, cy + reel_r),
        fill=(255, 246, 252, 255),
    )
    inner = reel_r * 0.86
    d.ellipse(
        (cx - inner, cy - inner, cx + inner, cy + inner),
        fill=(72, 18, 120, 255),
    )
    ring = reel_r * 0.62
    d.ellipse(
        (cx - ring, cy - ring, cx + ring, cy + ring),
        outline=(255, 150, 210, 255),
        width=max(6, int(size * 0.014)),
    )
    hub = reel_r * 0.34
    d.ellipse(
        (cx - hub, cy - hub, cx + hub, cy + hub),
        fill=(18, 8, 40, 255),
    )
    d.ellipse(
        (cx - hub * 0.55, cy - hub * 0.55, cx + hub * 0.55, cy + hub * 0.55),
        fill=(255, 90, 170, 255),
    )

    sprocket_r = reel_r * 0.72
    hole_r = max(8, size * 0.022)
    for i in range(8):
        ang = i * (math.pi / 4)
        hx = cx + sprocket_r * math.cos(ang)
        hy = cy + sprocket_r * math.sin(ang)
        d.ellipse((hx - hole_r, hy - hole_r, hx + hole_r, hy + hole_r), fill=(255, 210, 235, 255))

    # Play triangle
    play_s = hub * 0.85
    d.polygon(
        [
            (cx - play_s * 0.28, cy - play_s * 0.72),
            (cx - play_s * 0.28, cy + play_s * 0.72),
            (cx + play_s * 0.92, cy),
        ],
        fill=(255, 255, 255, 255),
    )

    # Sparks
    sx, sy = cx + reel_r * 0.92, cy - reel_r * 0.95
    _star(d, sx, sy, size * 0.11, size * 0.038, 4, (255, 252, 255, 255))
    _star(d, sx + size * 0.09, sy + size * 0.08, size * 0.042, size * 0.016, 4, (255, 210, 120, 250))
    _star(d, sx - size * 0.08, sy - size * 0.07, size * 0.03, size * 0.011, 4, (255, 255, 255, 240))
    return glow, layer


def build_icon():
    base = make_background(SIZE)
    glow, symbol = make_symbol_layer(SIZE)
    out = Image.alpha_composite(base, glow)
    out = Image.alpha_composite(out, symbol)
    return out.convert("RGB")


def write_mipmaps(master):
    resample = getattr(Image, "Resampling", Image).LANCZOS
    for folder, px in MIPMAPS.items():
        dest_dir = ANDROID_RES / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        scaled = master.resize((px, px), resample)
        scaled.save(dest_dir / "ic_launcher.png", "PNG", optimize=True)
        scaled.save(dest_dir / "ic_launcher_round.png", "PNG", optimize=True)


def write_ios_icons(master):
    resample = getattr(Image, "Resampling", Image).LANCZOS
    IOS_ICONSET.mkdir(parents=True, exist_ok=True)
    for name, px in IOS_SIZES.items():
        master.resize((px, px), resample).save(IOS_ICONSET / name, "PNG", optimize=True)


def main():
    ICON_1024.parent.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(ICON_1024, "PNG", optimize=True)
    write_mipmaps(icon)
    write_ios_icons(icon)
    print("saved", ICON_1024)
    for folder, px in MIPMAPS.items():
        print("saved {} ({}px)".format(folder, px))
    print("saved iOS AppIcon.appiconset")


if __name__ == "__main__":
    main()
