"""Render the Qianyi vector icon into crisp Windows icon resources."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "assets" / "qianyi-icon-vector.svg"
PNG_PATH = ROOT / "assets" / "qianyi-app-icon.png"
ICO_PATH = ROOT / "assets" / "qianyi-app.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render_svg(size: int, supersample: int = 8) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(SVG_PATH.read_bytes()))
    render_size = max(size, int(size) * max(1, int(supersample)))
    image = QImage(QSize(render_size, render_size), QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    pil = Image.open(__import__("io").BytesIO(bytes(buffer.data()))).convert("RGBA")
    if render_size != size:
        pil = pil.resize((size, size), Image.Resampling.LANCZOS)
    if size <= 48:
        pil = pil.filter(ImageFilter.UnsharpMask(radius=0.45, percent=120, threshold=2))
    return pil


def main() -> None:
    frames = [render_svg(size) for size in SIZES]
    frames[-1].save(
        ICO_PATH,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=frames[:-1],
    )
    master = render_svg(1024, supersample=2)
    master.save(PNG_PATH, format="PNG", optimize=True, dpi=(600, 600))
    print(f"wrote {PNG_PATH}")
    print(f"wrote {ICO_PATH}")


if __name__ == "__main__":
    main()
