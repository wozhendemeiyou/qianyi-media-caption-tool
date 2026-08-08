from __future__ import annotations

from datetime import datetime
import ctypes
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageTk

from media_caption_core import (
    APP_VERSION,
    BatchRunner,
    BatchSummary,
    CancelledError,
    CancellationToken,
    DEFAULT_MODEL_KEY,
    MAX_CONCURRENCY,
    MODELS,
    ScanResult,
    SettingsStore,
    caption_path_for,
    delete_project_metadata,
    export_csv,
    export_jsonl,
    find_similar_images,
    has_usable_caption,
    load_project_summary,
    open_image,
    prepend_trigger_word,
    scan_media,
    write_caption,
)


APP_TITLE = "芊熠智能打标工作台"
DEFAULT_PRESETS = {
    "详细自然语言": """# 专家：图像生成提示词工程师
## 目标
- 根据提供的媒体内容，准确描述对应的生成提示词
## 输出
- 格式：一段完整、连贯的自然语言
- 覆盖：主体、外观、动作、场景、构图、镜头、光线与风格
- 语言：中文
- 只输出提示词，不解释分析过程""",
    "精简词组标签": """分析媒体内容并输出中文关键词。只输出一行，以逗号分隔；覆盖主体、外观、动作、场景、构图、镜头、光线和风格，避免重复与解释。""",
    "视频自然语言": """作为视频生成提示词工程师，准确描述视频中的主体、连续动作、镜头运动、场景变化、光线、节奏和视觉风格。输出一段完整中文自然语言提示词，不解释分析过程。""",
}
STATUS_TEXT = {
    "pending": "待处理",
    "running": "处理中",
    "success": "成功",
    "skipped": "已跳过",
    "failed": "失败",
    "cancelled": "已取消",
    "orphan": "孤立 TXT",
}
FILTERS = {
    "全部状态": "all",
    "缺少 TXT": "missing_caption",
    "TXT 无效": "invalid_caption",
    "孤立 TXT": "orphan_caption",
    "待处理": "pending",
    "处理中": "running",
    "成功": "success",
    "已跳过": "skipped",
    "失败": "failed",
    "已取消": "cancelled",
    "相似图": "similar",
}
FOCUS_OPTIONS = {
    "训练主体": "subject",
    "风格 LoRA": "style",
    "风景 / 场景": "scene",
}
FOCUS_LABELS = {value: label for label, value in FOCUS_OPTIONS.items()}

COLORS = {
    "bg": "#070b0e",
    "surface": "#0d151a",
    "surface_alt": "#142129",
    "border": "#29404b",
    "text": "#eef8fb",
    "muted": "#91a4ad",
    "accent": "#1ed8ff",
    "accent_dark": "#082934",
    "warning": "#ffb340",
    "success": "#b8ff3d",
    "danger": "#ff667a",
    "info": "#59a8ff",
}
STATUS_COLORS = {
    "pending": COLORS["muted"],
    "running": COLORS["info"],
    "success": COLORS["success"],
    "skipped": "#b5b9b2",
    "failed": COLORS["danger"],
    "cancelled": COLORS["warning"],
    "orphan": COLORS["warning"],
}
TRANSPARENT_KEY = "#ff00ff"


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def resource_path(relative_path: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / Path(relative_path)


def maximize_main_window(root: tk.Tk) -> None:
    try:
        root.state("zoomed")
    except tk.TclError:
        try:
            root.wm_attributes("-zoomed", True)
        except tk.TclError:
            pass


def configure_main_window(root: tk.Tk) -> str:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    initial_width = max(960, min(1180, screen_width - 80))
    initial_height = max(620, min(760, screen_height - 80))
    x = max(0, (screen_width - initial_width) // 2)
    y = max(0, (screen_height - initial_height) // 2)
    geometry = f"{initial_width}x{initial_height}+{x}+{y}"
    root.geometry(geometry)
    root.minsize(960, 620)
    maximize_main_window(root)
    return geometry


def center_dialog(dialog: tk.Toplevel, parent: tk.Misc) -> tuple[int, int]:
    parent.update_idletasks()
    dialog.update_idletasks()
    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = max(1, parent.winfo_width())
    parent_height = max(1, parent.winfo_height())
    dialog_width = max(dialog.winfo_reqwidth(), dialog.winfo_width())
    dialog_height = max(dialog.winfo_reqheight(), dialog.winfo_height())

    centered_x = parent_x + (parent_width - dialog_width) // 2
    centered_y = parent_y + (parent_height - dialog_height) // 2
    min_x = max(0, parent_x)
    min_y = max(0, parent_y)
    max_x = min(parent.winfo_screenwidth() - dialog_width, parent_x + parent_width - dialog_width)
    max_y = min(parent.winfo_screenheight() - dialog_height, parent_y + parent_height - dialog_height)
    x = max(0, min(centered_x, parent.winfo_screenwidth() - dialog_width))
    y = max(0, min(centered_y, parent.winfo_screenheight() - dialog_height))
    if max_x >= min_x:
        x = max(min_x, min(x, max_x))
    if max_y >= min_y:
        y = max(min_y, min(y, max_y))
    dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
    dialog.update_idletasks()
    corrected_x = dialog.winfo_x() + x - dialog.winfo_rootx()
    corrected_y = dialog.winfo_y() + y - dialog.winfo_rooty()
    dialog.geometry(f"{dialog_width}x{dialog_height}+{corrected_x}+{corrected_y}")
    dialog.update_idletasks()
    return dialog.winfo_rootx(), dialog.winfo_rooty()


class MediaGallery(ttk.Frame):
    def __init__(self, parent, on_select, request_thumbnail, get_thumbnail):
        super().__init__(parent)
        self.on_select = on_select
        self.request_thumbnail = request_thumbnail
        self.get_thumbnail = get_thumbnail
        self.items: list[dict] = []
        self.selected: set[str] = set()
        self.page = 0
        self.page_size = 30
        self.card_frames: dict[str, tk.Frame] = {}
        self.image_labels: dict[str, tk.Label] = {}
        self.status_labels: dict[str, tk.Label] = {}
        self._render_after = None

        self.canvas = tk.Canvas(
            self,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = tk.Frame(self.canvas, background=COLORS["bg"])
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor=tk.NW)
        self.content.bind("<Configure>", self._content_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.content.bind("<MouseWheel>", self._mousewheel)

        pager = ttk.Frame(self)
        pager.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.page_text = tk.StringVar(value="0 / 0")
        self.total_text = tk.StringVar(value="0 个素材")
        ttk.Label(pager, textvariable=self.total_text, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Button(pager, text="上一页", command=self.previous_page).pack(side=tk.RIGHT)
        ttk.Button(pager, text="下一页", command=self.next_page).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Label(pager, textvariable=self.page_text, width=10, anchor=tk.CENTER).pack(side=tk.RIGHT, padx=8)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def destroy(self):
        if self._render_after is not None:
            try:
                self.after_cancel(self._render_after)
            except tk.TclError:
                pass
            self._render_after = None
        super().destroy()

    def _content_resized(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._render_after = self.after(100, self.render)

    def _mousewheel(self, event) -> None:
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def set_items(self, items: list[dict], selected: set[str]) -> None:
        self.items = list(items)
        self.selected = set(selected)
        page_count = self.page_count
        if self.page >= page_count:
            self.page = max(0, page_count - 1)
        self.render()

    @property
    def page_count(self) -> int:
        return math.ceil(len(self.items) / self.page_size) if self.items else 0

    def previous_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.render()

    def next_page(self) -> None:
        if self.page + 1 < self.page_count:
            self.page += 1
            self.render()

    def render(self) -> None:
        self._render_after = None
        for child in self.content.winfo_children():
            child.destroy()
        self.card_frames.clear()
        self.image_labels.clear()
        self.status_labels.clear()
        self.canvas.yview_moveto(0)
        width = max(320, self.canvas.winfo_width())
        columns = max(2, width // 184)
        start = self.page * self.page_size
        page_items = self.items[start : start + self.page_size]
        for column in range(columns):
            self.content.grid_columnconfigure(column, weight=1, uniform="gallery")
        for index, item in enumerate(page_items):
            path = item["path"]
            key = str(path)
            selected = key in self.selected
            card = tk.Frame(
                self.content,
                background=COLORS["surface_alt"],
                highlightthickness=2,
                highlightbackground=COLORS["info"] if selected else COLORS["border"],
                highlightcolor=COLORS["info"],
                cursor="hand2",
            )
            card.grid(row=index // columns, column=index % columns, padx=5, pady=5, sticky="nsew")
            image_label = tk.Label(
                card,
                text=path.suffix.upper().lstrip("."),
                background="#0b0d0b",
                foreground=COLORS["muted"],
                width=18,
                height=7,
                anchor=tk.CENTER,
            )
            image_label.pack(fill=tk.X)
            thumbnail = self.get_thumbnail(path)
            if thumbnail is not None:
                image_label.configure(image=thumbnail, text="", height=120)
            else:
                self.request_thumbnail(path)
            footer = tk.Frame(card, background=COLORS["surface_alt"])
            footer.pack(fill=tk.X, padx=7, pady=(6, 7))
            tk.Label(
                footer,
                text=path.name,
                background=COLORS["surface_alt"],
                foreground=COLORS["text"],
                anchor=tk.W,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(fill=tk.X)
            status_label = tk.Label(
                footer,
                text=STATUS_TEXT[item["status"]],
                background=COLORS["surface_alt"],
                foreground=STATUS_COLORS[item["status"]],
                anchor=tk.W,
                font=("Microsoft YaHei UI", 8),
            )
            status_label.pack(fill=tk.X, pady=(3, 0))
            self.card_frames[key] = card
            self.image_labels[key] = image_label
            self.status_labels[key] = status_label
            for widget in (card, image_label, footer, *footer.winfo_children()):
                widget.bind(
                    "<Button-1>",
                    lambda event, selected_path=path: self.on_select(
                        selected_path, bool(event.state & 0x0004)
                    ),
                )
                widget.bind("<MouseWheel>", self._mousewheel)
        pages = self.page_count
        self.total_text.set(f"{len(self.items)} 个素材")
        self.page_text.set(f"{self.page + 1 if pages else 0} / {pages}")
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def update_selection(self, selected: set[str]) -> None:
        self.selected = set(selected)
        for key, card in self.card_frames.items():
            card.configure(
                highlightbackground=COLORS["info"] if key in self.selected else COLORS["border"]
            )

    def update_thumbnail(self, path: Path, image) -> None:
        label = self.image_labels.get(str(path))
        if label is not None and label.winfo_exists():
            label.configure(image=image, text="", height=120)

    def update_item(self, item: dict) -> None:
        key = str(item["path"])
        for index, current in enumerate(self.items):
            if str(current["path"]) == key:
                self.items[index] = item
                break
        label = self.status_labels.get(key)
        if label is not None and label.winfo_exists():
            label.configure(
                text=STATUS_TEXT[item["status"]],
                foreground=STATUS_COLORS[item["status"]],
            )


class CaptionApp:
    def __init__(
        self,
        root: tk.Tk,
        settings_store: SettingsStore | None = None,
        show_splash: bool = True,
    ):
        self.root = root
        self.settings_store = settings_store or SettingsStore()
        self.settings = self.settings_store.load()
        presets = dict(DEFAULT_PRESETS)
        presets.update(self.settings.get("prompt_presets") or {})
        self.settings["prompt_presets"] = presets

        self.events: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=20000)
        self.runner: BatchRunner | None = None
        self.controller_thread: threading.Thread | None = None
        self.items: dict[str, dict] = {}
        self.row_paths: dict[str, Path] = {}
        self.path_rows: dict[str, str] = {}
        self.last_failed_paths: list[Path] = []
        self.preview_image = None
        self.closing = False
        self.selected_paths: set[str] = set()
        self.thumbnail_cache: dict[str, ImageTk.PhotoImage] = {}
        self.thumbnail_pending: set[str] = set()
        self.thumbnail_jobs: queue.Queue[Path] = queue.Queue(maxsize=300)
        self._selection_sync = False
        self.analysis_token: CancellationToken | None = None
        self.similar_paths: set[str] = set()
        self.orphan_caption_paths: list[Path] = []
        self.events_after_id = None
        self.sash_after_id = None
        self.splash_after_id = None
        self.scan_generation = 0
        self.launch_progress = 0
        self._launch_source: Image.Image | None = None
        self._launch_photo = None
        self._launch_photo_size: tuple[int, int] | None = None
        self._app_icon_photo = None
        self._project_banner_source: Image.Image | None = None
        self._project_banner_photo = None
        self.project_paths: dict[str, Path] = {}
        self.workspace_project: Path | None = None
        self.counts = {status: 0 for status in STATUS_TEXT}
        self.counts["total"] = 0

        self.root.title(f"{APP_TITLE} {APP_VERSION}")
        self._set_window_icon()
        self.normal_geometry = configure_main_window(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_style()
        self._build_ui()
        self._load_values()
        self._start_thumbnail_workers()
        if show_splash:
            self.show_launch()
        else:
            self.show_project_center()
        self.events_after_id = self.root.after(60, self._process_events)

    def _set_window_icon(self) -> None:
        try:
            with Image.open(resource_path("assets/qianyi-app-icon.png")) as source:
                self._app_icon_photo = ImageTk.PhotoImage(source.convert("RGBA"))
            self.root.iconphoto(True, self._app_icon_photo)
        except (OSError, RuntimeError, ValueError, tk.TclError):
            self._app_icon_photo = None
        try:
            self.root.iconbitmap(default=str(resource_path("assets/qianyi-app.ico")))
        except tk.TclError:
            pass

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(background=COLORS["bg"])
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", foreground=COLORS["muted"])
        style.configure("Title.TLabel", foreground=COLORS["text"], font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("CenterTitle.TLabel", foreground=COLORS["text"], font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("AlertTitle.TLabel", foreground=COLORS["warning"], font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Brand.TLabel", foreground=COLORS["accent"], font=("Segoe UI", 9, "bold"))
        style.configure("Stage.TLabel", foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("StageActive.TLabel", foreground=COLORS["accent"], font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Billing.TLabel", foreground=COLORS["warning"], font=("Microsoft YaHei UI", 9, "bold"))
        style.configure(
            "TButton", padding=(9, 5), background=COLORS["surface_alt"],
            foreground=COLORS["text"], bordercolor=COLORS["border"], relief=tk.FLAT,
        )
        style.map(
            "TButton",
            background=[("active", "#20343e"), ("disabled", COLORS["surface"])],
            foreground=[("disabled", "#626862")],
        )
        style.configure(
            "Primary.TButton", background=COLORS["accent"], foreground="#041014",
            bordercolor=COLORS["accent"], font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Primary.TButton", background=[("active", "#7eeaff"), ("disabled", "#264650")])
        style.configure(
            "Danger.TButton", background="#3a1820", foreground="#ff91a0",
            bordercolor="#74303d", font=("Microsoft YaHei UI", 9),
        )
        style.map("Danger.TButton", background=[("active", "#5a202d")])
        style.configure("TEntry", fieldbackground=COLORS["surface"], foreground=COLORS["text"], bordercolor=COLORS["border"], insertcolor=COLORS["text"])
        style.configure("TCombobox", fieldbackground=COLORS["surface"], foreground=COLORS["text"], arrowcolor=COLORS["muted"], bordercolor=COLORS["border"])
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["surface"])], foreground=[("readonly", COLORS["text"])])
        style.configure("TSpinbox", fieldbackground=COLORS["surface"], foreground=COLORS["text"], arrowcolor=COLORS["muted"])
        style.configure("TRadiobutton", background=COLORS["bg"], foreground=COLORS["text"])
        style.map("TRadiobutton", background=[("active", COLORS["bg"])], foreground=[("active", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])], foreground=[("active", COLORS["text"])])
        style.configure("Treeview", rowheight=28, background=COLORS["surface"], fieldbackground=COLORS["surface"], foreground=COLORS["text"], bordercolor=COLORS["border"], font=("Microsoft YaHei UI", 9))
        style.map("Treeview", background=[("selected", "#2b3c45")], foreground=[("selected", COLORS["text"])])
        style.configure("Treeview.Heading", background=COLORS["surface_alt"], foreground=COLORS["text"], bordercolor=COLORS["border"], font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview.Heading", background=[("active", "#20343e")])
        style.configure("TNotebook", background=COLORS["bg"], bordercolor=COLORS["border"])
        style.configure("TNotebook.Tab", padding=(12, 7), background=COLORS["surface"], foreground=COLORS["muted"])
        style.map("TNotebook.Tab", background=[("selected", COLORS["surface_alt"])], foreground=[("selected", COLORS["text"])])
        style.configure("TProgressbar", troughcolor=COLORS["surface"], background=COLORS["accent"], bordercolor=COLORS["border"])
        style.configure("TScrollbar", background=COLORS["surface_alt"], troughcolor=COLORS["bg"], bordercolor=COLORS["bg"], arrowcolor=COLORS["muted"])

    def _build_ui(self) -> None:
        self.view_host = ttk.Frame(self.root)
        self.view_host.pack(fill=tk.BOTH, expand=True)
        self.launch_frame = tk.Frame(self.view_host, background=TRANSPARENT_KEY)
        self.project_center_frame = ttk.Frame(self.view_host)
        self.workspace_frame = ttk.Frame(self.view_host)
        self._build_launch_view()
        self._build_project_center()

        outer = ttk.Frame(self.workspace_frame, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 9))
        title_block = ttk.Frame(header)
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="MEDIA CAPTION", style="Brand.TLabel").pack(anchor=tk.W)
        ttk.Label(title_block, text=f"{APP_TITLE} {APP_VERSION}", style="Title.TLabel").pack(anchor=tk.W)
        stage_bar = ttk.Frame(header)
        stage_bar.pack(side=tk.RIGHT, anchor=tk.S, pady=(0, 3))
        self.stage_labels = []
        for index, text in enumerate(("01 素材准备", "02 智能标注", "03 复核导出"), start=1):
            label = ttk.Label(stage_bar, text=text, style="StageActive.TLabel" if index == 1 else "Stage.TLabel")
            label.pack(side=tk.LEFT, padx=(16 if index > 1 else 0, 0))
            self.stage_labels.append(label)

        project = ttk.Frame(outer)
        project.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(project, text="← 项目中心", command=self.show_project_center).pack(side=tk.LEFT)
        ttk.Label(project, text="项目").pack(side=tk.LEFT)
        self.folder_var = tk.StringVar()
        self.folder_box = ttk.Combobox(project, textvariable=self.folder_var, state="normal")
        ttk.Button(project, text="设置", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Button(project, text="扫描", command=self.scan_project).pack(side=tk.RIGHT, padx=(6, 6))
        ttk.Button(project, text="浏览", command=self.browse_folder).pack(side=tk.RIGHT)
        self.folder_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 6))

        options = ttk.Frame(outer, padding=(0, 0, 0, 7))
        options.pack(fill=tk.X)
        mode_options = ttk.Frame(options)
        mode_options.pack(fill=tk.X)
        ttk.Label(mode_options, text="媒体").pack(side=tk.LEFT)
        self.media_mode_var = tk.StringVar(value="image")
        ttk.Radiobutton(mode_options, text="图片", value="image", variable=self.media_mode_var).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Radiobutton(mode_options, text="视频", value="video", variable=self.media_mode_var).pack(side=tk.LEFT, padx=(2, 14))
        ttk.Label(mode_options, text="输出").pack(side=tk.LEFT)
        self.caption_style_var = tk.StringVar(value="natural")
        ttk.Radiobutton(mode_options, text="自然语言", value="natural", variable=self.caption_style_var).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Radiobutton(mode_options, text="词组标签", value="phrases", variable=self.caption_style_var).pack(side=tk.LEFT, padx=(2, 14))
        ttk.Label(mode_options, text="视图").pack(side=tk.LEFT)
        self.view_mode_var = tk.StringVar(value="gallery")
        ttk.Radiobutton(mode_options, text="缩略图", value="gallery", variable=self.view_mode_var, command=self._switch_view).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Radiobutton(mode_options, text="紧凑列表", value="list", variable=self.view_mode_var, command=self._switch_view).pack(side=tk.LEFT, padx=(2, 0))

        model_options = ttk.Frame(options)
        model_options.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(model_options, text="后端").pack(side=tk.LEFT)
        self.backend_var = tk.StringVar(value="api")
        ttk.Radiobutton(
            model_options,
            text="外部 API",
            value="api",
            variable=self.backend_var,
            command=self._backend_changed,
        ).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Radiobutton(
            model_options,
            text="本地模型",
            value="local",
            variable=self.backend_var,
            command=self._backend_changed,
        ).pack(side=tk.LEFT, padx=(2, 14))
        ttk.Label(model_options, text="模型").pack(side=tk.LEFT)
        self.model_label_var = tk.StringVar()
        self.model_box = ttk.Combobox(model_options, textvariable=self.model_label_var, state="readonly", width=26)
        self.model_box["values"] = [model.label for model in MODELS.values()]
        self.model_box.pack(side=tk.LEFT, padx=(6, 6))
        self.model_box.bind("<<ComboboxSelected>>", self._model_changed)
        self.billing_var = tk.StringVar()
        ttk.Label(model_options, textvariable=self.billing_var, style="Billing.TLabel", width=23).pack(side=tk.LEFT)
        ttk.Label(model_options, text="并发").pack(side=tk.LEFT, padx=(12, 0))
        self.concurrency_var = tk.IntVar(value=3)
        self.concurrency_box = ttk.Spinbox(
            model_options,
            from_=1,
            to=MAX_CONCURRENCY,
            width=4,
            textvariable=self.concurrency_var,
        )
        self.concurrency_box.pack(side=tk.LEFT, padx=(6, 10))
        self.skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(model_options, text="跳过有效 TXT", variable=self.skip_var).pack(side=tk.LEFT)

        strategy_options = ttk.Frame(options)
        strategy_options.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(strategy_options, text="侧重点").pack(side=tk.LEFT)
        self.focus_label_var = tk.StringVar(value="训练主体")
        self.focus_box = ttk.Combobox(
            strategy_options,
            textvariable=self.focus_label_var,
            state="readonly",
            values=list(FOCUS_OPTIONS),
            width=14,
        )
        self.focus_box.pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(strategy_options, text="语言").pack(side=tk.LEFT)
        self.output_language_var = tk.StringVar(value="zh")
        ttk.Radiobutton(
            strategy_options,
            text="中文",
            value="zh",
            variable=self.output_language_var,
        ).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Radiobutton(
            strategy_options,
            text="English",
            value="en",
            variable=self.output_language_var,
        ).pack(side=tk.LEFT, padx=(2, 14))
        ttk.Label(strategy_options, text="触发词").pack(side=tk.LEFT)
        self.trigger_word_var = tk.StringVar()
        ttk.Entry(
            strategy_options,
            textvariable=self.trigger_word_var,
            width=24,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        local_model_options = ttk.Frame(options)
        local_model_options.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(local_model_options, text="本地模型目录").pack(side=tk.LEFT)
        self.local_model_var = tk.StringVar()
        self.local_model_entry = ttk.Entry(
            local_model_options,
            textvariable=self.local_model_var,
            state=tk.DISABLED,
        )
        self.local_model_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        self.local_model_button = ttk.Button(
            local_model_options,
            text="选择",
            command=self.browse_local_model,
            state=tk.DISABLED,
        )
        self.local_model_button.pack(side=tk.LEFT)

        actions = ttk.Frame(outer, padding=(0, 2, 0, 7))
        actions.pack(fill=tk.X)
        self.start_button = ttk.Button(actions, text="开始任务", width=9, style="Primary.TButton", command=self.start_task)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(actions, text="停止", width=7, state=tk.DISABLED, command=self.stop_task)
        self.stop_button.pack(side=tk.LEFT, padx=(6, 0))
        self.retry_button = ttk.Button(actions, text="重试失败", width=9, state=tk.DISABLED, command=self.retry_failed)
        self.retry_button.pack(side=tk.LEFT, padx=(6, 0))
        self.selected_button = ttk.Button(actions, text="处理选中", width=9, state=tk.DISABLED, command=self.process_selected)
        self.selected_button.pack(side=tk.LEFT, padx=(6, 0))
        self.batch_button = ttk.Menubutton(actions, text="批处理", width=9)
        batch_menu = tk.Menu(self.batch_button, tearoff=False)
        batch_menu.add_command(label="打标缺少 TXT", command=self.process_missing_captions)
        batch_menu.add_command(label="批量添加触发词", command=self.apply_trigger_to_results)
        batch_menu.add_separator()
        batch_menu.add_command(label="批量替换结果", command=self.batch_replace)
        batch_menu.add_command(label="查找相似图片", command=self.find_similar)
        batch_menu.add_separator()
        batch_menu.add_command(label="打开所选文件位置", command=self.open_selected_location)
        batch_menu.add_command(label="删除所选孤立 TXT", command=self.delete_selected_orphan_captions)
        self.batch_button["menu"] = batch_menu
        self.batch_button.pack(side=tk.LEFT, padx=(6, 0))
        export_button = ttk.Menubutton(actions, text="导出", width=7)
        export_menu = tk.Menu(export_button, tearoff=False)
        export_menu.add_command(label="训练数据 JSONL", command=lambda: self.export_results("jsonl"))
        export_menu.add_command(label="表格 CSV", command=lambda: self.export_results("csv"))
        export_button["menu"] = export_menu
        export_button.pack(side=tk.LEFT, padx=(6, 0))

        progress_row = ttk.Frame(outer)
        progress_row.pack(fill=tk.X, pady=(0, 7))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="就绪")
        ttk.Label(progress_row, textvariable=self.progress_text_var, width=22, anchor=tk.E).pack(side=tk.RIGHT)
        ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100, mode="determinate").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        filter_bar = ttk.Frame(outer)
        filter_bar.pack(fill=tk.X, pady=(0, 7))
        ttk.Label(filter_bar, text="搜索").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(6, 12))
        search_entry.bind("<KeyRelease>", lambda _event: self.refresh_table())
        ttk.Label(filter_bar, text="状态").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar(value="全部状态")
        filter_box = ttk.Combobox(filter_bar, textvariable=self.filter_var, state="readonly", values=list(FILTERS), width=12)
        filter_box.pack(side=tk.LEFT, padx=(6, 14))
        filter_box.bind("<<ComboboxSelected>>", self._filter_changed)
        self.selection_var = tk.StringVar(value="已选 0")
        ttk.Label(filter_bar, textvariable=self.selection_var, style="Muted.TLabel").pack(side=tk.RIGHT)
        self.stats_var = tk.StringVar(value="总数 0  ·  成功 0  ·  跳过 0  ·  失败 0")
        ttk.Label(filter_bar, textvariable=self.stats_var).pack(side=tk.LEFT)

        workspace = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        workspace.pack(fill=tk.BOTH, expand=True)
        self.workspace = workspace
        table_panel = ttk.Frame(workspace)
        preview_panel = ttk.Frame(workspace, padding=(10, 0, 0, 0))
        workspace.add(table_panel, weight=3)
        workspace.add(preview_panel, weight=2)

        self.table_frame = ttk.Frame(table_panel)
        self.tree = ttk.Treeview(self.table_frame, columns=("status", "file", "detail"), show="headings", selectmode="extended")
        self.tree.heading("status", text="状态")
        self.tree.heading("file", text="文件")
        self.tree.heading("detail", text="结果 / 详情")
        self.tree.column("status", width=82, minwidth=82, anchor=tk.CENTER, stretch=False)
        self.tree.column("file", width=250, minwidth=150)
        self.tree.column("detail", width=240, minwidth=150)
        scrollbar = ttk.Scrollbar(self.table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_item)
        self.tree.bind("<Control-a>", self.select_all_visible)
        self.tree.tag_configure("failed", foreground=COLORS["danger"])
        self.tree.tag_configure("success", foreground=COLORS["success"])
        self.tree.tag_configure("running", foreground=COLORS["info"])
        self.tree.tag_configure("skipped", foreground=COLORS["muted"])
        self.tree.tag_configure("orphan", foreground=COLORS["warning"])
        self.gallery = MediaGallery(
            table_panel,
            self._gallery_selected,
            self._request_thumbnail,
            lambda path: self.thumbnail_cache.get(str(path)),
        )

        self.right_tabs = ttk.Notebook(preview_panel)
        self.right_tabs.pack(fill=tk.BOTH, expand=True)
        preview_tab = ttk.Frame(self.right_tabs, padding=8)
        prompt_tab = ttk.Frame(self.right_tabs, padding=8)
        log_tab = ttk.Frame(self.right_tabs, padding=6)
        self.right_tabs.add(preview_tab, text="检查")
        self.right_tabs.add(prompt_tab, text="提示词")
        self.right_tabs.add(log_tab, text="运行")

        preview_head = ttk.Frame(preview_tab)
        preview_head.pack(fill=tk.X)
        ttk.Label(preview_head, text="当前任务项").pack(side=tk.LEFT)
        self.save_result_button = ttk.Button(preview_head, text="保存修改", command=self.save_selected_result, state=tk.DISABLED)
        self.save_result_button.pack(side=tk.RIGHT)
        self.preview_label = tk.Label(preview_tab, text="选择任务项", anchor=tk.CENTER, background="#0b0d0b", foreground=COLORS["muted"])
        self.preview_label.pack(fill=tk.BOTH, expand=True, pady=(7, 8))
        self.result_text = scrolledtext.ScrolledText(preview_tab, width=34, height=10, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        self.result_text.pack(fill=tk.X)
        self._style_text(self.result_text)

        prompt_bar = ttk.Frame(prompt_tab)
        prompt_bar.pack(fill=tk.X, pady=(0, 7))
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(prompt_bar, textvariable=self.preset_var, state="readonly", width=20)
        self.preset_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.preset_box.bind("<<ComboboxSelected>>", self.apply_preset)
        ttk.Button(prompt_bar, text="导入", width=6, command=self.import_prompt_file).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(prompt_bar, text="保存", width=6, command=self.save_preset).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(prompt_bar, text="删除", width=6, command=self.delete_preset).pack(side=tk.LEFT, padx=(6, 0))
        subject_row = ttk.Frame(prompt_tab)
        subject_row.pack(fill=tk.X, pady=(0, 7))
        ttk.Label(subject_row, text="主体过滤").pack(side=tk.LEFT)
        self.subject_filter_var = tk.StringVar()
        ttk.Entry(subject_row, textvariable=self.subject_filter_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(7, 0)
        )
        ttk.Label(prompt_tab, text="用户要求（可选）").pack(anchor=tk.W)
        self.user_prompt_text = scrolledtext.ScrolledText(
            prompt_tab, height=4, wrap=tk.WORD, font=("Microsoft YaHei UI", 9)
        )
        self.user_prompt_text.pack(fill=tk.X, pady=(4, 9))
        self._style_text(self.user_prompt_text)
        ttk.Label(prompt_tab, text="系统提示词模板").pack(anchor=tk.W)
        self.system_prompt_text = scrolledtext.ScrolledText(
            prompt_tab, wrap=tk.WORD, font=("Microsoft YaHei UI", 9)
        )
        self.system_prompt_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._style_text(self.system_prompt_text)

        self.log_text = scrolledtext.ScrolledText(log_tab, width=34, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.log_text)
        self.export_menu = export_menu
        self.batch_menu = batch_menu
        for menu in (self.export_menu, self.batch_menu):
            menu.configure(
                background=COLORS["surface_alt"], foreground=COLORS["text"],
                activebackground="#20343e", activeforeground=COLORS["text"],
                borderwidth=1,
            )

    def _build_launch_view(self) -> None:
        asset = resource_path("assets/launch-im-aios.jpg")
        try:
            with Image.open(asset) as source:
                self._launch_source = source.convert("RGB")
        except (OSError, ValueError):
            self._launch_source = None
        try:
            with Image.open(resource_path("assets/launch-qianyi.png")) as source:
                self._project_banner_source = source.convert("RGB")
        except (OSError, ValueError):
            self._project_banner_source = None
        self.launch_canvas = tk.Canvas(
            self.launch_frame,
            background=TRANSPARENT_KEY,
            borderwidth=0,
            highlightthickness=0,
        )
        self.launch_canvas.pack(fill=tk.BOTH, expand=True)
        self.launch_canvas.bind("<Configure>", self._render_launch)

    def _render_launch(self, event=None) -> None:
        width = max(2, event.width if event else self.launch_canvas.winfo_width())
        height = max(2, event.height if event else self.launch_canvas.winfo_height())
        self.launch_canvas.delete("all")
        if width < 320 or height < 240:
            return
        source_ratio = (
            self._launch_source.width / self._launch_source.height
            if self._launch_source is not None
            else 16 / 9
        )
        image_width = min(width * 0.84, (height - 170) * source_ratio)
        image_height = image_width / source_ratio
        image_left = (width - image_width) / 2
        image_top = 14
        image_right = image_left + image_width
        image_bottom = image_top + image_height
        target_size = (max(2, int(image_width)), max(2, int(image_height)))
        self.launch_canvas.create_rectangle(
            image_left - 7,
            image_top - 7,
            image_right + 7,
            image_bottom + 7,
            fill="#f8f8f4",
            outline="#f8f8f4",
            width=1,
        )
        if self._launch_source is not None:
            if self._launch_photo is None or self._launch_photo_size != target_size:
                rendered = ImageOps.fit(
                    self._launch_source, target_size, Image.Resampling.LANCZOS
                )
                self._launch_photo = ImageTk.PhotoImage(rendered)
                self._launch_photo_size = target_size
            self.launch_canvas.create_image(
                image_left,
                image_top,
                image=self._launch_photo,
                anchor=tk.NW,
            )
        center_x = width / 2
        status_text = "正在启动芊熠智能打标器..."
        status_y = image_bottom + 32
        self.launch_canvas.create_text(
            center_x + 2, status_y + 2, text=status_text,
            fill="#05070a", anchor=tk.CENTER, font=("Microsoft YaHei UI", 14, "bold"),
        )
        self.launch_canvas.create_text(
            center_x, status_y, text=status_text,
            fill="#fffaf5", anchor=tk.CENTER, font=("Microsoft YaHei UI", 14, "bold"),
        )
        percent_y = image_bottom + 68
        self.launch_canvas.create_text(
            center_x + 1, percent_y + 1, text=f"{self.launch_progress}%",
            fill="#06080b", anchor=tk.CENTER, font=("Segoe UI", 12, "bold"),
        )
        self.launch_canvas.create_text(
            center_x, percent_y, text=f"{self.launch_progress}%",
            fill="#fffaf5", anchor=tk.CENTER, font=("Segoe UI", 12, "bold"),
        )

        track_left = width * 0.25
        track_right = width * 0.75
        track_top = image_bottom + 91
        track_bottom = track_top + 30
        radius = (track_bottom - track_top) / 2

        def capsule_outline(x0, y0, x1, y1, color, line_width=1):
            capsule_radius = (y1 - y0) / 2
            self.launch_canvas.create_arc(
                x0, y0, x0 + capsule_radius * 2, y1,
                start=90, extent=180, style=tk.ARC, outline=color, width=line_width,
            )
            self.launch_canvas.create_arc(
                x1 - capsule_radius * 2, y0, x1, y1,
                start=270, extent=180, style=tk.ARC, outline=color, width=line_width,
            )
            self.launch_canvas.create_line(
                x0 + capsule_radius, y0, x1 - capsule_radius, y0,
                fill=color, width=line_width,
            )
            self.launch_canvas.create_line(
                x0 + capsule_radius, y1, x1 - capsule_radius, y1,
                fill=color, width=line_width,
            )

        capsule_outline(
            track_left + 2, track_top + 2, track_right + 2, track_bottom + 2,
            "#05070a", 3,
        )
        capsule_outline(
            track_left, track_top, track_right, track_bottom,
            "#f4eee8", 2,
        )

        inner_top = track_top + 6
        inner_bottom = track_bottom - 6
        inner_left = track_left + 6
        inner_right = track_right - 6
        active_right = inner_left + (inner_right - inner_left) * self.launch_progress / 100
        if self.launch_progress > 0:
            inner_height = inner_bottom - inner_top
            progress_right = max(inner_left + inner_height, active_right)
            capsule_outline(
                inner_left,
                inner_top,
                min(inner_right, progress_right),
                inner_bottom,
                "#5be0e6",
                2,
            )
            self.launch_canvas.create_oval(
                min(inner_right, progress_right) - 4,
                (inner_top + inner_bottom) / 2 - 4,
                min(inner_right, progress_right) + 4,
                (inner_top + inner_bottom) / 2 + 4,
                fill="",
                outline="#f5a06f",
                width=2,
            )

    def _build_project_center(self) -> None:
        self.project_banner = tk.Canvas(
            self.project_center_frame,
            height=176,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.project_banner.pack(fill=tk.X)
        self.project_banner.bind("<Configure>", self._render_project_banner)

        center_body = ttk.Frame(self.project_center_frame, padding=(18, 14, 18, 18))
        center_body.pack(fill=tk.BOTH, expand=True)
        heading = ttk.Frame(center_body)
        heading.pack(fill=tk.X, pady=(0, 10))
        title_block = ttk.Frame(heading)
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="项目中心", style="CenterTitle.TLabel").pack(anchor=tk.W)
        self.project_count_var = tk.StringVar(value="最近项目 0")
        ttk.Label(title_block, textvariable=self.project_count_var, style="Muted.TLabel").pack(anchor=tk.W, pady=(3, 0))
        ttk.Button(heading, text="设置", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Button(heading, text="删除项目", style="Danger.TButton", command=self.delete_selected_project).pack(side=tk.RIGHT, padx=(0, 7))
        ttk.Button(heading, text="打开项目", command=self.continue_selected_project).pack(side=tk.RIGHT, padx=(0, 7))
        self.return_project_button = ttk.Button(
            heading,
            text="返回当前项目",
            command=self.return_to_current_project,
            state=tk.DISABLED,
        )
        self.return_project_button.pack(side=tk.RIGHT, padx=(0, 7))
        ttk.Button(heading, text="添加项目", style="Primary.TButton", command=self.add_project).pack(side=tk.RIGHT, padx=(0, 7))

        project_panel = ttk.Frame(center_body, style="Surface.TFrame", padding=1)
        project_panel.pack(fill=tk.BOTH, expand=True)
        columns = ("project", "path", "state", "progress", "updated")
        self.project_tree = ttk.Treeview(project_panel, columns=columns, show="headings", selectmode="browse")
        self.project_tree.heading("project", text="项目")
        self.project_tree.heading("path", text="目录")
        self.project_tree.heading("state", text="状态")
        self.project_tree.heading("progress", text="进度")
        self.project_tree.heading("updated", text="最近运行")
        self.project_tree.column("project", width=150, minwidth=115, stretch=False)
        self.project_tree.column("path", width=300, minwidth=200)
        self.project_tree.column("state", width=100, minwidth=88, anchor=tk.CENTER, stretch=False)
        self.project_tree.column("progress", width=95, minwidth=88, anchor=tk.CENTER, stretch=False)
        self.project_tree.column("updated", width=138, minwidth=128, anchor=tk.CENTER, stretch=False)
        project_scroll = ttk.Scrollbar(project_panel, orient=tk.VERTICAL, command=self.project_tree.yview)
        self.project_tree.configure(yscrollcommand=project_scroll.set)
        self.project_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        project_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.project_tree.bind("<Double-1>", self._project_double_clicked)

        footer = ttk.Frame(center_body)
        footer.pack(fill=tk.X, pady=(9, 0))
        ttk.Label(
            footer,
            text="项目记录保存在本机应用目录；删除项目不会删除媒体文件。",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(footer, text="READY / LOCAL", style="Brand.TLabel").pack(side=tk.RIGHT)

    def _compose_project_banner(self, width: int, height: int) -> Image.Image | None:
        if self._project_banner_source is None:
            return None
        source = self._project_banner_source
        background_color = COLORS["bg"]
        background_crop = source.crop((0, 0, round(source.width * 0.575), source.height))
        rendered = ImageOps.fit(
            background_crop,
            (width, height),
            Image.Resampling.LANCZOS,
            centering=(0.45, 0.28),
        )
        rendered = Image.blend(
            rendered,
            Image.new("RGB", rendered.size, background_color),
            0.38,
        )

        portrait_crop = source.crop((
            round(source.width * 0.5625),
            round(source.height * 0.061),
            round(source.width * 0.919),
            round(source.height * 0.633),
        ))
        portrait_height = round(height * 1.29)
        portrait_width = round(
            portrait_crop.width * portrait_height / portrait_crop.height
        )
        portrait = portrait_crop.resize(
            (portrait_width, portrait_height), Image.Resampling.LANCZOS
        )
        portrait = Image.blend(
            portrait,
            Image.new("RGB", portrait.size, background_color),
            0.10,
        )

        edge = max(28, portrait_width // 6)
        edge_mask = Image.new("L", portrait.size, 255)
        edge_draw = ImageDraw.Draw(edge_mask)
        for x in range(edge):
            alpha = round(255 * x / max(1, edge - 1))
            edge_draw.line((x, 0, x, portrait_height), fill=alpha)
            edge_draw.line(
                (portrait_width - 1 - x, 0, portrait_width - 1 - x, portrait_height),
                fill=alpha,
            )
        bottom_mask = Image.new("L", portrait.size, 255)
        bottom_draw = ImageDraw.Draw(bottom_mask)
        portrait_fade_start = round(portrait_height * 0.66)
        for y in range(portrait_fade_start, portrait_height):
            alpha = round(
                255 * (portrait_height - 1 - y)
                / max(1, portrait_height - 1 - portrait_fade_start)
            )
            bottom_draw.line((0, y, portrait_width, y), fill=alpha)
        portrait_mask = ImageChops.darker(edge_mask, bottom_mask)
        portrait_x = round(width * 0.74 - portrait_width / 2)
        portrait_y = round(height * -0.13)
        rendered.paste(portrait, (portrait_x, portrait_y), portrait_mask)

        fade_mask = Image.new("L", (width, height), 0)
        fade_draw = ImageDraw.Draw(fade_mask)
        fade_start = round(height * 0.81)
        for y in range(fade_start, height):
            alpha = round(
                255 * (y - fade_start) / max(1, height - 1 - fade_start)
            )
            fade_draw.line((0, y, width, y), fill=alpha)
        rendered.paste(
            Image.new("RGB", rendered.size, background_color),
            (0, 0),
            fade_mask,
        )
        return rendered

    def _render_project_banner(self, event=None) -> None:
        width = max(2, event.width if event else self.project_banner.winfo_width())
        height = max(176, min(240, round(self.root.winfo_height() * 0.18)))
        if int(float(self.project_banner.cget("height"))) != height:
            self.project_banner.configure(height=height)
        self.project_banner.delete("all")
        rendered = self._compose_project_banner(width, height)
        if rendered is not None:
            self._project_banner_photo = ImageTk.PhotoImage(rendered)
            self.project_banner.create_image(
                0, 0, image=self._project_banner_photo, anchor=tk.NW
            )
        self.project_banner.create_text(
            24, 82, text="从项目开始，让每次标注都可继续。", anchor=tk.W,
            fill=COLORS["text"], font=("Microsoft YaHei UI", 22, "bold"),
        )
        self.project_banner.create_text(
            24, 124, text="最近状态、处理进度和素材目录集中在同一视图。", anchor=tk.W,
            fill=COLORS["muted"], font=("Microsoft YaHei UI", 10),
        )

    def _hide_views(self) -> None:
        for frame in (self.launch_frame, self.project_center_frame, self.workspace_frame):
            frame.pack_forget()

    def _use_transparent_launch_window(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.state("normal")
            self.root.overrideredirect(True)
            self.root.geometry(
                f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0"
            )
            self.root.configure(background=TRANSPARENT_KEY)
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
            self.root.wm_attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass

    def _restore_main_window(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.wm_attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        try:
            self.root.overrideredirect(False)
            self.root.wm_attributes("-topmost", False)
            self.root.configure(background=COLORS["bg"])
            self.root.state("normal")
            self.root.geometry(self.normal_geometry)
            maximize_main_window(self.root)
        except tk.TclError:
            pass

    def show_launch(self) -> None:
        self._hide_views()
        self._use_transparent_launch_window()
        self.launch_frame.pack(fill=tk.BOTH, expand=True)
        if self.splash_after_id is not None:
            try:
                self.root.after_cancel(self.splash_after_id)
            except tk.TclError:
                pass
        self.launch_progress = 0
        self._render_launch()
        self.splash_after_id = self.root.after(32, self._advance_launch_progress)

    def _advance_launch_progress(self) -> None:
        self.splash_after_id = None
        if self.closing or self.launch_frame.winfo_manager() != "pack":
            return
        self.launch_progress = min(100, self.launch_progress + 2)
        self._render_launch()
        if self.launch_progress >= 100:
            self.show_project_center()
            return
        self.splash_after_id = self.root.after(32, self._advance_launch_progress)

    def show_project_center(self) -> None:
        if self.splash_after_id is not None:
            try:
                self.root.after_cancel(self.splash_after_id)
            except tk.TclError:
                pass
            self.splash_after_id = None
        self._hide_views()
        self._restore_main_window()
        self.project_center_frame.pack(fill=tk.BOTH, expand=True)
        self.refresh_project_center()

    def show_workspace(self) -> None:
        self._hide_views()
        self._restore_main_window()
        self.workspace_frame.pack(fill=tk.BOTH, expand=True)
        if self.sash_after_id is not None:
            try:
                self.root.after_cancel(self.sash_after_id)
            except tk.TclError:
                pass
        self.sash_after_id = self.root.after(120, self._set_initial_sash)

    @staticmethod
    def _folder_key(folder: str | Path) -> str:
        try:
            return str(Path(folder).expanduser().resolve()).casefold()
        except (OSError, RuntimeError):
            return str(folder).casefold()

    def refresh_project_center(self) -> None:
        selected_key = None
        selection = self.project_tree.selection()
        if selection and selection[0] in self.project_paths:
            selected_key = self._folder_key(self.project_paths[selection[0]])
        elif self.workspace_project is not None:
            selected_key = self._folder_key(self.workspace_project)
        for row in self.project_tree.get_children():
            self.project_tree.delete(row)
        self.project_paths.clear()

        values = list(self.settings.get("recent_folders") or [])
        last_folder = str(self.settings.get("last_folder") or "").strip()
        if last_folder:
            values.insert(0, last_folder)
        folders = []
        seen = set()
        for value in values:
            key = self._folder_key(value)
            if not value or key in seen:
                continue
            seen.add(key)
            folders.append(Path(value))

        status_text = {
            "new": "尚未运行",
            "running": "处理中",
            "completed": "已完成",
            "stopped": "已停止",
            "failed": "有失败项",
        }
        selected_row = None
        for folder in folders:
            summary = load_project_summary(folder)
            total = summary["total"]
            completed = summary["success"] + summary["skipped"]
            progress = f"{completed}/{total}" if total else "尚未运行"
            state = status_text.get(summary["status"], summary["status"] or "尚未运行")
            if not summary["exists"]:
                state = "目录不可用"
            updated = summary["updated_at"].replace("T", " ")[:16] or "—"
            row = self.project_tree.insert(
                "",
                tk.END,
                values=(summary["name"], str(folder), state, progress, updated),
                tags=("missing",) if not summary["exists"] else (),
            )
            self.project_paths[row] = folder
            if self._folder_key(folder) == selected_key:
                selected_row = row
        self.project_tree.tag_configure("missing", foreground=COLORS["warning"])
        self.project_count_var.set(f"最近项目 {len(folders)}")
        current_available = bool(
            self.workspace_project is not None and self.workspace_project.is_dir()
        )
        self.return_project_button.configure(
            state=tk.NORMAL if current_available else tk.DISABLED
        )
        if selected_row:
            self.project_tree.selection_set(selected_row)
            self.project_tree.focus(selected_row)

    def _selected_project_folder(self) -> Path | None:
        selection = self.project_tree.selection()
        if not selection:
            return None
        return self.project_paths.get(selection[0])

    def add_project(self) -> None:
        selected = filedialog.askdirectory(
            title="添加媒体项目目录",
            initialdir=self.settings.get("last_folder") or None,
            parent=self.root,
        )
        if selected:
            self.open_project(Path(selected))

    def continue_selected_project(self) -> None:
        folder = self._selected_project_folder()
        if folder is None:
            messagebox.showinfo("项目中心", "请先选择一个项目", parent=self.root)
            return
        self.open_project(folder)

    def _project_double_clicked(self, event) -> None:
        row = self.project_tree.identify_row(event.y)
        if not row or row not in self.project_paths:
            return
        self.project_tree.selection_set(row)
        self.project_tree.focus(row)
        self.continue_selected_project()

    def return_to_current_project(self) -> bool:
        if self.workspace_project is None or not self.workspace_project.is_dir():
            messagebox.showinfo("项目中心", "当前没有可返回的项目", parent=self.root)
            return False
        self.folder_var.set(str(self.workspace_project))
        self.show_workspace()
        return True

    def open_project(self, folder: Path) -> bool:
        folder = Path(folder)
        is_current = bool(
            self.workspace_project is not None
            and self._folder_key(self.workspace_project) == self._folder_key(folder)
        )
        if is_current:
            return self.return_to_current_project()
        if self.runner and self.runner.running:
            messagebox.showwarning("切换项目", "任务运行时不能切换项目，请先停止任务", parent=self.root)
            return False
        if not folder.is_dir():
            messagebox.showwarning("项目不可用", "项目目录不存在或无法访问", parent=self.root)
            return False
        # Invalidate a scan that may still be finishing for the previous project.
        self.scan_generation += 1
        self.workspace_project = folder
        self.folder_var.set(str(folder))
        self._save_workspace_settings()
        self.show_workspace()
        self.scan_project()
        return True

    def delete_selected_project(self) -> None:
        folder = self._selected_project_folder()
        if folder is None:
            messagebox.showinfo("删除项目", "请先选择要删除的项目", parent=self.root)
            return
        self.delete_project(folder)

    def delete_project(self, folder: Path, confirm: bool = True) -> bool:
        folder = Path(folder)
        current_text = self.folder_var.get().strip()
        is_current = bool(
            self.workspace_project is not None
            and self._folder_key(self.workspace_project) == self._folder_key(folder)
        ) or bool(
            current_text and self._folder_key(current_text) == self._folder_key(folder)
        )
        if is_current and self.runner and self.runner.running:
            messagebox.showwarning("删除项目", "当前项目正在运行，请先停止任务", parent=self.root)
            return False
        if confirm and not messagebox.askyesno(
            "删除项目",
            f"确定从项目中心移除“{folder.name or folder}”吗？\n\n"
            "只移除项目记录和应用缓存，不删除任何媒体文件。",
            parent=self.root,
        ):
            return False
        try:
            delete_project_metadata(folder)
        except (OSError, ValueError) as error:
            messagebox.showerror("删除失败", f"无法清理项目缓存：{error}", parent=self.root)
            return False

        deleted_key = self._folder_key(folder)
        recent = [
            value for value in self.settings.get("recent_folders", [])
            if self._folder_key(value) != deleted_key
        ]
        self.settings["recent_folders"] = recent
        last_folder = str(self.settings.get("last_folder", "") or "").strip()
        if last_folder and self._folder_key(last_folder) == deleted_key:
            self.settings["last_folder"] = recent[0] if recent else ""
        if is_current:
            self.folder_var.set("")
            self._clear_workspace()
        self.folder_box["values"] = recent
        self.settings_store.save(self.settings)
        self.refresh_project_center()
        return True

    def _clear_workspace(self) -> None:
        self.scan_generation += 1
        self.workspace_project = None
        self._clear_items()
        self.gallery.set_items([], set())
        self.last_failed_paths.clear()
        self.preview_image = None
        self.preview_label.configure(image="", text="选择任务项")
        self.result_text.delete("1.0", tk.END)
        self.log_text.delete("1.0", tk.END)
        self.progress_var.set(0)
        self.progress_text_var.set("就绪")
        self._set_stage(1)
        self._restore_idle_controls()

    def _style_text(self, widget) -> None:
        widget.configure(
            background=COLORS["surface"], foreground=COLORS["text"],
            insertbackground=COLORS["text"], selectbackground="#34506a",
            relief=tk.FLAT, borderwidth=1, highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["info"],
        )
        if hasattr(widget, "vbar"):
            widget.vbar.configure(
                background=COLORS["surface_alt"], troughcolor=COLORS["bg"],
                activebackground="#20343e", borderwidth=0,
            )

    def _set_stage(self, active: int) -> None:
        for index, label in enumerate(self.stage_labels, start=1):
            label.configure(style="StageActive.TLabel" if index == active else "Stage.TLabel")

    def _switch_view(self) -> None:
        self.table_frame.pack_forget()
        self.gallery.pack_forget()
        if self.view_mode_var.get() == "gallery":
            self.gallery.pack(fill=tk.BOTH, expand=True)
        else:
            self.table_frame.pack(fill=tk.BOTH, expand=True)
        self.refresh_table()

    def _filter_changed(self, _event=None) -> None:
        if FILTERS.get(self.filter_var.get()) == "orphan_caption":
            self.view_mode_var.set("list")
            self._switch_view()
            return
        self.refresh_table()

    def _start_thumbnail_workers(self) -> None:
        for index in range(2):
            threading.Thread(
                target=self._thumbnail_worker,
                daemon=True,
                name=f"thumbnail-worker-{index}",
            ).start()

    def _request_thumbnail(self, path: Path) -> None:
        key = str(path)
        if key in self.thumbnail_cache or key in self.thumbnail_pending:
            return
        self.thumbnail_pending.add(key)
        try:
            self.thumbnail_jobs.put_nowait(path)
        except queue.Full:
            self.thumbnail_pending.discard(key)

    def _thumbnail_worker(self) -> None:
        while True:
            path = self.thumbnail_jobs.get()
            try:
                canvas = Image.new("RGB", (160, 120), "#0b0d0b")
                if path.suffix.casefold() in {".mp4", ".mov", ".avi"}:
                    draw = ImageDraw.Draw(canvas)
                    label = path.suffix.upper().lstrip(".") or "VIDEO"
                    draw.rounded_rectangle((38, 30, 122, 90), radius=7, outline="#4f5a4f", width=2)
                    draw.text((61, 53), label, fill="#aeb6ad")
                else:
                    source = open_image(path)
                    try:
                        fitted = ImageOps.contain(source, (160, 120), Image.Resampling.LANCZOS)
                        canvas.paste(fitted, ((160 - fitted.width) // 2, (120 - fitted.height) // 2))
                    finally:
                        source.close()
                self._post_event("thumbnail", {"path": path, "image": canvas})
            except (OSError, RuntimeError, ValueError):
                placeholder = Image.new("RGB", (160, 120), "#271818")
                ImageDraw.Draw(placeholder).text((64, 53), "ERR", fill="#ff8b8b")
                self._post_event("thumbnail", {"path": path, "image": placeholder})
            finally:
                self.thumbnail_jobs.task_done()

    def _set_initial_sash(self) -> None:
        self.sash_after_id = None
        width = self.workspace.winfo_width()
        if width > 100:
            self.workspace.sashpos(0, int(width * 0.62))

    def _load_values(self) -> None:
        self.folder_var.set(self.settings.get("last_folder", ""))
        self.folder_box["values"] = self.settings.get("recent_folders", [])
        self.concurrency_var.set(
            max(
                1,
                min(
                    MAX_CONCURRENCY,
                    int(self.settings.get("concurrency", 3)),
                ),
            )
        )
        self.skip_var.set(bool(self.settings.get("skip_existing", True)))
        self.media_mode_var.set(self.settings.get("media_mode", "image"))
        self.caption_style_var.set(self.settings.get("caption_style", "natural"))
        self.view_mode_var.set(self.settings.get("view_mode", "gallery"))
        self.subject_filter_var.set(self.settings.get("subject_filter", ""))
        self.backend_var.set(self.settings.get("backend", "api"))
        self.local_model_var.set(self.settings.get("local_model_folder", ""))
        focus = self.settings.get("labeling_focus", "subject")
        self.focus_label_var.set(FOCUS_LABELS.get(focus, "训练主体"))
        self.output_language_var.set(self.settings.get("output_language", "zh"))
        self.trigger_word_var.set(self.settings.get("trigger_word", ""))
        model = MODELS.get(self.settings.get("model_key"), MODELS[DEFAULT_MODEL_KEY])
        self.model_label_var.set(model.label)
        self.billing_var.set(model.billing_label())
        presets = self.settings["prompt_presets"]
        self.preset_box["values"] = list(presets)
        selected = self.settings.get("selected_preset")
        if selected not in presets:
            selected = next(iter(presets))
        self.preset_var.set(selected)
        self._set_system_prompt(presets[selected])
        self.user_prompt_text.insert("1.0", self.settings.get("user_prompt", ""))
        self._backend_changed()
        self._switch_view()

    def _model_key(self) -> str:
        label = self.model_label_var.get()
        return next((key for key, model in MODELS.items() if model.label == label), DEFAULT_MODEL_KEY)

    def _model_changed(self, _event=None) -> None:
        model = MODELS[self._model_key()]
        if self.backend_var.get() == "api":
            self.billing_var.set(model.billing_label())

    def _backend_changed(self) -> None:
        is_local = self.backend_var.get() == "local"
        self.model_box.configure(state=tk.DISABLED if is_local else "readonly")
        self.concurrency_box.configure(state=tk.DISABLED if is_local else tk.NORMAL)
        self.local_model_entry.configure(state=tk.NORMAL if is_local else tk.DISABLED)
        self.local_model_button.configure(state=tk.NORMAL if is_local else tk.DISABLED)
        if is_local:
            self.concurrency_var.set(1)
            self.billing_var.set("本地 / 不计费")
        else:
            self.billing_var.set(MODELS[self._model_key()].billing_label())

    def browse_local_model(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Hugging Face 视觉语言模型目录",
            initialdir=self.local_model_var.get() or None,
            parent=self.root,
        )
        if selected:
            self.local_model_var.set(selected)

    def _set_system_prompt(self, value: str) -> None:
        self.system_prompt_text.delete("1.0", tk.END)
        self.system_prompt_text.insert("1.0", value)

    def browse_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择媒体项目目录", initialdir=self.folder_var.get() or None)
        if selected:
            self.open_project(Path(selected))

    def apply_preset(self, _event=None) -> None:
        prompt = self.settings["prompt_presets"].get(self.preset_var.get())
        if prompt is not None:
            self._set_system_prompt(prompt)

    def import_prompt_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="导入系统提示词模板",
            filetypes=[("提示词文本", "*.txt *.md"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        try:
            prompt = Path(selected).read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as error:
            messagebox.showerror("导入失败", str(error), parent=self.root)
            return
        if not prompt:
            messagebox.showwarning("提示", "提示词文件为空", parent=self.root)
            return
        self._set_system_prompt(prompt)
        self.log(f"已导入系统提示词模板：{selected}")

    def save_preset(self) -> None:
        name = simpledialog.askstring("保存提示词预设", "预设名称", parent=self.root)
        prompt = self.system_prompt_text.get("1.0", tk.END).strip()
        if not name or not prompt:
            return
        self.settings["prompt_presets"][name] = prompt
        self.settings["selected_preset"] = name
        self.preset_box["values"] = list(self.settings["prompt_presets"])
        self.preset_var.set(name)
        self.settings_store.save(self.settings)

    def delete_preset(self) -> None:
        name = self.preset_var.get()
        if name in DEFAULT_PRESETS:
            messagebox.showinfo("提示", "内置预设不能删除", parent=self.root)
            return
        self.settings["prompt_presets"].pop(name, None)
        selected = next(iter(self.settings["prompt_presets"]))
        self.settings["selected_preset"] = selected
        self.preset_box["values"] = list(self.settings["prompt_presets"])
        self.preset_var.set(selected)
        self.apply_preset()
        self.settings_store.save(self.settings)

    def open_settings(self) -> tk.Toplevel:
        dialog = tk.Toplevel(self.root)
        dialog.title("安全设置")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="火山方舟 API Key").grid(row=0, column=0, sticky=tk.W)
        has_stored_key = bool(self.settings_store.get_api_key())
        masked_placeholder = "*" * 16 if has_stored_key else ""
        api_key_var = tk.StringVar(value=masked_placeholder)
        entry = ttk.Entry(frame, textvariable=api_key_var, width=58, show="•")
        entry.grid(row=0, column=1, padx=(10, 0), sticky=tk.EW)
        stored_var = tk.StringVar(value="已安全保存（脱敏显示）" if has_stored_key else "未设置")
        ttk.Label(frame, textvariable=stored_var).grid(row=1, column=1, sticky=tk.W, pady=(6, 0))
        ttk.Separator(frame).grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=12)

        def clear_key() -> None:
            if messagebox.askyesno("清除 API Key", "确定清除已保存的 API Key？", parent=dialog):
                try:
                    self.settings_store.set_api_key("")
                except OSError as error:
                    messagebox.showerror("清除失败", str(error), parent=dialog)
                    return
                api_key_var.set("")
                stored_var.set("未设置")

        def save() -> None:
            value = api_key_var.get().strip()
            try:
                if not (has_stored_key and value == masked_placeholder):
                    self.settings_store.set_api_key(value)
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=dialog)
                return
            self.settings_store.save(self.settings)
            dialog.destroy()
            self.log("设置已保存")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky=tk.E, pady=(2, 0))
        ttk.Button(buttons, text="清除密钥", command=clear_key).pack(side=tk.LEFT)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(buttons, text="保存", command=save).pack(side=tk.LEFT, padx=(6, 0))
        center_dialog(dialog, self.root)
        dialog.grab_set()
        dialog.lift()
        entry.focus_set()
        if masked_placeholder:
            entry.selection_range(0, tk.END)
        return dialog

    def _save_workspace_settings(self) -> None:
        folder = self.folder_var.get().strip()
        recent = [folder] + [value for value in self.settings.get("recent_folders", []) if value != folder] if folder else self.settings.get("recent_folders", [])
        self.settings.update({
            "last_folder": folder,
            "recent_folders": recent[:10],
            "concurrency": max(
                1, min(MAX_CONCURRENCY, int(self.concurrency_var.get()))
            ),
            "skip_existing": bool(self.skip_var.get()),
            "media_mode": self.media_mode_var.get(),
            "caption_style": self.caption_style_var.get(),
            "view_mode": self.view_mode_var.get(),
            "subject_filter": self.subject_filter_var.get().strip(),
            "backend": self.backend_var.get(),
            "local_model_folder": self.local_model_var.get().strip(),
            "labeling_focus": FOCUS_OPTIONS.get(
                self.focus_label_var.get(), "subject"
            ),
            "output_language": self.output_language_var.get(),
            "trigger_word": self.trigger_word_var.get().strip(),
            "user_prompt": self.user_prompt_text.get("1.0", tk.END).strip(),
            "model_key": self._model_key(),
            "selected_preset": self.preset_var.get(),
        })
        self.settings_store.save(self.settings)
        self.folder_box["values"] = self.settings["recent_folders"]
        self.refresh_project_center()

    def _post_event(self, kind: str, payload: dict) -> None:
        try:
            self.events.put_nowait((kind, payload))
        except queue.Full:
            if kind != "log":
                self.events.put((kind, payload), timeout=1)

    def scan_project(self) -> None:
        if self.controller_thread and self.controller_thread.is_alive():
            return
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            messagebox.showwarning("提示", "请选择有效的项目目录", parent=self.root)
            return
        mode = self.media_mode_var.get()
        self.scan_generation += 1
        scan_generation = self.scan_generation
        self._set_stage(1)
        self.progress_text_var.set("正在扫描...")
        self.log(f"扫描项目：{folder}")

        def scan() -> None:
            result = scan_media(folder, mode)
            self._post_event("scan", {"result": result, "scan_generation": scan_generation})
            self._post_event("scan_done", {"folder": folder, "scan_generation": scan_generation})

        self.controller_thread = threading.Thread(target=scan, daemon=True, name="media-scan")
        self.controller_thread.start()

    def _validate_task(self) -> tuple[Path, str, str] | None:
        folder = Path(self.folder_var.get().strip())
        system_prompt = self.system_prompt_text.get("1.0", tk.END).strip()
        user_prompt = self.user_prompt_text.get("1.0", tk.END).strip()
        api_key = self.settings_store.get_api_key()
        if not folder.is_dir():
            messagebox.showwarning("提示", "请选择有效的项目目录", parent=self.root)
            return None
        if not system_prompt:
            messagebox.showwarning("提示", "系统提示词模板不能为空", parent=self.root)
            return None
        prompt = system_prompt
        if user_prompt:
            prompt = f"{system_prompt}\n\n## 用户要求\n{user_prompt}"
        backend = self.backend_var.get()
        if backend == "local":
            model_folder = Path(self.local_model_var.get().strip())
            if self.media_mode_var.get() != "image":
                messagebox.showwarning(
                    "本地模型",
                    "本地模型后端当前只支持图片；视频请选择外部 API",
                    parent=self.root,
                )
                return None
            if not model_folder.is_dir() or not (model_folder / "config.json").is_file():
                messagebox.showwarning(
                    "本地模型",
                    "请选择包含 config.json 的 Hugging Face 视觉语言模型目录",
                    parent=self.root,
                )
                return None
            api_key = ""
        elif not api_key:
            messagebox.showwarning("提示", "请先在设置中填写 API Key", parent=self.root)
            return None
        return folder, prompt, api_key

    def start_task(self, only_paths: list[Path] | None = None, force: bool = False) -> None:
        if self.runner and self.runner.running:
            return
        validated = self._validate_task()
        if not validated:
            return
        folder, prompt, api_key = validated
        try:
            concurrency = max(
                1, min(MAX_CONCURRENCY, int(self.concurrency_var.get()))
            )
        except (TypeError, ValueError, tk.TclError):
            concurrency = 3
            self.concurrency_var.set(3)
        mode = self.media_mode_var.get()
        model_key = self._model_key()
        caption_style = self.caption_style_var.get()
        subject_filter = self.subject_filter_var.get().strip()
        backend = self.backend_var.get()
        local_model_folder = self.local_model_var.get().strip()
        labeling_focus = FOCUS_OPTIONS.get(self.focus_label_var.get(), "subject")
        output_language = self.output_language_var.get()
        trigger_word = self.trigger_word_var.get().strip()
        skip_existing = bool(self.skip_var.get()) and not force
        self._save_workspace_settings()
        self.runner = BatchRunner(self._post_event)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.retry_button.config(state=tk.DISABLED)
        self.selected_button.config(state=tk.DISABLED)
        self.batch_button.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self._set_stage(2)
        self.progress_text_var.set("准备任务...")
        if backend == "local":
            self.log(f"开始任务：本地模型 {Path(local_model_folder).name} / 单并发")
        else:
            self.log(
                f"开始任务：{MODELS[model_key].label} / "
                f"{MODELS[model_key].billing_label()}"
            )
        self.log(
            f"打标策略：{self.focus_label_var.get()} / "
            f"{'中文' if output_language == 'zh' else 'English'}"
            f"{(' / 触发词 ' + trigger_word) if trigger_word else ''}"
        )

        def run() -> None:
            assert self.runner is not None
            self.runner.run(
                folder=folder,
                mode=mode,
                prompt=prompt,
                model_key=model_key,
                api_key=api_key,
                concurrency=concurrency,
                skip_existing=skip_existing,
                caption_style=caption_style,
                subject_filter=subject_filter,
                backend=backend,
                local_model_folder=local_model_folder,
                labeling_focus=labeling_focus,
                output_language=output_language,
                trigger_word=trigger_word,
                only_paths=only_paths,
            )

        self.controller_thread = threading.Thread(target=run, daemon=True, name="batch-controller")
        self.controller_thread.start()

    def stop_task(self) -> None:
        if self.runner:
            self.runner.cancel()
        if self.analysis_token:
            self.analysis_token.cancel()
        self.stop_button.config(state=tk.DISABLED)
        self.progress_text_var.set("正在停止...")
        self.log("已请求停止，当前请求将被关闭或在超时边界内结束")

    def find_similar(self) -> None:
        if self.controller_thread and self.controller_thread.is_alive():
            return
        paths = [
            item["path"] for item in self.items.values()
            if item["path"].suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
        ]
        if len(paths) < 2:
            messagebox.showinfo("相似图", "项目中没有足够的图片", parent=self.root)
            return
        self.analysis_token = CancellationToken()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.selected_button.config(state=tk.DISABLED)
        self.batch_button.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_text_var.set("正在查找相似图...")
        self._set_stage(1)
        self.log(f"开始离线相似图检测：{len(paths)} 张")

        def progress(completed: int, total: int) -> None:
            self._post_event("analysis_progress", {"completed": completed, "total": total})

        def run() -> None:
            try:
                groups = find_similar_images(
                    paths, threshold=5, token=self.analysis_token, progress=progress
                )
            except CancelledError:
                self._post_event("similar_cancelled", {})
            except Exception as error:
                self._post_event("similar_error", {"error": str(error)})
            else:
                self._post_event("similar_done", {"groups": groups})

        self.controller_thread = threading.Thread(
            target=run, daemon=True, name="similarity-analysis"
        )
        self.controller_thread.start()

    def retry_failed(self) -> None:
        paths = [path for path in self.last_failed_paths if path.exists()]
        if not paths:
            messagebox.showinfo("提示", "没有可重试的失败项", parent=self.root)
            return
        self.start_task(paths, force=True)

    @staticmethod
    def _is_media_path(path: Path) -> bool:
        return path.suffix.casefold() in {
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif",
            ".mp4", ".mov", ".avi",
        }

    def process_selected(self) -> None:
        paths = [
            Path(value)
            for value in self.selected_paths
            if Path(value).is_file() and self._is_media_path(Path(value))
        ]
        if not paths:
            messagebox.showinfo("提示", "请先选择要处理的素材", parent=self.root)
            return
        self.start_task(paths, force=True)

    def process_missing_captions(self) -> None:
        paths = [
            item["path"]
            for item in self.items.values()
            if not item.get("caption_exists", False)
            and item["status"] != "failed"
            and self._is_media_path(item["path"])
        ]
        if not paths:
            messagebox.showinfo("缺少 TXT", "当前项目没有缺少 TXT 的可处理素材", parent=self.root)
            return
        self.start_task(paths, force=True)

    def open_selected_location(self) -> None:
        if len(self.selected_paths) != 1:
            messagebox.showinfo(
                "打开文件位置", "请先选择一个文件。", parent=self.root
            )
            return
        path = Path(next(iter(self.selected_paths)))
        if not path.is_file():
            messagebox.showwarning(
                "打开文件位置", "所选文件已不存在。", parent=self.root
            )
            return
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(path.resolve())])
        except OSError as error:
            messagebox.showerror("打开失败", str(error), parent=self.root)

    def delete_selected_orphan_captions(self) -> None:
        root = Path(self.folder_var.get().strip()).resolve()
        candidates: list[Path] = []
        for value in sorted(self.selected_paths):
            item = self.items.get(value, {})
            path = Path(value)
            try:
                inside_project = path.resolve().is_relative_to(root)
            except (OSError, RuntimeError):
                inside_project = False
            if (
                item.get("is_orphan", False)
                and path.suffix.casefold() == ".txt"
                and path.is_file()
                and inside_project
            ):
                candidates.append(path)
        if not candidates:
            messagebox.showinfo(
                "删除孤立 TXT", "请先在“孤立 TXT”筛选中选择文件。", parent=self.root
            )
            return
        if not messagebox.askyesno(
            "确认删除孤立 TXT",
            f"将永久删除 {len(candidates)} 个没有对应媒体的 TXT 文件，是否继续？",
            parent=self.root,
        ):
            return
        deleted = 0
        failures = []
        for path in candidates:
            try:
                path.unlink()
            except OSError as error:
                failures.append(f"{self._relative_path(path)}：{error}")
                continue
            key = str(path)
            self.items.pop(key, None)
            self.selected_paths.discard(key)
            deleted += 1
        deleted_keys = {str(path) for path in candidates if not path.exists()}
        self.orphan_caption_paths = [
            path for path in self.orphan_caption_paths if str(path) not in deleted_keys
        ]
        self.refresh_table()
        self._update_stats()
        self.log(f"已删除孤立 TXT：{deleted} 个")
        if failures:
            messagebox.showwarning(
                "部分删除失败", "\n".join(failures[:10]), parent=self.root
            )

    def select_all_visible(self, _event=None):
        visible = [item for item in self.items.values() if self._visible(item)]
        self.selected_paths = {str(item["path"]) for item in visible}
        self._sync_selection_widgets()
        self._display_selection()
        return "break"

    def _gallery_selected(self, path: Path, additive: bool) -> None:
        key = str(path)
        if additive:
            if key in self.selected_paths:
                self.selected_paths.remove(key)
            else:
                self.selected_paths.add(key)
        else:
            self.selected_paths = {key}
        self._sync_selection_widgets()
        self._display_selection()

    def _sync_selection_widgets(self) -> None:
        current_rows = set(self.tree.selection())
        target_rows = {
            self.path_rows[key] for key in self.selected_paths if key in self.path_rows
        }
        if current_rows != target_rows:
            self._selection_sync = True
            try:
                remove_rows = current_rows - target_rows
                add_rows = target_rows - current_rows
                if remove_rows:
                    self.tree.selection_remove(*remove_rows)
                if add_rows:
                    self.tree.selection_add(*add_rows)
            finally:
                self._selection_sync = False
        self.gallery.update_selection(self.selected_paths)
        count = len(self.selected_paths)
        self.selection_var.set(f"已选 {count}")
        has_selected_media = any(
            self._is_media_path(Path(value)) for value in self.selected_paths
        )
        self.selected_button.configure(
            state=tk.NORMAL if has_selected_media else tk.DISABLED
        )

    def _clear_items(self) -> None:
        self.items.clear()
        self.selected_paths.clear()
        self.thumbnail_cache.clear()
        self.thumbnail_pending.clear()
        self.similar_paths.clear()
        self.orphan_caption_paths.clear()
        self.row_paths.clear()
        self.path_rows.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.counts = {status: 0 for status in STATUS_TEXT}
        self.counts["total"] = 0
        self._sync_selection_widgets()
        self._update_stats()

    def _handle_scan(self, result: ScanResult) -> None:
        self._clear_items()
        missing = {str(path) for path in result.missing_captions}
        invalid = {str(path) for path in result.invalid_captions}
        self.orphan_caption_paths = list(result.orphan_captions)
        for path in result.files:
            if path in result.conflicts:
                self._set_item(path, "failed", result.conflicts[path])
            elif has_usable_caption(path):
                caption = caption_path_for(path).read_text(encoding="utf-8").strip()
                self._set_item(path, "skipped", caption)
            else:
                detail = (
                    "缺少对应 TXT"
                    if str(path) in missing
                    else "TXT 为空或包含错误信息"
                    if str(path) in invalid
                    else ""
                )
                self._set_item(path, "pending", detail)
        for path, detail in result.unreadable.items():
            self._set_item(path, "failed", detail)
        for path in result.orphan_captions:
            self._set_orphan_item(path)
        self.refresh_table()
        self._update_stats()
        self._set_stage(2)
        self.log(
            f"扫描完成：媒体 {len(result.files) + len(result.unreadable)}，"
            f"不可读 {len(result.unreadable)}，输出冲突 {len(result.conflicts)}，"
            f"缺少 TXT {len(result.missing_captions)}，TXT 无效 {len(result.invalid_captions)}，"
            f"孤立 TXT {len(result.orphan_captions)}，忽略系统目录 {result.ignored_directories}"
        )
        for path in result.orphan_captions[:10]:
            self.log(f"孤立 TXT（无对应媒体）：{self._relative_path(path)}")
        if len(result.orphan_captions) > 10:
            self.log(f"另有 {len(result.orphan_captions) - 10} 个孤立 TXT")

    def _set_item(self, path: Path, status: str, detail: str) -> None:
        key = str(path)
        old = self.items.get(key)
        old_visible = self._visible(old) if old is not None else None
        if old:
            old_status = old["status"]
            self.counts[old_status] = max(0, self.counts[old_status] - 1)
        else:
            self.counts["total"] += 1
        caption_path = caption_path_for(path)
        self.items[key] = {
            "path": path,
            "status": status,
            "detail": detail,
            "caption_exists": caption_path.is_file(),
            "caption_usable": has_usable_caption(path),
        }
        self.counts[status] += 1
        if old_visible is not None and old_visible != self._visible(self.items[key]):
            self.refresh_table()
            self._update_stats()
            return
        row = self.path_rows.get(key)
        if row and self.tree.exists(row):
            relative = self._relative_path(path)
            self.tree.item(row, values=(STATUS_TEXT[status], relative, detail.replace("\n", " ")[:220]), tags=(status,))
        self.gallery.update_item(self.items[key])
        self._update_stats()

    def _set_orphan_item(self, path: Path) -> None:
        self.items[str(path)] = {
            "path": path,
            "status": "orphan",
            "detail": "无对应图片或视频文件",
            "caption_exists": True,
            "caption_usable": True,
            "is_orphan": True,
        }

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(Path(self.folder_var.get())))
        except ValueError:
            return str(path)

    def _visible(self, item: dict) -> bool:
        status_filter = FILTERS.get(self.filter_var.get(), "all")
        is_orphan = bool(item.get("is_orphan", False))
        if is_orphan and status_filter != "orphan_caption":
            return False
        if not is_orphan and status_filter == "orphan_caption":
            return False
        if status_filter == "missing_caption" and item.get("caption_exists", False):
            return False
        if status_filter == "invalid_caption" and not (
            item.get("caption_exists", False) and not item.get("caption_usable", False)
        ):
            return False
        if status_filter == "similar" and str(item["path"]) not in self.similar_paths:
            return False
        if status_filter not in {
            "all", "similar", "missing_caption", "invalid_caption", "orphan_caption"
        } and item["status"] != status_filter:
            return False
        query = self.search_var.get().strip().casefold()
        if not query:
            return True
        return query in str(item["path"]).casefold() or query in item["detail"].casefold()

    def refresh_table(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.row_paths.clear()
        self.path_rows.clear()
        visible_items = [
            item for _key, item in sorted(self.items.items(), key=lambda pair: pair[0].casefold())
            if self._visible(item)
        ]
        for item in visible_items:
            key = str(item["path"])
            row = self.tree.insert(
                "", tk.END,
                values=(STATUS_TEXT[item["status"]], self._relative_path(item["path"]), item["detail"].replace("\n", " ")[:220]),
                tags=(item["status"],),
            )
            self.row_paths[row] = item["path"]
            self.path_rows[key] = row
        gallery_items = [
            item for item in visible_items if not item.get("is_orphan", False)
        ]
        self.gallery.set_items(gallery_items, self.selected_paths)
        self._sync_selection_widgets()

    def _update_stats(self) -> None:
        failed = self.counts["failed"]
        missing = sum(
            not item.get("caption_exists", False) for item in self.items.values()
        )
        invalid = sum(
            item.get("caption_exists", False) and not item.get("caption_usable", False)
            for item in self.items.values()
        )
        self.stats_var.set(
            f"总 {self.counts['total']} · 缺 {missing} · 无效 {invalid} · "
            f"孤 {len(self.orphan_caption_paths)} · 成 {self.counts['success']} · 失 {failed}"
        )

    def _process_events(self) -> None:
        pending_after_id = self.events_after_id
        self.events_after_id = None
        if pending_after_id is not None:
            try:
                self.root.after_cancel(pending_after_id)
            except tk.TclError:
                pass
        if self.closing:
            return
        processed = 0
        while processed < 120:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if kind == "scan":
                if payload.get("scan_generation") != self.scan_generation:
                    continue
                self._handle_scan(payload["result"])
            elif kind == "scan_done":
                if payload.get("scan_generation") != self.scan_generation:
                    continue
                self.progress_text_var.set("扫描完成")
            elif kind == "thumbnail":
                path = payload["path"]
                key = str(path)
                self.thumbnail_pending.discard(key)
                image = ImageTk.PhotoImage(payload["image"])
                self.thumbnail_cache[key] = image
                self.gallery.update_thumbnail(path, image)
            elif kind == "status":
                path = payload["path"]
                status = payload["status"]
                detail = payload.get("detail", "")
                self._set_item(path, status, detail)
                self.log(f"{STATUS_TEXT[status]}：{self._relative_path(path)}{(' | ' + detail[:300]) if detail else ''}")
            elif kind == "progress":
                completed = payload["completed"]
                total = payload["total"]
                eta = payload["eta"]
                self.progress_var.set(completed / total * 100 if total else 100)
                self.progress_text_var.set(f"{completed}/{total}  ·  ETA {self._format_eta(eta)}")
            elif kind == "analysis_progress":
                completed, total = payload["completed"], payload["total"]
                self.progress_var.set(completed / total * 100 if total else 100)
                self.progress_text_var.set(f"相似图分析 {completed}/{total}")
            elif kind == "similar_done":
                groups = payload["groups"]
                self.similar_paths = {
                    str(path) for group in groups for path in group
                }
                self.filter_var.set("相似图" if groups else "全部状态")
                self.refresh_table()
                self.progress_var.set(100)
                self.progress_text_var.set(
                    f"相似图 {len(groups)} 组 / {len(self.similar_paths)} 张"
                    if groups else "未发现相似图"
                )
                self._set_stage(3)
                self._restore_idle_controls()
                self.log(
                    f"相似图检测完成：{len(groups)} 组，{len(self.similar_paths)} 张"
                )
            elif kind == "similar_cancelled":
                self.progress_text_var.set("相似图检测已取消")
                self._restore_idle_controls()
                self.log("相似图检测已取消")
            elif kind == "similar_error":
                self.progress_text_var.set("相似图检测失败")
                self._restore_idle_controls()
                self.log(f"相似图检测失败：{payload['error']}")
            elif kind == "done":
                self._handle_done(payload["status"], payload["summary"], payload["journal_dir"])
        self.events_after_id = self.root.after(60, self._process_events)

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        return f"{seconds // 60} 分 {seconds % 60} 秒"

    def _restore_idle_controls(self) -> None:
        self.analysis_token = None
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.retry_button.config(state=tk.NORMAL if self.last_failed_paths else tk.DISABLED)
        has_selected_media = any(
            self._is_media_path(Path(value)) for value in self.selected_paths
        )
        self.selected_button.config(
            state=tk.NORMAL if has_selected_media else tk.DISABLED
        )
        self.batch_button.config(state=tk.NORMAL)

    def _handle_done(self, status: str, summary: BatchSummary, journal_dir: Path) -> None:
        self.last_failed_paths = [path for path, _detail in summary.failures]
        self._restore_idle_controls()
        if status == "stopped":
            self.progress_text_var.set("已停止")
        else:
            self.progress_var.set(100)
            self.progress_text_var.set("任务完成")
            self._set_stage(3)
        self.log(
            f"任务结束：成功 {summary.success}，跳过 {summary.skipped}，"
            f"失败 {summary.failed}，取消 {summary.cancelled}；日志 {journal_dir}"
        )
        self.refresh_project_center()

    def show_selected_item(self, _event=None) -> None:
        if self._selection_sync:
            return
        selection = self.tree.selection()
        self.selected_paths = {
            str(self.row_paths[row]) for row in selection if row in self.row_paths
        }
        self._sync_selection_widgets()
        self._display_selection()

    def _display_selection(self) -> None:
        self.result_text.delete("1.0", tk.END)
        self.save_result_button.config(state=tk.DISABLED)
        paths = [Path(value) for value in sorted(self.selected_paths)]
        if not paths:
            self.preview_label.config(image="", text="选择任务项")
            self.preview_image = None
            return
        if len(paths) > 1:
            self.preview_label.config(image="", text=f"已选择 {len(paths)} 项")
            self.result_text.insert(
                "1.0", "孤立 TXT 不会进入打标或训练数据导出。"
            )
            self.preview_image = None
            return
        path = paths[0]
        item = self.items.get(str(path), {})
        if item.get("is_orphan", False):
            self.preview_label.config(
                image="", text=f"孤立 TXT\n{path.name}\n无对应媒体文件"
            )
            try:
                self.result_text.insert("1.0", path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as error:
                self.result_text.insert("1.0", f"读取 TXT 失败：{error}")
            self.preview_image = None
            return
        caption_path = caption_path_for(path)
        if caption_path.exists():
            try:
                self.result_text.insert("1.0", caption_path.read_text(encoding="utf-8"))
                self.save_result_button.config(state=tk.NORMAL)
            except (OSError, UnicodeError) as error:
                self.result_text.insert("1.0", f"读取结果失败：{error}")
        if path.suffix.casefold() in {".mp4", ".mov", ".avi"}:
            size_mb = path.stat().st_size / 1024 / 1024
            self.preview_label.config(image="", text=f"{path.name}\n{size_mb:.1f} MB")
            self.preview_image = None
            return
        try:
            preview = open_image(path)
            preview.thumbnail((420, 330), Image.Resampling.LANCZOS)
            self.preview_image = ImageTk.PhotoImage(preview.copy())
            preview.close()
            self.preview_label.config(image=self.preview_image, text="")
        except (OSError, RuntimeError, ValueError) as error:
            self.preview_label.config(image="", text=f"预览失败\n{error}")
            self.preview_image = None

    def save_selected_result(self) -> None:
        if len(self.selected_paths) != 1:
            return
        path = Path(next(iter(self.selected_paths)))
        caption = self.result_text.get("1.0", tk.END).strip()
        if not caption:
            messagebox.showwarning("提示", "结果不能为空", parent=self.root)
            return
        write_caption(path, caption)
        self._set_item(path, "success", caption)
        self.log(f"已保存修改：{self._relative_path(path)}")

    def apply_trigger_to_results(self) -> None:
        trigger_word = self.trigger_word_var.get().strip()
        if not trigger_word:
            messagebox.showinfo("触发词", "请先在打标策略中填写触发词", parent=self.root)
            return
        selected = [
            Path(value)
            for value in self.selected_paths
            if Path(value).is_file()
            and self._is_media_path(Path(value))
            and has_usable_caption(Path(value))
        ]
        paths = selected or [
            item["path"]
            for item in self.items.values()
            if self._visible(item)
            and self._is_media_path(item["path"])
            and has_usable_caption(item["path"])
        ]
        if not paths:
            messagebox.showinfo("触发词", "当前范围没有可更新的有效 TXT", parent=self.root)
            return
        scope = "已选素材" if selected else "当前筛选范围"
        if not messagebox.askyesno(
            "批量添加触发词",
            f"将在{scope}的 {len(paths)} 个 TXT 最前面添加“{trigger_word}”。\n"
            "已有相同前缀的文件不会重复添加。是否继续？",
            parent=self.root,
        ):
            return
        changed = 0
        for path in paths:
            caption = caption_path_for(path).read_text(encoding="utf-8").strip()
            updated = prepend_trigger_word(
                caption,
                trigger_word,
                self.output_language_var.get(),
            )
            if updated == caption:
                continue
            write_caption(path, updated)
            current_status = self.items.get(str(path), {}).get("status", "success")
            self._set_item(path, current_status, updated)
            changed += 1
        self.log(f"触发词批量添加完成：更新 {changed} 个 TXT")

    def batch_replace(self) -> None:
        selected = [
            Path(value) for value in self.selected_paths
            if Path(value).exists()
            and self._is_media_path(Path(value))
            and has_usable_caption(Path(value))
        ]
        paths = selected or [
            item["path"] for item in self.items.values()
            if self._visible(item)
            and self._is_media_path(item["path"])
            and has_usable_caption(item["path"])
        ]
        if not paths:
            messagebox.showinfo("提示", "当前筛选范围没有有效结果", parent=self.root)
            return
        source = simpledialog.askstring("批量替换", "查找文本", parent=self.root)
        if source is None or source == "":
            return
        replacement = simpledialog.askstring("批量替换", "替换为", parent=self.root)
        if replacement is None:
            return
        scope = "已选" if selected else "当前筛选范围"
        if not messagebox.askyesno("确认批量替换", f"将在{scope}的 {len(paths)} 个结果中替换，是否继续？", parent=self.root):
            return
        changed = 0
        for path in paths:
            caption = caption_path_for(path).read_text(encoding="utf-8")
            if source not in caption:
                continue
            updated = caption.replace(source, replacement)
            write_caption(path, updated)
            self._set_item(path, "success", updated)
            changed += 1
        self.log(f"批量替换完成：更新 {changed} 个结果")

    def export_results(self, format_name: str) -> None:
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            return
        selected = [
            Path(value) for value in self.selected_paths
            if Path(value).exists() and self._is_media_path(Path(value))
        ]
        paths = selected or [
            item["path"] for item in self.items.values()
            if self._visible(item) and self._is_media_path(item["path"])
        ]
        if not paths:
            messagebox.showinfo(
                "导出结果", "当前范围没有可导出的媒体标签。", parent=self.root
            )
            return
        suffix = ".jsonl" if format_name == "jsonl" else ".csv"
        destination = filedialog.asksaveasfilename(
            title="导出结果", defaultextension=suffix,
            filetypes=[(format_name.upper(), f"*{suffix}")],
            initialfile=f"captions-{datetime.now():%Y%m%d-%H%M%S}{suffix}",
        )
        if not destination:
            return
        count = export_jsonl(paths, Path(destination), folder) if format_name == "jsonl" else export_csv(paths, Path(destination), folder)
        self.log(f"已导出 {count} 条结果：{destination}")

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 2000:
            self.log_text.delete("1.0", f"{line_count - 1800}.0")
        self.log_text.see(tk.END)

    def close(self) -> None:
        active = bool(
            (self.runner and self.runner.running)
            or (self.analysis_token and self.controller_thread and self.controller_thread.is_alive())
        )
        if active:
            if not messagebox.askyesno("确认退出", "任务正在运行，确定停止并退出？", parent=self.root):
                return
            if self.runner:
                self.runner.cancel()
            if self.analysis_token:
                self.analysis_token.cancel()
        try:
            self._save_workspace_settings()
        except (OSError, ValueError, tk.TclError):
            pass
        self.closing = True
        for after_id in (
            self.events_after_id,
            self.sash_after_id,
            self.splash_after_id,
        ):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        self.events_after_id = None
        self.sash_after_id = None
        self.splash_after_id = None
        self.root.destroy()


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        root.withdraw()
    app = CaptionApp(root, show_splash=not smoke_test)
    if smoke_test:
        if app._launch_source is None:
            raise RuntimeError("启动视觉资源未加载")
        root.update_idletasks()
        root.update()
        root.destroy()
        print("GUI_SMOKE_OK")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
