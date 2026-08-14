from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import media_caption_core as core
import media_caption_tool_v3 as gui


OUTPUT = ROOT / "docs" / "images"
PUBLIC_PROJECT_PATH = r"D:\AI-Datasets\人物训练集"


class MemorySecretStore(core.SecretStore):
    def get(self) -> str:
        return ""

    def set(self, value: str) -> None:
        pass


def update_for(root: tk.Tk, seconds: float = 0.25) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        time.sleep(0.02)


def capture(widget: tk.Misc, name: str) -> None:
    widget.update_idletasks()
    hwnd = ctypes.windll.user32.GetAncestor(widget.winfo_id(), 2)
    image = ImageGrab.grab(window=hwnd).convert("RGB")
    destination = OUTPUT / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, optimize=True)
    print(f"{destination} · {image.width}x{image.height}")


def create_media(folder: Path) -> None:
    palette = (
        ("#3273dc", "人物肖像"),
        ("#d8596f", "服装细节"),
        ("#d39a21", "逆光场景"),
        ("#3a8c67", "环境构图"),
        ("#7462b5", "镜头语言"),
        ("#4e91a8", "风格参考"),
        ("#b45b36", "动作姿态"),
        ("#68717a", "材质光影"),
    )
    for index, (color, label) in enumerate(palette, start=1):
        image = Image.new("RGB", (480, 320), color)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (22, 22, 458, 298), radius=24, outline="#f7f4e9", width=4
        )
        draw.ellipse((56, 58, 196, 198), fill="#f7f4e9")
        draw.rectangle((230, 74, 410, 96), fill="#f7f4e9")
        draw.rectangle((230, 116, 374, 134), fill="#f7f4e9")
        draw.text((58, 238), f"DATASET {index:02d}  {label}", fill="#171a18")
        image.save(folder / f"sample-{index:02d}.jpg", quality=92)


def populate_public_project_rows(app: gui.CaptionApp) -> None:
    for item in app.project_tree.get_children():
        app.project_tree.delete(item)
    rows = (
        ("人物训练集", PUBLIC_PROJECT_PATH, "已完成", "48 / 48", "2026-08-14 21:30"),
        (
            "产品素材复核",
            r"D:\AI-Datasets\产品素材复核",
            "进行中",
            "66 / 120",
            "2026-08-14 20:42",
        ),
        (
            "短视频分镜",
            r"D:\AI-Datasets\短视频分镜",
            "上次中断",
            "11 / 27",
            "2026-08-13 23:18",
        ),
    )
    for row in rows:
        app.project_tree.insert("", tk.END, values=row)
    app.project_count_var.set("最近项目 3")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qianyi-public-docs-") as directory:
        project = Path(directory) / "dataset"
        project.mkdir()
        create_media(project)
        (project / "orphan-caption.txt").write_text(
            "这是一条没有对应媒体文件的历史标签。", encoding="utf-8"
        )

        store = core.SettingsStore(
            Path(directory) / "settings.json",
            Path(directory) / "legacy.json",
            MemorySecretStore(),
        )
        settings = store.load()
        settings.update(
            {
                "auto_check_updates": False,
                "theme": "day",
                "prompt_presets": {},
                "selected_preset": "",
                "user_prompt": "",
                "api_models": {},
                "api_endpoints": {},
                "custom_api_endpoint": "",
                "last_folder": "",
                "recent_folders": [],
            }
        )
        store.save(settings)

        gui.enable_dpi_awareness()
        root = tk.Tk()
        root.geometry("1500x1000+40+20")
        app = gui.CaptionApp(root, store, show_splash=False)
        try:
            root.state("normal")
            root.deiconify()

            app.show_launch()
            root.geometry("1900x1080+20+20")
            update_for(root, 0.35)
            capture(root, "launch.png")

            app.show_project_center()
            root.geometry("1360x860+40+30")
            populate_public_project_rows(app)
            update_for(root, 0.3)
            capture(root, "project-center.png")

            app.folder_var.set(str(project))
            app.workspace_project = project
            app.show_workspace()
            root.geometry("1500x1000+25+20")
            app._handle_scan(core.scan_media(project, "image"))
            app.folder_var.set(PUBLIC_PROJECT_PATH)
            first = next(iter(app.items.values()))
            app._gallery_selected(first["path"], False)
            app.refresh_table()
            update_for(root, 0.8)
            capture(root, "workbench.png")

            app.inspector_tabs.select(app.prompt_tab)
            update_for(root, 0.25)
            capture(root, "prompt-editor.png")

            app.inspector_tabs.select(app.result_tab)
            app.right_tabs.select(app.task_settings_tab)
            app._toggle_sampling_panel(True)
            update_for(root, 0.25)
            capture(root, "sampling.png")

            app._toggle_sampling_panel(False)
            app.right_tabs.select(app.preview_tab)
            app.filter_var.set("孤立 TXT")
            app._filter_changed()
            update_for(root, 0.25)
            capture(root, "orphan-txt.png")

            dialog = app.open_platform_config()
            update_for(root, 0.3)
            capture(dialog, "platform-settings.png")
            dialog.destroy()

            app.show_system_info()
            update_for(root, 0.3)
            capture(root, "system-info.png")
        finally:
            app.close()


if __name__ == "__main__":
    main()
