"""Минималистичный логотип TravelEasy: три варианта знака, надписи и превью.

Две цветовые схемы: classic (тёмно-синий, голубой, золото) — файлы в этой папке,
white (белый фон, мягкий голубой, светлое золото) — в папке white/.
Знаки — чистый вектор (SVG без текста). PNG рендерятся headless-Chromium.
"""
import subprocess
from pathlib import Path

from PIL import Image

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
OUT = Path(__file__).resolve().parent

NAVY = "#0A3A57"
SKY = "#00A3D9"
GOLD = "#FFC20E"
WHITE = "#FFFFFF"

# Светлая схема
SOFT_SKY = "#2BA8E0"
SOFT_GOLD = "#FFC845"
PALE_GOLD = "#FFE39A"
INK = "#27465B"

# Самолёт носом вверх, центр в (0, 0), высота 200.
PLANE = (
    "M 0 -100 C 9 -100 14 -88 14 -74 L 14 -20 L 92 22 L 92 40 L 14 18 L 13 62 L 40 80 "
    "L 40 94 L 0 84 L -40 94 L -40 80 L -13 62 L -14 18 L -92 40 L -92 22 L -14 -20 "
    "L -14 -74 C -14 -88 -9 -100 0 -100 Z"
)


def plane(x: float, y: float, angle: float, scale: float, color: str) -> str:
    return (
        f'<path d="{PLANE}" fill="{color}" stroke="{color}" stroke-width="6" stroke-linejoin="round" '
        f'transform="translate({x} {y}) rotate({angle}) scale({scale})"/>'
    )


# Цвета знака на разных фонах: main — основной цвет, accent — золотой акцент,
# sun — круг солнца, inner — цвет внутри фигуры, on_accent — самолёт поверх солнца.
# alt — второй вариант логотипа: тёмный в classic, инверсный (на голубом) в white.
SCHEMES = {
    "classic": {
        "dir": OUT,
        "light": {"main": NAVY, "accent": GOLD, "sun": GOLD, "inner": WHITE, "on_accent": NAVY},
        "avatar": {"main": WHITE, "accent": GOLD, "sun": GOLD, "inner": SKY, "on_accent": NAVY},
        "alt": {"main": WHITE, "accent": GOLD, "sun": GOLD, "inner": NAVY, "on_accent": NAVY},
        "avatar_bg": SKY,
        "alt_bg": NAVY,
        "alt_name": "dark",
        "words": {"light": (NAVY, SKY, SKY), "alt": (WHITE, GOLD, SKY)},
    },
    "white": {
        "dir": OUT / "white",
        "light": {"main": SOFT_SKY, "accent": SOFT_GOLD, "sun": PALE_GOLD, "inner": WHITE, "on_accent": SOFT_SKY},
        "avatar": {"main": SOFT_SKY, "accent": SOFT_GOLD, "sun": PALE_GOLD, "inner": WHITE, "on_accent": SOFT_SKY},
        "alt": {"main": WHITE, "accent": PALE_GOLD, "sun": WHITE, "inner": SOFT_SKY, "on_accent": SOFT_SKY},
        "avatar_bg": WHITE,
        "alt_bg": SOFT_SKY,
        "alt_name": "inverse",
        "words": {"light": (INK, SOFT_SKY, SOFT_SKY), "alt": (WHITE, PALE_GOLD, WHITE)},
    },
}


# Каждый знак рисуется в квадрате 300×300 с центром (150, 150).
def mark_sun(c: dict) -> str:
    """Самолёт внутри солнца — отсылка к флагу Казахстана. На тёмном фоне читается как вырез."""
    return f'<circle cx="150" cy="150" r="118" fill="{c["sun"]}"/>' + plane(150, 150, 45, 0.86, c["on_accent"])


def mark_percent(c: dict) -> str:
    """Знак процента, где косая черта — взлетающий самолёт: скидки на перелёты."""
    return (
        f'<circle cx="50" cy="50" r="30" fill="{c["accent"]}"/>'
        f'<circle cx="250" cy="250" r="30" fill="{c["accent"]}"/>'
        + plane(150, 150, 45, 1.0, c["main"])
    )


TAG = "M 36 150 L 98 90 L 250 90 Q 272 90 272 112 L 272 188 Q 272 210 250 210 L 98 210 Z"


def mark_tag(c: dict) -> str:
    """Ценник с самолётом: дешёвые билеты."""
    return (
        f'<g transform="rotate(-40 150 150)">'
        f'<path d="{TAG}" fill="{c["main"]}" stroke="{c["main"]}" stroke-width="14" stroke-linejoin="round"/>'
        f'<circle cx="86" cy="150" r="14" fill="{c["accent"]}"/>'
        + plane(190, 150, 90, 0.5, c["inner"])
        + "</g>"
    )


CONCEPTS = {
    "sun": ("A — Солнце", mark_sun),
    "percent": ("B — Процент", mark_percent),
    "tag": ("C — Ценник", mark_tag),
}


def svg(width: int, height: int, body: str, background: str | None = None) -> str:
    bg = f'<rect width="{width}" height="{height}" fill="{background}"/>' if background else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{bg}{body}</svg>'
    )


def icon(mark, colors: dict, size: int = 300) -> str:
    return f'<g transform="scale({size / 300})">{mark(colors)}</g>'


def wordmark(x: int, y: int, size: int, travel: str, easy: str, kz: str) -> str:
    style = f"font-family:Inter;font-weight:800;font-size:{size}px;letter-spacing:-0.03em"
    return (
        f'<text x="{x}" y="{y}" style="{style}"><tspan fill="{travel}">travel</tspan>'
        f'<tspan fill="{easy}">easy</tspan>'
        f'<tspan fill="{kz}" dx="{size * 0.18}" style="font-size:{size * 0.42}px;letter-spacing:0.08em">KZ</tspan></text>'
    )


def render(out: Path, name: str, markup: str, width: int, height: int, transparent: bool = True) -> Path:
    html = out / f".{name}.html"
    page_bg = "transparent" if transparent else "white"
    html.write_text(f'<html><body style="margin:0;background:{page_bg}">{markup}</body></html>', encoding="utf-8")
    png = out / f"{name}.png"
    args = [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            f"--window-size={width},{height + 300}", f"--screenshot={png}", html.as_uri()]
    if transparent:
        args.insert(-2, "--default-background-color=00000000")
    subprocess.run(args, check=True, capture_output=True)
    html.unlink()
    Image.open(png).crop((0, 0, width, height)).save(png)
    return png


def build(scheme: dict, key: str, mark) -> list[Path]:
    out = scheme["dir"]
    out.mkdir(exist_ok=True)
    files = []
    # 1. Знак — вектор и PNG 1024 на прозрачном фоне.
    (out / f"{key}-icon.svg").write_text(svg(300, 300, icon(mark, scheme["light"])), encoding="utf-8")
    files.append(render(out, f"{key}-icon", svg(1024, 1024, icon(mark, scheme["light"], 1024)), 1024, 1024))
    # 2. Аватар для Telegram/TikTok, с запасом под круглую обрезку.
    avatar = svg(
        640, 640, f'<g transform="translate(110 110)">{icon(mark, scheme["avatar"], 420)}</g>', scheme["avatar_bg"]
    )
    (out / f"{key}-avatar.svg").write_text(avatar, encoding="utf-8")
    files.append(render(out, f"{key}-avatar", avatar, 640, 640, transparent=False))
    # 3. Горизонтальный логотип: на светлом фоне и второй вариант (тёмный или инверсный).
    for theme, name, bg in (("light", "light", None), ("alt", scheme["alt_name"], scheme["alt_bg"])):
        body = (
            f'<g transform="translate(40 40)">{icon(mark, scheme[theme], 240)}</g>'
            + wordmark(310, 205, 112, *scheme["words"][theme])
        )
        files.append(render(out, f"{key}-logo-{name}", svg(1240, 320, body, bg), 1240, 320, transparent=bg is None))
    return files


def preview(scheme: dict, paths: dict[str, list[Path]]) -> Path:
    from PIL import ImageDraw, ImageFont

    sheet = Image.new("RGB", (1500, 1150), "#F4F6F8")
    label_font = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf", 34)
    for col, (key, files) in enumerate(paths.items()):
        x = 30 + col * 490
        icon_png, avatar_png, light_png, dark_png = (Image.open(p).convert("RGBA") for p in files)
        card = Image.new("RGBA", (460, 460), "white")
        card.alpha_composite(icon_png.resize((380, 380), Image.LANCZOS), (40, 40))
        sheet.paste(card, (x, 30))
        mask = Image.new("L", (640, 640), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 640, 640), fill=255)
        av = avatar_png.copy(); av.putalpha(mask)
        sheet.paste(av.resize((220, 220), Image.LANCZOS), (x + 120, 520), av.resize((220, 220), Image.LANCZOS))
        small = av.resize((64, 64), Image.LANCZOS)
        sheet.paste(small, (x + 360, 676), small)
        if scheme["avatar_bg"] == WHITE:  # белый круг на светлом фоне превью — обводим, чтобы было видно
            draw = ImageDraw.Draw(sheet)
            draw.ellipse((x + 120, 520, x + 340, 740), outline="#D5DEE5", width=2)
            draw.ellipse((x + 360, 676, x + 424, 740), outline="#D5DEE5", width=2)
        for i, logo in enumerate((light_png, dark_png)):
            tile = Image.new("RGBA", (460, 119), "white")
            if i and scheme["alt_bg"]:
                tile = Image.new("RGBA", (460, 119), scheme["alt_bg"])
            tile.alpha_composite(logo.resize((460, 119), Image.LANCZOS))
            sheet.paste(tile, (x, 790 + i * 140))
        ImageDraw.Draw(sheet).text((x + 230, 1100), CONCEPTS[key][0], fill=NAVY, font=label_font, anchor="mm")
    path = scheme["dir"] / "preview.png"
    sheet.save(path)
    return path


if __name__ == "__main__":
    for scheme in SCHEMES.values():
        built = {key: build(scheme, key, mark) for key, (_, mark) in CONCEPTS.items()}
        print(preview(scheme, built))
