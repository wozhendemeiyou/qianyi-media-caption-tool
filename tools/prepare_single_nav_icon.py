from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SIZE = 120
OUTPUT_SIZE = 30
PALETTES = {
    "night": {"primary": (121, 164, 220, 255), "accent": (238, 122, 112, 255)},
    "day": {"primary": (40, 92, 150, 255), "accent": (184, 75, 67, 255)},
}


def render(primary, accent) -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((18, 18, 102, 102), radius=15, outline=primary, width=8)
    draw.line((30, 42, 30, 30, 42, 30), fill=accent, width=7, joint="curve")
    draw.line((78, 90, 90, 90, 90, 78), fill=accent, width=7, joint="curve")
    star = ((60, 32), (67, 51), (87, 60), (67, 68), (60, 88), (52, 68), (33, 60), (52, 51))
    draw.polygon(star, fill=primary)
    draw.ellipse((55, 55, 65, 65), fill=accent)
    return canvas.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    for theme, palette in PALETTES.items():
        output = ROOT / "assets" / "nav-icons" / theme / "single.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        render(palette["primary"], palette["accent"]).save(output, optimize=True)
        print(output)


if __name__ == "__main__":
    main()
