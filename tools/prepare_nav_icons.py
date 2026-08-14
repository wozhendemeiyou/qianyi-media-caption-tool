from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "assets" / "nav-icons"
SOURCE_ROOT = Path(
    os.environ.get(
        "QIANYI_NAV_ICON_SOURCES",
        ROOT / "assets" / "nav-icon-sources",
    )
).expanduser()
ICON_SIZE = 30
CONTENT_SIZE = 28

SOURCES = {
    "project": SOURCE_ROOT / "project.png",
    "image": SOURCE_ROOT / "image.png",
    "video": SOURCE_ROOT / "video.png",
    "platform": SOURCE_ROOT / "platform.png",
    "night": SOURCE_ROOT / "night.png",
    "day": SOURCE_ROOT / "day.png",
}

SOURCE_CROPS = {
    # Candidate B: sliders panel with a connected provider node.
    "platform": (1080, 150, 1880, 880),
}

PALETTES = {
    "night": {
        "primary": (121, 164, 220),
        "accent": (238, 122, 112),
    },
    "day": {
        "primary": (40, 92, 150),
        "accent": (184, 75, 67),
    },
}


def _system_layers() -> tuple[Image.Image, Image.Image]:
    primary = Image.new("L", (256, 256), 0)
    accent = Image.new("L", (256, 256), 0)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(primary)
    draw.ellipse((24, 24, 232, 232), outline=255, width=24)
    draw.rounded_rectangle((116, 103, 140, 190), radius=12, fill=255)
    accent_draw = ImageDraw.Draw(accent)
    accent_draw.ellipse((114, 64, 142, 92), fill=255)
    return primary, accent


def _extract_layers(source: Image.Image) -> tuple[Image.Image, Image.Image]:
    primary_alpha = []
    accent_alpha = []
    for red, green, blue in source.convert("RGB").get_flattened_data():
        teal_chroma = min(green - red, blue - red)
        coral_chroma = min(red - green, red - blue)
        primary_alpha.append(max(0, min(255, round((teal_chroma - 5) * 255 / 30))))
        accent_alpha.append(max(0, min(255, round((coral_chroma - 5) * 255 / 30))))
    size = source.size
    primary = Image.new("L", size)
    accent = Image.new("L", size)
    primary.putdata(primary_alpha)
    accent.putdata(accent_alpha)
    return primary, accent


def _render_icon(layers, primary_color, accent_color) -> Image.Image:
    primary, accent = (layer.copy() for layer in layers)
    combined = Image.new("L", primary.size)
    combined_data = [
        max(primary_value, accent_value)
        for primary_value, accent_value in zip(
            primary.get_flattened_data(),
            accent.get_flattened_data(),
            strict=True,
        )
    ]
    combined.putdata(combined_data)
    bbox = combined.getbbox()
    if bbox is None:
        raise ValueError("No visible icon shape could be extracted")
    left, top, right, bottom = bbox
    padding = max(8, round(max(right - left, bottom - top) * 0.035))
    crop_box = (
        max(0, left - padding),
        max(0, top - padding),
        min(primary.width, right + padding),
        min(primary.height, bottom + padding),
    )
    primary = primary.crop(crop_box)
    accent = accent.crop(crop_box)

    scale = min(CONTENT_SIZE / primary.width, CONTENT_SIZE / primary.height)
    target = (
        max(1, round(primary.width * scale)),
        max(1, round(primary.height * scale)),
    )
    primary = primary.resize(target, Image.Resampling.LANCZOS)
    accent = accent.resize(target, Image.Resampling.LANCZOS)
    primary_peak = primary.getextrema()[1]
    accent_peak = accent.getextrema()[1]
    if 0 < primary_peak < 255:
        primary = primary.point(
            lambda value, peak=primary_peak: round(value * 255 / peak)
        )
    if 0 < accent_peak < 255:
        accent = accent.point(
            lambda value, peak=accent_peak: round(value * 255 / peak)
        )
    rendered = Image.new("RGBA", target, primary_color + (0,))
    rendered.putalpha(primary)
    accent_layer = Image.new("RGBA", target, accent_color + (0,))
    accent_layer.putalpha(accent)
    rendered.alpha_composite(accent_layer)

    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(
        rendered,
        ((ICON_SIZE - target[0]) // 2, (ICON_SIZE - target[1]) // 2),
    )
    return canvas


def main() -> None:
    for source_path in SOURCES.values():
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
    layers_by_icon = {}
    for icon_key, source_path in SOURCES.items():
        with Image.open(source_path) as source:
            crop_box = SOURCE_CROPS.get(icon_key)
            icon_source = source.crop(crop_box) if crop_box else source
            layers_by_icon[icon_key] = _extract_layers(icon_source)
    layers_by_icon["system"] = _system_layers()
    for theme_key, palette in PALETTES.items():
        output_dir = OUTPUT_ROOT / theme_key
        output_dir.mkdir(parents=True, exist_ok=True)
        for icon_key, layers in layers_by_icon.items():
            output_path = output_dir / f"{icon_key}.png"
            icon = _render_icon(
                layers,
                palette["primary"],
                palette["accent"],
            )
            icon.save(output_path, optimize=True)
            alpha_range = icon.getchannel("A").getextrema()
            print(f"{output_path} {icon.size} alpha={alpha_range}")


if __name__ == "__main__":
    main()
