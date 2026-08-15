from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "provider-icons"
ICON_SIZE = 32
SOURCES = {
    "volcengine": "volcengine.com",
    "openai": "openai.com",
    "google": "ai.google.dev",
    "moonshot": "moonshot.cn",
    "qwen": "aliyun.com",
    "siliconflow": "siliconflow.cn",
}
FALLBACK_COLORS = {
    "volcengine": "#1769ff",
    "openai": "#111111",
    "google": "#4285f4",
    "moonshot": "#171717",
    "qwen": "#6c55d9",
    "siliconflow": "#16a085",
}


def fallback_icon(key: str) -> Image.Image:
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (2, 2, ICON_SIZE - 3, ICON_SIZE - 3),
        radius=6,
        fill=FALLBACK_COLORS[key],
    )
    label = {
        "volcengine": "V",
        "openai": "O",
        "google": "G",
        "moonshot": "K",
        "qwen": "Q",
        "siliconflow": "S",
    }[key]
    font = ImageFont.load_default(size=18)
    bounds = draw.textbbox((0, 0), label, font=font)
    draw.text(
        ((ICON_SIZE - (bounds[2] - bounds[0])) / 2, 4),
        label,
        fill="white",
        font=font,
    )
    return canvas


def connection_icon() -> Image.Image:
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    color = "#3f79bf"
    draw.rounded_rectangle((3, 10, 18, 22), radius=6, outline=color, width=3)
    draw.rounded_rectangle((14, 10, 29, 22), radius=6, outline=color, width=3)
    draw.line((11, 16, 21, 16), fill=color, width=3)
    return canvas


def download_icon(key: str, domain: str) -> Image.Image:
    request = Request(
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
        headers={"User-Agent": "Qianyi-Media-Caption-Tool"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            source = Image.open(BytesIO(response.read())).convert("RGBA")
        contained = ImageOps.contain(source, (28, 28), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(
            contained,
            ((ICON_SIZE - contained.width) // 2, (ICON_SIZE - contained.height) // 2),
        )
        return canvas
    except (OSError, ValueError):
        return fallback_icon(key)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for key, domain in SOURCES.items():
        icon = download_icon(key, domain)
        path = OUTPUT / f"{key}.png"
        icon.save(path, optimize=True)
        print(f"{path} {icon.size} alpha={icon.getchannel('A').getextrema()}")
    link_path = OUTPUT / "connection.png"
    link = connection_icon()
    link.save(link_path, optimize=True)
    print(f"{link_path} {link.size} alpha={link.getchannel('A').getextrema()}")
    custom_path = OUTPUT / "custom.png"
    link.save(custom_path, optimize=True)
    print(f"{custom_path} {link.size} alpha={link.getchannel('A').getextrema()}")


if __name__ == "__main__":
    main()
