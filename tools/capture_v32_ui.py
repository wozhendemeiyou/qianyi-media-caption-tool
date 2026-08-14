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
        gui.enable_dpi_awareness()
        root = tk.Tk()
        root.geometry("960x620+80+60")
        app = gui.CaptionApp(root, store, show_splash=False)
        try:
            root.state("normal")
            root.deiconify()
            app.show_launch()
            root.geometry("1900x1080+180+90")
            update_for(root, 0.35)
            capture_window(root, output / "launch-1900x1080.png", composited=True)

            app.workspace_project = projects[0]
            app.show_project_center()
            root.state("normal")
            root.geometry("1200x760+80+40")
            update_for(root, 0.35)
            capture_window(root, output / "project-center-night-1200x760.png")

            app._set_theme("day")
            update_for(root, 0.25)
            capture_window(root, output / "project-center-day-1200x760.png")

            app.folder_var.set(str(projects[0]))
            app.show_workspace()
            root.state("normal")
            root.geometry("1500x1200+40+20")
            app._handle_scan(core.scan_media(projects[0], "image"))
            first_item = next(iter(app.items.values()))
            app._gallery_selected(first_item["path"], False)
            app.folder_var.set(r"D:\AI-Datasets\人物训练集")
            app._relative_path = lambda path: path.name
            app.refresh_table()
            update_for(root, 0.9)
            capture_window(root, output / "workbench-day-1500x1200.png")

            app._set_theme("night")
            update_for(root, 0.25)
            capture_window(root, output / "workbench-night-1500x1200.png")
            app._show_update_banner({
                "tag": "v99.0",
                "url": "https://github.com/example/releases/tag/v99.0",
                "notes": "视觉测试更新",
                "is_newer": True,
            })
            update_for(root, 0.2)
            capture_window(root, output / "workbench-update-banner.png")
            app.dismiss_update_banner()
            app.show_system_info()
            update_for(root, 0.25)
            capture_window(root, output / "system-info-night.png")
            app._set_theme("day")
            update_for(root, 0.25)
            capture_window(root, output / "system-info-day.png")
            app.show_workspace()
            update_for(root, 0.25)

            root.geometry("1024x720+60+40")
            update_for(root, 0.35)
            capture_window(root, output / "workbench-day-1024x720.png")
            root.geometry("1500x1200+40+20")
            update_for(root, 0.35)

            tooltip = app.nav_tooltips[2]
            tooltip.show()
            update_for(root, 0.1)
            capture_window(tooltip.window, output / "tooltip-day.png")
            tooltip.hide()

            app.inspector_tabs.select(app.prompt_tab)
            app.user_prompt_text.insert("1.0", "保留人物五官、发型与服装细节，弱化背景元素。")
            update_for(root, 0.25)
            capture_window(root, output / "prompt-editor-day-1500x1200.png")
            app._set_theme("night")
            update_for(root, 0.25)
            capture_window(root, output / "prompt-editor-night-1500x1200.png")
            app._set_theme("day")
            app.inspector_tabs.select(app.result_tab)

            app.sampling_preset_var.set("创意扩写")
            app.apply_sampling_preset()
            app.right_tabs.select(app.task_settings_tab)
            update_for(root, 0.25)
            capture_window(root, output / "task-settings-collapsed.png")
            app._toggle_sampling_panel(True)
            update_for(root, 0.25)
            capture_window(root, output / "task-settings-expanded.png")
            app._toggle_sampling_panel(False)
            app.right_tabs.select(app.preview_tab)

            app.filter_var.set("孤立 TXT")
            app._filter_changed()
            update_for(root, 0.25)
            capture_window(root, output / "orphan-txt-filter.png")

            dialog = app.open_platform_config()
            update_for(root, 0.25)
            capture_window(dialog, output / "platform-settings.png")
            dialog.destroy()

        finally:
            gui.load_project_summary = original_summary
            app.close()


if __name__ == "__main__":
    main()
