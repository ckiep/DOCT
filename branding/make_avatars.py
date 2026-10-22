"""Рисует аватары канала и бота (640×640 PNG) через headless Chromium."""
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
OUT = Path(__file__).resolve().parent

PLANE = (
    "M 0 -150 C 14 -150 20 -130 20 -110 L 20 -30 L 140 30 L 140 55 L 20 20 L 18 95 "
    "L 65 125 L 65 145 L 0 128 L -65 145 L -65 125 L -18 95 L -20 20 L -140 55 "
    "L -140 30 L -20 -30 L -20 -110 C -20 -130 -14 -150 0 -150 Z"
)

BADGE_PERCENT = '<text x="0" y="22" text-anchor="middle" font-family="Inter" font-weight="800" font-size="64" fill="#fff">%</text>'
BADGE_BELL = (
    '<path d="M 0 -27 C 15 -27 21 -14 21 0 L 21 11 L 30 21 L -30 21 L -21 11 L -21 0 C -21 -14 -15 -27 0 -27 Z" fill="#fff"/>'
    '<circle cx="0" cy="29" r="7" fill="#fff"/><circle cx="0" cy="-31" r="5" fill="#fff"/>'
)


def rays() -> str:
    parts = []
    for i in range(16):
        a = math.radians(i * 360 / 16)
        w = math.radians(3.2)
        r1, r2 = 174, 206
        pts = [
            (320 + r1 * math.cos(a - w), 300 + r1 * math.sin(a - w)),
            (320 + r2 * math.cos(a), 300 + r2 * math.sin(a)),
            (320 + r1 * math.cos(a + w), 300 + r1 * math.sin(a + w)),
        ]
        parts.append('<polygon points="%s" fill="#FFD84D"/>' % " ".join(f"{x:.1f},{y:.1f}" for x, y in pts))
    return "".join(parts)


def svg(badge: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640" viewBox="0 0 640 640">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#22D3EE"/>
      <stop offset="0.55" stop-color="#00A6D6"/>
      <stop offset="1" stop-color="#0069A8"/>
    </linearGradient>
    <radialGradient id="sun" cx="0.4" cy="0.35" r="0.75">
      <stop offset="0" stop-color="#FFE57A"/>
      <stop offset="1" stop-color="#FDB813"/>
    </radialGradient>
    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
      <feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#003B5C" flood-opacity="0.35"/>
    </filter>
  </defs>
  <rect width="640" height="640" fill="url(#sky)"/>
  {rays()}
  <circle cx="320" cy="300" r="160" fill="url(#sun)"/>
  <path d="M 70 560 C 170 520 200 430 250 390" fill="none" stroke="#fff" stroke-width="9"
        stroke-linecap="round" stroke-dasharray="2 22" opacity="0.8"/>
  <g transform="translate(325 300) rotate(45) scale(1.12)" filter="url(#shadow)">
    <path d="{PLANE}" fill="#fff"/>
    <rect x="54" y="-26" width="20" height="40" rx="9" fill="#fff"/>
    <rect x="-74" y="-26" width="20" height="40" rx="9" fill="#fff"/>
  </g>
  <g transform="translate(462 458)" filter="url(#shadow)">
    <circle r="66" fill="#EF4444"/>
    <circle r="66" fill="none" stroke="#fff" stroke-width="8"/>
    {badge}
  </g>
</svg>"""


def render(name: str, badge: str) -> Path:
    svg_path = OUT / f"{name}.svg"
    svg_path.write_text(svg(badge), encoding="utf-8")
    html = OUT / f".{name}.html"
    html.write_text(f'<html><body style="margin:0">{svg(badge)}</body></html>', encoding="utf-8")
    png = OUT / f"{name}.png"
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--window-size=640,900", f"--screenshot={png}", html.as_uri()],
        check=True, capture_output=True,
    )
    html.unlink()
    Image.open(png).crop((0, 0, 640, 640)).save(png)
    return png


if __name__ == "__main__":
    for path in (render("avatar-channel", BADGE_PERCENT), render("avatar-bot", BADGE_BELL)):
        print(path)
