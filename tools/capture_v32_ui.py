from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

from PIL import Image, ImageChops, ImageDraw, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import media_caption_core as core
import media_caption_tool_v3 as gui


class MemorySecretStore(core.SecretStore):
    def get(self) -> str:
        return ""

    def set(self, value: str) -> None:
        pass


def update_for(root: tk.Tk, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        time.sleep(0.02)


def capture_window(
    widget: tk.Misc,
    destination: Path,
    *,
    composited: bool = False,
) -> None:
    widget.update_idletasks()
    hwnd = ctypes.windll.user32.GetAncestor(widget.winfo_id(), 2)
    if composited:
        foreground = ImageGrab.grab(window=hwnd).convert("RGB")
        background = Image.new("RGB", foreground.size, "#22292d")
        checker = ImageDraw.Draw(background)
        tile = 40
        for y in range(0, foreground.height, tile):
            for x in range(0, foreground.width, tile):
                if (x // tile + y // tile) % 2:
                    checker.rectangle(
                        (x, y, x + tile - 1, y + tile - 1), fill="#2d363b"
                    )
        red, green, blue = foreground.split()
        mask = ImageChops.multiply(
            ImageChops.multiply(
                red.point(lambda value: 255 if value > 240 else 0),
                green.point(lambda value: 255 if value < 24 else 0),
            ),
            blue.point(lambda value: 255 if value > 240 else 0),
        )
        image = Image.composite(background, foreground, mask)
    else:
        image = ImageGrab.grab(window=hwnd)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    print(f"{destination} {image.width}x{image.height} hwnd={hwnd}")


def create_media(folder: Path) -> None:
    colors = ["#12cbed", "#aaf23b", "#f4a62a", "#ed5470", "#547df0", "#53d698"]
    for index in range(12):
        image = Image.new("RGB", (360, 240), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        draw.rectangle((18, 18, 342, 222), outline="#eef8fb", width=3)
        draw.line((28, 200 - index * 4, 330, 48 + index * 5), fill="#071015", width=5)
        draw.text((30, 30), f"FRAME {index + 1:02d}", fill="#071015")
        image.save(folder / f"frame-{index + 1:02d}.jpg")


def main() -> None:
    output = ROOT / "analysis" / "ui-v34"
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        projects = [temp / "人物训练集", temp / "产品素材复核", temp / "短视频分镜", temp / "旧项目目录"]
        for folder in projects[:3]:
            folder.mkdir()
        create_media(projects[0])
        (projects[0] / "orphan-caption.txt").write_text(
            "这是一条没有对应媒体文件的旧标签。", encoding="utf-8"
        )

        store = core.SettingsStore(
            temp / "settings.json",
            temp / "legacy.json",
            MemorySecretStore(),
        )
        settings = store.load()
        settings.update({
            "last_folder": str(projects[0]),
            "recent_folders": [str(path) for path in projects],
            "concurrency": 10,
        })
        store.save(settings)

        summaries = {
            gui.CaptionApp._folder_key(projects[0]): ("completed", 48, 43, 4, 1, "2026-07-19T20:42:00"),
            gui.CaptionApp._folder_key(projects[1]): ("running", 120, 66, 12, 0, "2026-07-19T20:36:00"),
            gui.CaptionApp._folder_key(projects[2]): ("stopped", 27, 11, 6, 2, "2026-07-18T23:18:00"),
        }
        original_summary = gui.load_project_summary

        def visual_summary(folder: Path):
            status, total, success, skipped, failed, updated = summaries.get(
                gui.CaptionApp._folder_key(folder),
                ("new", 0, 0, 0, 0, ""),
            )
            return {
                "folder": str(folder),
                "name": folder.name,
                "exists": folder.is_dir(),
                "status": status,
                "updated_at": updated,
                "total": total,
                "success": success,
                "skipped": skipped,
                "failed": failed,
            }

        gui.load_project_summary = visual_summary
        root = tk.Tk()
        root.geometry("960x620+80+60")
        app = gui.CaptionApp(root, store, show_splash=False)
        try:
            root.state("normal")
            root.deiconify()
            app.show_launch()
            root.geometry("1180x680+70+50")
            update_for(root, 0.35)
            capture_window(root, output / "launch-1180x680.png", composited=True)

            app.workspace_project = projects[0]
            app.show_project_center()
            root.state("normal")
            root.geometry("960x620+80+60")
            update_for(root, 0.35)
            capture_window(root, output / "project-center-960x620.png")

            app.folder_var.set(str(projects[0]))
            app.show_workspace()
            root.state("normal")
            root.geometry("1180x680+70+50")
            app._handle_scan(core.scan_media(projects[0], "image"))
            app.folder_var.set(r"D:\AI-Datasets\人物训练集")
            app._relative_path = lambda path: path.name
            app.refresh_table()
            update_for(root, 0.9)
            capture_window(root, output / "workbench-1180x680.png")

            app.right_tabs.select(1)
            app.user_prompt_text.insert("1.0", "保留人物五官、发型与服装细节，弱化背景元素。")
            update_for(root, 0.25)
            capture_window(root, output / "prompt-editor-1180x680.png")
            app.right_tabs.select(0)

            root.geometry("960x620+80+60")
            update_for(root, 0.35)
            capture_window(root, output / "workbench-960x620.png")

            app.filter_var.set("孤立 TXT")
            app._filter_changed()
            update_for(root, 0.25)
            capture_window(root, output / "orphan-txt-filter.png")

            dialog = app.open_settings()
            update_for(root, 0.25)
            capture_window(dialog, output / "settings-dialog.png")
            dialog.destroy()

        finally:
            gui.load_project_summary = original_summary
            app.close()


if __name__ == "__main__":
    main()
