from __future__ import annotations

from datetime import datetime
import ctypes
import math
import os
import secrets
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
import webbrowser

from PIL import Image, ImageDraw, ImageGrab, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # Upload buttons remain available without the optional runtime.
    DND_FILES = None
    TkinterDnD = None

from media_caption_core import (
    API_PROVIDERS,
    APP_VERSION,
    AUDIO_EXTENSIONS,
    BatchRunner,
    BatchSummary,
    CancelledError,
    CancellationToken,
    DEFAULT_MODEL_KEY,
    DEFAULT_PROVIDER_KEY,
    DEFAULT_SAMPLING,
    IMAGE_EXTENSIONS,
    LMSTUDIO_CAPTION_TOKEN_LIMIT,
    LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
    LLAMA_CPP_DEFAULT_GPU_LAYERS,
    LMSTUDIO_LOAD_PROFILE_DEFAULT,
    MAX_CONCURRENCY,
    MODELS,
    ScanResult,
    SettingsStore,
    VIDEO_EXTENSIONS,
    app_data_dir,
    caption_path_for,
    check_latest_release,
    count_output_characters,
    create_app_backup,
    create_diagnostic_bundle,
    create_windows_update_script,
    delete_project_metadata,
    download_release_asset,
    export_csv,
    export_jsonl,
    extract_update_executable,
    find_similar_images,
    has_usable_caption,
    load_project_summary,
    load_incomplete_paths,
    launch_windows_update_installer,
    list_lmstudio_models,
    load_lmstudio_model,
    maybe_create_automatic_backup,
    normalize_sampling,
    open_image,
    prepend_trigger_word,
    scan_media,
    test_provider_connection,
    unload_lmstudio_model,
    write_caption,
)
from media_caption_worker import (
    MediaWorkerController,
    MediaWorkerError,
    run_worker_cli,
)


APP_TITLE = "芊熠智能打标工作台"
UI_FONT = "HarmonyOS Sans SC"
LATIN_FONT = "Segoe UI Variable Text"
MONO_FONT = "Consolas"
# The workbench is deliberately text-forward.  Keep these values in one
# place so the complete UI can be enlarged without hunting through every
# ttk style and classic Tk text widget separately.
# Readability scale for the workbench.  Tk rendering is normalized to a
# 96-DPI logical canvas on Windows, so these values are the actual visual
# point sizes rather than being magnified a second time by system DPI.
UI_TEXT_SIZE = 15
UI_SMALL_SIZE = 14
UI_MEDIUM_SIZE = 15
UI_TITLE_SIZE = 21
UI_SECTION_SIZE = 16
UI_MONO_SIZE = 13
# Form controls need a slightly larger optical size than surrounding helper
# text.  The adaptive typography hook below steps this down only on compact
# windows so values remain readable without being clipped.
UI_INPUT_SIZE = 16
WORKSPACE_LEFT_WIDTH = 360
WORKSPACE_CENTER_WIDTH = 600
WORKSPACE_RIGHT_WIDTH = 420
RELEASE_NOTES = (
    "工作区重构为大尺寸三列布局，统一可读字体层级并完善滚动与控件显示",
    "新增 llama.cpp 原生 GGUF 本地运行方式，自动管理 llama-server、主模型与 mmproj 生命周期",
    "新增 LM Studio 本地服务模式，支持模型读取、加载/卸载以及本地视觉反推",
    "本地反推统一传递采样、MTP 与思考标签设置，并完善显存/并发提示与失败诊断",
    "新增独立单次反推模块，支持图片、音频和视频片段编辑后反推",
    "扩充主流 API 供应商与模型，新增自定义 OpenAI 兼容接口配置",
    "重做日光/夜光主题与原生控件同步，修复颜色残留、窗口恢复闪屏和缩略图闪烁",
    "接入 GitHub Releases 检查、下载、覆盖安装与自动重启更新流程",
    "优化二次打标覆盖写入、结果即时反馈、耗时/字数/速度日志和 Windows EXE 元信息",
)
FEATURE_GROUPS = (
    ("素材工作流", "支持独立单次反推、图片粘贴、音视频片段编辑与项目批量处理。"),
    ("智能打标", "按训练目标生成自然语言或词组标签，支持多平台视觉模型。"),
    ("质量与整理", "检测缺失、无效及孤立 TXT，支持相似图分析和批量替换。"),
    ("任务与导出", "固定任务栏管理并发、重试、停止，并导出 JSONL 或 CSV。"),
)
# Public builds intentionally ship without task prompt templates. Users can
# write, import, and save their own templates locally; none are committed or
# bundled into GitHub releases.
DEFAULT_PRESETS: dict[str, str] = {}
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
LOCAL_RUNTIME_OPTIONS = {
    "Hugging Face 本地目录": "huggingface",
    "LM Studio 本地服务": "lmstudio",
    "llama.cpp 原生 GGUF": "llamacpp",
}
LOCAL_RUNTIME_LABELS = {
    value: label for label, value in LOCAL_RUNTIME_OPTIONS.items()
}
LMSTUDIO_LOAD_PROFILE_OPTIONS = {
    "低显存安全（推荐）": "low_vram",
    "纯 CPU（最稳定）": "cpu",
    "沿用 LM Studio 预设": "inherit",
}
LMSTUDIO_LOAD_PROFILE_LABELS = {
    value: label for label, value in LMSTUDIO_LOAD_PROFILE_OPTIONS.items()
}
PROVIDER_LABELS = {provider.label: key for key, provider in API_PROVIDERS.items()}
# Public platform options include maintained built-in routes plus an explicit
# OpenAI-compatible custom route whose endpoint and model are entered locally.
PUBLIC_PROVIDER_KEYS = (
    "volcengine",
    "openai",
    "google",
    "moonshot",
    "qwen",
    "siliconflow",
    "custom",
)
PUBLIC_PROVIDER_LABELS = {
    API_PROVIDERS[key].label: key for key in PUBLIC_PROVIDER_KEYS
}
API_KEY_PORTALS = {
    "volcengine": (
        "火山引擎控制台",
        "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
    ),
    "openai": ("OpenAI Platform", "https://platform.openai.com/api-keys"),
    "google": ("Google AI Studio", "https://aistudio.google.com/app/apikey"),
    "moonshot": ("Kimi 开放平台", "https://platform.moonshot.cn/console/api-keys"),
    "qwen": ("阿里云百炼", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    "siliconflow": (
        "SiliconFlow 控制台",
        "https://cloud.siliconflow.cn/account/ak",
    ),
    "custom": ("自定义接口", ""),
}
LOCAL_RUNTIME_PORTALS = {
    "lmstudio": {
        "title": "LM Studio",
        "url": "https://lmstudio.ai/download",
        "button": "打开 LM Studio 下载页",
        "description": (
            "适合不想手动管理命令行的本地视觉模型用户。LM Studio 提供桌面界面、"
            "模型加载、显存/CPU 参数和 OpenAI 兼容本地服务；启动服务后即可在本软件中选择 LM Studio。"
        ),
    },
    "llamacpp": {
        "title": "llama.cpp",
        "url": "https://github.com/ggml-org/llama.cpp/releases",
        "button": "打开 llama.cpp 下载页",
        "description": (
            "适合直接运行 GGUF 单体视觉模型。llama.cpp 负责本地推理，本软件会管理 llama-server、"
            "主 GGUF 与 mmproj 文件，并支持图片反推时传入采样参数。"
        ),
    },
}
SAMPLING_PRESETS = {
    "稳定标注": {
        "max_tokens": 1200, "temperature": 0.1, "top_p": 0.85, "top_k": 0,
        "frequency_penalty": 0.0, "presence_penalty": 0.0, "seed": 42,
    },
    "平衡反推": dict(DEFAULT_SAMPLING),
    "创意扩写": {
        "max_tokens": 2400, "temperature": 0.8, "top_p": 0.95, "top_k": 50,
        "frequency_penalty": 0.1, "presence_penalty": 0.15, "seed": None,
    },
}

THEMES = {
    "night": {
        "bg": "#27302c",
        "surface": "#303934",
        "surface_alt": "#39433d",
        "border": "#505c55",
        "text": "#f4f1e8",
        "muted": "#c0bdb3",
        "accent": "#79a4dc",
        "accent_dark": "#34465e",
        "warning": "#e0b52c",
        "success": "#78c695",
        "danger": "#ee7a70",
        "info": "#79a4dc",
        "topbar": "#303934",
        "topbar_border": "#59645d",
        "hover": "#48534c",
        "selection": "#504724",
        "media_bg": "#202824",
        "primary_fg": "#fffdf5",
        "primary_hover": "#8db4e4",
        "primary_disabled": "#4c5f72",
        "disabled_fg": "#858b84",
        "danger_bg": "#513330",
        "danger_border": "#98605a",
        "danger_hover": "#68403c",
        "semantic_blue": "#4b7fbd",
        "semantic_yellow": "#c7971f",
        "semantic_red": "#bb554e",
        "semantic_violet": "#7d6aca",
        "update_bg": "#4c421f",
        "update_border": "#d0a924",
        "input_bg": "#35403a",
        "input_readonly": "#323c37",
        "input_hover": "#3d4942",
        "input_border": "#66736b",
        "input_focus": "#79a4dc",
        "input_disabled": "#2b332f",
    },
    "day": {
        "bg": "#f3f1e9",
        "surface": "#fbfaf5",
        "surface_alt": "#ebe8de",
        "border": "#d2cec2",
        "text": "#20201d",
        "muted": "#69665f",
        "accent": "#285c96",
        "accent_dark": "#e1e8ef",
        "warning": "#9b6e09",
        "success": "#36744c",
        "danger": "#b84b43",
        "info": "#285c96",
        "topbar": "#fbfaf5",
        "topbar_border": "#c9c4b8",
        "hover": "#dedacf",
        "selection": "#eadfbb",
        "media_bg": "#e6e2d7",
        "primary_fg": "#fffdf5",
        "primary_hover": "#1f4b7c",
        "primary_disabled": "#9babb9",
        "disabled_fg": "#99958c",
        "danger_bg": "#f2dfdb",
        "danger_border": "#d6aaa4",
        "danger_hover": "#e9cbc6",
        "semantic_blue": "#3f79bf",
        "semantic_yellow": "#c7951f",
        "semantic_red": "#b94c45",
        "semantic_violet": "#7767b6",
        "update_bg": "#f2e5b7",
        "update_border": "#c89c16",
        "input_bg": "#fffdf7",
        "input_readonly": "#f8f5ec",
        "input_hover": "#f1eee5",
        "input_border": "#b9b4a8",
        "input_focus": "#285c96",
        "input_disabled": "#e8e5dc",
    },
}
DEFAULT_THEME_KEY = "night"
COLORS = dict(THEMES[DEFAULT_THEME_KEY])
STATUS_COLORS = {}


def activate_theme(theme_key: str) -> str:
    normalized = theme_key if theme_key in THEMES else DEFAULT_THEME_KEY
    COLORS.clear()
    COLORS.update(THEMES[normalized])
    STATUS_COLORS.clear()
    STATUS_COLORS.update({
        "pending": COLORS["muted"],
        "running": COLORS["info"],
        "success": COLORS["success"],
        "skipped": COLORS["muted"],
        "failed": COLORS["danger"],
        "cancelled": COLORS["warning"],
        "orphan": COLORS["warning"],
    })
    return normalized


activate_theme(DEFAULT_THEME_KEY)
def enable_dpi_awareness() -> None:
    """Enable native Windows rendering before Tk creates its first window."""
    if sys.platform != "win32":
        return
    # Prefer per-monitor v2.  The fallbacks cover older Windows builds and
    # Python/Tk combinations where the newer API is not exported.
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def configure_tk_rendering(root: tk.Tk) -> None:
    """Keep Windows from enlarging Tk widgets a second time.

    Tk can report a monitor-derived scaling value above 1.0 even after the
    process is DPI-aware.  This pixel-oriented interface uses a stable 96-DPI
    logical canvas; normalizing Tk's own scaling keeps the 1180x760 window and
    three-column measurements unchanged while rendering text and bitmap icons
    at their native pixel size.
    """
    if sys.platform != "win32":
        return
    try:
        current = float(root.tk.call("tk", "scaling"))
        if current > 1.05:
            root.tk.call("tk", "scaling", 1.0)
    except (tk.TclError, TypeError, ValueError):
        pass


def resource_path(relative_path: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / Path(relative_path)


def _configure_window_geometry(
    root: tk.Tk,
    target_width: int,
    target_height: int,
) -> str:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    # Leave only a slim safety margin for the Windows frame.  The previous
    # 80px cap made a DPI-scaled 1920x1080 desktop report a much smaller
    # logical work area and clipped the three-column inspector again.
    initial_width = max(960, min(target_width, screen_width - 40))
    initial_height = max(620, min(target_height, screen_height - 40))
    x = max(0, (screen_width - initial_width) // 2)
    y = max(0, (screen_height - initial_height) // 2)
    geometry = f"{initial_width}x{initial_height}+{x}+{y}"
    root.geometry(geometry)
    # Keep a conservative fallback minimum for callers that use this helper
    # directly; CaptionApp replaces it with the active view's exact size when
    # switching between launch and workspace windows.
    root.minsize(1024, 720)
    return geometry


def _geometry_size(geometry: str) -> tuple[int, int]:
    """Return the width/height portion of a Tk geometry string."""
    try:
        size = geometry.split("+", 1)[0]
        width, height = size.lower().split("x", 1)
        return max(1, int(width)), max(1, int(height))
    except (AttributeError, TypeError, ValueError):
        return 960, 620


def configure_launch_window(root: tk.Tk) -> str:
    # The launch view is intentionally a little wider so the hero image keeps
    # its composition without becoming cramped.
    return _configure_window_geometry(root, 1440, 820)


def configure_main_window(root: tk.Tk) -> str:
    # The working surface gets more height for the three-column inspector and
    # result panels, which reduces clipped labels on dense layouts.
    return _configure_window_geometry(root, 1720, 1200)


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


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.after_id = None
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _event=None) -> None:
        self.hide()
        self.after_id = self.widget.after(450, self.show)

    def show(self) -> None:
        self.after_id = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window.geometry(f"+{x}+{y}")
        tk.Label(
            self.window,
            text=self.text,
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            borderwidth=1,
            relief=tk.SOLID,
            padx=8,
            pady=4,
            font=(UI_FONT, UI_SMALL_SIZE),
        ).pack()

    def hide(self, _event=None) -> None:
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class SlideSwitch(ttk.Frame):
    """Compact theme-aware on/off control for dense settings headings."""

    def __init__(
        self,
        parent,
        text: str,
        variable: tk.BooleanVar,
        *,
        command=None,
    ):
        super().__init__(parent)
        self.variable = variable
        self.command = command
        self.enabled = True
        self.label = ttk.Label(self, text=text, style="Muted.TLabel")
        self.label.pack(side=tk.LEFT, padx=(0, 7))
        self.canvas = tk.Canvas(
            self,
            width=38,
            height=22,
            highlightthickness=0,
            borderwidth=0,
            background=COLORS["bg"],
            cursor="hand2",
        )
        self.canvas.pack(side=tk.LEFT)
        self._trace_id = self.variable.trace_add("write", self._variable_changed)
        for widget in (self, self.label, self.canvas):
            widget.bind("<Button-1>", self._toggle, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.redraw()

    def _variable_changed(self, *_args) -> None:
        self.redraw()

    def _toggle(self, _event=None):
        if not self.enabled:
            return "break"
        self.variable.set(not bool(self.variable.get()))
        if self.command is not None:
            self.command()
        return "break"

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.canvas.configure(cursor="hand2" if self.enabled else "arrow")
        self.label.configure(
            style="TLabel" if self.enabled else "Muted.TLabel"
        )
        self.redraw()

    def redraw(self) -> None:
        try:
            if not self.canvas.winfo_exists():
                return
            self.canvas.configure(background=COLORS["bg"])
            self.canvas.delete("all")
            selected = bool(self.variable.get())
            track = (
                COLORS["accent"]
                if selected and self.enabled
                else (
                    COLORS["input_disabled"]
                    if not self.enabled
                    else COLORS["input_border"]
                )
            )
            knob = (
                COLORS["primary_fg"]
                if self.enabled
                else COLORS["disabled_fg"]
            )
            self.canvas.create_oval(1, 2, 19, 20, fill=track, outline="")
            self.canvas.create_rectangle(10, 2, 28, 20, fill=track, outline="")
            self.canvas.create_oval(19, 2, 37, 20, fill=track, outline="")
            center_x = 28 if selected else 10
            self.canvas.create_oval(
                center_x - 7,
                4,
                center_x + 7,
                18,
                fill=knob,
                outline="",
            )
        except tk.TclError:
            pass

    def _destroyed(self, event) -> None:
        if event.widget is not self or not self._trace_id:
            return
        try:
            self.variable.trace_remove("write", self._trace_id)
        except tk.TclError:
            pass
        self._trace_id = ""


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("memory_load", ctypes.c_uint32),
        ("total_physical", ctypes.c_uint64),
        ("available_physical", ctypes.c_uint64),
        ("total_page_file", ctypes.c_uint64),
        ("available_page_file", ctypes.c_uint64),
        ("total_virtual", ctypes.c_uint64),
        ("available_virtual", ctypes.c_uint64),
        ("available_extended_virtual", ctypes.c_uint64),
    ]


class HardwareMonitor:
    def __init__(self, interval: float = 2.0):
        self.interval = max(0.5, interval)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._last_cpu: tuple[int, int] | None = None
        self._gpu_sample = {
            "percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "temperature_c": None,
        }

    @staticmethod
    def _filetime_value(value: _FileTime) -> int:
        return (int(value.high) << 32) | int(value.low)

    def _cpu_percent(self) -> float | None:
        if sys.platform != "win32":
            return None
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        try:
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
        except (AttributeError, OSError):
            return None
        idle_value = self._filetime_value(idle)
        total_value = self._filetime_value(kernel) + self._filetime_value(user)
        previous = self._last_cpu
        self._last_cpu = (idle_value, total_value)
        if previous is None or total_value <= previous[1]:
            return 0.0
        idle_delta = idle_value - previous[0]
        total_delta = total_value - previous[1]
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))

    @staticmethod
    def _memory_sample() -> dict:
        if sys.platform != "win32":
            return {"percent": None, "used_gb": None, "total_gb": None}
        status = _MemoryStatusEx()
        status.length = ctypes.sizeof(status)
        try:
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return {"percent": None, "used_gb": None, "total_gb": None}
        except (AttributeError, OSError):
            return {"percent": None, "used_gb": None, "total_gb": None}
        gib = 1024 ** 3
        used = (status.total_physical - status.available_physical) / gib
        total = status.total_physical / gib
        return {
            "percent": float(status.memory_load),
            "used_gb": used,
            "total_gb": total,
        }

    @staticmethod
    def _query_gpu() -> dict:
        unavailable = {
            "percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "temperature_c": None,
        }
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return unavailable
        if completed.returncode != 0 or not completed.stdout.strip():
            return unavailable
        first = completed.stdout.strip().splitlines()[0].split(",")
        if len(first) < 3:
            return unavailable

        def number(index: int) -> float | None:
            try:
                return float(first[index].strip())
            except (IndexError, TypeError, ValueError):
                return None

        return {
            "percent": number(0),
            "memory_used_mb": number(1),
            "memory_total_mb": number(2),
            "temperature_c": number(3),
        }

    def sample(self, refresh_gpu: bool = True) -> dict:
        if refresh_gpu:
            self._gpu_sample = self._query_gpu()
        cpu = self._cpu_percent()
        return {
            "cpu_percent": cpu,
            "cpu_temperature_c": None,
            "memory": self._memory_sample(),
            "gpu": dict(self._gpu_sample),
        }

    def start(self, callback) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()

        def monitor() -> None:
            cycle = 0
            while not self.stop_event.is_set():
                callback(self.sample(refresh_gpu=cycle % 3 == 0))
                cycle += 1
                if self.stop_event.wait(self.interval):
                    break

        self.thread = threading.Thread(
            target=monitor, daemon=True, name="hardware-monitor"
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()


class MediaGallery(ttk.Frame):
    def __init__(self, parent, on_select, request_thumbnail, get_thumbnail):
        super().__init__(parent)
        self.on_select = on_select
        self.request_thumbnail = request_thumbnail
        self.get_thumbnail = get_thumbnail
        self.items: list[dict] = []
        self.selected: set[str] = set()
        self.page = 0
        self.page_size = 16
        self.card_frames: dict[str, tk.Frame] = {}
        self.image_labels: dict[str, tk.Label] = {}
        self.status_labels: dict[str, tk.Label] = {}
        self._render_after = None
        self._configured_columns = 0

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
        self.cancel_pending_layout()
        super().destroy()

    def _content_resized(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event) -> None:
        width = max(1, int(event.width))
        try:
            self.canvas.itemconfigure(self.window_id, width=width)
        except tk.TclError:
            return
        if width <= 1 or self._toplevel_is_iconic():
            self.cancel_pending_layout()
            return
        if self._columns_for_width(width) == self._configured_columns:
            return
        self.cancel_pending_layout()
        self._render_after = self.after(80, self.refresh_layout)

    @staticmethod
    def _columns_for_width(width: int) -> int:
        return max(2, max(320, int(width)) // 190)

    def _toplevel_is_iconic(self) -> bool:
        try:
            return str(self.winfo_toplevel().state()) in {"iconic", "withdrawn"}
        except tk.TclError:
            return True

    def cancel_pending_layout(self) -> None:
        if self._render_after is None:
            return
        try:
            self.after_cancel(self._render_after)
        except tk.TclError:
            pass
        self._render_after = None

    def _configure_grid_columns(self, columns: int) -> None:
        for column in range(max(self._configured_columns, columns)):
            self.content.grid_columnconfigure(
                column, weight=0, minsize=0, uniform=""
            )
        for column in range(columns):
            self.content.grid_columnconfigure(column, weight=1, uniform="gallery")
        self._configured_columns = columns

    def refresh_layout(self) -> None:
        self._render_after = None
        if self._toplevel_is_iconic():
            return
        try:
            width = self.canvas.winfo_width()
            if width <= 1 or not self.canvas.winfo_ismapped():
                return
            self.canvas.itemconfigure(self.window_id, width=width)
        except tk.TclError:
            return
        columns = self._columns_for_width(width)
        if columns == self._configured_columns:
            return
        self._configure_grid_columns(columns)
        start = self.page * self.page_size
        page_items = self.items[start : start + self.page_size]
        for index, item in enumerate(page_items):
            card = self.card_frames.get(str(item["path"]))
            if card is not None and card.winfo_exists():
                card.grid_configure(row=index // columns, column=index % columns)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

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
        self.cancel_pending_layout()
        for child in self.content.winfo_children():
            child.destroy()
        self.card_frames.clear()
        self.image_labels.clear()
        self.status_labels.clear()
        self.canvas.yview_moveto(0)
        width = max(320, self.canvas.winfo_width())
        columns = self._columns_for_width(width)
        self._configure_grid_columns(columns)
        start = self.page * self.page_size
        page_items = self.items[start : start + self.page_size]
        for index, item in enumerate(page_items):
            path = item["path"]
            key = str(path)
            selected = key in self.selected
            card = tk.Frame(
                self.content,
                background=COLORS["surface"],
                highlightthickness=2 if selected else 1,
                highlightbackground=COLORS["accent"] if selected else COLORS["border"],
                highlightcolor=COLORS["accent"],
                cursor="hand2",
            )
            card.grid(row=index // columns, column=index % columns, padx=5, pady=5, sticky="nsew")
            image_label = tk.Label(
                card,
                text=path.suffix.upper().lstrip("."),
                background=COLORS["media_bg"],
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
            footer = tk.Frame(card, background=COLORS["surface"])
            footer.pack(fill=tk.X, padx=7, pady=(6, 7))
            tk.Label(
                footer,
                text=path.name,
                background=COLORS["surface"],
                foreground=COLORS["text"],
                anchor=tk.W,
                font=(UI_FONT, UI_SMALL_SIZE, "bold"),
            ).pack(fill=tk.X)
            status_label = tk.Label(
                footer,
                text=STATUS_TEXT[item["status"]],
                background=COLORS["surface"],
                foreground=STATUS_COLORS[item["status"]],
                anchor=tk.W,
                font=(UI_FONT, UI_SMALL_SIZE),
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

    def apply_theme(self) -> None:
        """Repaint visible gallery widgets without destroying thumbnails.

        Rebuilding the gallery on every day/night switch was the main source
        of the visible colour-transition pause and thumbnail flash.
        """
        item_by_key = {str(item["path"]): item for item in self.items}
        for key, card in tuple(self.card_frames.items()):
            try:
                if not card.winfo_exists():
                    continue
                selected = key in self.selected
                card.configure(
                    background=COLORS["surface"],
                    highlightbackground=(
                        COLORS["accent"] if selected else COLORS["border"]
                    ),
                    highlightcolor=COLORS["accent"],
                )
                image_label = self.image_labels.get(key)
                if image_label is not None:
                    image_label.configure(
                        background=COLORS["media_bg"],
                        foreground=COLORS["muted"],
                    )
                footer = next(
                    (child for child in card.winfo_children() if child is not image_label),
                    None,
                )
                if footer is not None:
                    footer.configure(background=COLORS["surface"])
                    labels = footer.winfo_children()
                    if labels:
                        labels[0].configure(
                            background=COLORS["surface"],
                            foreground=COLORS["text"],
                        )
                    if len(labels) > 1:
                        item = item_by_key.get(key, {})
                        labels[1].configure(
                            background=COLORS["surface"],
                            foreground=STATUS_COLORS.get(
                                item.get("status", "pending"), COLORS["muted"]
                            ),
                        )
            except tk.TclError:
                continue

    def update_selection(self, selected: set[str]) -> None:
        self.selected = set(selected)
        for key, card in self.card_frames.items():
            card.configure(
                highlightthickness=2 if key in self.selected else 1,
                highlightbackground=COLORS["accent"] if key in self.selected else COLORS["border"],
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
        configure_tk_rendering(self.root)
        self.production_start = settings_store is None
        if show_splash:
            self.root.withdraw()
        self.settings_store = settings_store or SettingsStore()
        self.settings = self.settings_store.load()
        self.theme_key = activate_theme(
            self.settings.get("theme", DEFAULT_THEME_KEY)
        )
        presets = dict(DEFAULT_PRESETS)
        presets.update(self.settings.get("prompt_presets") or {})
        self.settings["prompt_presets"] = presets

        self.events: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=20000)
        self.runner: BatchRunner | None = None
        self.controller_thread: threading.Thread | None = None
        self.active_task_context = "batch"
        self.media_edit_worker: MediaWorkerController | None = None
        self.media_edit_worker_lock = threading.Lock()
        self.media_edit_cancelled = threading.Event()
        self.items: dict[str, dict] = {}
        self.row_paths: dict[str, Path] = {}
        self.path_rows: dict[str, str] = {}
        self.last_failed_paths: list[Path] = []
        self.preview_image = None
        self._preview_source_image: Image.Image | None = None
        self._preview_source_key: tuple[str, int, int] | None = None
        self._preview_render_key: tuple[str, int, int, int, int] | None = None
        self.closing = False
        self.selected_paths: set[str] = set()
        self.single_image_path: Path | None = None
        self.single_media_path: Path | None = None
        self.single_task_path: Path | None = None
        self.single_task_kind = "image"
        self.single_media_info: dict = {}
        self.single_image_preview_source: Image.Image | None = None
        self.single_image_preview_photo = None
        self.single_media_preview_source: Image.Image | None = None
        self.single_media_preview_photo = None
        self.single_media_preview_frames: list[Image.Image] = []
        self.single_timeline_photo = None
        self.single_clip_operation = "reverse"
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
        self.update_after_id = None
        self._result_feedback_after_id = None
        self.update_check_running = False
        self.update_download_running = False
        self.update_install_pending = False
        self.latest_release: dict | None = None
        self.update_banner_visible = False
        self.maintenance_running = False
        self.scan_generation = 0
        self.launch_progress = 0
        self._launch_source: Image.Image | None = None
        self._launch_photo = None
        self._launch_photo_size: tuple[int, int] | None = None
        self._launch_icon_source: Image.Image | None = None
        self._launch_icon_photo = None
        self._launch_icon_photo_size: tuple[int, int] | None = None
        self._app_icon_photo = None
        self.toolbar_icons: dict[str, dict[str, ImageTk.PhotoImage]] = {}
        self.provider_icons: dict[str, ImageTk.PhotoImage] = {}
        self._form_style_images: dict[str, ImageTk.PhotoImage] = {}
        self._themed_text_widgets: dict[tk.Text, bool] = {}
        self._bound_text_widgets: set[tk.Text] = set()
        self._bound_comboboxes: set[ttk.Combobox] = set()
        self._themed_menus: dict[tk.Menu, str] = {}
        self._theme_sync_after_ids: set[str] = set()
        self._text_sync_after_ids: dict[tk.Text, str] = {}
        self._workspace_header_layout: str | None = None
        self._filter_bar_layout: str | None = None
        self._canvas_heading_compact: bool | None = None
        self._preview_panel_layout: tuple[int, int] | None = None
        self._system_features_wraplength: int | None = None
        self._typography_bucket: str | None = None
        self._window_suspended = False
        self._window_resume_after_id = None
        self.theme_buttons: dict[str, list[ttk.Button]] = {
            "night": [],
            "day": [],
        }
        self.project_paths: dict[str, Path] = {}
        self.workspace_project: Path | None = None
        self.pending_resume_paths: list[Path] | None = None
        self._active_provider_key: str | None = None
        self.api_model_by_provider = dict(self.settings.get("api_models") or {})
        self.api_endpoint_by_provider = dict(
            self.settings.get("api_endpoints") or {}
        )
        self.hardware_monitor = HardwareMonitor()
        self.counts = {status: 0 for status in STATUS_TEXT}
        self.counts["total"] = 0
        self.caption_health_counts = {"missing": 0, "invalid": 0}

        self.root.title(f"{APP_TITLE} {APP_VERSION}")
        self._set_window_icon()
        self._load_toolbar_icons()
        self._load_provider_icons()
        self.launch_geometry = configure_launch_window(self.root)
        self.normal_geometry = configure_main_window(self.root)
        self.launch_size = _geometry_size(self.launch_geometry)
        self.normal_size = _geometry_size(self.normal_geometry)
        self.root.minsize(*self.normal_size)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_style()
        self._build_ui()
        self.root.bind("<Configure>", self._adaptive_typography, add="+")
        self._adaptive_typography(force=True)
        self.root.bind("<Control-v>", self._paste_single_clipboard, add="+")
        self.root.bind("<Unmap>", self._window_unmapped, add="+")
        self.root.bind("<Map>", self._window_mapped, add="+")
        self._install_form_wheel_guards()
        self._load_values()
        self._start_thumbnail_workers()
        self.hardware_monitor.start(
            lambda sample: self._post_event("hardware", {"sample": sample})
        )
        if self.production_start and "--smoke-test" not in sys.argv:
            threading.Thread(
                target=self._run_automatic_backup,
                daemon=True,
                name="automatic-metadata-backup",
            ).start()
        if show_splash:
            self.show_launch()
        else:
            self.show_project_center()
        self.events_after_id = self.root.after(60, self._process_events)
        if (
            show_splash
            and self.production_start
            and self.settings.get("auto_check_updates", True)
        ):
            self.update_after_id = self.root.after(
                900, self._run_scheduled_update_check
            )

    def _set_window_icon(self) -> None:
        try:
            with Image.open(resource_path("assets/qianyi-app-icon.png")) as source:
                self._app_icon_photo = ImageTk.PhotoImage(
                    source.convert("RGBA"), master=self.root
                )
            self.root.iconphoto(True, self._app_icon_photo)
        except (OSError, RuntimeError, ValueError, tk.TclError):
            self._app_icon_photo = None
        try:
            self.root.iconbitmap(default=str(resource_path("assets/qianyi-app.ico")))
        except tk.TclError:
            pass

    def _load_toolbar_icons(self) -> None:
        icon_keys = (
            "project", "image", "video", "single", "platform", "system", "night", "day"
        )
        for theme_key in THEMES:
            theme_icons = {}
            for icon_key in icon_keys:
                try:
                    with Image.open(
                        resource_path(
                            f"assets/nav-icons/{theme_key}/{icon_key}.png"
                        )
                    ) as source:
                        theme_icons[icon_key] = ImageTk.PhotoImage(
                            source.convert("RGBA"), master=self.root
                        )
                except (OSError, RuntimeError, ValueError, tk.TclError):
                    continue
            self.toolbar_icons[theme_key] = theme_icons

    def _toolbar_icon(self, icon_key: str):
        return self.toolbar_icons.get(self.theme_key, {}).get(icon_key, "")

    def _load_provider_icons(self) -> None:
        self.provider_icons.clear()
        for provider_key in (*PUBLIC_PROVIDER_KEYS, "connection"):
            try:
                with Image.open(
                    resource_path(f"assets/provider-icons/{provider_key}.png")
                ) as source:
                    self.provider_icons[provider_key] = ImageTk.PhotoImage(
                        source.convert("RGBA"), master=self.root
                    )
            except (OSError, RuntimeError, ValueError, tk.TclError):
                continue
        if "custom" not in self.provider_icons and "connection" in self.provider_icons:
            self.provider_icons["custom"] = self.provider_icons["connection"]

    def _configure_style(self, apply_global_palette: bool = True) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self._apply_tk_palette(apply_global_palette)
        ui_font = UI_FONT
        text_font = (ui_font, UI_TEXT_SIZE)
        small_font = (ui_font, UI_SMALL_SIZE)
        medium_font = (ui_font, UI_MEDIUM_SIZE, "bold")
        title_font = (ui_font, UI_TITLE_SIZE, "bold")
        input_font = (ui_font, UI_INPUT_SIZE)
        self.root.configure(background=COLORS["bg"])
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=text_font)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("SectionCard.TFrame", background=COLORS["surface"])
        style.configure("Topbar.TFrame", background=COLORS["topbar"])
        style.configure("SideRail.TFrame", background=COLORS["surface"])
        style.configure("StatusBar.TFrame", background=COLORS["surface_alt"])
        style.configure(
            "StatusPill.TFrame",
            background=COLORS["surface"],
            bordercolor=COLORS["border"],
            borderwidth=1,
            relief=tk.SOLID,
        )
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure(
            "SurfaceMuted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=small_font,
        )
        style.configure(
            "DialogTitle.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(ui_font, 17, "bold"),
        )
        style.configure(
            "DialogField.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=medium_font,
        )
        style.configure(
            "DialogError.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["danger"],
            font=small_font,
        )
        style.configure(
            "SurfaceSection.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=medium_font,
        )
        style.configure("Muted.TLabel", foreground=COLORS["muted"])
        style.configure("Title.TLabel", foreground=COLORS["text"], font=title_font)
        style.configure("SectionTitle.TLabel", foreground=COLORS["text"], font=(ui_font, UI_SECTION_SIZE, "bold"))
        style.configure("CenterTitle.TLabel", foreground=COLORS["text"], font=(ui_font, 22, "bold"))
        style.configure("TopbarTitle.TLabel", background=COLORS["topbar"], foreground=COLORS["text"], font=(ui_font, 17, "bold"))
        style.configure("TopbarBrand.TLabel", background=COLORS["topbar"], foreground=COLORS["accent"], font=(LATIN_FONT, UI_SMALL_SIZE, "bold"))
        style.configure("TopbarMuted.TLabel", background=COLORS["topbar"], foreground=COLORS["muted"])
        style.configure(
            "StatusBar.TLabel",
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            font=small_font,
        )
        style.configure(
            "StatusBackend.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["accent"],
            font=medium_font,
        )
        style.configure("AlertTitle.TLabel", foreground=COLORS["warning"], font=(ui_font, 19, "bold"))
        style.configure("Brand.TLabel", foreground=COLORS["accent"], font=(LATIN_FONT, UI_SMALL_SIZE, "bold"))
        style.configure("Stage.TLabel", foreground=COLORS["muted"], font=small_font)
        style.configure("StageActive.TLabel", foreground=COLORS["accent"], font=medium_font)
        style.configure("Billing.TLabel", foreground=COLORS["warning"], font=medium_font)
        style.configure("Update.TFrame", background=COLORS["update_bg"])
        style.configure("Update.TLabel", background=COLORS["update_bg"], foreground=COLORS["text"], font=medium_font)
        style.configure("UpdateMuted.TLabel", background=COLORS["update_bg"], foreground=COLORS["muted"], font=small_font)
        style.configure("StatusPending.TLabel", foreground=COLORS["info"], font=medium_font)
        style.configure("StatusSuccess.TLabel", foreground=COLORS["success"], font=medium_font)
        style.configure("StatusError.TLabel", foreground=COLORS["danger"], font=medium_font)
        for progress_style, color in (
            ("StatusCpu.Horizontal.TProgressbar", COLORS["accent"]),
            ("StatusGpu.Horizontal.TProgressbar", COLORS["semantic_violet"]),
            ("StatusMemory.Horizontal.TProgressbar", COLORS["success"]),
            ("StatusTemperature.Horizontal.TProgressbar", COLORS["danger"]),
        ):
            style.configure(
                progress_style,
                troughcolor=COLORS["input_disabled"],
                background=color,
                bordercolor=COLORS["surface_alt"],
                lightcolor=color,
                darkcolor=color,
                thickness=7,
            )
        style.configure("SemanticBlue.TLabel", background=COLORS["semantic_blue"], foreground="#ffffff", font=("Segoe UI Symbol", 11, "bold"), anchor=tk.CENTER)
        style.configure("SemanticYellow.TLabel", background=COLORS["semantic_yellow"], foreground="#171715", font=("Segoe UI Symbol", 11, "bold"), anchor=tk.CENTER)
        style.configure("SemanticRed.TLabel", background=COLORS["semantic_red"], foreground="#ffffff", font=("Segoe UI Symbol", 11, "bold"), anchor=tk.CENTER)
        style.configure("SemanticViolet.TLabel", background=COLORS["semantic_violet"], foreground="#ffffff", font=("Segoe UI Symbol", 11, "bold"), anchor=tk.CENTER)
        style.configure(
            "TButton", padding=(9, 5), background=COLORS["surface_alt"],
            foreground=COLORS["text"], bordercolor=COLORS["border"], relief=tk.FLAT,
        )
        style.map(
            "TButton",
            background=[("active", COLORS["hover"]), ("disabled", COLORS["surface"])],
            foreground=[("disabled", COLORS["disabled_fg"])],
        )
        style.configure(
            "Primary.TButton", background=COLORS["accent"], foreground=COLORS["primary_fg"],
            bordercolor=COLORS["accent"], font=medium_font,
        )
        style.map("Primary.TButton", background=[("active", COLORS["primary_hover"]), ("disabled", COLORS["primary_disabled"])])
        style.configure(
            "Nav.TButton", padding=(8, 10), background=COLORS["surface"],
            foreground=COLORS["muted"], bordercolor=COLORS["surface"],
            borderwidth=1, relief=tk.FLAT,
            font=small_font,
        )
        style.map(
            "Nav.TButton",
            background=[("active", COLORS["hover"]), ("pressed", COLORS["selection"])],
            foreground=[("active", COLORS["text"])],
            bordercolor=[("active", COLORS["accent"])],
        )
        style.configure(
            "NavActive.TButton", padding=(8, 10), background=COLORS["selection"],
            foreground=COLORS["accent"], bordercolor=COLORS["accent"],
            borderwidth=1, relief=tk.FLAT,
            font=(ui_font, UI_SMALL_SIZE, "bold"),
        )
        style.map(
            "NavActive.TButton",
            background=[("active", COLORS["accent_dark"])],
            bordercolor=[("active", COLORS["accent"])],
        )
        style.configure(
            "Theme.TButton", padding=(8, 6), background=COLORS["surface_alt"],
            foreground=COLORS["text"], bordercolor=COLORS["topbar_border"],
            borderwidth=1, relief=tk.FLAT, font=("Segoe UI Symbol", 15),
        )
        style.map(
            "Theme.TButton",
            background=[("active", COLORS["hover"])],
            foreground=[("active", COLORS["text"])],
            bordercolor=[("active", COLORS["accent"])],
        )
        style.configure(
            "ThemeActive.TButton", padding=(8, 6),
            background=COLORS["selection"], foreground=COLORS["accent"],
            bordercolor=COLORS["accent"], borderwidth=1, relief=tk.FLAT,
            font=("Segoe UI Symbol", 15, "bold"),
        )
        style.map(
            "ThemeActive.TButton",
            background=[("active", COLORS["accent_dark"])],
            bordercolor=[("active", COLORS["accent"])],
        )
        style.configure(
            "Icon.TButton", padding=(6, 5), background=COLORS["surface_alt"],
            foreground=COLORS["text"], bordercolor=COLORS["border"],
            font=("Segoe UI Symbol", 12),
        )
        style.map("Icon.TButton", background=[("active", COLORS["hover"])])
        style.configure(
            "Provider.TMenubutton",
            padding=(10, 3),
            background=COLORS["input_readonly"],
            foreground=COLORS["text"],
            bordercolor=COLORS["input_border"],
            borderwidth=1,
            relief=tk.FLAT,
            font=input_font,
        )
        style.map(
            "Provider.TMenubutton",
            background=[
                ("disabled", COLORS["input_disabled"]),
                ("active", COLORS["input_hover"]),
            ],
            foreground=[("disabled", COLORS["disabled_fg"])],
            bordercolor=[("focus", COLORS["input_focus"])],
        )
        style.configure(
            "DialogClose.TButton",
            padding=(7, 3),
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            bordercolor=COLORS["surface"],
            borderwidth=0,
            relief=tk.FLAT,
            font=("Segoe UI Symbol", 14),
        )
        style.map(
            "DialogClose.TButton",
            background=[("active", COLORS["danger_bg"])],
            foreground=[("active", COLORS["danger"])],
            bordercolor=[("active", COLORS["danger_border"])],
        )
        style.configure(
            "Danger.TButton", background=COLORS["danger_bg"], foreground=COLORS["danger"],
            bordercolor=COLORS["danger_border"], font=small_font,
        )
        style.map("Danger.TButton", background=[("active", COLORS["danger_hover"])])
        form_options = {
            "fieldbackground": COLORS["input_bg"],
            "background": COLORS["input_bg"],
            "foreground": COLORS["text"],
            "bordercolor": COLORS["input_border"],
            "lightcolor": COLORS["input_border"],
            "darkcolor": COLORS["input_border"],
            "insertcolor": COLORS["text"],
            "selectbackground": COLORS["selection"],
            "selectforeground": COLORS["text"],
            "borderwidth": 1,
            "relief": tk.FLAT,
        }
        rounded_field = self._configure_rounded_form_element(style)
        style.layout(
            "TEntry",
            [(rounded_field, {
                "sticky": "nswe",
                "children": [("Entry.padding", {
                    "sticky": "nswe",
                    "children": [("Entry.textarea", {"sticky": "nswe"})],
                })],
            })],
        )
        style.configure("TEntry", padding=(9, 2), font=input_font, **form_options)
        style.map(
            "TEntry",
            fieldbackground=[
                ("disabled", COLORS["input_disabled"]),
                ("readonly", COLORS["input_readonly"]),
                ("active", COLORS["input_hover"]),
            ],
            bordercolor=[("focus", COLORS["input_focus"])],
            lightcolor=[("focus", COLORS["input_focus"])],
            darkcolor=[("focus", COLORS["input_focus"])],
            foreground=[("disabled", COLORS["disabled_fg"])],
        )
        style.configure(
            "TCombobox", padding=(9, 1), arrowcolor=COLORS["muted"],
            arrowsize=14, font=input_font, **form_options,
        )
        style.layout(
            "TCombobox",
            [(rounded_field, {
                "sticky": "nswe",
                "children": [
                    ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                    ("Combobox.padding", {
                        "sticky": "nswe",
                        "children": [("Combobox.textarea", {"sticky": "nswe"})],
                    }),
                ],
            })],
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("disabled", COLORS["input_disabled"]),
                ("readonly", COLORS["input_readonly"]),
            ],
            background=[
                ("disabled", COLORS["input_disabled"]),
                ("active", COLORS["input_hover"]),
                ("readonly", COLORS["input_readonly"]),
            ],
            bordercolor=[("focus", COLORS["input_focus"])],
            lightcolor=[("focus", COLORS["input_focus"])],
            darkcolor=[("focus", COLORS["input_focus"])],
            arrowcolor=[
                ("disabled", COLORS["disabled_fg"]),
                ("active", COLORS["text"]),
            ],
            foreground=[
                ("disabled", COLORS["disabled_fg"]),
                ("readonly", COLORS["text"]),
            ],
        )
        style.configure(
            "TSpinbox", padding=(8, 1), arrowcolor=COLORS["muted"],
            arrowsize=12, font=input_font, **form_options,
        )
        style.layout(
            "TSpinbox",
            [(rounded_field, {
                "sticky": "nswe",
                "children": [
                    ("null", {
                        "side": "right",
                        "sticky": "",
                        "children": [
                            ("Spinbox.uparrow", {"side": "top", "sticky": "e"}),
                            ("Spinbox.downarrow", {"side": "bottom", "sticky": "e"}),
                        ],
                    }),
                    ("Spinbox.padding", {
                        "sticky": "nswe",
                        "children": [("Spinbox.textarea", {"sticky": "nswe"})],
                    }),
                ],
            })],
        )
        style.map(
            "TSpinbox",
            fieldbackground=[("disabled", COLORS["input_disabled"])],
            background=[
                ("disabled", COLORS["input_disabled"]),
                ("active", COLORS["input_hover"]),
            ],
            bordercolor=[("focus", COLORS["input_focus"])],
            lightcolor=[("focus", COLORS["input_focus"])],
            darkcolor=[("focus", COLORS["input_focus"])],
            arrowcolor=[
                ("disabled", COLORS["disabled_fg"]),
                ("active", COLORS["text"]),
            ],
            foreground=[("disabled", COLORS["disabled_fg"])],
        )
        # Popup Listboxes are synchronized explicitly because ttk caches them
        # after the first opening. The option database covers newly created
        # popdowns, while ``_sync_combobox_popdowns`` repairs existing ones.
        self.root.option_add("*TCombobox*Listbox.font", input_font, "interactive")
        indicator_options = {
            "indicatorcolor": COLORS["input_bg"],
            "indicatorrelief": tk.FLAT,
            "bordercolor": COLORS["input_border"],
            "lightcolor": COLORS["input_border"],
            "darkcolor": COLORS["input_border"],
        }
        style.configure(
            "TRadiobutton", background=COLORS["bg"], foreground=COLORS["text"],
            **indicator_options,
        )
        style.map(
            "TRadiobutton",
            background=[("active", COLORS["bg"])],
            foreground=[("active", COLORS["text"])],
            indicatorcolor=[("selected", COLORS["accent"]), ("active", COLORS["input_hover"])],
        )
        style.configure(
            "Surface.TRadiobutton",
            background=COLORS["surface"], foreground=COLORS["text"],
            **indicator_options,
        )
        style.map(
            "Surface.TRadiobutton",
            background=[("active", COLORS["surface"])],
            foreground=[("active", COLORS["text"])],
            indicatorcolor=[("selected", COLORS["accent"]), ("active", COLORS["input_hover"])],
        )
        style.configure(
            "TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"],
            **indicator_options,
        )
        style.map(
            "TCheckbutton",
            background=[("active", COLORS["bg"])],
            foreground=[("active", COLORS["text"])],
            indicatorcolor=[("selected", COLORS["accent"]), ("active", COLORS["input_hover"])],
        )
        style.configure(
            "Surface.TCheckbutton",
            background=COLORS["surface"], foreground=COLORS["text"],
            **indicator_options,
        )
        style.map(
            "Surface.TCheckbutton",
            background=[("active", COLORS["surface"])],
            foreground=[("active", COLORS["text"])],
            indicatorcolor=[("selected", COLORS["accent"]), ("active", COLORS["input_hover"])],
        )
        style.configure(
            "TLabelframe",
            background=COLORS["bg"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            borderwidth=1,
            relief=tk.FLAT,
        )
        style.configure(
            "TLabelframe.Label",
            background=COLORS["bg"], foreground=COLORS["muted"],
            font=medium_font,
        )
        style.configure(
            "Treeview", rowheight=30, background=COLORS["surface"],
            fieldbackground=COLORS["surface"], foreground=COLORS["text"],
            bordercolor=COLORS["border"], lightcolor=COLORS["border"],
            darkcolor=COLORS["border"], borderwidth=0, font=text_font,
        )
        style.map("Treeview", background=[("selected", COLORS["selection"])], foreground=[("selected", COLORS["text"])])
        style.configure("Treeview.Heading", background=COLORS["surface_alt"], foreground=COLORS["text"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], font=medium_font)
        style.map("Treeview.Heading", background=[("active", COLORS["hover"])])
        style.configure(
            "TNotebook", background=COLORS["bg"], bordercolor=COLORS["bg"],
            lightcolor=COLORS["bg"], darkcolor=COLORS["bg"], borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab", padding=(12, 7), background=COLORS["surface"],
            foreground=COLORS["muted"], bordercolor=COLORS["border"],
            lightcolor=COLORS["border"], darkcolor=COLORS["border"],
            borderwidth=1, relief=tk.FLAT,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["surface_alt"]), ("active", COLORS["hover"])],
            foreground=[("selected", COLORS["text"]), ("active", COLORS["text"])],
            bordercolor=[("selected", COLORS["accent"])],
            lightcolor=[("selected", COLORS["accent"])],
            darkcolor=[("selected", COLORS["accent"])],
        )
        style.configure(
            "Compact.TNotebook",
            background=COLORS["bg"],
            bordercolor=COLORS["bg"],
            lightcolor=COLORS["bg"],
            darkcolor=COLORS["bg"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Compact.TNotebook.Tab",
            padding=(7, 6),
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            borderwidth=1,
            relief=tk.FLAT,
            font=small_font,
        )
        style.map(
            "Compact.TNotebook.Tab",
            background=[("selected", COLORS["surface_alt"])],
            foreground=[("selected", COLORS["text"])],
            bordercolor=[("selected", COLORS["accent"])],
            lightcolor=[("selected", COLORS["accent"])],
            darkcolor=[("selected", COLORS["accent"])],
        )
        style.configure(
            "TProgressbar", troughcolor=COLORS["surface"], background=COLORS["accent"],
            bordercolor=COLORS["border"], lightcolor=COLORS["border"],
            darkcolor=COLORS["border"], borderwidth=0,
        )
        style.configure(
            "TScrollbar", background=COLORS["surface_alt"], troughcolor=COLORS["bg"],
            bordercolor=COLORS["bg"], lightcolor=COLORS["surface_alt"],
            darkcolor=COLORS["surface_alt"], arrowcolor=COLORS["muted"],
            borderwidth=0, relief=tk.FLAT,
        )
        style.configure(
            "ComboboxPopdown.TFrame",
            background=COLORS["input_bg"],
            bordercolor=COLORS["input_border"],
            lightcolor=COLORS["input_border"],
            darkcolor=COLORS["input_border"],
            borderwidth=0,
            relief=tk.FLAT,
        )
        style.configure("TSeparator", background=COLORS["border"])

    def _adaptive_typography(self, _event=None, force: bool = False) -> None:
        """Keep form values readable while a user resizes the workbench.

        The main workbench uses a stable reading scale.  Only dense form
        controls step between three size buckets, so a narrow window avoids
        clipping while a normal or maximized window keeps the larger value
        text the user expects.  Bucket changes are guarded to avoid resize
        feedback loops.
        """
        if self.closing:
            return
        try:
            width = int(self.root.winfo_width())
        except (tk.TclError, TypeError, ValueError):
            return
        if width <= 1:
            # Hidden roots are used during startup and in headless UI tests.
            # Use the normal readable size until the real window is mapped.
            bucket, size = "large", UI_INPUT_SIZE
        elif width < 1180:
            bucket, size = "compact", 14
        elif width < 1500:
            bucket, size = "medium", 15
        else:
            bucket, size = "large", UI_INPUT_SIZE
        if not force and bucket == self._typography_bucket:
            return
        self._typography_bucket = bucket
        font = (UI_FONT, size)
        try:
            style = ttk.Style(self.root)
            for style_name in ("TEntry", "TCombobox", "TSpinbox", "Provider.TMenubutton"):
                style.configure(style_name, font=font)
            self.root.option_add("*TCombobox*Listbox.font", font, "interactive")
            for widget in self._walk_widget_tree():
                if isinstance(
                    widget,
                    (ttk.Entry, ttk.Combobox, ttk.Spinbox, ttk.Menubutton),
                ):
                    try:
                        widget.configure(font=font)
                    except tk.TclError:
                        pass
            self._sync_combobox_popdowns()
        except tk.TclError:
            pass

    def _apply_tk_palette(self, apply_global_palette: bool = True) -> None:
        """Keep classic Tk widgets on the same palette as ttk.

        Windows can repaint Text/Listbox widgets from the process palette after
        a ttk theme change. Updating the classic palette first prevents an old
        day/night color from being restored during a later expose event.
        """
        if apply_global_palette:
            try:
                self.root.tk.call(
                    "tk_setPalette",
                    "background", COLORS["bg"],
                    "foreground", COLORS["text"],
                    "activeBackground", COLORS["hover"],
                    "activeForeground", COLORS["text"],
                    "selectBackground", COLORS["selection"],
                    "selectForeground", COLORS["text"],
                    "highlightColor", COLORS["input_focus"],
                    "highlightBackground", COLORS["input_border"],
                    "insertBackground", COLORS["text"],
                    "disabledForeground", COLORS["disabled_fg"],
                    "troughColor", COLORS["surface"],
                )
            except tk.TclError:
                pass
        for pattern, value in (
            ("*Text.background", COLORS["input_bg"]),
            ("*Text.foreground", COLORS["text"]),
            ("*Text.insertBackground", COLORS["text"]),
            ("*Text.selectBackground", COLORS["selection"]),
            ("*Text.selectForeground", COLORS["text"]),
            ("*Listbox.background", COLORS["input_bg"]),
            ("*Listbox.foreground", COLORS["text"]),
            ("*Listbox.selectBackground", COLORS["selection"]),
            ("*Listbox.selectForeground", COLORS["text"]),
            ("*Listbox.disabledForeground", COLORS["disabled_fg"]),
            ("*Listbox.highlightBackground", COLORS["input_border"]),
            ("*Listbox.highlightColor", COLORS["input_focus"]),
            ("*Menu.background", COLORS["surface_alt"]),
            ("*Menu.foreground", COLORS["text"]),
            ("*Menu.activeBackground", COLORS["hover"]),
            ("*Menu.activeForeground", COLORS["text"]),
            ("*Menu.disabledForeground", COLORS["disabled_fg"]),
            ("*TCombobox*Listbox.background", COLORS["input_bg"]),
            ("*TCombobox*Listbox.foreground", COLORS["text"]),
            ("*TCombobox*Listbox.selectBackground", COLORS["selection"]),
            ("*TCombobox*Listbox.selectForeground", COLORS["text"]),
            ("*TCombobox*Listbox.disabledForeground", COLORS["disabled_fg"]),
            ("*TCombobox*Listbox.highlightBackground", COLORS["input_border"]),
            ("*TCombobox*Listbox.highlightColor", COLORS["input_focus"]),
        ):
            # ``interactive`` outranks cached Windows/startup defaults and is
            # also inherited by popdowns created after a theme switch.
            self.root.option_add(pattern, value, "interactive")

    def _configure_rounded_form_element(self, style: ttk.Style) -> str:
        element_name = f"{self.theme_key.title()}.RoundedForm.field"
        if element_name in style.element_names():
            return element_name

        specs = {
            "normal": (COLORS["input_bg"], COLORS["input_border"]),
            "readonly": (COLORS["input_readonly"], COLORS["input_border"]),
            "hover": (COLORS["input_hover"], COLORS["input_border"]),
            "focus": (COLORS["input_bg"], COLORS["input_focus"]),
            "disabled": (COLORS["input_disabled"], COLORS["border"]),
        }
        photos = {}
        for state, (fill, outline) in specs.items():
            scale = 4
            width, height = 64, 40
            image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (2 * scale, 2 * scale, (width - 2) * scale, (height - 2) * scale),
                radius=8 * scale,
                fill=fill,
                outline=outline,
                width=1 * scale,
            )
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.root)
            self._form_style_images[f"{self.theme_key}:{state}"] = photo
            photos[state] = photo

        style.element_create(
            element_name,
            "image",
            photos["normal"],
            ("disabled", photos["disabled"]),
            ("focus", photos["focus"]),
            ("active", photos["hover"]),
            ("readonly", photos["readonly"]),
            border=(10, 10, 10, 10),
            sticky="nsew",
        )
        return element_name

    def _build_theme_toggle(self, parent) -> ttk.Frame:
        controls = ttk.Frame(parent, style="Topbar.TFrame")
        for theme_key, symbol, tooltip in (
            ("night", "☾", "夜光模式"),
            ("day", "☀", "日光模式"),
        ):
            button = ttk.Button(
                controls,
                text="" if self._toolbar_icon(theme_key) else symbol,
                image=self._toolbar_icon(theme_key),
                command=lambda key=theme_key: self._set_theme(key),
            )
            button.image = self._toolbar_icon(theme_key)
            button.pack(side=tk.LEFT, padx=(0, 4) if theme_key == "night" else 0)
            self.theme_buttons[theme_key].append(button)
            ToolTip(button, tooltip)
        self._refresh_theme_buttons()
        return controls

    def _refresh_theme_buttons(self) -> None:
        for theme_key, buttons in self.theme_buttons.items():
            style = "ThemeActive.TButton" if theme_key == self.theme_key else "Theme.TButton"
            for button in buttons:
                icon = self._toolbar_icon(theme_key)
                fallback = "☾" if theme_key == "night" else "☀"
                button.configure(
                    style=style,
                    image=icon,
                    text="" if icon else fallback,
                )
                button.image = icon

    def _refresh_toolbar_icons(self) -> None:
        if not hasattr(self, "nav_buttons"):
            return
        for icon_key, label, fallback in (
            ("project", "项目中心", "⌂"),
            ("image", "图像打标", "▧"),
            ("video", "视频反推", "▶"),
            ("single", "单次反推", "✦"),
            ("platform", "平台设置", "☷"),
            ("system", "系统说明", "ⓘ"),
        ):
            icon = self._toolbar_icon(icon_key)
            button = self.nav_buttons.get(icon_key)
            if button is not None:
                button.configure(
                    image=icon,
                    text=label if icon else f"{fallback}\n{label}",
                )
                button.image = icon
        self._refresh_system_nav_icons()
        self._refresh_single_nav_icons()

    def _set_window_redraw(self, enabled: bool) -> None:
        """Freeze native painting while a complete theme palette is applied."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.root.winfo_id())
            ctypes.windll.user32.SendMessageW(
                hwnd, 0x000B, 1 if enabled else 0, 0  # WM_SETREDRAW
            )
            if enabled:
                ctypes.windll.user32.RedrawWindow(
                    hwnd,
                    None,
                    None,
                    0x0080 | 0x0100 | 0x0200,  # FRAME|INVALIDATE|UPDATENOW
                )
        except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
            pass

    def _set_theme(self, theme_key: str) -> None:
        normalized = theme_key if theme_key in THEMES else DEFAULT_THEME_KEY
        # Drop any deferred palette/layout work queued by a view change. If it
        # runs after this synchronous switch, Windows paints the old palette
        # for a second pass and the transition looks sluggish.
        self._cancel_theme_sync_jobs()
        self._cancel_text_sync_jobs()
        self._set_window_redraw(False)
        try:
            self.theme_key = activate_theme(normalized)
            self.settings["theme"] = self.theme_key
            # Reconfigure ttk and native widgets in-place.  Re-running
            # tk_setPalette here repaints every widget in the process and is
            # what made the theme switch visibly crawl on Windows.
            self._configure_style(apply_global_palette=False)
            self._apply_native_theme()
            self._refresh_toolbar_icons()
            self._refresh_theme_buttons()
            self._adaptive_typography(force=True)
        finally:
            self._set_window_redraw(True)

    def _apply_native_theme(self) -> None:
        self.root.configure(background=COLORS["bg"])
        self.gallery.canvas.configure(background=COLORS["bg"])
        self.gallery.content.configure(background=COLORS["bg"])
        self.gallery.apply_theme()
        self.prompt_form_canvas.configure(background=COLORS["bg"])
        self.task_settings_canvas.configure(background=COLORS["bg"])
        self.preview_viewport.configure(background=COLORS["media_bg"])
        self.preview_label.configure(
            background=COLORS["media_bg"], foreground=COLORS["muted"]
        )
        for canvas, color_key in (
            (getattr(self, "single_image_canvas", None), "media_bg"),
            (getattr(self, "single_media_canvas", None), "media_bg"),
            (getattr(self, "single_editor_preview_canvas", None), "media_bg"),
            (getattr(self, "single_timeline_canvas", None), "input_readonly"),
        ):
            if canvas is not None:
                canvas.configure(background=COLORS[color_key])
        if (
            hasattr(self, "single_image_canvas")
            and getattr(self, "single_reverse_frame", None) is not None
            and self.single_reverse_frame.winfo_manager() == "pack"
        ):
            self._render_single_image()
            self._render_single_media_preview()
            self._render_single_timeline()
        for label, tone in self.semantic_icon_labels:
            label.configure(
                background=COLORS[f"semantic_{tone}"],
                foreground="#ffffff" if tone != "yellow" else "#171715",
            )
        self._sync_themed_text_widgets()
        self._sync_themed_menus()
        self._sync_combobox_popdowns()
        self._sync_slide_switches()
        self.tree.tag_configure("failed", foreground=COLORS["danger"])
        self.tree.tag_configure("success", foreground=COLORS["success"])
        self.tree.tag_configure("running", foreground=COLORS["info"])
        self.tree.tag_configure("skipped", foreground=COLORS["muted"])
        self.tree.tag_configure("orphan", foreground=COLORS["warning"])
        self.project_tree.tag_configure("missing", foreground=COLORS["warning"])

    def _prompt_form_content_resized(self, _event=None) -> None:
        try:
            bounds = self.prompt_form_canvas.bbox("all")
            self.prompt_form_canvas.configure(
                scrollregion=bounds or (0, 0, 0, 0)
            )
        except tk.TclError:
            pass

    def _left_platform_mousewheel(self, event):
        """Scroll the complete left inspector without changing field values."""
        canvas = getattr(self, "left_platform_canvas", None)
        if canvas is None:
            return None
        try:
            if not canvas.winfo_ismapped():
                return None
            first, last = canvas.yview()
            delta = getattr(event, "delta", 0)
            if delta == 0:
                delta = 120 if getattr(event, "num", 0) == 4 else -120
            units = max(1, abs(int(delta)) // 120)
            if delta > 0:
                if first <= 0.0:
                    return "break"
                canvas.yview_scroll(-units, "units")
            else:
                if last >= 1.0:
                    return "break"
                canvas.yview_scroll(units, "units")
        except tk.TclError:
            return None
        return "break"

    def _bind_left_platform_wheel(self, widget) -> None:
        """Install wheel handling on every child of the left inspector."""
        try:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._left_platform_mousewheel, add="+")
            for child in widget.winfo_children():
                self._bind_left_platform_wheel(child)
        except tk.TclError:
            pass

    def _prompt_form_canvas_resized(self, event) -> None:
        try:
            self.prompt_form_canvas.itemconfigure(
                self.prompt_form_window_id,
                width=max(1, int(event.width)),
            )
            self._prompt_form_content_resized()
        except tk.TclError:
            pass

    def _prompt_form_mousewheel(self, event):
        canvas = self.prompt_form_canvas
        try:
            if not canvas.winfo_ismapped():
                return None
            first, last = canvas.yview()
        except tk.TclError:
            return None
        if event.delta > 0 and first <= 0.0:
            return "break"
        if event.delta < 0 and last >= 1.0:
            return "break"
        units = max(1, abs(int(event.delta)) // 120)
        canvas.yview_scroll(-units if event.delta > 0 else units, "units")
        return "break"

    def _prompt_text_mousewheel(self, event, widget: tk.Text):
        try:
            if not widget.winfo_ismapped():
                return "break"
            first, last = widget.yview()
        except tk.TclError:
            return "break"
        units = max(1, abs(int(event.delta)) // 120)
        if event.delta > 0 and first > 0.0:
            widget.yview_scroll(-units, "units")
            return "break"
        if event.delta < 0 and last < 1.0:
            widget.yview_scroll(units, "units")
            return "break"
        return self._prompt_form_mousewheel(event)

    def _task_settings_content_resized(self, _event=None) -> None:
        try:
            bounds = self.task_settings_canvas.bbox("all")
            self.task_settings_canvas.configure(scrollregion=bounds or (0, 0, 0, 0))
        except tk.TclError:
            pass

    def _task_settings_canvas_resized(self, event) -> None:
        try:
            self.task_settings_canvas.itemconfigure(
                self.task_settings_window_id,
                width=max(1, int(event.width)),
            )
            self._task_settings_content_resized()
        except tk.TclError:
            pass

    def _task_settings_mousewheel(self, event, force: bool = False):
        canvas = self.task_settings_canvas
        try:
            if not canvas.winfo_ismapped():
                return None
            pointer_x = canvas.winfo_pointerx()
            pointer_y = canvas.winfo_pointery()
            left = canvas.winfo_rootx()
            top = canvas.winfo_rooty()
            if not force and not (
                left <= pointer_x < left + canvas.winfo_width()
                and top <= pointer_y < top + canvas.winfo_height()
            ):
                return None
            first, last = canvas.yview()
        except tk.TclError:
            return None
        if event.delta > 0 and first <= 0.0:
            return "break"
        if event.delta < 0 and last >= 1.0:
            return "break"
        units = max(1, abs(int(event.delta)) // 120)
        canvas.yview_scroll(-units if event.delta > 0 else units, "units")
        return "break"

    def _install_form_wheel_guards(self) -> None:
        # ttk binds MouseWheel to Combobox/Spinbox classes by default, which
        # silently changes the current option when the user only intends to
        # scroll the inspector. Replace that class behavior. The pop-down uses
        # a Listbox class, so an explicitly opened option list remains scrollable.
        for class_name in ("TCombobox", "TSpinbox"):
            self.root.bind_class(
                class_name,
                "<MouseWheel>",
                self._form_control_mousewheel,
            )

    def _form_control_mousewheel(self, event):
        if hasattr(self, "task_settings_canvas"):
            # Class bindings receive the event before Tk's native combobox or
            # spinbox handler.  Force the inspector scroll here so the wheel
            # never changes a selected option, even when the pointer is over a
            # child element whose coordinates are reported differently on DPI
            # scaled Windows desktops.
            self._task_settings_mousewheel(event, force=True)
        return "break"

    def _refresh_task_settings_scroll(self, show_bottom: bool = False) -> None:
        if self.closing:
            return
        self.task_settings_content.update_idletasks()
        self._task_settings_content_resized()
        if show_bottom:
            self.task_settings_canvas.yview_moveto(1.0)

    def _window_unmapped(self, event) -> None:
        if self.closing or event.widget is not self.root:
            return
        self._window_suspended = True
        if self._window_resume_after_id is not None:
            try:
                self.root.after_cancel(self._window_resume_after_id)
            except tk.TclError:
                pass
            self._window_resume_after_id = None
        if hasattr(self, "gallery"):
            self.gallery.cancel_pending_layout()
        if self._preview_resize_after is not None:
            try:
                self.root.after_cancel(self._preview_resize_after)
            except tk.TclError:
                pass
            self._preview_resize_after = None
        self._cancel_theme_sync_jobs()
        self._cancel_text_sync_jobs()

    def _window_mapped(self, event) -> None:
        if self.closing or event.widget is not self.root or not self._window_suspended:
            return
        if self._window_resume_after_id is not None:
            try:
                self.root.after_cancel(self._window_resume_after_id)
            except tk.TclError:
                pass
        self._window_resume_after_id = self.root.after(
            90, self._resume_after_window_map
        )

    def _resume_after_window_map(self) -> None:
        self._window_resume_after_id = None
        if self.closing:
            return
        try:
            if str(self.root.state()) in {"iconic", "withdrawn"}:
                return
        except tk.TclError:
            return
        self._window_suspended = False
        if self.workspace_frame.winfo_manager() != "pack":
            return
        self._layout_workspace_header(self.workspace_header.winfo_width())
        self._layout_filter_bar(self.filter_bar.winfo_width())
        self.gallery.refresh_layout()
        self._preview_resized()

    def _layout_workspace_header(self, width: int) -> None:
        layout = "compact" if width < 1180 else "wide"
        if layout == self._workspace_header_layout:
            return
        self._workspace_header_layout = layout
        title = self.workspace_title_block
        project = self.workspace_project_bar
        controls = self.workspace_topbar_controls
        title.grid(
            row=0, column=0, columnspan=1, sticky=tk.W, padx=0, pady=0
        )
        controls.grid(
            row=0, column=2, columnspan=1, sticky=tk.E, padx=0, pady=0
        )
        if layout == "compact":
            project.grid(
                row=1, column=0, columnspan=3, sticky=tk.EW,
                padx=0, pady=(8, 0),
            )
        else:
            project.grid(
                row=0, column=1, columnspan=1, sticky=tk.EW,
                padx=(14, 10), pady=0,
            )

    def _layout_filter_bar(self, width: int) -> None:
        layout = "compact" if width < 760 else "wide"
        if layout == self._filter_bar_layout:
            return
        self._filter_bar_layout = layout
        for column in range(6):
            self.filter_bar.grid_columnconfigure(column, weight=0)

        if layout == "compact":
            self.filter_bar.grid_columnconfigure(0, weight=1)
            self.search_entry.grid(
                row=0, column=0, columnspan=5, sticky=tk.EW,
                padx=(0, 10), pady=(0, 7),
            )
            self.selection_label.grid(
                row=0, column=5, columnspan=1, sticky=tk.E,
                padx=0, pady=0,
            )
            self.filter_status_label.grid(
                row=1, column=0, columnspan=1, sticky=tk.W,
                padx=0, pady=0,
            )
            self.filter_box.grid(
                row=1, column=1, columnspan=1, sticky=tk.W,
                padx=(6, 12), pady=0,
            )
            self.gallery_view_button.grid(
                row=1, column=2, columnspan=1, sticky=tk.W,
                padx=0, pady=0,
            )
            self.list_view_button.grid(
                row=1, column=3, columnspan=1, sticky=tk.W,
                padx=(4, 0), pady=0,
            )
        else:
            self.filter_bar.grid_columnconfigure(0, weight=1)
            self.search_entry.grid(
                row=0, column=0, columnspan=1, sticky=tk.EW,
                padx=0, pady=0,
            )
            self.filter_status_label.grid(
                row=0, column=1, columnspan=1, sticky=tk.W,
                padx=(12, 6), pady=0,
            )
            self.filter_box.grid(
                row=0, column=2, columnspan=1, sticky=tk.W,
                padx=0, pady=0,
            )
            self.gallery_view_button.grid(
                row=0, column=3, columnspan=1, sticky=tk.W,
                padx=(12, 2), pady=0,
            )
            self.list_view_button.grid(
                row=0, column=4, columnspan=1, sticky=tk.W,
                padx=0, pady=0,
            )
            self.selection_label.grid(
                row=0, column=5, columnspan=1, sticky=tk.E,
                padx=(12, 0), pady=0,
            )

    def _layout_preview_panel(self, event) -> None:
        viewport_height = max(140, min(390, event.height - 250))
        wraplength = max(160, event.width - 20)
        layout = (viewport_height, wraplength)
        if layout == self._preview_panel_layout:
            return
        self._preview_panel_layout = layout
        if int(self.preview_viewport.cget("height")) != viewport_height:
            self.preview_viewport.configure(height=viewport_height)
        self.selected_item_label.configure(wraplength=wraplength)

    def _layout_canvas_heading(self, event) -> None:
        subtitle = getattr(self.canvas_heading, "subtitle_label", None)
        if subtitle is None:
            return
        compact = event.width < 620
        if compact == self._canvas_heading_compact:
            return
        self._canvas_heading_compact = compact
        if compact:
            subtitle.pack_forget()
        elif not subtitle.winfo_manager():
            subtitle.pack(anchor=tk.W, pady=(1, 0))

    def _layout_system_features(self, event) -> None:
        try:
            if self.system_info_canvas.winfo_width() <= 1:
                return
        except tk.TclError:
            return
        wraplength = max(250, (event.width - 70) // 2)
        if wraplength == self._system_features_wraplength:
            return
        self._system_features_wraplength = wraplength
        for label in self.system_feature_descriptions:
            label.configure(wraplength=wraplength)

    def _layout_runtime_portals(self, width: int) -> None:
        try:
            if self.system_info_canvas.winfo_width() <= 1:
                return
        except tk.TclError:
            return
        wraplength = max(180, (int(width) - 50) // 2)
        for label in getattr(self, "system_runtime_descriptions", ()):
            label.configure(wraplength=wraplength)

    def _layout_system_update_state(self, event) -> None:
        label = getattr(self, "system_update_state_label", None)
        if label is None:
            return
        try:
            if self.system_info_canvas.winfo_width() <= 1:
                return
        except tk.TclError:
            return
        try:
            date_width = self.system_release_date_label.winfo_reqwidth()
            label.configure(wraplength=max(220, int(event.width) - date_width - 24))
        except tk.TclError:
            pass

    def _system_info_content_resized(self, _event=None) -> None:
        try:
            if self.system_info_canvas.winfo_width() <= 1:
                return
        except tk.TclError:
            return
        try:
            bounds = self.system_info_canvas.bbox("all")
            self.system_info_canvas.configure(
                scrollregion=bounds or (0, 0, 0, 0)
            )
        except tk.TclError:
            pass

    def _system_info_canvas_resized(self, event) -> None:
        try:
            if (
                int(getattr(event, "width", 0)) <= 1
            ):
                return
            self.system_info_canvas.itemconfigure(
                self.system_info_window_id,
                width=max(1, int(event.width)),
            )
            self._system_info_content_resized()
        except tk.TclError:
            pass

    def _fit_system_info_content(self) -> None:
        """Stretch the embedded system-info surface to the visible canvas."""
        if self.closing:
            return
        try:
            canvas = self.system_info_canvas
            if not canvas.winfo_exists():
                return
            width = int(canvas.winfo_width())
            if width <= 1:
                return
            canvas.itemconfigure(self.system_info_window_id, width=width)
            self._system_info_content_resized()
        except (tk.TclError, TypeError, ValueError):
            pass

    def _system_info_mousewheel(self, event):
        canvas = getattr(self, "system_info_canvas", None)
        if canvas is None:
            return None
        try:
            first, last = canvas.yview()
            delta = getattr(event, "delta", 0)
            if delta == 0:
                delta = 120 if getattr(event, "num", 0) == 4 else -120
            units = max(1, abs(int(delta)) // 120)
            if delta > 0:
                if first <= 0.0:
                    return "break"
                canvas.yview_scroll(-units, "units")
            else:
                if last >= 1.0:
                    return "break"
                canvas.yview_scroll(units, "units")
        except tk.TclError:
            return None
        return "break"

    def _bind_system_info_wheel(self, widget) -> None:
        try:
            # Let the release-notes Text widget keep its own internal scroll.
            if isinstance(widget, tk.Text):
                return
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._system_info_mousewheel, add="+")
            for child in widget.winfo_children():
                self._bind_system_info_wheel(child)
        except tk.TclError:
            pass

    def open_local_runtime_portal(self, runtime_key: str) -> None:
        info = LOCAL_RUNTIME_PORTALS.get(runtime_key)
        if not info:
            return
        try:
            opened = webbrowser.open(info["url"], new=2)
        except OSError as error:
            messagebox.showerror(
                "无法打开下载页", str(error), parent=self.root
            )
            return
        if not opened:
            messagebox.showinfo(
                "下载地址",
                f"请在浏览器中访问：\n{info['url']}",
                parent=self.root,
            )

    def _semantic_heading(
        self,
        parent,
        symbol: str,
        title: str,
        tone: str = "blue",
        subtitle: str = "",
    ) -> ttk.Frame:
        row = ttk.Frame(parent)
        icon = tk.Label(
            row,
            text=symbol,
            width=2,
            height=1,
            background=COLORS[f"semantic_{tone}"],
            foreground="#ffffff" if tone != "yellow" else "#171715",
            font=("Segoe UI Symbol", 11, "bold"),
            anchor=tk.CENTER,
        )
        icon.pack(side=tk.LEFT, padx=(0, 9))
        self.semantic_icon_labels.append((icon, tone))
        copy = ttk.Frame(row)
        copy.pack(side=tk.LEFT, fill=tk.X, expand=True)
        row.copy_frame = copy
        ttk.Label(copy, text=title, style="SectionTitle.TLabel").pack(anchor=tk.W)
        if subtitle:
            subtitle_label = ttk.Label(copy, text=subtitle, style="Muted.TLabel")
            subtitle_label.pack(anchor=tk.W, pady=(1, 0))
            row.subtitle_label = subtitle_label
        return row

    def _build_ui(self) -> None:
        self.semantic_icon_labels: list[tuple[tk.Label, str]] = []
        self.view_host = ttk.Frame(self.root)
        self.view_host.pack(fill=tk.BOTH, expand=True)
        self.launch_frame = tk.Frame(self.view_host, background="#090d1d")
        self.project_center_frame = ttk.Frame(self.view_host)
        self.workspace_frame = ttk.Frame(self.view_host)
        self.single_reverse_frame = ttk.Frame(self.view_host)
        self.system_info_frame = ttk.Frame(self.view_host)
        self._build_launch_view()
        self._build_project_center()
        self._build_system_info()

        status_host = ttk.Frame(
            self.workspace_frame,
            style="StatusBar.TFrame",
            padding=(12, 6),
        )
        status_host.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_host = status_host
        self.platform_status_var = tk.StringVar(value="火山引擎")
        self.footer_platform_var = tk.StringVar(value="火山引擎")
        backend_pill = ttk.Frame(
            status_host,
            style="StatusPill.TFrame",
            padding=(12, 8),
        )
        backend_pill.pack(side=tk.LEFT)
        ttk.Label(
            backend_pill,
            textvariable=self.footer_platform_var,
            style="StatusBackend.TLabel",
        ).pack()

        hardware_panel = ttk.Frame(status_host, style="StatusBar.TFrame")
        hardware_panel.pack(side=tk.RIGHT)
        self.cpu_percent_var = tk.DoubleVar(value=0)
        self.gpu_percent_var = tk.DoubleVar(value=0)
        self.memory_percent_var = tk.DoubleVar(value=0)
        self.vram_percent_var = tk.DoubleVar(value=0)
        self.cpu_temperature_var = tk.DoubleVar(value=0)
        self.gpu_temperature_var = tk.DoubleVar(value=0)
        self.cpu_metric_var = tk.StringVar(value="--")
        self.gpu_metric_var = tk.StringVar(value="--")
        self.memory_metric_var = tk.StringVar(value="--")
        self.vram_metric_var = tk.StringVar(value="--")
        self.cpu_temperature_metric_var = tk.StringVar(value="--")
        self.gpu_temperature_metric_var = tk.StringVar(value="--")

        def status_metric(
            row: int,
            column: int,
            label: str,
            progress_variable: tk.DoubleVar,
            text_variable: tk.StringVar,
            progress_style: str,
        ) -> None:
            metric = ttk.Frame(hardware_panel, style="StatusBar.TFrame")
            metric.grid(
                row=row,
                column=column,
                sticky=tk.EW,
                padx=(8 if column else 0, 0),
                pady=(0, 3) if row == 0 else (3, 0),
            )
            ttk.Label(
                metric,
                text=label,
                # The previous 9-character slot was just wide enough for the
                # old 11px font.  It clips CJK labels and icon-prefixed labels
                # after the workbench readability scale is applied.
                width=10,
                anchor=tk.W,
                style="StatusBar.TLabel",
            ).pack(side=tk.LEFT)
            ttk.Progressbar(
                metric,
                variable=progress_variable,
                maximum=100,
                length=50,
                mode="determinate",
                style=progress_style,
            ).pack(side=tk.LEFT, padx=(2, 4))
            ttk.Label(
                metric,
                textvariable=text_variable,
                # Reserve one extra character for values such as 17.7/64G
                # and 100%, preventing the rightmost glyph from being cut.
                width=9,
                anchor=tk.W,
                style="StatusBar.TLabel",
            ).pack(side=tk.LEFT)

        status_metric(
            0, 0, "⚙ CPU", self.cpu_percent_var, self.cpu_metric_var,
            "StatusCpu.Horizontal.TProgressbar",
        )
        status_metric(
            1, 0, "▣ GPU", self.gpu_percent_var, self.gpu_metric_var,
            "StatusGpu.Horizontal.TProgressbar",
        )
        status_metric(
            0, 1, "♨ CPU温度", self.cpu_temperature_var,
            self.cpu_temperature_metric_var,
            "StatusTemperature.Horizontal.TProgressbar",
        )
        status_metric(
            1, 1, "▤ 显存", self.vram_percent_var, self.vram_metric_var,
            "StatusGpu.Horizontal.TProgressbar",
        )
        status_metric(
            0, 2, "▥ 内存", self.memory_percent_var, self.memory_metric_var,
            "StatusMemory.Horizontal.TProgressbar",
        )
        status_metric(
            1, 2, "♨ 显卡温度", self.gpu_temperature_var,
            self.gpu_temperature_metric_var,
            "StatusTemperature.Horizontal.TProgressbar",
        )

        task_bar = ttk.Frame(
            self.workspace_frame, style="Surface.TFrame", padding=(12, 8)
        )
        task_bar.pack(side=tk.BOTTOM, fill=tk.X)
        action_row = ttk.Frame(task_bar, style="Surface.TFrame")
        action_row.pack(fill=tk.X)
        self.start_button = ttk.Button(
            action_row, text="开始任务", width=11,
            style="Primary.TButton", command=self.start_task,
        )
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(
            action_row, text="停止", width=7,
            state=tk.DISABLED, command=self.stop_task,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(6, 0))
        self.retry_button = ttk.Button(
            action_row, text="重试失败", width=8,
            state=tk.DISABLED, command=self.retry_failed,
        )
        self.retry_button.pack(side=tk.LEFT, padx=(6, 0))
        self.selected_button = ttk.Button(
            action_row, text="处理选中", width=8,
            state=tk.DISABLED, command=self.process_selected,
        )
        self.selected_button.pack(side=tk.LEFT, padx=(6, 0))
        self.batch_button = ttk.Menubutton(action_row, text="批处理", width=8)
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
        export_button = ttk.Menubutton(action_row, text="导出", width=7)
        export_menu = tk.Menu(export_button, tearoff=False)
        export_menu.add_command(
            label="训练数据 JSONL", command=lambda: self.export_results("jsonl")
        )
        export_menu.add_command(
            label="表格 CSV", command=lambda: self.export_results("csv")
        )
        export_button["menu"] = export_menu
        export_button.pack(side=tk.LEFT, padx=(6, 12))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="")
        self.stats_var = tk.StringVar(value="总数 0  ·  成功 0  ·  跳过 0  ·  失败 0")
        task_status_row = ttk.Frame(task_bar, style="Surface.TFrame")
        task_status_row.pack(fill=tk.X, pady=(7, 0))
        ttk.Label(
            task_status_row, textvariable=self.progress_text_var,
            width=16, anchor=tk.E, style="Surface.TLabel",
        ).pack(side=tk.RIGHT)
        ttk.Progressbar(
            task_status_row, variable=self.progress_var, maximum=100,
            mode="determinate",
        ).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Label(
            task_status_row, textvariable=self.stats_var,
            style="Surface.TLabel",
        ).pack(side=tk.LEFT)

        header = ttk.Frame(
            self.workspace_frame, style="Topbar.TFrame", padding=(12, 8)
        )
        header.pack(side=tk.TOP, fill=tk.X)
        self.workspace_header = header
        header.grid_columnconfigure(1, weight=1)
        title_block = ttk.Frame(header, style="Topbar.TFrame")
        self.workspace_title_block = title_block
        ttk.Label(
            title_block, text=f"{APP_TITLE} {APP_VERSION}",
            style="TopbarTitle.TLabel",
        ).pack(anchor=tk.W)
        project = ttk.Frame(header, style="Topbar.TFrame")
        self.workspace_project_bar = project
        ttk.Label(project, text="项目", style="TopbarMuted.TLabel").pack(side=tk.LEFT)
        self.folder_var = tk.StringVar()
        self.folder_box = ttk.Combobox(
            project, textvariable=self.folder_var, state="normal"
        )
        self.folder_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 6))
        ttk.Button(project, text="浏览", command=self.browse_folder).pack(side=tk.LEFT)
        ttk.Button(project, text="扫描", command=self.scan_project).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        topbar_controls = ttk.Frame(header, style="Topbar.TFrame")
        self.workspace_topbar_controls = topbar_controls
        self._build_theme_toggle(topbar_controls).pack(side=tk.LEFT)
        self._layout_workspace_header(1500)
        header.bind(
            "<Configure>",
            lambda event: self._layout_workspace_header(event.width),
        )

        self.update_banner = ttk.Frame(
            self.workspace_frame, style="Update.TFrame", padding=(12, 7)
        )
        banner_copy = ttk.Frame(self.update_banner, style="Update.TFrame")
        banner_copy.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.update_banner_var = tk.StringVar(value="发现新版本")
        ttk.Label(
            banner_copy, textvariable=self.update_banner_var, style="Update.TLabel"
        ).pack(side=tk.LEFT)
        ttk.Label(
            banner_copy, text="新功能和修复已可下载", style="UpdateMuted.TLabel"
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(
            self.update_banner, text="立即更新", command=self.download_and_install_update
        ).pack(side=tk.RIGHT, padx=(7, 0))
        ttk.Button(
            self.update_banner, text="×", width=3, command=self.dismiss_update_banner
        ).pack(side=tk.RIGHT)

        module_bar = ttk.Frame(
            self.workspace_frame, style="Topbar.TFrame", padding=(12, 8)
        )
        module_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        self.workspace_nav = ttk.Frame(module_bar, style="Topbar.TFrame")
        self.workspace_nav.pack(side=tk.LEFT, padx=(10, 0))
        nav_specs = (
            ("project", "项目中心", "⌂", self.show_project_center),
            ("image", "图像打标", "▧", lambda: self.select_workflow("image")),
            ("video", "视频反推", "▶", lambda: self.select_workflow("video")),
            ("single", "单次反推", "✦", self.show_single_reverse),
            ("platform", "平台设置", "☷", self.open_platform_config),
            ("system", "系统说明", "ⓘ", self.show_system_info),
        )
        self.nav_buttons = {}
        for key, label, fallback, command in nav_specs:
            icon = self._toolbar_icon(key)
            button = ttk.Button(
                self.workspace_nav,
                text=label if icon else f"{fallback} {label}",
                image=icon,
                compound=tk.LEFT,
                style="Nav.TButton",
                width=10,
                command=command,
            )
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.nav_buttons[key] = button
        self.nav_tooltips = [
            ToolTip(self.nav_buttons[key], label)
            for key, label in (
                ("project", "项目中心"),
                ("image", "图像打标"),
                ("video", "视频反推"),
                ("single", "单次反推"),
                ("platform", "平台设置"),
                ("system", "系统说明"),
            )
        ]
        self.stage_labels = []
        self.media_mode_var = tk.StringVar(value="image")
        self.workflow_mode_var = tk.StringVar(value="图像打标")
        self.caption_style_var = tk.StringVar(value="natural")
        self.output_language_var = tk.StringVar(value="zh")
        self.backend_var = tk.StringVar(value="api")
        self.skip_var = tk.BooleanVar(value=True)
        self.concurrency_var = tk.IntVar(value=3)
        self.focus_label_var = tk.StringVar(value="训练主体")
        self.trigger_word_var = tk.StringVar()
        self.local_model_var = tk.StringVar()
        self.local_runtime_var = tk.StringVar(value="huggingface")
        self.lmstudio_base_url_var = tk.StringVar(
            value="http://localhost:1234/v1"
        )
        self.lmstudio_model_var = tk.StringVar()
        self.lmstudio_load_profile_var = tk.StringVar(
            value=LMSTUDIO_LOAD_PROFILE_DEFAULT
        )
        self.llama_server_path_var = tk.StringVar()
        self.llama_model_path_var = tk.StringVar()
        self.llama_mmproj_path_var = tk.StringVar()
        self.llama_model_alias_var = tk.StringVar()
        self.llama_context_length_var = tk.IntVar(
            value=LLAMA_CPP_DEFAULT_CONTEXT_LENGTH
        )
        self.llama_gpu_layers_var = tk.IntVar(
            value=LLAMA_CPP_DEFAULT_GPU_LAYERS
        )
        self.enable_mtp_var = tk.BooleanVar(value=False)
        self.remove_thinking_tags_var = tk.BooleanVar(value=True)

        body = ttk.Frame(self.workspace_frame, padding=(10, 10, 10, 8))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        workspace = ttk.Frame(body)
        workspace.pack(fill=tk.BOTH, expand=True)
        workspace.grid_rowconfigure(0, weight=1)
        # Keep the inspector columns compact while giving the media canvas the
        # most room.  Fixed minimums prevent the text-heavy side panels from
        # being squeezed into clipped labels when the window is resized.
        workspace.grid_columnconfigure(0, minsize=WORKSPACE_LEFT_WIDTH, weight=0)
        workspace.grid_columnconfigure(1, minsize=WORKSPACE_CENTER_WIDTH, weight=1)
        workspace.grid_columnconfigure(2, minsize=WORKSPACE_RIGHT_WIDTH, weight=0)
        self.workspace = workspace

        left_panel = ttk.Frame(workspace, padding=(0, 0, 12, 0))
        left_panel.grid(row=0, column=0, sticky="nsew")
        table_panel = ttk.Frame(workspace, padding=(0, 0, 10, 0))
        table_panel.grid(row=0, column=1, sticky="nsew")
        # Give the result inspector a predictable compact width so the middle
        # media area receives the extra room instead of the notebook's natural
        # requested width taking it all back.
        preview_panel = ttk.Frame(workspace, width=WORKSPACE_RIGHT_WIDTH)
        preview_panel.pack_propagate(False)
        preview_panel.grid(row=0, column=2, sticky="nsew")

        left_scroll_host = ttk.Frame(left_panel)
        left_scroll_host.pack(fill=tk.BOTH, expand=True)
        self.left_platform_canvas = tk.Canvas(
            left_scroll_host,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.left_platform_scrollbar = ttk.Scrollbar(
            left_scroll_host,
            orient=tk.VERTICAL,
            command=self.left_platform_canvas.yview,
        )
        self.left_platform_canvas.configure(
            yscrollcommand=self.left_platform_scrollbar.set
        )
        self.left_platform_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_platform_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        left_content = ttk.Frame(self.left_platform_canvas)
        left_content_window_id = self.left_platform_canvas.create_window(
            (0, 0), window=left_content, anchor=tk.NW
        )
        left_content.bind(
            "<Configure>",
            lambda _event: self.left_platform_canvas.configure(
                scrollregion=self.left_platform_canvas.bbox("all")
            ),
        )
        self.left_platform_canvas.bind(
            "<Configure>",
            lambda event: self.left_platform_canvas.itemconfigure(
                left_content_window_id, width=event.width
            ),
        )
        platform_card = ttk.Frame(
            left_content, style="Surface.TFrame", padding=(14, 12)
        )
        platform_card.pack(fill=tk.X, pady=(0, 10))
        platform_head = self._semantic_heading(
            platform_card,
            "☷",
            "平台设置",
            "blue",
            "运行后端与模型",
        )
        platform_head.pack(fill=tk.X)
        platform_actions = ttk.Frame(platform_head, style="Surface.TFrame")
        platform_actions.pack(side=tk.RIGHT)
        ttk.Button(
            platform_actions,
            text="完整设置",
            command=self.open_platform_config,
        ).pack(side=tk.RIGHT)
        platform_form = ttk.Frame(platform_card)
        platform_form.pack(fill=tk.X, pady=(10, 0))
        self.provider_label_var = tk.StringVar()
        self.model_label_var = tk.StringVar()
        self.billing_var = tk.StringVar()

        backend_row = ttk.Frame(platform_form)
        backend_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            backend_row, text="运行方式", style="Surface.TLabel", width=10
        ).pack(side=tk.LEFT)
        backend_choices = ttk.Frame(backend_row, style="Surface.TFrame")
        backend_choices.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Radiobutton(
            backend_choices,
            text="外部 API",
            value="api",
            variable=self.backend_var,
            style="Surface.TRadiobutton",
            command=self._backend_changed,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            backend_choices,
            text="本地模型",
            value="local",
            variable=self.backend_var,
            style="Surface.TRadiobutton",
            command=self._backend_changed,
        ).pack(side=tk.LEFT, padx=(10, 0))

        provider_row = ttk.Frame(platform_form)
        provider_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(provider_row, text="服务商", style="Surface.TLabel", width=10).pack(
            side=tk.LEFT
        )
        self.provider_box = ttk.Combobox(
            provider_row,
            textvariable=self.provider_label_var,
            state="readonly",
            values=[API_PROVIDERS[key].label for key in PUBLIC_PROVIDER_KEYS],
            width=20,
        )
        self.provider_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.provider_box.bind("<<ComboboxSelected>>", self._provider_changed)

        model_row = ttk.Frame(platform_form)
        model_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(model_row, text="模型", style="Surface.TLabel", width=10).pack(
            side=tk.LEFT
        )
        self.model_box = ttk.Combobox(
            model_row,
            textvariable=self.model_label_var,
            state="readonly",
            values=[model.label for model in MODELS.values()],
            width=20,
        )
        self.model_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.model_box.bind("<<ComboboxSelected>>", self._model_changed)

        endpoint_row = ttk.Frame(platform_form)
        self.endpoint_row = endpoint_row
        self.endpoint_label = ttk.Label(
            endpoint_row, text="接口地址", style="Surface.TLabel", width=10
        )
        self.custom_endpoint_var = tk.StringVar()
        self.endpoint_box = ttk.Combobox(
            endpoint_row,
            textvariable=self.custom_endpoint_var,
            state="readonly",
            values=(),
            width=20,
        )
        self.endpoint_box.bind("<<ComboboxSelected>>", self._endpoint_changed)

        local_row = ttk.Frame(platform_form)
        local_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(
            local_row, text="本地模型", style="Surface.TLabel", width=10
        ).pack(side=tk.LEFT)
        self.local_model_entry = ttk.Entry(
            local_row, textvariable=self.local_model_var
        )
        self.local_model_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.local_model_button = ttk.Button(
            local_row, text="选择", command=self.browse_local_model
        )
        self.local_model_button.pack(side=tk.LEFT, padx=(6, 0))

        runtime_row = ttk.Frame(platform_form)
        runtime_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            runtime_row, text="本地方式", style="Surface.TLabel", width=10
        ).pack(side=tk.LEFT)
        self.local_runtime_label_var = tk.StringVar(
            value=LOCAL_RUNTIME_LABELS.get(
                self.local_runtime_var.get(), "Hugging Face 本地目录"
            )
        )
        self.local_runtime_box = ttk.Combobox(
            runtime_row,
            textvariable=self.local_runtime_label_var,
            state="readonly",
            values=list(LOCAL_RUNTIME_OPTIONS),
        )
        self.local_runtime_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def workspace_local_runtime_changed(_event=None) -> None:
            self.local_runtime_var.set(
                LOCAL_RUNTIME_OPTIONS.get(
                    self.local_runtime_label_var.get(), "huggingface"
                )
            )
            self._backend_changed()

        self.local_runtime_box.bind(
            "<<ComboboxSelected>>", workspace_local_runtime_changed
        )
        ttk.Label(
            platform_card,
            text="本地模式可切换 Hugging Face、LM Studio 和 llama.cpp。",
            style="Muted.TLabel",
            wraplength=320,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(8, 0))

        ttk.Label(
            platform_card,
            textvariable=self.platform_status_var,
            style="Muted.TLabel",
            wraplength=320,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(10, 0))

        self.sampling_shell = ttk.Frame(
            left_content, style="Surface.TFrame", padding=(12, 10)
        )
        self.sampling_shell.pack(fill=tk.BOTH, expand=True)
        self._build_sampling_panel(self.sampling_shell)
        self.sampling_tab = self.sampling_shell
        # Bind every control in the left inspector, so the wheel scrolls the
        # inspector instead of changing a combobox/spinbox value.
        self._bind_left_platform_wheel(left_panel)

        canvas_title = self._semantic_heading(
            table_panel, "▧", "素材画布", "yellow", "MEDIA CANVAS"
        )
        self.canvas_heading = canvas_title
        canvas_title.pack(fill=tk.X, pady=(0, 8))
        table_panel.bind("<Configure>", self._layout_canvas_heading, add="+")

        filter_bar = ttk.Frame(table_panel, style="Surface.TFrame", padding=(10, 8))
        filter_bar.pack(fill=tk.X, pady=(0, 8))
        self.filter_bar = filter_bar
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=30)
        self.search_entry = search_entry
        search_entry.bind("<KeyRelease>", lambda _event: self.refresh_table())
        filter_status_label = ttk.Label(
            filter_bar, text="状态", style="Surface.TLabel"
        )
        self.filter_status_label = filter_status_label
        self.filter_var = tk.StringVar(value="全部状态")
        filter_box = ttk.Combobox(
            filter_bar, textvariable=self.filter_var, state="readonly",
            values=list(FILTERS), width=12,
        )
        self.filter_box = filter_box
        filter_box.bind("<<ComboboxSelected>>", self._filter_changed)
        self.view_mode_var = tk.StringVar(value="gallery")
        gallery_view_button = ttk.Radiobutton(
            filter_bar, text="缩略图", value="gallery",
            variable=self.view_mode_var, command=self._switch_view,
        )
        self.gallery_view_button = gallery_view_button
        list_view_button = ttk.Radiobutton(
            filter_bar, text="列表", value="list",
            variable=self.view_mode_var, command=self._switch_view,
        )
        self.list_view_button = list_view_button
        self.selection_var = tk.StringVar(value="已选 0")
        selection_label = ttk.Label(
            filter_bar, textvariable=self.selection_var,
            style="Surface.TLabel",
        )
        self.selection_label = selection_label
        self._layout_filter_bar(900)
        filter_bar.bind(
            "<Configure>", lambda event: self._layout_filter_bar(event.width)
        )
        self.table_frame = ttk.Frame(table_panel)
        self.tree = ttk.Treeview(
            self.table_frame, columns=("status", "file", "detail"),
            show="headings", selectmode="extended",
        )
        self.tree.heading("status", text="状态")
        self.tree.heading("file", text="文件")
        self.tree.heading("detail", text="结果 / 详情")
        self.tree.column("status", width=82, minwidth=82, anchor=tk.CENTER, stretch=False)
        self.tree.column("file", width=250, minwidth=150)
        self.tree.column("detail", width=240, minwidth=150)
        scrollbar = ttk.Scrollbar(
            self.table_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
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
            table_panel, self._gallery_selected, self._request_thumbnail,
            lambda path: self.thumbnail_cache.get(str(path)),
        )

        self.right_tabs = ttk.Notebook(preview_panel)
        self.right_tabs.pack(fill=tk.BOTH, expand=True)
        self.right_tabs.bind(
            "<<NotebookTabChanged>>", self._native_view_changed, add="+"
        )
        self.preview_tab = ttk.Frame(self.right_tabs, padding=10)
        self.task_settings_tab = ttk.Frame(self.right_tabs, padding=10)
        self.right_tabs.add(self.preview_tab, text="当前素材")
        self.right_tabs.add(self.task_settings_tab, text="任务设置")

        preview_head = self._semantic_heading(
            self.preview_tab, "◎", "当前素材", "yellow"
        )
        preview_head.pack(fill=tk.X)
        self.save_result_button = ttk.Button(
            preview_head, text="保存", width=5, command=self.save_selected_result,
            state=tk.DISABLED,
        )
        self.save_result_button.pack(side=tk.RIGHT, before=preview_head.winfo_children()[0])
        self.preview_viewport = tk.Frame(
            self.preview_tab, background=COLORS["media_bg"], height=360,
        )
        self.preview_viewport.pack(fill=tk.X, pady=(8, 8))
        self.preview_viewport.pack_propagate(False)
        self.preview_label = tk.Label(
            self.preview_viewport, text="选择素材查看详情", anchor=tk.CENTER,
            background=COLORS["media_bg"], foreground=COLORS["muted"],
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        self._preview_resize_after = None
        self.preview_label.bind("<Configure>", self._preview_resized)
        self.selected_item_var = tk.StringVar(value="尚未选择素材")
        self.selected_item_label = ttk.Label(
            self.preview_tab, textvariable=self.selected_item_var,
            style="Muted.TLabel", anchor=tk.W, justify=tk.LEFT,
        )
        self.selected_item_label.pack(fill=tk.X, pady=(0, 8))

        inspector_tabs = ttk.Notebook(self.preview_tab, style="Compact.TNotebook")
        inspector_tabs.pack(fill=tk.BOTH, expand=True)
        inspector_tabs.bind(
            "<<NotebookTabChanged>>", self._native_view_changed, add="+"
        )
        self.result_tab = ttk.Frame(inspector_tabs, padding=7)
        self.prompt_tab = ttk.Frame(inspector_tabs, padding=7)
        self.log_tab = ttk.Frame(inspector_tabs, padding=7)
        inspector_tabs.add(self.result_tab, text="标注结果")
        inspector_tabs.add(self.prompt_tab, text="提示词")
        inspector_tabs.add(self.log_tab, text="运行日志")
        self.inspector_tabs = inspector_tabs
        self.preview_tab.bind("<Configure>", self._layout_preview_panel)
        self.result_state_var = tk.StringVar(value="● 等待标注结果")
        self.result_state_label = ttk.Label(
            self.result_tab,
            textvariable=self.result_state_var,
            style="SurfaceMuted.TLabel",
            anchor=tk.W,
        )
        self.result_state_label.pack(fill=tk.X, pady=(0, 6))
        self.result_text = scrolledtext.ScrolledText(
            self.result_tab, width=34, wrap=tk.WORD,
            font=(UI_FONT, UI_TEXT_SIZE),
        )
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.result_text)

        prompt_bar = ttk.Frame(self.prompt_tab)
        prompt_bar.pack(fill=tk.X, pady=(0, 7))
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(
            prompt_bar, textvariable=self.preset_var,
            state="readonly", width=14,
        )
        self.preset_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.preset_box.bind("<<ComboboxSelected>>", self.apply_preset)
        ttk.Button(
            prompt_bar, text="导入", width=5, command=self.import_prompt_file
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(
            prompt_bar, text="保存", width=5, command=self.save_preset
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(
            prompt_bar, text="删除", width=5, command=self.delete_preset
        ).pack(side=tk.LEFT, padx=(5, 0))
        self.prompt_scroll_host = ttk.Frame(self.prompt_tab)
        self.prompt_scroll_host.pack(fill=tk.BOTH, expand=True)
        self.prompt_form_canvas = tk.Canvas(
            self.prompt_scroll_host,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.prompt_form_scrollbar = ttk.Scrollbar(
            self.prompt_scroll_host,
            orient=tk.VERTICAL,
            command=self.prompt_form_canvas.yview,
        )
        self.prompt_form_canvas.configure(
            yscrollcommand=self.prompt_form_scrollbar.set
        )
        self.prompt_form_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.prompt_form_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        self.prompt_form_content = ttk.Frame(self.prompt_form_canvas)
        self.prompt_form_window_id = self.prompt_form_canvas.create_window(
            (0, 0), window=self.prompt_form_content, anchor=tk.NW
        )
        self.prompt_form_content.bind(
            "<Configure>", self._prompt_form_content_resized
        )
        self.prompt_form_canvas.bind(
            "<Configure>", self._prompt_form_canvas_resized
        )

        self.prompt_subject_row = ttk.Frame(self.prompt_form_content)
        self.prompt_subject_row.pack(fill=tk.X, pady=(0, 7))
        self.prompt_subject_label = ttk.Label(
            self.prompt_subject_row, text="主体过滤"
        )
        self.prompt_subject_label.pack(side=tk.LEFT)
        self.subject_filter_var = tk.StringVar()
        self.subject_filter_entry = ttk.Entry(
            self.prompt_subject_row, textvariable=self.subject_filter_var
        )
        self.subject_filter_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(7, 0)
        )
        self.prompt_user_label = ttk.Label(
            self.prompt_form_content, text="用户要求（可选）"
        )
        self.prompt_user_label.pack(anchor=tk.W)
        self.user_prompt_text = scrolledtext.ScrolledText(
            self.prompt_form_content, height=4, wrap=tk.WORD,
            font=(UI_FONT, UI_TEXT_SIZE),
        )
        self.user_prompt_text.pack(fill=tk.X, pady=(4, 8))
        self._style_text(self.user_prompt_text)
        self.prompt_system_header = ttk.Frame(self.prompt_form_content)
        self.prompt_system_header.pack(fill=tk.X)
        self.prompt_system_label = ttk.Label(
            self.prompt_system_header, text="系统提示词模板"
        )
        self.prompt_system_label.pack(side=tk.LEFT)
        self.system_prompt_metric_var = tk.StringVar(
            value="完整加载 · 0 字符 · 0 行"
        )
        self.system_prompt_metric_label = ttk.Label(
            self.prompt_system_header,
            textvariable=self.system_prompt_metric_var,
            style="Muted.TLabel",
        )
        self.system_prompt_metric_label.pack(side=tk.RIGHT)
        self.system_prompt_text = scrolledtext.ScrolledText(
            self.prompt_form_content, wrap=tk.WORD, font=(UI_FONT, UI_TEXT_SIZE)
        )
        self.system_prompt_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._style_text(self.system_prompt_text)
        self.system_prompt_text.bind(
            "<<Modified>>", self._system_prompt_modified, add="+"
        )
        self.system_prompt_text.edit_modified(False)
        for widget in (
            self.prompt_form_canvas,
            self.prompt_form_content,
            self.prompt_subject_row,
            self.prompt_subject_label,
            self.subject_filter_entry,
            self.prompt_user_label,
            self.prompt_system_header,
            self.prompt_system_label,
            self.system_prompt_metric_label,
        ):
            widget.bind("<MouseWheel>", self._prompt_form_mousewheel)
        for text_widget in (self.user_prompt_text, self.system_prompt_text):
            text_widget.bind(
                "<MouseWheel>",
                lambda event, target=text_widget: self._prompt_text_mousewheel(
                    event, target
                ),
            )
        self.log_text = scrolledtext.ScrolledText(
            self.log_tab, width=34, wrap=tk.WORD, font=(MONO_FONT, UI_MONO_SIZE)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.log_text)

        task_scroll_host = ttk.Frame(self.task_settings_tab)
        task_scroll_host.pack(fill=tk.BOTH, expand=True)
        self.task_settings_canvas = tk.Canvas(
            task_scroll_host,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.task_settings_scrollbar = ttk.Scrollbar(
            task_scroll_host,
            orient=tk.VERTICAL,
            command=self.task_settings_canvas.yview,
        )
        self.task_settings_canvas.configure(
            yscrollcommand=self.task_settings_scrollbar.set
        )
        self.task_settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.task_settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        self.task_settings_content = ttk.Frame(self.task_settings_canvas)
        self.task_settings_window_id = self.task_settings_canvas.create_window(
            (0, 0), window=self.task_settings_content, anchor=tk.NW
        )
        self.task_settings_content.bind(
            "<Configure>", self._task_settings_content_resized
        )
        self.task_settings_canvas.bind(
            "<Configure>", self._task_settings_canvas_resized
        )
        self.root.bind(
            "<MouseWheel>", self._task_settings_mousewheel, add="+"
        )

        task_intro = ttk.Frame(self.task_settings_content)
        task_intro.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            task_intro, textvariable=self.workflow_mode_var,
            style="Title.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(
            task_intro, text="平台设置", command=self.open_platform_config
        ).pack(side=tk.RIGHT)
        ttk.Label(
            self.task_settings_content, textvariable=self.platform_status_var,
            style="Muted.TLabel",
        ).pack(fill=tk.X, pady=(0, 8))

        format_row = ttk.Frame(
            self.task_settings_content,
            style="SectionCard.TFrame",
            padding=(12, 10),
        )
        self.format_card = format_row
        format_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            format_row, text="输出与视图", style="SurfaceSection.TLabel"
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 9))
        ttk.Label(
            format_row, text="输出格式", style="Surface.TLabel"
        ).grid(row=1, column=0, sticky=tk.W)
        ttk.Radiobutton(
            format_row, text="自然语言", value="natural",
            variable=self.caption_style_var, style="Surface.TRadiobutton",
        ).grid(row=1, column=1, sticky=tk.W, padx=(10, 0))
        ttk.Radiobutton(
            format_row, text="词组标签", value="phrases",
            variable=self.caption_style_var, style="Surface.TRadiobutton",
        ).grid(row=1, column=2, sticky=tk.W, padx=(8, 0))
        ttk.Label(
            format_row, text="输出语言", style="Surface.TLabel"
        ).grid(
            row=2, column=0, sticky=tk.W, pady=(8, 0)
        )
        ttk.Radiobutton(
            format_row, text="中文", value="zh", variable=self.output_language_var,
            style="Surface.TRadiobutton",
        ).grid(row=2, column=1, sticky=tk.W, padx=(10, 0), pady=(8, 0))
        ttk.Radiobutton(
            format_row, text="English", value="en", variable=self.output_language_var,
            style="Surface.TRadiobutton",
        ).grid(row=2, column=2, sticky=tk.W, padx=(8, 0), pady=(8, 0))

        strategy = ttk.Frame(
            self.task_settings_content,
            style="SectionCard.TFrame",
            padding=(12, 10),
        )
        self.strategy_card = strategy
        strategy.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            strategy, text="任务策略", style="SurfaceSection.TLabel"
        ).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 9))
        ttk.Label(
            strategy, text="侧重点", style="Surface.TLabel"
        ).grid(row=1, column=0, sticky=tk.W)
        self.focus_box = ttk.Combobox(
            strategy, textvariable=self.focus_label_var, state="readonly",
            values=list(FOCUS_OPTIONS), width=16,
        )
        self.focus_box.bind(
            "<MouseWheel>", self._form_control_mousewheel, add="+"
        )
        self.focus_box.grid(row=1, column=1, sticky=tk.EW, padx=(10, 0))
        ttk.Label(
            strategy, text="并发", style="Surface.TLabel"
        ).grid(
            row=1, column=2, sticky=tk.W, padx=(10, 0)
        )
        self.concurrency_box = ttk.Spinbox(
            strategy, from_=1, to=MAX_CONCURRENCY, width=4,
            textvariable=self.concurrency_var,
        )
        self.concurrency_box.bind(
            "<MouseWheel>", self._form_control_mousewheel, add="+"
        )
        self.concurrency_box.grid(row=1, column=3, sticky=tk.W, padx=(6, 0))
        ttk.Label(
            strategy, text="触发词", style="Surface.TLabel"
        ).grid(
            row=2, column=0, sticky=tk.W, pady=(8, 0)
        )
        ttk.Entry(strategy, textvariable=self.trigger_word_var).grid(
            row=2, column=1, columnspan=3, sticky=tk.EW,
            padx=(10, 0), pady=(8, 0),
        )
        ttk.Checkbutton(
            strategy, text="跳过有效 TXT", variable=self.skip_var,
            style="Surface.TCheckbutton",
        ).grid(row=3, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))
        strategy.grid_columnconfigure(1, weight=1)

        self.sampling_expanded = False

        self.export_menu = export_menu
        self.batch_menu = batch_menu
        self._register_themed_menu(self.export_menu, "surface")
        self._register_themed_menu(self.batch_menu, "surface")
        self._build_single_reverse()

    def _build_single_reverse(self) -> None:
        topbar = ttk.Frame(
            self.single_reverse_frame, style="Topbar.TFrame", padding=(18, 10)
        )
        topbar.pack(fill=tk.X)
        title_block = ttk.Frame(topbar, style="Topbar.TFrame")
        title_block.pack(side=tk.LEFT)
        ttk.Label(
            title_block,
            text=f"{APP_TITLE} {APP_VERSION}",
            style="TopbarTitle.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            title_block,
            text="单次任务 · 无需建立项目，结果可独立保存",
            style="TopbarMuted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        actions = ttk.Frame(topbar, style="Topbar.TFrame")
        actions.pack(side=tk.RIGHT)
        ttk.Button(
            actions, text="平台设置", command=self.open_platform_config
        ).pack(side=tk.LEFT)
        self._build_theme_toggle(actions).pack(side=tk.LEFT, padx=(10, 0))

        module_bar = ttk.Frame(
            self.single_reverse_frame, style="Topbar.TFrame", padding=(12, 8)
        )
        module_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        nav_specs = (
            ("project", "项目中心", self.show_project_center),
            ("image", "图像打标", lambda: self.select_workflow("image")),
            ("video", "视频反推", lambda: self.select_workflow("video")),
            ("single", "单次反推", self.show_single_reverse),
            ("platform", "平台设置", self.open_platform_config),
            ("system", "系统说明", self.show_system_info),
        )
        self.single_nav_buttons = {}
        for key, label, command in nav_specs:
            button = ttk.Button(
                module_bar,
                text=label,
                image=self._toolbar_icon(key),
                compound=tk.LEFT,
                style="NavActive.TButton" if key == "single" else "Nav.TButton",
                width=10,
                command=command,
            )
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.single_nav_buttons[key] = button

        body = ttk.Frame(self.single_reverse_frame)
        body.pack(fill=tk.BOTH, expand=True)

        content = ttk.Frame(body, padding=(20, 16, 20, 18))
        content.pack(fill=tk.BOTH, expand=True)
        page_head = ttk.Frame(content)
        page_head.pack(fill=tk.X, pady=(0, 14))
        heading = self._semantic_heading(
            page_head,
            "✦",
            "单次反推",
            "yellow",
            "拖入一个素材，快速反推、检查并保存结果，不进入项目批处理。",
        )
        heading.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.single_mode_var = tk.StringVar(value="image")
        mode_controls = ttk.Frame(page_head, style="Surface.TFrame", padding=3)
        mode_controls.pack(side=tk.RIGHT)
        self.single_mode_buttons = {}
        for mode, label in (("image", "▧  图片"), ("media", "▷  音视频")):
            button = ttk.Button(
                mode_controls,
                text=label,
                width=11,
                command=lambda value=mode: self._set_single_mode(value),
            )
            button.pack(side=tk.LEFT, padx=(0, 4) if mode == "image" else 0)
            self.single_mode_buttons[mode] = button

        self.single_progress_var = tk.DoubleVar(value=0)
        self.single_progress_text_var = tk.StringVar(value="等待选择素材")
        self.single_status_var = tk.StringVar(value="● 等待反推结果")
        self.single_metrics_var = tk.StringVar(value="")
        self.single_image_file_var = tk.StringVar(value="尚未选择图片")
        self.single_media_file_var = tk.StringVar(value="尚未选择音视频")
        self.single_media_meta_var = tk.StringVar(value="拖入或点击选择文件")
        self.single_media_probe_var = tk.StringVar(value="选择音视频后读取时长与轨道")
        self.single_platform_summary_var = tk.StringVar()
        self.single_prompt_summary_var = tk.StringVar()
        self.single_language_summary_var = tk.StringVar()

        self.single_content_host = ttk.Frame(content)
        self.single_content_host.pack(fill=tk.BOTH, expand=True)
        self.single_image_frame = ttk.Frame(self.single_content_host)
        self.single_media_frame = ttk.Frame(self.single_content_host)

        image_workspace = ttk.PanedWindow(
            self.single_image_frame, orient=tk.HORIZONTAL
        )
        image_workspace.pack(fill=tk.BOTH, expand=True)
        image_input = ttk.Frame(image_workspace, style="Surface.TFrame", padding=14)
        image_side = ttk.Frame(image_workspace, padding=(12, 0, 0, 0))
        image_workspace.add(image_input, weight=3)
        image_workspace.add(image_side, weight=2)
        image_head = self._semantic_heading(
            image_input, "1", "上传一张图片", "yellow", "单文件模式"
        )
        image_head.pack(fill=tk.X, pady=(0, 10))
        self.single_image_canvas = tk.Canvas(
            image_input,
            height=500,
            background=COLORS["media_bg"],
            highlightthickness=0,
            cursor="hand2",
        )
        self.single_image_canvas.pack(fill=tk.BOTH, expand=True)
        self.single_image_canvas.bind("<Button-1>", lambda _event: self.choose_single_image())
        self.single_image_canvas.bind("<Configure>", lambda _event: self._render_single_image())
        self._register_single_drop_target(self.single_image_canvas)
        image_footer = ttk.Frame(image_input, style="Surface.TFrame")
        image_footer.pack(fill=tk.X, pady=(9, 0))
        ttk.Label(
            image_footer,
            text="支持 JPG / PNG / WEBP / GIF / HEIC\nCtrl+V 粘贴 · 拖放导入",
            style="Muted.TLabel",
            justify=tk.LEFT,
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(
            image_footer, text="● 仅在本机预处理", style="StatusSuccess.TLabel"
        ).grid(row=0, column=1, sticky=tk.E)
        image_footer.grid_columnconfigure(0, weight=1)

        settings_card = ttk.Frame(image_side, style="Surface.TFrame", padding=14)
        settings_card.pack(fill=tk.X, pady=(0, 12))
        settings_head = self._semantic_heading(
            settings_card, "2", "本次设置", "blue", "沿用平台设置"
        )
        settings_head.pack(fill=tk.X, pady=(0, 9))
        for label, variable in (
            ("平台与模型", self.single_platform_summary_var),
            ("提示词模板", self.single_prompt_summary_var),
            ("输出与清理", self.single_language_summary_var),
        ):
            row = ttk.Frame(settings_card, style="Surface.TFrame")
            row.pack(fill=tk.X, pady=6)
            ttk.Label(
                row, text=label, style="SurfaceMuted.TLabel", width=11
            ).pack(side=tk.LEFT)
            ttk.Label(
                row,
                textvariable=variable,
                style="Surface.TLabel",
                anchor=tk.E,
            ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        image_result = ttk.Frame(image_side, style="Surface.TFrame", padding=14)
        image_result.pack(fill=tk.BOTH, expand=True)
        result_head = self._semantic_heading(
            image_result, "3", "反推结果", "red", "可编辑"
        )
        result_head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            image_result,
            textvariable=self.single_status_var,
            style="SurfaceMuted.TLabel",
        ).pack(fill=tk.X, pady=(0, 5))
        self.single_image_result_text = scrolledtext.ScrolledText(
            image_result, wrap=tk.WORD, font=(UI_FONT, UI_TEXT_SIZE), height=10
        )
        self.single_image_result_text.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.single_image_result_text)
        ttk.Label(
            image_result,
            textvariable=self.single_metrics_var,
            style="Muted.TLabel",
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Progressbar(
            image_result,
            variable=self.single_progress_var,
            maximum=100,
            mode="determinate",
        ).pack(fill=tk.X, pady=(7, 0))
        image_actions = ttk.Frame(image_result, style="Surface.TFrame")
        image_actions.pack(fill=tk.X, pady=(9, 0))
        self.single_image_run_button = ttk.Button(
            image_actions,
            text="开始单次反推",
            style="Primary.TButton",
            state=tk.DISABLED,
            command=self.start_single_image_reverse,
        )
        self.single_image_run_button.pack(side=tk.RIGHT)
        ttk.Button(
            image_actions, text="保存结果", command=self.save_single_result
        ).pack(side=tk.RIGHT, padx=(0, 7))
        ttk.Button(
            image_actions, text="复制结果", command=self.copy_single_result
        ).pack(side=tk.RIGHT, padx=(0, 7))

        media_workspace = ttk.PanedWindow(
            self.single_media_frame, orient=tk.HORIZONTAL
        )
        media_workspace.pack(fill=tk.BOTH, expand=True)
        media_input = ttk.Frame(media_workspace, style="Surface.TFrame", padding=14)
        media_editor = ttk.Frame(media_workspace, padding=(12, 0, 0, 0))
        media_workspace.add(media_input, weight=2)
        media_workspace.add(media_editor, weight=3)
        media_head = self._semantic_heading(
            media_input, "1", "音视频输入", "yellow", "拖入或点击选择"
        )
        media_head.pack(fill=tk.X, pady=(0, 10))
        self.single_media_canvas = tk.Canvas(
            media_input,
            height=420,
            background=COLORS["media_bg"],
            highlightthickness=0,
            cursor="hand2",
        )
        self.single_media_canvas.pack(fill=tk.BOTH, expand=True)
        self.single_media_canvas.bind("<Button-1>", lambda _event: self.choose_single_media_file())
        self.single_media_canvas.bind("<Configure>", lambda _event: self._render_single_media_preview())
        self._register_single_drop_target(self.single_media_canvas)
        media_file_card = ttk.Frame(media_input, style="SurfaceAlt.TFrame", padding=10)
        media_file_card.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(
            media_file_card,
            textvariable=self.single_media_file_var,
            style="Surface.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            media_file_card,
            textvariable=self.single_media_meta_var,
            style="SurfaceMuted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(
            media_input,
            text="重新选择音视频",
            command=self.choose_single_media_file,
        ).pack(fill=tk.X, pady=(10, 0))

        self.single_media_tabs = ttk.Notebook(media_editor)
        self.single_media_tabs.pack(fill=tk.BOTH, expand=True)
        editor_tab = ttk.Frame(self.single_media_tabs, padding=14)
        media_result_tab = ttk.Frame(self.single_media_tabs, padding=14)
        self.single_media_tabs.add(editor_tab, text="片段编辑器")
        self.single_media_tabs.add(media_result_tab, text="反推结果")
        editor_head = self._semantic_heading(
            editor_tab, "2", "片段编辑器", "blue", "手工选择开始与结束位置"
        )
        editor_head.pack(fill=tk.X, pady=(0, 8))
        editor_tools = ttk.Frame(editor_tab)
        editor_tools.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(
            editor_tools, text="播放选区", command=self.preview_single_media_selection
        ).pack(side=tk.LEFT)
        ttk.Button(
            editor_tools, text="恢复全长", command=self.reset_single_media_selection
        ).pack(side=tk.LEFT, padx=(7, 0))
        self.single_include_audio_var = tk.BooleanVar(value=True)
        self.single_audio_check = ttk.Checkbutton(
            editor_tools,
            text="保留音频",
            variable=self.single_include_audio_var,
        )
        self.single_audio_check.pack(side=tk.RIGHT)
        ttk.Label(
            editor_tab,
            textvariable=self.single_media_probe_var,
            style="Muted.TLabel",
        ).pack(fill=tk.X, pady=(0, 7))
        self.single_editor_preview_canvas = tk.Canvas(
            editor_tab,
            height=190,
            background=COLORS["media_bg"],
            highlightthickness=0,
        )
        self.single_editor_preview_canvas.pack(fill=tk.X)
        self.single_editor_preview_canvas.bind(
            "<Configure>", lambda _event: self._render_single_media_preview()
        )
        self.single_timeline_canvas = tk.Canvas(
            editor_tab,
            height=88,
            background=COLORS["input_readonly"],
            highlightthickness=0,
        )
        self.single_timeline_canvas.pack(fill=tk.X, pady=(10, 8))
        self.single_timeline_canvas.bind(
            "<Configure>", lambda _event: self._render_single_timeline()
        )
        self.single_clip_start_var = tk.DoubleVar(value=0.0)
        self.single_clip_end_var = tk.DoubleVar(value=1.0)
        self.single_clip_start_text_var = tk.StringVar(value="00:00:00.0")
        self.single_clip_end_text_var = tk.StringVar(value="00:00:01.0")
        self.single_clip_duration_text_var = tk.StringVar(value="00:00:01.0")
        self.single_clip_duration = 1.0
        self.single_clip_updating = False
        scale_grid = ttk.Frame(editor_tab)
        scale_grid.pack(fill=tk.X)
        scale_grid.grid_columnconfigure(1, weight=1)
        ttk.Label(scale_grid, text="开始位置").grid(row=0, column=0, sticky=tk.W)
        self.single_clip_start_scale = ttk.Scale(
            scale_grid,
            from_=0,
            to=1,
            variable=self.single_clip_start_var,
            state=tk.DISABLED,
            command=lambda _value: self._single_clip_changed("start"),
        )
        self.single_clip_start_scale.grid(row=0, column=1, sticky=tk.EW, padx=10)
        ttk.Label(
            scale_grid,
            textvariable=self.single_clip_start_text_var,
            style="Brand.TLabel",
            width=12,
        ).grid(row=0, column=2, sticky=tk.E)
        ttk.Label(scale_grid, text="结束位置").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.single_clip_end_scale = ttk.Scale(
            scale_grid,
            from_=0,
            to=1,
            variable=self.single_clip_end_var,
            state=tk.DISABLED,
            command=lambda _value: self._single_clip_changed("end"),
        )
        self.single_clip_end_scale.grid(row=1, column=1, sticky=tk.EW, padx=10, pady=(8, 0))
        ttk.Label(
            scale_grid,
            textvariable=self.single_clip_end_text_var,
            style="Brand.TLabel",
            width=12,
        ).grid(row=1, column=2, sticky=tk.E, pady=(8, 0))
        clip_summary = ttk.Frame(editor_tab, style="Surface.TFrame", padding=(10, 8))
        clip_summary.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(
            clip_summary, text="选中片段", style="SurfaceMuted.TLabel"
        ).pack(side=tk.LEFT)
        ttk.Label(
            clip_summary,
            textvariable=self.single_clip_duration_text_var,
            style="Brand.TLabel",
        ).pack(side=tk.RIGHT)
        editor_actions = ttk.Frame(editor_tab)
        editor_actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            editor_actions,
            text="原文件不会被修改，片段与 TXT 保存到独立目录。",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT)
        self.single_clip_reverse_button = ttk.Button(
            editor_actions,
            text="截取并反推",
            style="Primary.TButton",
            state=tk.DISABLED,
            command=self.start_single_media_reverse,
        )
        self.single_clip_reverse_button.pack(side=tk.RIGHT)
        self.single_clip_save_button = ttk.Button(
            editor_actions,
            text="仅截取保存",
            state=tk.DISABLED,
            command=self.save_single_media_clip,
        )
        self.single_clip_save_button.pack(side=tk.RIGHT, padx=(0, 7))

        media_result_head = self._semantic_heading(
            media_result_tab, "3", "反推结果", "red", "可编辑"
        )
        media_result_head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            media_result_tab,
            textvariable=self.single_status_var,
            style="SurfaceMuted.TLabel",
        ).pack(fill=tk.X, pady=(0, 6))
        self.single_media_result_text = scrolledtext.ScrolledText(
            media_result_tab, wrap=tk.WORD, font=(UI_FONT, UI_TEXT_SIZE)
        )
        self.single_media_result_text.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.single_media_result_text)
        ttk.Label(
            media_result_tab,
            textvariable=self.single_metrics_var,
            style="Muted.TLabel",
        ).pack(fill=tk.X, pady=(6, 0))
        ttk.Progressbar(
            media_result_tab,
            variable=self.single_progress_var,
            maximum=100,
            mode="determinate",
        ).pack(fill=tk.X, pady=(7, 0))
        media_result_actions = ttk.Frame(media_result_tab)
        media_result_actions.pack(fill=tk.X, pady=(9, 0))
        ttk.Button(
            media_result_actions, text="保存结果", command=self.save_single_result
        ).pack(side=tk.RIGHT)
        ttk.Button(
            media_result_actions, text="复制结果", command=self.copy_single_result
        ).pack(side=tk.RIGHT, padx=(0, 7))

        self.single_action_buttons = [
            self.single_image_run_button,
            self.single_clip_reverse_button,
            self.single_clip_save_button,
        ]
        self._set_single_mode("image")
        self._update_single_settings_summary()

    def _build_sampling_panel(self, parent: ttk.Frame) -> None:
        self.sampling_support_var = tk.StringVar()
        heading = ttk.Frame(parent, style="Surface.TFrame")
        heading.pack(fill=tk.X, pady=(0, 7))
        # Keep the value for internal compatibility, but do not render the
        # redundant summary line beside the title.
        self.sampling_summary_var = tk.StringVar(value="")
        title_row = ttk.Frame(heading, style="Surface.TFrame")
        title_row.pack(fill=tk.X)
        ttk.Label(
            title_row, text="采样参数", style="Title.TLabel"
        ).pack(side=tk.LEFT)
        actions = ttk.Frame(heading, style="Surface.TFrame")
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            actions, text="采样预设", style="SurfaceMuted.TLabel"
        ).pack(side=tk.LEFT)
        self.sampling_preset_var = tk.StringVar(value="平衡反推")
        self.sampling_preset_box = ttk.Combobox(
            actions,
            textvariable=self.sampling_preset_var,
            state="readonly",
            width=16,
            values=list(SAMPLING_PRESETS),
        )
        self.sampling_preset_box.pack(side=tk.LEFT, padx=(8, 0))
        self.sampling_preset_box.bind(
            "<<ComboboxSelected>>", self.apply_sampling_preset
        )
        self.sampling_preset_box.bind(
            "<MouseWheel>", self._form_control_mousewheel, add="+"
        )
        self.sampling_reset_button = ttk.Button(
            actions,
            text="↻",
            width=4,
            style="Icon.TButton",
            command=self.reset_sampling,
        )
        self.sampling_reset_button.pack(side=tk.LEFT, padx=(6, 0))
        self.sampling_reset_tooltip = ToolTip(
            self.sampling_reset_button, "恢复默认采样参数"
        )
        self.max_tokens_var = tk.IntVar(value=DEFAULT_SAMPLING["max_tokens"])
        self.temperature_var = tk.DoubleVar(value=DEFAULT_SAMPLING["temperature"])
        self.top_p_var = tk.DoubleVar(value=DEFAULT_SAMPLING["top_p"])
        self.top_k_var = tk.IntVar(value=DEFAULT_SAMPLING["top_k"])
        self.frequency_penalty_var = tk.DoubleVar(
            value=DEFAULT_SAMPLING["frequency_penalty"]
        )
        self.presence_penalty_var = tk.DoubleVar(
            value=DEFAULT_SAMPLING["presence_penalty"]
        )
        self.seed_var = tk.StringVar()
        controls = ttk.Frame(parent, style="Surface.TFrame")
        controls.pack(fill=tk.X)
        self.sampling_control_widgets = {}
        fields = (
            ("Max Tokens", self.max_tokens_var, 64, 32768, 64),
            ("Temperature", self.temperature_var, 0.0, 2.0, 0.05),
            ("Top P", self.top_p_var, 0.0, 1.0, 0.05),
            ("Top K", self.top_k_var, 0, 500, 1),
            ("频率惩罚", self.frequency_penalty_var, -2.0, 2.0, 0.05),
            ("存在惩罚", self.presence_penalty_var, -2.0, 2.0, 0.05),
            ("Seed", self.seed_var, None, None, None),
        )
        for index, (label, variable, minimum, maximum, increment) in enumerate(fields):
            group = ttk.Frame(controls, style="Surface.TFrame")
            group.grid(
                row=index // 2, column=index % 2, sticky=tk.EW,
                padx=(0, 0)
                if label == "Seed"
                else ((0, 8) if index % 2 == 0 else (0, 0)),
                pady=(0, 7),
                columnspan=2 if label == "Seed" else 1,
            )
            ttk.Label(
                group, text=label, style="Surface.TLabel", anchor=tk.W
            ).pack(fill=tk.X)
            if minimum is None:
                seed_row = ttk.Frame(group, style="Surface.TFrame")
                seed_row.pack(fill=tk.X, pady=(3, 0))
                control = ttk.Entry(seed_row, textvariable=variable, width=9)
                control.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self.seed_random_button = ttk.Button(
                    seed_row,
                    text="⚅",
                    width=3,
                    style="Icon.TButton",
                    command=self.randomize_sampling_seed,
                )
                self.seed_random_button.pack(side=tk.LEFT, padx=(6, 0))
                self.seed_random_tooltip = ToolTip(
                    self.seed_random_button, "随机生成种子"
                )
                self.sampling_save_button = ttk.Button(
                    seed_row,
                    text="保存参数",
                    command=self.save_sampling_settings,
                )
                self.sampling_save_button.pack(side=tk.RIGHT, padx=(8, 0))
                self.sampling_control_widgets[label] = control
                self.seed_entry = control
            else:
                control = ttk.Spinbox(
                    group,
                    from_=minimum,
                    to=maximum,
                    increment=increment,
                    textvariable=variable,
                    width=9,
                )
                control.pack(fill=tk.X, pady=(3, 0))
                self.sampling_control_widgets[label] = control
            control.bind(
                "<MouseWheel>", self._form_control_mousewheel, add="+"
            )
        for column in range(2):
            controls.grid_columnconfigure(column, weight=1, uniform="sampling")
        ttk.Label(
            parent,
            textvariable=self.sampling_support_var,
            style="Muted.TLabel",
            wraplength=320,
        ).pack(anchor=tk.W, fill=tk.X, pady=(2, 0))

    def _build_launch_view(self) -> None:
        self._launch_source = None
        for asset_name in ("assets/launch-hero.jpg", "assets/launch-hero.png"):
            try:
                with Image.open(resource_path(asset_name)) as source:
                    self._launch_source = source.convert("RGB")
                break
            except (OSError, ValueError):
                continue
        try:
            with Image.open(resource_path("assets/qianyi-app-icon.png")) as source:
                self._launch_icon_source = source.convert("RGBA")
        except (OSError, ValueError):
            self._launch_icon_source = None
        self.launch_canvas = tk.Canvas(
            self.launch_frame,
            background="#090d1d",
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

        target_size = (width, height)
        if self._launch_photo is None or self._launch_photo_size != target_size:
            self._launch_photo = ImageTk.PhotoImage(
                self._compose_launch_background(width, height),
                master=self.root,
            )
            self._launch_photo_size = target_size
        self.launch_canvas.create_image(
            0, 0, image=self._launch_photo, anchor=tk.NW
        )

        scale = min(width / 1920, height / 1080)
        scale = max(0.68, min(1.4, scale))
        margin_x = max(38, round(width * 0.032))
        brand_y = max(34, round(height * 0.068))
        icon_size = max(42, round(58 * scale))
        icon_target = (icon_size, icon_size)
        if (
            self._launch_icon_source is not None
            and (self._launch_icon_photo is None or self._launch_icon_photo_size != icon_target)
        ):
            rendered_icon = ImageOps.fit(
                self._launch_icon_source, icon_target, Image.Resampling.LANCZOS
            )
            self._launch_icon_photo = ImageTk.PhotoImage(
                rendered_icon, master=self.root
            )
            self._launch_icon_photo_size = icon_target
        if self._launch_icon_photo is not None:
            self.launch_canvas.create_image(
                margin_x, brand_y, image=self._launch_icon_photo, anchor=tk.NW
            )

        brand_x = margin_x + icon_size + max(14, round(16 * scale))
        brand_font = max(17, round(28 * scale))
        version_font = max(9, round(11 * scale))
        font_pixels = 1.25
        self.launch_canvas.create_text(
            brand_x, brand_y + icon_size * 0.38, text=APP_TITLE, anchor=tk.W,
            fill="#f7f4ff",
            font=(UI_FONT, -round(brand_font * font_pixels), "bold"),
        )
        self.launch_canvas.create_text(
            brand_x, brand_y + icon_size * 0.82, text=f"v{APP_VERSION}", anchor=tk.W,
            fill="#a8a7c8",
            font=("Segoe UI", -round(version_font * font_pixels), "bold"),
        )

        panel_width = min(width * 0.45, max(520, 740 * scale))
        content_y = max(245, height * 0.39)
        eyebrow_font = max(10, round(13 * scale))
        title_font = max(22, round(32 * scale))
        body_font = max(10, round(13 * scale))
        status_title, status_detail = self._launch_status_copy()
        self.launch_canvas.create_text(
            margin_x, content_y, text="正在准备工作环境", anchor=tk.W,
            fill="#a796ff",
            font=(UI_FONT, -round(eyebrow_font * font_pixels), "bold"),
        )
        self.launch_canvas.create_text(
            margin_x, content_y + 47 * scale, text=status_title, anchor=tk.W,
            fill="#f8f7ff",
            font=(UI_FONT, -round(title_font * font_pixels), "bold"),
        )

        track_y = content_y + 103 * scale
        track_right = margin_x + panel_width
        self.launch_canvas.create_line(
            margin_x, track_y, track_right, track_y, fill="#353854",
            width=max(3, round(4 * scale)), capstyle=tk.ROUND,
        )
        progress_right = margin_x + panel_width * self.launch_progress / 100
        if self.launch_progress > 0:
            self.launch_canvas.create_line(
                margin_x, track_y, progress_right, track_y, fill="#8172ff",
                width=max(3, round(4 * scale)), capstyle=tk.ROUND,
            )
        dot_radius = max(4, round(5 * scale))
        self.launch_canvas.create_oval(
            progress_right - dot_radius, track_y - dot_radius,
            progress_right + dot_radius, track_y + dot_radius,
            fill="#9b8cff", outline="#d2cbff", width=1,
        )
        self.launch_canvas.create_text(
            track_right, track_y - 19 * scale, text=f"{self.launch_progress}%", anchor=tk.E,
            fill="#f2efff",
            font=(LATIN_FONT, -round(body_font * font_pixels), "bold"),
        )

        card_top = track_y + 16 * scale
        card_height = max(72, 88 * scale)
        card_bottom = card_top + card_height
        self.launch_canvas.create_rectangle(
            margin_x, card_top, track_right, card_bottom,
            fill="#17172e", outline="#51488b", width=1,
        )
        spinner_x = margin_x + 30 * scale
        spinner_y = (card_top + card_bottom) / 2
        spinner_r = max(9, 11 * scale)
        self.launch_canvas.create_arc(
            spinner_x - spinner_r, spinner_y - spinner_r,
            spinner_x + spinner_r, spinner_y + spinner_r,
            start=(self.launch_progress * 9) % 360, extent=245, style=tk.ARC,
            outline="#8d7cff", width=max(2, round(2 * scale)),
        )
        text_x = margin_x + 58 * scale
        self.launch_canvas.create_text(
            text_x, spinner_y - 11 * scale, text="视觉工作台", anchor=tk.W,
            fill="#f0edff",
            font=(UI_FONT, -round(body_font * font_pixels), "bold"),
        )
        self.launch_canvas.create_text(
            text_x, spinner_y + 14 * scale, text=status_detail, anchor=tk.W,
            fill="#a7a5bd",
            font=(
                UI_FONT,
                -round(max(9, body_font - 1) * font_pixels),
            ),
        )
        self.launch_canvas.create_text(
            track_right - 18 * scale, spinner_y,
            text="已完成" if self.launch_progress >= 100 else "进行中", anchor=tk.E,
            fill="#b7adff",
            font=(UI_FONT, -round(body_font * font_pixels), "bold"),
        )

        footer_y = height - max(46, round(61 * scale))
        footer_dot = max(3, round(4 * scale))
        self.launch_canvas.create_oval(
            margin_x, footer_y - footer_dot, margin_x + footer_dot * 2,
            footer_y + footer_dot, fill="#8172ff", outline="",
        )
        self.launch_canvas.create_text(
            margin_x + footer_dot * 2 + 10 * scale, footer_y,
            text="启动准备画面已加载", anchor=tk.W, fill="#777993",
            font=(
                UI_FONT,
                -round(max(9, round(11 * scale)) * font_pixels),
            ),
        )

    def _compose_launch_background(self, width: int, height: int) -> Image.Image:
        width = max(2, width)
        height = max(2, height)
        if self._launch_source is not None:
            background = ImageOps.fit(
                self._launch_source,
                (width, height),
                Image.Resampling.LANCZOS,
                centering=(0.62, 0.5),
            )
        else:
            top = (16, 20, 47)
            bottom = (8, 12, 29)
            gradient = []
            for y in range(height):
                ratio = y / max(1, height - 1)
                gradient.append(
                    tuple(round(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3))
                )
            background = Image.new("RGB", (1, height))
            background.putdata(gradient)
            background = background.resize((width, height))

            purple = Image.new("RGB", (width, height), "#31224d")
            purple_mask = Image.new("L", (width, 1))
            purple_mask.putdata(
                [round(150 * (x / max(1, width - 1)) ** 1.7) for x in range(width)]
            )
            purple_mask = purple_mask.resize((width, height))
            background = Image.composite(purple, background, purple_mask)

            pattern = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(pattern)
            right_start = round(width * 0.52)
            grid_step = max(52, width // 26)
            for x in range(right_start, width, grid_step):
                draw.line((x, 0, x, height), fill=(142, 130, 220, 20), width=1)
            for y in range(grid_step // 2, height, grid_step):
                draw.line((right_start, y, width, y), fill=(112, 183, 219, 14), width=1)
            draw.line(
                (right_start, height * 0.72, width, height * 0.22),
                fill=(120, 103, 255, 58), width=max(2, width // 960),
            )
            draw.line(
                (right_start + width * 0.08, height, width, height * 0.47),
                fill=(217, 125, 196, 34), width=max(2, width // 1200),
            )
            background = Image.alpha_composite(background.convert("RGBA"), pattern).convert("RGB")

        left_veil = Image.new("RGB", (width, height), "#090d1d")
        veil_mask = Image.new("L", (width, 1))
        veil_values = []
        for x in range(width):
            ratio = x / max(1, width - 1)
            if ratio <= 0.25:
                alpha = 255
            elif ratio >= 0.62:
                alpha = 0
            else:
                progress = (ratio - 0.25) / 0.37
                smooth = progress * progress * (3.0 - 2.0 * progress)
                alpha = round(255 * (1.0 - smooth))
            veil_values.append(alpha)
        veil_mask.putdata(veil_values)
        veil_mask = veil_mask.resize((width, height))
        background = Image.composite(left_veil, background, veil_mask)

        bottom_veil = Image.new("RGB", (width, height), "#090d1d")
        bottom_mask = Image.new("L", (1, height))
        bottom_mask.putdata(
            [
                0 if y / max(1, height - 1) < 0.78
                else round(205 * (y / max(1, height - 1) - 0.78) / 0.22)
                for y in range(height)
            ]
        )
        bottom_mask = bottom_mask.resize((width, height))
        return Image.composite(bottom_veil, background, bottom_mask)

    def _launch_status_copy(self) -> tuple[str, str]:
        if self.launch_progress < 35:
            return "正在加载视觉工作台", "正在加载界面与视觉资源"
        if self.launch_progress < 75:
            return "正在初始化标注组件", "正在准备模型、项目与任务组件"
        return "工作台即将就绪", "正在完成最后的启动检查"

    def _build_project_center(self) -> None:
        topbar = ttk.Frame(
            self.project_center_frame,
            style="Topbar.TFrame",
            padding=(18, 10),
        )
        topbar.pack(fill=tk.X)
        title_block = ttk.Frame(topbar, style="Topbar.TFrame")
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="项目中心", style="TopbarTitle.TLabel").pack(anchor=tk.W)
        self.project_count_var = tk.StringVar(value="最近项目 0")
        ttk.Label(title_block, textvariable=self.project_count_var, style="TopbarMuted.TLabel").pack(anchor=tk.W, pady=(2, 0))
        actions = ttk.Frame(topbar, style="Topbar.TFrame")
        actions.pack(side=tk.RIGHT)
        ttk.Button(
            actions,
            text="单次反推",
            style="Primary.TButton",
            command=self.show_single_reverse,
        ).pack(side=tk.LEFT)
        ttk.Button(actions, text="添加项目", command=self.add_project).pack(
            side=tk.LEFT, padx=(7, 0)
        )
        self.return_project_button = ttk.Button(
            actions,
            text="返回当前项目",
            command=self.return_to_current_project,
            state=tk.DISABLED,
        )
        self.return_project_button.pack(side=tk.LEFT, padx=(7, 0))
        ttk.Button(actions, text="打开项目", command=self.continue_selected_project).pack(side=tk.LEFT, padx=(7, 0))
        self.resume_project_button = ttk.Button(
            actions,
            text="恢复未完成",
            command=self.resume_selected_project,
        )
        self.resume_project_button.pack(side=tk.LEFT, padx=(7, 0))
        ttk.Button(actions, text="删除项目", style="Danger.TButton", command=self.delete_selected_project).pack(side=tk.LEFT, padx=(7, 0))
        self._build_theme_toggle(actions).pack(side=tk.LEFT, padx=(10, 0))

        center_body = ttk.Frame(self.project_center_frame, padding=(18, 14, 18, 18))
        center_body.pack(fill=tk.BOTH, expand=True)

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

    def _build_system_info(self) -> None:
        topbar = ttk.Frame(
            self.system_info_frame, style="Topbar.TFrame", padding=(18, 10)
        )
        topbar.pack(fill=tk.X)
        title_block = ttk.Frame(topbar, style="Topbar.TFrame")
        title_block.pack(side=tk.LEFT)
        ttk.Label(
            title_block, text="系统说明", style="TopbarTitle.TLabel"
        ).pack(anchor=tk.W)
        ttk.Label(
            title_block,
            text=f"功能、版本与更新中心  ·  v{APP_VERSION}",
            style="TopbarMuted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        actions = ttk.Frame(topbar, style="Topbar.TFrame")
        actions.pack(side=tk.RIGHT)
        ttk.Button(
            actions, text="返回工作区", command=self.show_workspace
        ).pack(side=tk.LEFT)
        self._build_theme_toggle(actions).pack(side=tk.LEFT, padx=(10, 0))

        module_bar = ttk.Frame(
            self.system_info_frame, style="Topbar.TFrame", padding=(12, 8)
        )
        module_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        nav_specs = (
            ("project", "项目中心", self.show_project_center),
            ("image", "图像打标", lambda: self.select_workflow("image")),
            ("video", "视频反推", lambda: self.select_workflow("video")),
            ("single", "单次反推", self.show_single_reverse),
            ("platform", "平台设置", self.open_platform_config),
            ("system", "系统说明", self.show_system_info),
        )
        self.system_nav_buttons = {}
        for key, label, command in nav_specs:
            button = ttk.Button(
                module_bar,
                text=label,
                image=self._toolbar_icon(key),
                compound=tk.LEFT,
                style="NavActive.TButton" if key == "system" else "Nav.TButton",
                width=10,
                command=command,
            )
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.system_nav_buttons[key] = button

        body = ttk.Frame(self.system_info_frame)
        body.pack(fill=tk.BOTH, expand=True)
        self.system_info_canvas = tk.Canvas(
            body,
            background=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.system_info_scrollbar = ttk.Scrollbar(
            body,
            orient=tk.VERTICAL,
            command=self.system_info_canvas.yview,
        )
        self.system_info_canvas.configure(
            yscrollcommand=self.system_info_scrollbar.set
        )
        self.system_info_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.system_info_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        content = ttk.Frame(
            self.system_info_canvas, padding=(20, 18, 20, 18)
        )
        self.system_info_content = content
        self.system_info_window_id = self.system_info_canvas.create_window(
            (0, 0), window=content, anchor=tk.NW
        )
        content.bind(
            "<Configure>", self._system_info_content_resized, add="+"
        )
        self.system_info_canvas.bind(
            "<Configure>", self._system_info_canvas_resized, add="+"
        )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.system_info_canvas.bind(
                sequence, self._system_info_mousewheel, add="+"
            )
        intro = self._semantic_heading(
            content,
            "ⓘ",
            "芊熠智能打标工作台",
            "blue",
            "面向图像与视频训练素材的本地智能标注工作台",
        )
        intro.pack(fill=tk.X, pady=(0, 16))

        feature_host = ttk.Frame(content)
        feature_host.pack(fill=tk.X)
        self.system_feature_descriptions = []
        for column in range(2):
            feature_host.grid_columnconfigure(column, weight=1, uniform="feature")
        feature_symbols = ("▧", "✦", "✓", "⇩")
        feature_tones = ("yellow", "violet", "red", "blue")
        for index, (title, description) in enumerate(FEATURE_GROUPS):
            panel = ttk.Frame(
                feature_host, style="Surface.TFrame", padding=(14, 12)
            )
            panel.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 7) if index % 2 == 0 else (7, 0),
                pady=(0, 10),
            )
            heading = self._semantic_heading(
                panel,
                feature_symbols[index],
                title,
                feature_tones[index],
            )
            heading.pack(fill=tk.X)
            description_label = ttk.Label(
                panel,
                text=description,
                style="Surface.TLabel",
                wraplength=560,
                justify=tk.LEFT,
            )
            description_label.pack(fill=tk.X, pady=(9, 0))
            self.system_feature_descriptions.append(description_label)
        feature_host.bind("<Configure>", self._layout_system_features)

        maintenance_panel = ttk.Frame(
            content, style="Surface.TFrame", padding=(14, 12)
        )
        maintenance_panel.pack(fill=tk.X, pady=(4, 10))
        maintenance_head = self._semantic_heading(
            maintenance_panel,
            "⚙",
            "运行维护",
            "yellow",
            "LOCAL SAFETY",
        )
        maintenance_head.pack(fill=tk.X)
        maintenance_actions = ttk.Frame(
            maintenance_head, style="Surface.TFrame"
        )
        maintenance_actions.pack(side=tk.RIGHT)
        self.diagnostic_button = ttk.Button(
            maintenance_actions,
            text="导出诊断包",
            command=self.export_diagnostic_bundle,
        )
        self.diagnostic_button.pack(side=tk.RIGHT)
        self.backup_button = ttk.Button(
            maintenance_actions,
            text="立即备份",
            command=self.backup_metadata_now,
        )
        self.backup_button.pack(side=tk.RIGHT, padx=(0, 7))
        self.system_maintenance_state_var = tk.StringVar(
            value="项目状态每日自动备份；诊断包不会包含 API Key、媒体或完整标签内容。"
        )
        ttk.Label(
            maintenance_panel,
            textvariable=self.system_maintenance_state_var,
            style="Surface.TLabel",
            wraplength=920,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(9, 0))

        runtime_panel = ttk.Frame(
            content, style="Surface.TFrame", padding=(14, 12)
        )
        runtime_panel.pack(fill=tk.X, pady=(4, 10))
        runtime_head = self._semantic_heading(
            runtime_panel,
            "⌁",
            "本地运行组件",
            "blue",
            "DOWNLOADS & USAGE",
        )
        runtime_head.pack(fill=tk.X)
        runtime_host = ttk.Frame(runtime_panel)
        runtime_host.pack(fill=tk.X, pady=(10, 0))
        self.system_runtime_descriptions = []
        for column in range(2):
            runtime_host.grid_columnconfigure(column, weight=1, uniform="runtime")
        runtime_specs = (
            ("lmstudio", "◉", "yellow"),
            ("llamacpp", "⌘", "violet"),
        )
        for index, (runtime_key, symbol, tone) in enumerate(runtime_specs):
            info = LOCAL_RUNTIME_PORTALS[runtime_key]
            card = ttk.Frame(
                runtime_host, style="SurfaceAlt.TFrame", padding=(12, 10)
            )
            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0, 6) if index == 0 else (6, 0),
            )
            heading = self._semantic_heading(card, symbol, info["title"], tone)
            heading.pack(fill=tk.X)
            description = ttk.Label(
                card,
                text=info["description"],
                style="Surface.TLabel",
                wraplength=420,
                justify=tk.LEFT,
            )
            description.pack(fill=tk.X, pady=(8, 9))
            self.system_runtime_descriptions.append(description)
            ttk.Button(
                card,
                text=info["button"],
                command=lambda key=runtime_key: self.open_local_runtime_portal(key),
            ).pack(anchor=tk.W)
        runtime_host.bind(
            "<Configure>",
            lambda event: self._layout_runtime_portals(event.width),
        )

        version_panel = ttk.Frame(
            content, style="Surface.TFrame", padding=(14, 12)
        )
        version_panel.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        version_head = self._semantic_heading(
            version_panel, "↻", "版本信息", "violet", "GITHUB RELEASES"
        )
        version_head.pack(fill=tk.X)
        version_actions = ttk.Frame(version_head)
        version_actions.pack(side=tk.RIGHT)
        ttk.Button(
            version_actions,
            text="检查版本",
            command=lambda: self.check_for_updates(True),
        ).pack(side=tk.RIGHT)
        self.system_update_button = ttk.Button(
            version_actions,
            text="下载并安装",
            style="Primary.TButton",
            command=self.download_and_install_update,
            state=tk.DISABLED,
        )
        self.system_update_button.pack(side=tk.RIGHT, padx=(0, 7))

        self.system_update_state_var = tk.StringVar(
            value=f"当前版本 v{APP_VERSION}  ·  等待检查 GitHub Release"
        )
        self.system_release_date_var = tk.StringVar(value="")
        state_row = ttk.Frame(version_panel, style="Surface.TFrame")
        state_row.pack(fill=tk.X, pady=(12, 8))
        self.system_update_state_label = ttk.Label(
            state_row,
            textvariable=self.system_update_state_var,
            style="StageActive.TLabel",
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=620,
        )
        self.system_update_state_label.pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.system_release_date_label = ttk.Label(
            state_row,
            textvariable=self.system_release_date_var,
            style="Muted.TLabel",
            anchor=tk.E,
        )
        self.system_release_date_label.pack(side=tk.RIGHT, padx=(12, 0))
        state_row.bind(
            "<Configure>", self._layout_system_update_state, add="+"
        )

        self.system_auto_update_var = tk.BooleanVar(
            value=bool(self.settings.get("auto_check_updates", True))
        )
        self.system_auto_update_check = ttk.Checkbutton(
            version_panel,
            text="启动后自动检查 GitHub 版本更新",
            variable=self.system_auto_update_var,
            command=self._save_auto_update_preference,
            style="Surface.TCheckbutton",
        )
        self.system_auto_update_check.pack(anchor=tk.W, pady=(0, 9))

        self.system_release_notes = scrolledtext.ScrolledText(
            version_panel,
            height=8,
            wrap=tk.WORD,
            font=(UI_FONT, UI_TEXT_SIZE),
        )
        self.system_release_notes.pack(fill=tk.BOTH, expand=True)
        self._style_text(self.system_release_notes, readonly=True)
        self._set_release_notes(
            "本版更新\n\n" + "\n".join(f"• {note}" for note in RELEASE_NOTES)
        )
        self._bind_system_info_wheel(content)

    def _hide_views(self) -> None:
        for frame in (
            self.launch_frame,
            self.project_center_frame,
            self.workspace_frame,
            self.single_reverse_frame,
            self.system_info_frame,
        ):
            frame.pack_forget()

    def _use_fullscreen_launch_window(self) -> None:
        if sys.platform != "win32":
            return
        try:
            # The launch page and workspace now use separate tuned sizes so the
            # hero image and inspector can breathe without crowding the text.
            self.root.overrideredirect(False)
            geometry = getattr(self, "launch_geometry", None)
            if not geometry:
                geometry = configure_launch_window(self.root)
            self.root.minsize(*getattr(self, "launch_size", _geometry_size(geometry)))
            self.root.geometry(geometry)
            self.root.configure(background="#090d1d")
            self.root.wm_attributes("-topmost", True)
        except tk.TclError:
            pass

    def _restore_main_window(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.overrideredirect(False)
            self.root.wm_attributes("-topmost", False)
            self.root.configure(background=COLORS["bg"])
            self.root.state("normal")
            geometry = getattr(self, "normal_geometry", None)
            if not geometry:
                geometry = configure_main_window(self.root)
            self.root.minsize(*getattr(self, "normal_size", _geometry_size(geometry)))
            self.root.geometry(geometry)
        except tk.TclError:
            pass

    def show_launch(self) -> None:
        self._hide_views()
        self._use_fullscreen_launch_window()
        self.launch_frame.pack(fill=tk.BOTH, expand=True)
        if self.splash_after_id is not None:
            try:
                self.root.after_cancel(self.splash_after_id)
            except tk.TclError:
                pass
        self.launch_progress = 0
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
        except tk.TclError:
            pass
        # Map the window before the first idle layout.  On DPI-aware Windows
        # builds, updating a withdrawn Tk root here can block before any UI is
        # painted, which looks like the EXE failed to open.
        self.root.update_idletasks()
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
        if self.project_center_frame.winfo_manager() != "pack":
            self._hide_views()
            self._restore_main_window()
            self.project_center_frame.pack(fill=tk.BOTH, expand=True)
        self.refresh_project_center()
        self._schedule_theme_sync()

    def show_system_info(self) -> None:
        self._hide_views()
        self._restore_main_window()
        self.system_info_frame.pack(fill=tk.BOTH, expand=True)
        self._refresh_system_nav_icons()
        self._fit_system_info_content()
        self.root.after_idle(self._fit_system_info_content)
        if self.latest_release is not None:
            self._apply_release_to_system_info(self.latest_release)
        self._schedule_theme_sync()

    def show_single_reverse(self) -> None:
        self._hide_views()
        self._restore_main_window()
        self.single_reverse_frame.pack(fill=tk.BOTH, expand=True)
        self._update_single_settings_summary()
        self._refresh_single_nav_icons()
        self._set_single_mode(self.single_mode_var.get())
        self.root.after_idle(self._render_single_image)
        self.root.after_idle(self._render_single_media_preview)
        self.root.after_idle(self._render_single_timeline)
        self._schedule_theme_sync()

    def _save_auto_update_preference(self) -> None:
        enabled = bool(self.system_auto_update_var.get())
        self.settings["auto_check_updates"] = enabled
        self.settings_store.save(self.settings)
        if enabled and self.latest_release is None:
            self.check_for_updates(False)

    def show_workspace(self) -> None:
        already_visible = self.workspace_frame.winfo_manager() == "pack"
        if not already_visible:
            self._hide_views()
            self._restore_main_window()
            self.workspace_frame.pack(fill=tk.BOTH, expand=True)
        self._media_mode_changed(scan=False)
        self._schedule_theme_sync()
        if already_visible:
            return
        if self.sash_after_id is not None:
            try:
                self.root.after_cancel(self.sash_after_id)
            except tk.TclError:
                pass
        self.sash_after_id = self.root.after(120, self._set_initial_sash)

    def _native_view_changed(self, _event=None) -> None:
        self._schedule_theme_sync()

    def _set_release_notes(self, text: str) -> None:
        self.system_release_notes.configure(state=tk.NORMAL)
        self.system_release_notes.delete("1.0", tk.END)
        self.system_release_notes.insert("1.0", text.strip())
        self.system_release_notes.configure(state=tk.DISABLED)
        # Setting a native Text widget to disabled can restore the Windows
        # system background, so reapply the readonly surface after locking it.
        self._style_text(self.system_release_notes, readonly=True)
        self._schedule_theme_sync()

    def _refresh_release_notes_style(self) -> None:
        if self.closing or not self.system_release_notes.winfo_exists():
            return
        self._style_text(self.system_release_notes, readonly=True)

    def _refresh_system_nav_icons(self) -> None:
        if not hasattr(self, "system_nav_buttons"):
            return
        for key, button in self.system_nav_buttons.items():
            icon = self._toolbar_icon(key)
            button.configure(
                image=icon,
                style="NavActive.TButton" if key == "system" else "Nav.TButton",
            )
            button.image = icon

    def _refresh_single_nav_icons(self) -> None:
        if not hasattr(self, "single_nav_buttons"):
            return
        for key, button in self.single_nav_buttons.items():
            icon = self._toolbar_icon(key)
            button.configure(
                image=icon,
                style="NavActive.TButton" if key == "single" else "Nav.TButton",
            )
            button.image = icon

    def _run_automatic_backup(self) -> None:
        try:
            created = maybe_create_automatic_backup()
        except (OSError, ValueError, RuntimeError) as error:
            self._post_event(
                "maintenance_error",
                {"error": str(error), "automatic": True},
            )
            return
        if created is not None:
            self._post_event(
                "maintenance_done",
                {"path": created, "kind": "backup", "automatic": True},
            )

    def _start_maintenance_export(self, kind: str, destination: Path) -> None:
        if self.maintenance_running:
            return
        self.maintenance_running = True
        self.backup_button.configure(state=tk.DISABLED)
        self.diagnostic_button.configure(state=tk.DISABLED)
        label = "项目状态备份" if kind == "backup" else "隐私脱敏诊断包"
        self.system_maintenance_state_var.set(f"正在生成{label}…")

        def worker() -> None:
            try:
                if kind == "backup":
                    output = create_app_backup(destination=destination)
                else:
                    output = create_diagnostic_bundle(destination=destination)
                self._post_event(
                    "maintenance_done",
                    {"path": output, "kind": kind, "automatic": False},
                )
            except (OSError, ValueError, RuntimeError) as error:
                self._post_event(
                    "maintenance_error",
                    {"error": str(error), "automatic": False},
                )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"manual-{kind}-export",
        ).start()

    def backup_metadata_now(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="保存项目状态备份",
            defaultextension=".zip",
            filetypes=[("ZIP 备份", "*.zip")],
            initialfile=f"qianyi-backup-{datetime.now():%Y%m%d-%H%M%S}.zip",
        )
        if destination:
            self._start_maintenance_export("backup", Path(destination))

    def export_diagnostic_bundle(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="导出隐私脱敏诊断包",
            defaultextension=".zip",
            filetypes=[("ZIP 诊断包", "*.zip")],
            initialfile=f"qianyi-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.zip",
        )
        if destination:
            self._start_maintenance_export("diagnostics", Path(destination))

    def check_for_updates(self, manual: bool = False) -> None:
        if self.update_check_running:
            if manual:
                self.system_update_state_var.set("正在检查 GitHub Release…")
            return
        self.update_check_running = True
        if manual:
            self.system_update_state_var.set("正在检查 GitHub Release…")

        def worker() -> None:
            try:
                release = check_latest_release()
                self._post_event(
                    "update_checked", {"release": release, "manual": manual}
                )
            except Exception as error:
                self._post_event(
                    "update_error", {"error": str(error), "manual": manual}
                )

        threading.Thread(
            target=worker, daemon=True, name="github-release-check"
        ).start()

    def _run_scheduled_update_check(self) -> None:
        self.update_after_id = None
        if not self.closing:
            self.check_for_updates()

    def _apply_release_to_system_info(self, release: dict) -> None:
        tag = release.get("tag") or "未知版本"
        if release.get("is_newer"):
            state = f"发现新版本 {tag}  ·  当前 v{APP_VERSION}"
            published = str(release.get("published_at") or "")[:10]
            self.system_release_date_var.set(
                f"发布日期 {published}" if published else ""
            )
            notes = str(release.get("notes") or "").strip()
            self._set_release_notes(notes or "该 Release 未提供更新说明。")
            can_install = bool(release.get("windows_asset")) and bool(
                getattr(sys, "frozen", False)
            )
            self.system_update_button.configure(
                state=tk.NORMAL if can_install else tk.DISABLED,
                text="下载并安装" if can_install else "暂无安装包",
            )
        else:
            state = f"当前已是最新版本 v{APP_VERSION}  ·  GitHub {tag}"
            # A development build can be newer than the public release. Keep
            # its own bundled changelog instead of replacing it with old notes.
            self.system_release_date_var.set("")
            self._set_release_notes(
                "本版更新\n\n" + "\n".join(f"• {note}" for note in RELEASE_NOTES)
            )
            self.system_update_button.configure(state=tk.DISABLED, text="已是最新版")
        self.system_update_state_var.set(state)

    def _show_update_banner(self, release: dict) -> None:
        self.latest_release = release
        self.update_banner_var.set(
            f"发现新版本 {release.get('tag', '')}  ·  当前 v{APP_VERSION}"
        )
        if not self.update_banner_visible:
            self.update_banner.pack(
                side=tk.TOP, fill=tk.X, after=self.workspace_header
            )
            self.update_banner_visible = True

    def download_and_install_update(self) -> None:
        if self.update_download_running:
            return
        if (self.runner and self.runner.running) or (
            self.controller_thread and self.controller_thread.is_alive()
        ):
            messagebox.showwarning(
                "软件更新",
                "请等待当前扫描、分析或标注任务结束后再安装更新。",
                parent=self.root,
            )
            return
        release = self.latest_release or {}
        asset = release.get("windows_asset")
        if not release.get("is_newer") or not isinstance(asset, dict):
            messagebox.showinfo(
                "软件更新", "当前没有可安装的 Windows 新版本。", parent=self.root
            )
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "软件更新",
                "源码运行模式不会覆盖 Python 环境，请在打包后的 EXE 中使用自动更新。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "下载并安装更新",
            f"将下载 {release.get('tag', '新版本')}，完成后自动关闭、覆盖并重启软件。\n\n"
            "项目文件和平台设置不会被删除。是否继续？",
            parent=self.root,
        ):
            return
        self.update_download_running = True
        self.system_update_button.configure(state=tk.DISABLED, text="准备下载…")
        self.system_update_state_var.set("正在准备下载更新…")

        def worker() -> None:
            try:
                update_dir = Path(tempfile.mkdtemp(prefix="qianyi-update-"))
                last_percent = -1

                def progress(received: int, total: int) -> None:
                    nonlocal last_percent
                    percent = min(100, round(received * 100 / total)) if total else 0
                    if percent == last_percent or (percent < 100 and percent % 2):
                        return
                    last_percent = percent
                    self._post_event(
                        "update_download_progress",
                        {"received": received, "total": total, "percent": percent},
                    )

                package = download_release_asset(asset, update_dir, progress)
                executable = extract_update_executable(package, update_dir / "payload")
                target = Path(sys.executable).resolve()
                write_test = target.parent / ".qianyi-update-write-test"
                write_test.write_bytes(b"ok")
                write_test.unlink(missing_ok=True)
                runtime_home = Path(str(getattr(sys, "_MEIPASS", "")))
                bootloader_pid = (
                    os.getppid()
                    if runtime_home.name.upper().startswith("_MEI")
                    else None
                )
                script = create_windows_update_script(
                    executable,
                    target,
                    os.getpid(),
                    update_dir,
                    bootloader_pid=bootloader_pid,
                )
                self._post_event(
                    "update_download_ready",
                    {"script": script, "tag": release.get("tag", "")},
                )
            except Exception as error:
                self._post_event("update_download_error", {"error": str(error)})

        threading.Thread(
            target=worker, daemon=True, name="release-update-download"
        ).start()

    def _start_update_installer(self, script: Path, tag: str) -> None:
        try:
            installer_process = launch_windows_update_installer(script)
        except (OSError, ValueError) as error:
            self.update_download_running = False
            self.system_update_button.configure(state=tk.NORMAL, text="重新安装")
            messagebox.showerror("无法启动更新器", str(error), parent=self.root)
            return
        self.root.after(
            250,
            lambda: self._finish_update_handoff(
                installer_process, script, tag
            ),
        )

    def _finish_update_handoff(
        self, installer_process: subprocess.Popen, script: Path, tag: str
    ) -> None:
        return_code = installer_process.poll()
        if return_code is not None:
            self.update_download_running = False
            self.system_update_state_var.set("无法启动独立更新器")
            self.system_update_button.configure(state=tk.NORMAL, text="重新安装")
            log_path = script.parent / "update-install.log"
            try:
                detail = log_path.read_text(encoding="utf-8-sig").strip()
            except OSError:
                detail = ""
            message = f"更新器启动后立即退出（代码 {return_code}）。"
            if detail:
                message += f"\n\n{detail[-1200:]}"
            messagebox.showerror("无法启动更新器", message, parent=self.root)
            return
        self.update_install_pending = True
        self.log(f"更新 {tag} 已下载，正在退出并安装")
        self.close()

    def dismiss_update_banner(self) -> None:
        self.update_banner.pack_forget()
        self.update_banner_visible = False

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
            "interrupted": "上次意外中断",
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

    def resume_selected_project(self) -> None:
        folder = self._selected_project_folder()
        if folder is None:
            messagebox.showinfo("项目中心", "请先选择一个项目", parent=self.root)
            return
        if self.runner and self.runner.running:
            messagebox.showwarning(
                "恢复任务", "当前已有任务正在运行", parent=self.root
            )
            return
        summary = load_project_summary(folder)
        mode = summary.get("mode") if summary.get("mode") in {"image", "video"} else "image"
        paths = load_incomplete_paths(folder, mode)
        if not paths:
            messagebox.showinfo(
                "恢复任务",
                "该项目没有可恢复的失败、取消或未开始项目。",
                parent=self.root,
            )
            return
        self.media_mode_var.set(mode)
        self.pending_resume_paths = paths
        if not self.open_project(folder):
            self.pending_resume_paths = None
            return
        if not (self.controller_thread and self.controller_thread.is_alive()):
            self.root.after_idle(self._start_pending_resume)

    def _start_pending_resume(self) -> None:
        paths = self.pending_resume_paths
        self.pending_resume_paths = None
        if not paths or self.closing:
            return
        available = [path for path in paths if path.is_file()]
        if not available:
            messagebox.showinfo(
                "恢复任务", "未完成素材已经不存在。", parent=self.root
            )
            return
        self.log(f"恢复上次未完成任务：{len(available)} 个素材")
        self.start_task(available, force=True)

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
        self.pending_resume_paths = None
        self._clear_items()
        self.gallery.set_items([], set())
        self.last_failed_paths.clear()
        self.preview_image = None
        self.preview_label.configure(image="", text="选择任务项")
        self.result_text.delete("1.0", tk.END)
        self.log_text.delete("1.0", tk.END)
        self.progress_var.set(0)
        self.progress_text_var.set("")
        self._set_stage(1)
        self._restore_idle_controls()

    def _walk_widget_tree(self):
        """Yield every live widget, including widgets in open child dialogs."""
        pending = [self.root]
        seen: set[str] = set()
        while pending:
            parent = pending.pop()
            try:
                path = str(parent)
                if path in seen or not parent.winfo_exists():
                    continue
                seen.add(path)
                children = parent.winfo_children()
            except tk.TclError:
                continue
            yield parent
            pending.extend(children)

    def _register_themed_menu(self, menu: tk.Menu, role: str = "surface") -> None:
        self._themed_menus[menu] = "input" if role == "input" else "surface"
        self._style_themed_menu(menu)

    def _style_themed_menu(self, menu: tk.Menu) -> None:
        role = self._themed_menus.get(menu, "surface")
        background = (
            COLORS["input_bg"] if role == "input" else COLORS["surface_alt"]
        )
        active_background = (
            COLORS["selection"] if role == "input" else COLORS["hover"]
        )
        try:
            if not menu.winfo_exists():
                self._themed_menus.pop(menu, None)
                return
            menu.configure(
                background=background,
                foreground=COLORS["text"],
                font=(UI_FONT, UI_INPUT_SIZE),
                activebackground=active_background,
                activeforeground=COLORS["text"],
                disabledforeground=COLORS["disabled_fg"],
                selectcolor=COLORS["accent"],
                borderwidth=1,
                activeborderwidth=0,
                relief=tk.FLAT,
            )
        except tk.TclError:
            self._themed_menus.pop(menu, None)

    def _sync_themed_menus(self) -> None:
        for menu in tuple(self._themed_menus):
            self._style_themed_menu(menu)

    def _style_combobox_popdown(self, combobox: ttk.Combobox) -> None:
        """Apply the active palette to ttk's cached native popup Listbox."""
        try:
            if not combobox.winfo_exists():
                self._bound_comboboxes.discard(combobox)
                return
            popdown = str(
                combobox.tk.call(
                    "ttk::combobox::PopdownWindow", combobox._w
                )
            )
            frame = f"{popdown}.f"
            listbox = f"{frame}.l"
            scrollbar = f"{frame}.sb"
            combobox.tk.call(
                popdown,
                "configure",
                "-background",
                COLORS["input_border"],
            )
            combobox.tk.call(
                frame,
                "configure",
                "-style",
                "ComboboxPopdown.TFrame",
            )
            combobox.tk.call(
                listbox,
                "configure",
                "-font",
                (UI_FONT, UI_INPUT_SIZE),
                "-background",
                COLORS["input_bg"],
                "-foreground",
                COLORS["text"],
                "-selectbackground",
                COLORS["selection"],
                "-selectforeground",
                COLORS["text"],
                "-disabledforeground",
                COLORS["disabled_fg"],
                "-highlightbackground",
                COLORS["input_border"],
                "-highlightcolor",
                COLORS["input_focus"],
                "-highlightthickness",
                1,
                "-borderwidth",
                0,
                "-selectborderwidth",
                0,
                "-relief",
                tk.FLAT,
                "-activestyle",
                "none",
            )
            combobox.tk.call(
                scrollbar,
                "configure",
                "-style",
                "TScrollbar",
            )
        except tk.TclError:
            # A popdown can disappear while its dialog is being destroyed.
            pass

    def _register_combobox_theme(self, combobox: ttk.Combobox) -> None:
        if combobox not in self._bound_comboboxes:
            def queue_popdown_style(_event=None, target=combobox):
                # ttk creates the native Listbox after the mouse/key event has
                # propagated.  Styling immediately is therefore racy on
                # Windows; one idle pass catches both newly-created and cached
                # popdowns without adding a visible transition delay.
                try:
                    self.root.after_idle(
                        lambda: self._style_combobox_popdown(target)
                    )
                except tk.TclError:
                    pass

            for sequence in (
                "<ButtonPress-1>",
                "<KeyPress-F4>",
                "<Alt-Down>",
            ):
                combobox.bind(
                    sequence,
                    queue_popdown_style,
                    add="+",
                )
            self._bound_comboboxes.add(combobox)
        self._style_combobox_popdown(combobox)

    def _sync_combobox_popdowns(self) -> None:
        live_comboboxes: set[ttk.Combobox] = set()
        for widget in self._walk_widget_tree():
            if isinstance(widget, ttk.Combobox):
                live_comboboxes.add(widget)
                self._register_combobox_theme(widget)
        for combobox in tuple(self._bound_comboboxes - live_comboboxes):
            try:
                exists = bool(combobox.winfo_exists())
            except tk.TclError:
                exists = False
            if not exists:
                self._bound_comboboxes.discard(combobox)

    def _sync_slide_switches(self) -> None:
        for widget in self._walk_widget_tree():
            if isinstance(widget, SlideSwitch):
                widget.redraw()

    def _sync_native_theme_widgets(self) -> None:
        self._sync_themed_text_widgets()
        self._sync_combobox_popdowns()
        self._sync_themed_menus()
        self._sync_slide_switches()

    def _style_text(
        self,
        widget: tk.Text,
        readonly: bool = False,
        palette: dict | None = None,
        register: bool = True,
    ) -> None:
        palette = palette or THEMES[self.theme_key]
        if register:
            self._themed_text_widgets[widget] = readonly
            if widget not in self._bound_text_widgets:
                for sequence in ("<Map>", "<Visibility>", "<FocusIn>"):
                    widget.bind(
                        sequence,
                        lambda _event, target=widget: self._schedule_text_widget_sync(target),
                        add="+",
                    )
                self._bound_text_widgets.add(widget)
        field_background = (
            palette["input_readonly"] if readonly else palette["input_bg"]
        )
        if not register and self._text_widget_matches_palette(
            widget, field_background, palette
        ):
            return
        try:
            original_state = str(widget.cget("state"))
        except tk.TclError:
            return
        if original_state == tk.DISABLED:
            widget.configure(state=tk.NORMAL)
        options = {
            "background": field_background,
            "foreground": palette["text"],
            "insertbackground": palette["text"],
            "selectbackground": palette["selection"],
            "selectforeground": palette["text"],
            "inactiveselectbackground": palette["selection"],
            "relief": tk.FLAT,
            "borderwidth": 0,
            "highlightthickness": 1,
            "highlightbackground": palette["input_border"],
            "highlightcolor": palette["input_focus"],
            "padx": 10,
            "pady": 8,
            "spacing1": 2,
            "spacing3": 2,
        }
        widget.configure(**options)
        frame = getattr(widget, "frame", None)
        if frame is not None:
            try:
                frame.configure(
                    background=field_background,
                    borderwidth=0,
                    highlightthickness=0,
                )
            except tk.TclError:
                pass
        if hasattr(widget, "vbar"):
            if not isinstance(widget.vbar, ttk.Scrollbar):
                widget.vbar.destroy()
                widget.vbar = ttk.Scrollbar(
                    widget.frame, orient=tk.VERTICAL, command=widget.yview
                )
                widget.vbar.pack(side=tk.RIGHT, fill=tk.Y)
                tk.Pack.pack_configure(
                    widget, side=tk.LEFT, fill=tk.BOTH, expand=True
                )
                widget.configure(yscrollcommand=widget.vbar.set)
            widget.vbar.configure(style="TScrollbar")
        if original_state == tk.DISABLED:
            widget.configure(state=tk.DISABLED)
            # State transitions can restore a cached Windows system brush.
            # Apply the critical colors once more after locking the widget.
            widget.configure(
                background=field_background,
                foreground=palette["text"],
                selectbackground=palette["selection"],
                selectforeground=palette["text"],
            )
        # ``configure`` above already invalidates the native text control.
        # Generating a synthetic Expose event here used to force Windows to
        # repaint, but it also re-triggered the Visibility/Focus synchronizer
        # while Tk was draining idle callbacks.  That feedback loop is what
        # made theme changes appear to stall on some machines.

    @staticmethod
    def _text_widget_matches_palette(
        widget: tk.Text,
        field_background: str,
        palette: dict,
    ) -> bool:
        expected = {
            "background": field_background,
            "foreground": palette["text"],
            "insertbackground": palette["text"],
            "selectbackground": palette["selection"],
            "selectforeground": palette["text"],
            "highlightbackground": palette["input_border"],
            "highlightcolor": palette["input_focus"],
        }
        try:
            if any(
                str(widget.cget(option)).casefold() != str(value).casefold()
                for option, value in expected.items()
            ):
                return False
            frame = getattr(widget, "frame", None)
            if frame is not None and (
                str(frame.cget("background")).casefold()
                != str(field_background).casefold()
            ):
                return False
        except tk.TclError:
            return False
        return True

    def _schedule_text_widget_sync(self, widget: tk.Text) -> None:
        if self.closing or self._window_suspended:
            return
        if widget in self._text_sync_after_ids:
            return

        def refresh() -> None:
            self._text_sync_after_ids.pop(widget, None)
            self._refresh_themed_text_widget(widget)

        after_id = self.root.after_idle(refresh)
        self._text_sync_after_ids[widget] = after_id

    def _cancel_text_sync_jobs(self) -> None:
        for after_id in tuple(self._text_sync_after_ids.values()):
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._text_sync_after_ids.clear()

    def _refresh_themed_text_widget(self, widget: tk.Text) -> None:
        if self.closing:
            return
        try:
            exists = widget.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        readonly = self._themed_text_widgets.get(widget, False)
        self._style_text(
            widget,
            readonly=readonly,
            palette=THEMES[self.theme_key],
            register=False,
        )

    def _discover_themed_text_widgets(self) -> None:
        for widget in self._walk_widget_tree():
            if not isinstance(widget, tk.Text) or widget in self._themed_text_widgets:
                continue
            try:
                readonly = str(widget.cget("state")) == tk.DISABLED
            except tk.TclError:
                continue
            self._style_text(
                widget,
                readonly=readonly,
                palette=THEMES[self.theme_key],
                register=True,
            )

    def _sync_themed_text_widgets(self) -> None:
        self._discover_themed_text_widgets()
        palette = THEMES[self.theme_key]
        for widget, readonly in tuple(self._themed_text_widgets.items()):
            try:
                exists = widget.winfo_exists()
            except tk.TclError:
                exists = False
            if not exists:
                self._themed_text_widgets.pop(widget, None)
                self._bound_text_widgets.discard(widget)
                continue
            self._style_text(
                widget, readonly=readonly, palette=palette, register=False
            )

    def _schedule_theme_sync(self) -> None:
        if self.closing:
            return
        self._cancel_theme_sync_jobs()
        # View changes and newly-created native widgets can safely synchronize
        # after Tk finishes mapping them. Theme buttons themselves use the
        # synchronous path in ``_set_theme``; keeping this helper idle-only
        # avoids constructing every combobox popdown while the window is still
        # being built.
        after_id = None

        def sync() -> None:
            self._theme_sync_after_ids.discard(after_id)
            if not self.closing:
                self._sync_native_theme_widgets()

        after_id = self.root.after_idle(sync)
        self._theme_sync_after_ids.add(after_id)

    def _cancel_theme_sync_jobs(self) -> None:
        for after_id in tuple(self._theme_sync_after_ids):
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._theme_sync_after_ids.clear()

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
                canvas = Image.new("RGB", (160, 120), COLORS["media_bg"])
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
        try:
            self.workspace.grid_columnconfigure(
                0, minsize=WORKSPACE_LEFT_WIDTH, weight=0
            )
            self.workspace.grid_columnconfigure(
                1, minsize=WORKSPACE_CENTER_WIDTH, weight=1
            )
            self.workspace.grid_columnconfigure(
                2, minsize=WORKSPACE_RIGHT_WIDTH, weight=0
            )
        except tk.TclError:
            pass

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
        self.local_runtime_var.set(
            self.settings.get("local_runtime", "huggingface")
        )
        self.local_model_var.set(self.settings.get("local_model_folder", ""))
        self.lmstudio_base_url_var.set(
            self.settings.get("lmstudio_base_url", "http://localhost:1234/v1")
        )
        self.lmstudio_model_var.set(self.settings.get("lmstudio_model", ""))
        self.lmstudio_load_profile_var.set(
            self.settings.get(
                "lmstudio_load_profile", LMSTUDIO_LOAD_PROFILE_DEFAULT
            )
        )
        self.llama_server_path_var.set(self.settings.get("llama_server_path", ""))
        self.llama_model_path_var.set(self.settings.get("llama_model_path", ""))
        self.llama_mmproj_path_var.set(self.settings.get("llama_mmproj_path", ""))
        self.llama_model_alias_var.set(self.settings.get("llama_model_alias", ""))
        self.llama_context_length_var.set(
            int(self.settings.get("llama_context_length", LLAMA_CPP_DEFAULT_CONTEXT_LENGTH))
        )
        self.llama_gpu_layers_var.set(
            int(self.settings.get("llama_gpu_layers", LLAMA_CPP_DEFAULT_GPU_LAYERS))
        )
        focus = self.settings.get("labeling_focus", "subject")
        self.focus_label_var.set(FOCUS_LABELS.get(focus, "训练主体"))
        self.output_language_var.set(self.settings.get("output_language", "zh"))
        self.trigger_word_var.set(self.settings.get("trigger_word", ""))
        self.enable_mtp_var.set(bool(self.settings.get("enable_mtp", False)))
        self.remove_thinking_tags_var.set(
            bool(self.settings.get("remove_thinking_tags", True))
        )
        model = MODELS.get(self.settings.get("model_key"), MODELS[DEFAULT_MODEL_KEY])
        self.model_label_var.set(model.label)
        provider_key = self.settings.get("provider_key", DEFAULT_PROVIDER_KEY)
        if provider_key not in PUBLIC_PROVIDER_KEYS:
            provider_key = DEFAULT_PROVIDER_KEY
        provider = API_PROVIDERS[provider_key]
        self.provider_label_var.set(provider.label)
        legacy_endpoint = self.settings.get("custom_api_endpoint", "").strip()
        if legacy_endpoint:
            self.api_endpoint_by_provider.setdefault("custom", legacy_endpoint)
        self._set_sampling_values(self.settings.get("sampling"))
        presets = self.settings["prompt_presets"]
        self.preset_box["values"] = list(presets)
        selected = self.settings.get("selected_preset")
        if selected not in presets:
            selected = next(iter(presets), "")
        self.preset_var.set(selected)
        self._set_system_prompt(presets.get(selected, ""))
        self.user_prompt_text.insert("1.0", self.settings.get("user_prompt", ""))
        self._provider_changed()
        self._backend_changed()
        self._media_mode_changed(scan=False)
        self._switch_view()

    def _provider_key(self) -> str:
        return PROVIDER_LABELS.get(
            self.provider_label_var.get(), DEFAULT_PROVIDER_KEY
        )

    def _api_model(self) -> str:
        if self._provider_key() == DEFAULT_PROVIDER_KEY:
            return MODELS[self._model_key()].model_id
        return self.model_label_var.get().strip()

    def _platform_log_summary(self) -> str:
        """Describe the backend that was actually saved, not the dormant API fields."""
        if self.backend_var.get() == "local":
            if self.local_runtime_var.get() == "lmstudio":
                model = self.lmstudio_model_var.get().strip() or "未选择模型"
                return f"LM Studio / {model}"
            if self.local_runtime_var.get() == "llamacpp":
                model_path = self.llama_model_path_var.get().strip()
                model = Path(model_path).name if model_path else "未选择 GGUF"
                return f"llama.cpp / {model}"
            folder = self.local_model_var.get().strip()
            model = Path(folder).name if folder else "未选择模型目录"
            return f"Hugging Face 本地模型 / {model}"
        provider = API_PROVIDERS[self._provider_key()]
        model = self._api_model() or "未选择模型"
        return f"{provider.label} / {model}"

    def _default_endpoint(self, provider_key: str) -> str:
        provider = API_PROVIDERS[provider_key]
        if provider_key == DEFAULT_PROVIDER_KEY:
            return MODELS[self._model_key()].chat_url_for()
        if provider.chat_url:
            return provider.chat_url
        return provider.endpoint_suggestions[0] if provider.endpoint_suggestions else ""

    def _endpoint_changed(self, _event=None) -> None:
        endpoint = self.custom_endpoint_var.get().strip()
        if endpoint:
            self.api_endpoint_by_provider[self._provider_key()] = endpoint

    def _model_key(self) -> str:
        label = self.model_label_var.get()
        return next((key for key, model in MODELS.items() if model.label == label), DEFAULT_MODEL_KEY)

    def _model_changed(self, _event=None) -> None:
        provider_key = self._provider_key()
        if provider_key == DEFAULT_PROVIDER_KEY:
            self.settings["model_key"] = self._model_key()
            if _event is not None:
                endpoint = self._default_endpoint(provider_key)
                self.custom_endpoint_var.set(endpoint)
                self.api_endpoint_by_provider[provider_key] = endpoint
            billing = MODELS[self._model_key()].billing_label()
        else:
            self.api_model_by_provider[provider_key] = self.model_label_var.get().strip()
            billing = API_PROVIDERS[provider_key].billing
        if self.backend_var.get() == "api":
            self.billing_var.set(billing)
        self._update_platform_status()

    def _provider_changed(self, _event=None) -> None:
        if self._active_provider_key:
            current_model = self.model_label_var.get().strip()
            current_endpoint = self.custom_endpoint_var.get().strip()
            if self._active_provider_key != DEFAULT_PROVIDER_KEY and current_model:
                self.api_model_by_provider[self._active_provider_key] = current_model
            if current_endpoint:
                self.api_endpoint_by_provider[self._active_provider_key] = current_endpoint
        provider_key = self._provider_key()
        provider = API_PROVIDERS[provider_key]
        self._active_provider_key = provider_key
        if provider_key == DEFAULT_PROVIDER_KEY:
            model = MODELS.get(
                self.settings.get("model_key"), MODELS[DEFAULT_MODEL_KEY]
            )
            self.model_box.configure(
                values=[value.label for value in MODELS.values()], state="readonly"
            )
            self.model_label_var.set(model.label)
        else:
            model = self.api_model_by_provider.get(provider_key) or provider.default_model
            self.model_box.configure(
                values=provider.model_suggestions,
                state=tk.NORMAL if provider.allows_custom_endpoint else "readonly",
            )
            self.model_label_var.set(model)
        endpoint = (
            self.api_endpoint_by_provider.get(provider_key)
            or self._default_endpoint(provider_key)
        )
        self.custom_endpoint_var.set(endpoint)
        if endpoint:
            self.api_endpoint_by_provider[provider_key] = endpoint
        self.endpoint_box.configure(
            values=provider.endpoint_suggestions,
            state=(
                tk.NORMAL
                if provider.allows_custom_endpoint and self.backend_var.get() == "api"
                else (
                    "readonly" if self.backend_var.get() == "api" else tk.DISABLED
                )
            ),
        )
        show_endpoint = bool(
            provider.allows_custom_endpoint and self.backend_var.get() == "api"
        )
        if show_endpoint:
            if self.endpoint_row.winfo_manager() != "pack":
                self.endpoint_row.pack(
                    fill=tk.X,
                    pady=(0, 8),
                    before=self.local_model_entry.master,
                )
            if self.endpoint_label.winfo_manager() != "pack":
                self.endpoint_label.pack(side=tk.LEFT)
            if self.endpoint_box.winfo_manager() != "pack":
                self.endpoint_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        else:
            self.endpoint_label.pack_forget()
            self.endpoint_box.pack_forget()
            self.endpoint_row.pack_forget()
        self._model_changed()
        supported = [
            name
            for name in (
                "max_tokens", "temperature", "top_p", "top_k",
                "frequency_penalty", "presence_penalty", "seed",
            )
            if name in provider.supported_parameters
        ]
        self.sampling_support_var.set("当前平台发送参数：" + " · ".join(supported))

    def _sampling_values(self) -> dict:
        values = {}
        variables = {
            "max_tokens": self.max_tokens_var,
            "temperature": self.temperature_var,
            "top_p": self.top_p_var,
            "top_k": self.top_k_var,
            "frequency_penalty": self.frequency_penalty_var,
            "presence_penalty": self.presence_penalty_var,
            "seed": self.seed_var,
        }
        for key, variable in variables.items():
            try:
                values[key] = variable.get()
            except tk.TclError:
                values[key] = DEFAULT_SAMPLING[key]
        normalized = normalize_sampling(values)
        self._set_sampling_values(normalized)
        return normalized

    def _set_sampling_values(self, values) -> None:
        sampling = normalize_sampling(values)
        self.max_tokens_var.set(sampling["max_tokens"])
        self.temperature_var.set(sampling["temperature"])
        self.top_p_var.set(sampling["top_p"])
        self.top_k_var.set(sampling["top_k"])
        self.frequency_penalty_var.set(sampling["frequency_penalty"])
        self.presence_penalty_var.set(sampling["presence_penalty"])
        self.seed_var.set("" if sampling["seed"] is None else str(sampling["seed"]))
        if hasattr(self, "sampling_summary_var"):
            self.sampling_summary_var.set(
                f"{self.sampling_preset_var.get()}  ·  "
                f"{sampling['max_tokens']} tokens  ·  T{sampling['temperature']:g}"
            )

    def _toggle_sampling_panel(self, expanded: bool | None = None) -> None:
        self.sampling_expanded = (
            not self.sampling_expanded if expanded is None else bool(expanded)
        )

    def save_sampling_settings(self) -> None:
        self._set_sampling_values(self._sampling_values())
        self._save_workspace_settings()
        self.log("采样参数已保存")
        messagebox.showinfo(
            "参数已保存",
            "采样参数已保存，后续反推将使用当前设置。",
            parent=self.root,
        )

    def randomize_sampling_seed(self) -> None:
        self.seed_var.set(str(secrets.randbelow(2_147_483_648)))
        self._set_sampling_values(self._sampling_values())

    def apply_sampling_preset(self, _event=None) -> None:
        values = SAMPLING_PRESETS.get(self.sampling_preset_var.get())
        if values is not None:
            self._set_sampling_values(values)

    def reset_sampling(self) -> None:
        self.sampling_preset_var.set("平衡反推")
        self._set_sampling_values(DEFAULT_SAMPLING)

    def _update_platform_status(self) -> None:
        mode = "视频反推" if self.media_mode_var.get() == "video" else "图像打标"
        if self.backend_var.get() == "local":
            if self.local_runtime_var.get() == "lmstudio":
                model = self.lmstudio_model_var.get().strip() or "未选择模型"
                platform = f"LM Studio · {model}"
                footer_platform = "◉  LM Studio"
            elif self.local_runtime_var.get() == "llamacpp":
                model_path = self.llama_model_path_var.get().strip()
                model = Path(model_path).name if model_path else "未选择 GGUF"
                platform = f"llama.cpp · {model}"
                footer_platform = "◉  llama.cpp"
            else:
                folder = self.local_model_var.get().strip()
                platform = f"Hugging Face · {Path(folder).name if folder else '未选择目录'}"
                footer_platform = "◉  Hugging Face"
        else:
            provider = API_PROVIDERS[self._provider_key()]
            model = self._api_model() or "未选择模型"
            platform = f"{provider.label} · {model}"
            footer_platform = f"◉  {provider.label}"
        self.platform_status_var.set(f"{mode}  |  {platform}")
        self.footer_platform_var.set(footer_platform)
        self._update_single_settings_summary()

    @staticmethod
    def _set_status_variable(variable, value) -> None:
        try:
            current = variable.get()
        except tk.TclError:
            return
        if current != value:
            variable.set(value)

    def _set_hardware_metric(
        self,
        progress_variable: tk.DoubleVar,
        text_variable: tk.StringVar,
        percent: float | None,
        text: str,
    ) -> None:
        bounded = 0.0 if percent is None else max(0.0, min(100.0, float(percent)))
        self._set_status_variable(progress_variable, bounded)
        self._set_status_variable(text_variable, text)

    def _apply_hardware_sample(self, sample: dict) -> None:
        cpu = sample.get("cpu_percent")
        self._set_hardware_metric(
            self.cpu_percent_var,
            self.cpu_metric_var,
            cpu,
            "--" if cpu is None else f"{float(cpu):.0f}%",
        )
        cpu_temperature = sample.get("cpu_temperature_c")
        self._set_hardware_metric(
            self.cpu_temperature_var,
            self.cpu_temperature_metric_var,
            cpu_temperature,
            "--" if cpu_temperature is None else f"{float(cpu_temperature):.0f}°C",
        )

        memory = sample.get("memory") if isinstance(sample.get("memory"), dict) else {}
        memory_percent = memory.get("percent")
        memory_used = memory.get("used_gb")
        memory_total = memory.get("total_gb")
        memory_text = "--"
        if memory_used is not None and memory_total is not None:
            memory_text = f"{float(memory_used):.1f}/{float(memory_total):.0f}G"
        self._set_hardware_metric(
            self.memory_percent_var,
            self.memory_metric_var,
            memory_percent,
            memory_text,
        )

        gpu = sample.get("gpu") if isinstance(sample.get("gpu"), dict) else {}
        gpu_percent = gpu.get("percent")
        self._set_hardware_metric(
            self.gpu_percent_var,
            self.gpu_metric_var,
            gpu_percent,
            "--" if gpu_percent is None else f"{float(gpu_percent):.0f}%",
        )
        vram_used = gpu.get("memory_used_mb")
        vram_total = gpu.get("memory_total_mb")
        vram_percent = None
        vram_text = "--"
        if vram_used is not None and vram_total:
            vram_percent = float(vram_used) / float(vram_total) * 100.0
            vram_text = f"{float(vram_used) / 1024:.1f}/{float(vram_total) / 1024:.0f}G"
        self._set_hardware_metric(
            self.vram_percent_var,
            self.vram_metric_var,
            vram_percent,
            vram_text,
        )
        gpu_temperature = gpu.get("temperature_c")
        self._set_hardware_metric(
            self.gpu_temperature_var,
            self.gpu_temperature_metric_var,
            gpu_temperature,
            "--" if gpu_temperature is None else f"{float(gpu_temperature):.0f}°C",
        )

    def _set_nav_active(self, key: str) -> None:
        for name, button in self.nav_buttons.items():
            button.configure(style="NavActive.TButton" if name == key else "Nav.TButton")

    def select_workflow(self, mode: str) -> None:
        if mode not in {"image", "video"}:
            return
        if self.runner and self.runner.running:
            return
        self.media_mode_var.set(mode)
        self.show_workspace()
        self._media_mode_changed(scan=True)

    def _media_mode_changed(self, scan: bool = False) -> None:
        mode = self.media_mode_var.get()
        is_video = mode == "video"
        self.workflow_mode_var.set("视频反推" if is_video else "图像打标")
        self.start_button.configure(text="开始视频反推" if is_video else "开始图像打标")
        self._set_nav_active("video" if is_video else "image")
        self._update_platform_status()
        if (
            scan
            and Path(self.folder_var.get().strip()).is_dir()
            and not (self.controller_thread and self.controller_thread.is_alive())
        ):
            self.scan_project()

    def show_sampling_panel(self) -> None:
        self.show_workspace()
        self._set_nav_active("video" if self.media_mode_var.get() == "video" else "image")
        self.root.after_idle(self.sampling_preset_box.focus_set)

    def _backend_changed(self) -> None:
        is_local = self.backend_var.get() == "local"
        is_huggingface = self.local_runtime_var.get() == "huggingface"
        is_lmstudio = is_local and self.local_runtime_var.get() == "lmstudio"
        is_llamacpp = is_local and self.local_runtime_var.get() == "llamacpp"
        self.provider_box.configure(state=tk.DISABLED if is_local else "readonly")
        if is_local:
            self.model_box.configure(state=tk.DISABLED)
        else:
            self.model_box.configure(
                state=(
                    tk.NORMAL
                    if API_PROVIDERS[self._provider_key()].allows_custom_endpoint
                    else "readonly"
                )
            )
        self.concurrency_box.configure(state=tk.NORMAL)
        self.local_model_entry.configure(
            state=tk.NORMAL if is_local and is_huggingface else tk.DISABLED
        )
        self.local_model_button.configure(
            state=tk.NORMAL if is_local and is_huggingface else tk.DISABLED
        )
        if hasattr(self, "local_runtime_box"):
            self.local_runtime_box.configure(
                state="readonly" if is_local else tk.DISABLED
            )
        self.endpoint_box.configure(
            state=(
                tk.NORMAL
                if not is_local and API_PROVIDERS[self._provider_key()].allows_custom_endpoint
                else ("readonly" if not is_local else tk.DISABLED)
            )
        )
        show_endpoint = bool(
            not is_local and API_PROVIDERS[self._provider_key()].allows_custom_endpoint
        )
        if show_endpoint:
            if self.endpoint_row.winfo_manager() != "pack":
                self.endpoint_row.pack(
                    fill=tk.X,
                    pady=(0, 8),
                    before=self.local_model_entry.master,
                )
            if self.endpoint_label.winfo_manager() != "pack":
                self.endpoint_label.pack(side=tk.LEFT)
            if self.endpoint_box.winfo_manager() != "pack":
                self.endpoint_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        else:
            self.endpoint_label.pack_forget()
            self.endpoint_box.pack_forget()
            self.endpoint_row.pack_forget()
        if is_local:
            self.billing_var.set("本地 / 不计费")
        else:
            self._model_changed()
        sampling_state = tk.DISABLED if is_lmstudio else tk.NORMAL
        self.sampling_preset_box.configure(
            state=tk.DISABLED if is_lmstudio else "readonly"
        )
        self.sampling_reset_button.configure(state=sampling_state)
        for control in self.sampling_control_widgets.values():
            control.configure(state=sampling_state)
        if is_lmstudio:
            self.sampling_support_var.set(
                "LM Studio 使用服务端采样设置；工作台仅附加 "
                f"{LMSTUDIO_CAPTION_TOKEN_LIMIT} token 输出安全上限"
            )
        elif is_llamacpp:
            self.sampling_support_var.set(
                "llama.cpp 使用工作台采样参数；模型由本机 llama-server 运行"
            )
        elif is_local:
            self.sampling_support_var.set(
                "Hugging Face 本地模式使用工作台采样参数"
            )
        else:
            provider = API_PROVIDERS[self._provider_key()]
            supported = [
                name
                for name in (
                    "max_tokens", "temperature", "top_p", "top_k",
                    "frequency_penalty", "presence_penalty", "seed",
                )
                if name in provider.supported_parameters
            ]
            self.sampling_support_var.set(
                "当前平台发送参数：" + " · ".join(supported)
            )
        self._update_platform_status()

    def browse_local_model(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Hugging Face 视觉语言模型目录",
            initialdir=self.local_model_var.get() or None,
            parent=self.root,
        )
        if selected:
            self.local_model_var.set(selected)

    def _update_system_prompt_metrics(self) -> None:
        value = self.system_prompt_text.get("1.0", "end-1c")
        line_count = value.count("\n") + 1 if value else 0
        self.system_prompt_metric_var.set(
            f"完整加载 · {len(value):,} 字符 · {line_count:,} 行"
        )

    def _system_prompt_modified(self, _event=None) -> None:
        try:
            if not self.system_prompt_text.edit_modified():
                return
            self.system_prompt_text.edit_modified(False)
        except tk.TclError:
            return
        self._update_system_prompt_metrics()

    def _set_system_prompt(self, value: str) -> None:
        self.system_prompt_text.delete("1.0", tk.END)
        self.system_prompt_text.insert("1.0", value)
        self.system_prompt_text.mark_set(tk.INSERT, "1.0")
        self.system_prompt_text.xview_moveto(0.0)
        self.system_prompt_text.yview_moveto(0.0)
        self.system_prompt_text.edit_modified(False)
        self._update_system_prompt_metrics()

    def _set_single_mode(self, mode: str) -> None:
        if mode not in {"image", "media"}:
            mode = "image"
        self.single_mode_var.set(mode)
        self.single_image_frame.pack_forget()
        self.single_media_frame.pack_forget()
        target = self.single_image_frame if mode == "image" else self.single_media_frame
        target.pack(fill=tk.BOTH, expand=True)
        for key, button in self.single_mode_buttons.items():
            button.configure(style="Primary.TButton" if key == mode else "TButton")
        self.root.after_idle(
            self._render_single_image if mode == "image" else self._render_single_media_preview
        )
        if mode == "media":
            self.root.after_idle(self._render_single_timeline)

    def _update_single_settings_summary(self) -> None:
        if not hasattr(self, "single_platform_summary_var"):
            return
        if self.backend_var.get() == "local":
            if self.local_runtime_var.get() == "lmstudio":
                model = self.lmstudio_model_var.get().strip() or "未选择模型"
                platform = f"LM Studio · {model}"
            elif self.local_runtime_var.get() == "llamacpp":
                model_path = self.llama_model_path_var.get().strip()
                platform = f"llama.cpp · {Path(model_path).name if model_path else '未选择 GGUF'}"
            else:
                folder = self.local_model_var.get().strip()
                platform = f"Hugging Face · {Path(folder).name if folder else '未选择目录'}"
        else:
            provider = API_PROVIDERS[self._provider_key()]
            platform = f"{provider.label} · {self._api_model() or '未选择模型'}"
        preset = self.preset_var.get().strip() or "当前模板"
        language = "中文" if self.output_language_var.get() == "zh" else "English"
        style = "词组标签" if self.caption_style_var.get() == "phrases" else "自然语言"
        thinking = "移除思考标签" if self.remove_thinking_tags_var.get() else "保留思考标签"
        self.single_platform_summary_var.set(platform)
        self.single_prompt_summary_var.set(preset)
        self.single_language_summary_var.set(f"{language} · {style} · {thinking}")

    def _restore_single_controls(self, assume_idle: bool = False) -> None:
        if not hasattr(self, "single_image_run_button"):
            return
        busy = False if assume_idle else self._single_task_running()
        self.single_image_run_button.configure(
            state=tk.NORMAL if self.single_image_path is not None and not busy else tk.DISABLED,
            text="开始单次反推" if not busy else "正在处理…",
        )
        media_ready = bool(self.single_media_path and self.single_media_info)
        state = tk.NORMAL if media_ready and not busy else tk.DISABLED
        self.single_clip_reverse_button.configure(
            state=state,
            text="截取并反推" if not busy else "正在处理…",
        )
        self.single_clip_save_button.configure(state=state)
        self.single_clip_start_scale.configure(state=state)
        self.single_clip_end_scale.configure(state=state)
        has_audio = bool(self.single_media_info.get("audio_streams"))
        has_video = bool(self.single_media_info.get("video_streams"))
        audio_state = tk.NORMAL if media_ready and has_video and has_audio and not busy else tk.DISABLED
        self.single_audio_check.configure(state=audio_state)

    def choose_single_image(self) -> None:
        if self._single_task_running():
            return
        selected = filedialog.askopenfilename(
            title="选择一张图片",
            initialdir=str(self.single_image_path.parent) if self.single_image_path else (self.folder_var.get() or None),
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.webp *.gif *.heic *.heif"),
                ("所有文件", "*.*"),
            ],
            parent=self.root,
        )
        if selected:
            self._load_single_image(Path(selected))

    def _load_single_image(self, path: Path) -> bool:
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            self.single_status_var.set("● 文件不可用，请选择受支持的图片")
            return False
        try:
            source = open_image(path)
            try:
                preview = source.copy()
            finally:
                source.close()
        except (OSError, RuntimeError, SyntaxError, ValueError) as error:
            self.single_status_var.set(f"● 图片读取失败：{error}")
            return False
        if self.single_image_preview_source is not None:
            self.single_image_preview_source.close()
        self.single_image_preview_source = preview
        self.single_image_path = path
        self.single_task_kind = "image"
        size_mb = path.stat().st_size / 1024 / 1024
        self.single_image_file_var.set(
            f"{path.name} · {preview.width}×{preview.height} · {size_mb:.2f} MB"
        )
        self.single_status_var.set("● 图片已就绪，可开始单次反推")
        self.single_metrics_var.set("")
        self.single_progress_var.set(0)
        self.single_progress_text_var.set("等待开始")
        self._render_single_image()
        self._restore_single_controls()
        return True

    def _render_single_image(self) -> None:
        canvas = getattr(self, "single_image_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(120, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        source = self.single_image_preview_source
        if source is None:
            pad = max(26, min(width, height) // 8)
            canvas.create_rectangle(
                pad,
                pad,
                width - pad,
                height - pad,
                outline=COLORS["input_border"],
                width=2,
                dash=(7, 6),
            )
            canvas.create_text(
                width / 2,
                height / 2 - 18,
                text="拖入图片，或点击选择",
                fill=COLORS["text"],
                font=(UI_FONT, 14),
            )
            canvas.create_text(
                width / 2,
                height / 2 + 18,
                text="也可以按 Ctrl+V 粘贴剪贴板图片",
                fill=COLORS["muted"],
                font=(UI_FONT, UI_TEXT_SIZE),
            )
            return
        preview = source.copy()
        preview.thumbnail((max(80, width - 34), max(80, height - 54)), Image.Resampling.LANCZOS)
        self.single_image_preview_photo = ImageTk.PhotoImage(
            preview, master=self.root
        )
        preview.close()
        canvas.create_image(width / 2, height / 2 - 10, image=self.single_image_preview_photo)
        canvas.create_text(
            width / 2,
            height - 16,
            text=self.single_image_file_var.get(),
            fill=COLORS["muted"],
            font=(UI_FONT, UI_SMALL_SIZE),
        )

    def start_single_image_reverse(self) -> None:
        path = self.single_image_path
        if path is None or self._single_task_running():
            return
        self.single_task_kind = "image"
        self.single_task_path = path
        self.single_image_result_text.delete("1.0", tk.END)
        self.single_status_var.set("● 正在准备图片反推…")
        self.single_metrics_var.set("")
        self.single_progress_var.set(5)
        self._restore_single_controls()
        self.start_task(
            [path],
            force=True,
            context="single",
            folder_override=path.parent,
            mode_override="image",
        )

    def _single_result_widget(self) -> tk.Text:
        return (
            self.single_media_result_text
            if self.single_task_kind == "media"
            else self.single_image_result_text
        )

    def save_single_result(self) -> None:
        widget = self._single_result_widget()
        result = widget.get("1.0", "end-1c").strip()
        if not result:
            self.single_status_var.set("● 暂无可保存的反推结果")
            return
        source = self.single_task_path or self.single_image_path or self.single_media_path
        initialfile = f"{source.stem if source else '单次反推结果'}.txt"
        destination = filedialog.asksaveasfilename(
            title="保存单次反推结果",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            initialdir=str(source.parent) if source else None,
            initialfile=initialfile,
            parent=self.root,
        )
        if not destination:
            return
        try:
            Path(destination).write_text(result + "\n", encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self.single_status_var.set(f"● 保存失败：{error}")
            return
        self.single_status_var.set(f"● 已保存：{Path(destination).name}")

    def copy_single_result(self) -> None:
        result = self._single_result_widget().get("1.0", "end-1c").strip()
        if not result:
            self.single_status_var.set("● 暂无可复制的反推结果")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(result)
        self.root.update_idletasks()
        self.single_status_var.set("● 反推结果已复制到剪贴板")

    def _paste_single_clipboard(self, _event=None):
        if (
            not hasattr(self, "single_reverse_frame")
            or self.single_reverse_frame.winfo_manager() != "pack"
            or self.single_mode_var.get() != "image"
            or self._single_task_running()
        ):
            return None
        focus = self.root.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            return None
        try:
            clipboard = ImageGrab.grabclipboard()
        except (OSError, RuntimeError):
            clipboard = None
        if isinstance(clipboard, list):
            for value in clipboard:
                candidate = Path(value)
                if candidate.is_file() and candidate.suffix.casefold() in IMAGE_EXTENSIONS:
                    self._load_single_image(candidate)
                    return "break"
        if not isinstance(clipboard, Image.Image):
            self.single_status_var.set("● 剪贴板中没有可用图片")
            return None
        cache_dir = app_data_dir() / "single-reverse" / "clipboard"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"clipboard-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
        try:
            clipboard.convert("RGB").save(path, format="PNG")
        except (OSError, ValueError) as error:
            self.single_status_var.set(f"● 粘贴图片失败：{error}")
            return None
        self._load_single_image(path)
        return "break"

    def choose_single_media_file(self) -> None:
        if self._single_task_running():
            return
        selected = filedialog.askopenfilename(
            title="选择音频或视频",
            initialdir=str(self.single_media_path.parent) if self.single_media_path else (self.folder_var.get() or None),
            filetypes=[
                ("音视频文件", "*.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                ("所有文件", "*.*"),
            ],
            parent=self.root,
        )
        if selected:
            self._load_single_media(Path(selected))

    def _load_single_media(self, path: Path) -> bool:
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.casefold() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            self.single_status_var.set("● 文件不可用，请选择受支持的音频或视频")
            return False
        self.single_media_path = path
        self.single_task_kind = "media"
        self.single_media_info = {}
        self.single_media_file_var.set(path.name)
        self.single_media_meta_var.set("正在读取媒体信息…")
        self.single_media_probe_var.set("正在读取时长、视频与音频轨道…")
        self.single_status_var.set("● 正在准备片段编辑器…")
        self.single_progress_var.set(4)
        self.single_clip_start_var.set(0.0)
        self.single_clip_end_var.set(1.0)
        self._restore_single_controls()
        self._render_single_media_preview()
        self._render_single_timeline()

        def probe() -> None:
            preview_dir = (
                app_data_dir()
                / "single-reverse"
                / "previews"
                / f"{datetime.now():%Y%m%d-%H%M%S-%f}"
            )
            preview_dir.mkdir(parents=True, exist_ok=True)
            try:
                with MediaWorkerController([path.parent, preview_dir]) as worker:
                    info = worker.probe(path)
                    frames = []
                    if info.get("video_streams"):
                        frames = worker.extract_frames(
                            path,
                            preview_dir,
                            frame_count=6,
                            max_width=720,
                        )
                self._post_event(
                    "single_media_probe_result",
                    {"path": path, "ok": True, "info": info, "frames": frames},
                )
            except (MediaWorkerError, OSError, RuntimeError, ValueError) as error:
                self._post_event(
                    "single_media_probe_result",
                    {"path": path, "ok": False, "error": str(error)},
                )

        threading.Thread(target=probe, daemon=True, name="single-media-probe").start()
        return True

    def _apply_single_media_probe(self, payload: dict) -> None:
        path = Path(payload.get("path") or "")
        if self.single_media_path is None or path != self.single_media_path:
            return
        if not payload.get("ok"):
            error = str(payload.get("error") or "媒体组件不可用")
            self.single_media_probe_var.set(f"读取失败：{error}")
            self.single_media_meta_var.set("无法读取媒体时长与轨道")
            self.single_status_var.set(f"● 音视频加载失败：{error}")
            self.single_progress_var.set(0)
            self._restore_single_controls(assume_idle=True)
            return
        info = dict(payload.get("info") or {})
        duration = max(0.25, float(info.get("duration") or 0.25))
        self.single_media_info = info
        self.single_clip_duration = duration
        self.single_clip_start_scale.configure(to=duration)
        self.single_clip_end_scale.configure(to=duration)
        self.single_clip_start_var.set(0.0)
        self.single_clip_end_var.set(duration)
        has_video = bool(info.get("video_streams"))
        has_audio = bool(info.get("audio_streams"))
        if not has_video and not has_audio:
            self.single_media_info = {}
            self.single_status_var.set("● 文件中没有可用的音视频轨道")
            self._restore_single_controls(assume_idle=True)
            return
        for frame in self.single_media_preview_frames:
            frame.close()
        self.single_media_preview_frames = []
        for frame_path in payload.get("frames") or ():
            try:
                source = open_image(Path(frame_path))
                try:
                    self.single_media_preview_frames.append(source.copy())
                finally:
                    source.close()
            except (OSError, RuntimeError, ValueError):
                continue
        if self.single_media_preview_source is not None:
            self.single_media_preview_source.close()
            self.single_media_preview_source = None
        if self.single_media_preview_frames:
            self.single_media_preview_source = self.single_media_preview_frames[0].copy()
        size_mb = path.stat().st_size / 1024 / 1024
        kind = "视频" if has_video else "音频"
        tracks = "视频轨 + 音频轨" if has_video and has_audio else ("视频轨" if has_video else "音频轨")
        self.single_media_meta_var.set(
            f"{kind} · {self._format_media_time(duration)} · {size_mb:.2f} MB · {tracks}"
        )
        self.single_media_probe_var.set(
            f"总时长 {self._format_media_time(duration)} · 拖动滑块选择需要反推的片段"
        )
        self.single_include_audio_var.set(has_audio)
        self._single_clip_changed("end")
        self.single_status_var.set("● 音视频已就绪，可预览、保存或截取并反推")
        self.single_progress_var.set(0)
        self._restore_single_controls(assume_idle=True)
        self._render_single_media_preview()
        self._render_single_timeline()

    def _render_single_media_preview(self) -> None:
        canvases = [
            getattr(self, "single_media_canvas", None),
            getattr(self, "single_editor_preview_canvas", None),
        ]
        if not hasattr(self, "single_media_preview_photos"):
            self.single_media_preview_photos = {}
        for canvas in canvases:
            if canvas is None or not canvas.winfo_exists():
                continue
            canvas.delete("all")
            width = max(120, canvas.winfo_width())
            height = max(100, canvas.winfo_height())
            source = self.single_media_preview_source
            if source is not None:
                preview = source.copy()
                preview.thumbnail((max(80, width - 24), max(70, height - 36)), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(preview, master=self.root)
                preview.close()
                self.single_media_preview_photos[canvas] = photo
                canvas.create_image(width / 2, height / 2 - 5, image=photo)
            elif self.single_media_path and self.single_media_path.suffix.casefold() in AUDIO_EXTENSIONS:
                center = height / 2
                count = max(24, min(80, width // 9))
                step = width / (count + 2)
                for index in range(count):
                    amplitude = (0.25 + 0.7 * abs(math.sin(index * 0.77))) * (height * 0.28)
                    x = step * (index + 1.5)
                    canvas.create_line(
                        x,
                        center - amplitude,
                        x,
                        center + amplitude,
                        fill=COLORS["accent"],
                        width=max(2, int(step * 0.45)),
                    )
                canvas.create_text(
                    width / 2,
                    height - 18,
                    text="音频波形预览",
                    fill=COLORS["muted"],
                    font=(UI_FONT, UI_SMALL_SIZE),
                )
            else:
                canvas.create_text(
                    width / 2,
                    height / 2 - 12,
                    text="拖入音频或视频，或点击选择",
                    fill=COLORS["text"],
                    font=(UI_FONT, 13),
                )
                canvas.create_text(
                    width / 2,
                    height / 2 + 18,
                    text="支持视频片段与音频片段反推",
                    fill=COLORS["muted"],
                    font=(UI_FONT, UI_TEXT_SIZE),
                )

    def _render_single_timeline(self) -> None:
        canvas = getattr(self, "single_timeline_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(160, canvas.winfo_width())
        height = max(72, canvas.winfo_height())
        if not self.single_media_path:
            canvas.create_text(
                width / 2,
                height / 2,
                text="选择音视频后显示时间轴",
                fill=COLORS["muted"],
                font=(UI_FONT, UI_TEXT_SIZE),
            )
            return
        image = Image.new("RGBA", (width, height), COLORS["input_readonly"])
        draw = ImageDraw.Draw(image, "RGBA")
        frames = self.single_media_preview_frames
        if frames:
            segment = max(1, math.ceil(width / len(frames)))
            for index, frame in enumerate(frames):
                tile = ImageOps.fit(
                    frame.convert("RGB"),
                    (segment, height),
                    method=Image.Resampling.LANCZOS,
                ).convert("RGBA")
                image.alpha_composite(tile, (index * segment, 0))
                tile.close()
        else:
            center = height / 2
            for x in range(8, width - 8, 7):
                amplitude = (0.2 + 0.75 * abs(math.sin(x * 0.12))) * height * 0.38
                draw.rounded_rectangle(
                    (x, center - amplitude, x + 3, center + amplitude),
                    radius=1,
                    fill=COLORS["accent"] + "cc",
                )
        duration = max(0.25, float(self.single_clip_duration or 0.25))
        start = max(0.0, min(duration, float(self.single_clip_start_var.get())))
        end = max(start, min(duration, float(self.single_clip_end_var.get())))
        start_x = int(width * start / duration)
        end_x = int(width * end / duration)
        draw.rectangle((0, 0, start_x, height), fill=(18, 22, 20, 150))
        draw.rectangle((end_x, 0, width, height), fill=(18, 22, 20, 150))
        draw.rectangle(
            (max(1, start_x), 1, max(start_x + 1, end_x - 1), height - 2),
            outline=COLORS["semantic_yellow"],
            width=3,
        )
        for x in (start_x, end_x):
            draw.rounded_rectangle(
                (max(0, x - 5), 8, min(width - 1, x + 5), height - 8),
                radius=3,
                fill=COLORS["semantic_yellow"],
            )
        self.single_timeline_photo = ImageTk.PhotoImage(image, master=self.root)
        image.close()
        canvas.create_image(0, 0, image=self.single_timeline_photo, anchor=tk.NW)

    def _single_clip_changed(self, changed: str) -> None:
        if self.single_clip_updating:
            return
        self.single_clip_updating = True
        try:
            duration = max(0.25, float(self.single_clip_duration or 0.25))
            start = max(0.0, min(duration, float(self.single_clip_start_var.get())))
            end = max(0.0, min(duration, float(self.single_clip_end_var.get())))
            if changed == "start" and start > end - 0.25:
                end = min(duration, start + 0.25)
                if end - start < 0.25:
                    start = max(0.0, end - 0.25)
            elif changed == "end" and end < start + 0.25:
                start = max(0.0, end - 0.25)
                if end - start < 0.25:
                    end = min(duration, start + 0.25)
            self.single_clip_start_var.set(start)
            self.single_clip_end_var.set(end)
            self.single_clip_start_text_var.set(self._format_media_time(start))
            self.single_clip_end_text_var.set(self._format_media_time(end))
            self.single_clip_duration_text_var.set(
                f"{self._format_media_time(end - start)} / 总长 {self._format_media_time(duration)}"
            )
        finally:
            self.single_clip_updating = False
        self._render_single_timeline()

    def reset_single_media_selection(self) -> None:
        if not self.single_media_info or self._single_task_running():
            return
        self.single_clip_start_var.set(0.0)
        self.single_clip_end_var.set(self.single_clip_duration)
        self._single_clip_changed("end")
        self.single_status_var.set("● 已恢复为完整音视频范围")

    def preview_single_media_selection(self) -> None:
        source = self.single_media_path
        if source is None or not self.single_media_info or self._single_task_running():
            return
        output_dir = app_data_dir() / "single-reverse" / "preview-clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"preview-{datetime.now():%Y%m%d-%H%M%S-%f}.mp4"
        self.start_single_clip_task(
            source,
            float(self.single_clip_start_var.get()),
            float(self.single_clip_end_var.get()),
            include_audio=bool(self.single_include_audio_var.get()),
            action="preview",
            output_path=output,
        )

    def save_single_media_clip(self) -> None:
        source = self.single_media_path
        if source is None or not self.single_media_info or self._single_task_running():
            return
        destination = filedialog.asksaveasfilename(
            title="保存所选片段",
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4")],
            initialdir=str(source.parent),
            initialfile=f"{source.stem}_片段.mp4",
            parent=self.root,
        )
        if not destination:
            return
        self.start_single_clip_task(
            source,
            float(self.single_clip_start_var.get()),
            float(self.single_clip_end_var.get()),
            include_audio=bool(self.single_include_audio_var.get()),
            action="save",
            output_path=Path(destination),
        )

    def start_single_media_reverse(self) -> None:
        source = self.single_media_path
        if source is None or not self.single_media_info or self._single_task_running():
            return
        self.single_task_kind = "media"
        self.single_media_result_text.delete("1.0", tk.END)
        self.single_media_tabs.select(0)
        self.start_single_clip_task(
            source,
            float(self.single_clip_start_var.get()),
            float(self.single_clip_end_var.get()),
            include_audio=bool(self.single_include_audio_var.get()),
            action="reverse",
        )

    def _single_task_running(self) -> bool:
        if self.runner and self.runner.running:
            return True
        with self.media_edit_worker_lock:
            return self.media_edit_worker is not None

    def _register_single_drop_target(self, widget: tk.Misc) -> None:
        if DND_FILES is None or not hasattr(widget, "drop_target_register"):
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._single_media_dropped)
        except (AttributeError, tk.TclError):
            return

    def _single_media_dropped(self, event) -> str:
        try:
            values = self.root.tk.splitlist(str(getattr(event, "data", "")))
        except tk.TclError:
            values = ()
        paths = [
            Path(value).resolve()
            for value in values
            if Path(value).is_file()
            and Path(value).suffix.casefold()
            in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
        ]
        if not paths:
            self.single_status_var.set("● 拖放未识别：请拖入受支持的图片、音频或视频")
            return "break"
        if len(paths) > 1:
            self.single_status_var.set(f"● 单次反推一次处理一个文件，已采用：{paths[0].name}")
        self._accept_single_media(paths[0])
        return "break"

    def _accept_single_media(self, path: Path) -> None:
        if self._single_task_running():
            return
        if path.suffix.casefold() in IMAGE_EXTENSIONS:
            self._set_single_mode("image")
            self._load_single_image(path)
        elif path.suffix.casefold() in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            self._set_single_mode("media")
            self._load_single_media(path)

    @staticmethod
    def _format_media_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        whole = int(seconds)
        hours, remainder = divmod(whole, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{int((seconds - whole) * 10):01d}"

    def _open_media_file(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                webbrowser.open(path.resolve().as_uri())
        except OSError as error:
            messagebox.showerror("打开视频失败", str(error), parent=self.root)

    @staticmethod
    def _new_single_clip_path(source: Path, start: float, end: float) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = source.parent / f"{source.stem}_片段反推_{timestamp}"
        suffix = 2
        while candidate.exists():
            candidate = source.parent / f"{source.stem}_片段反推_{timestamp}-{suffix}"
            suffix += 1
        start_ms = max(0, int(round(start * 1000)))
        end_ms = max(start_ms, int(round(end * 1000)))
        return candidate / f"{source.stem}_片段_{start_ms}-{end_ms}ms.mp4"

    def start_single_clip_task(
        self,
        source: Path,
        start_seconds: float,
        end_seconds: float,
        include_audio: bool = True,
        action: str = "reverse",
        output_path: Path | None = None,
    ) -> None:
        if self._single_task_running():
            return
        if action not in {"preview", "save", "reverse"}:
            raise ValueError(f"不支持的片段操作：{action}")
        source = Path(source).resolve()
        output_path = (
            Path(output_path).resolve()
            if output_path is not None
            else self._new_single_clip_path(source, start_seconds, end_seconds)
        )
        self.media_edit_cancelled.clear()
        self.single_clip_operation = action
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.retry_button.configure(state=tk.DISABLED)
        self.selected_button.configure(state=tk.DISABLED)
        self.batch_button.configure(state=tk.DISABLED)
        self.single_progress_var.set(8)
        action_text = {
            "preview": "正在生成选区预览…",
            "save": "正在保存所选片段…",
            "reverse": "正在截取片段，随后开始反推…",
        }[action]
        self.single_progress_text_var.set(action_text)
        self.single_status_var.set(f"● {action_text}")
        self._restore_single_controls()
        self.log(
            f"单次反推片段处理：{source.name} · "
            f"{self._format_media_time(start_seconds)}–"
            f"{self._format_media_time(end_seconds)}"
        )

        def trim() -> None:
            worker: MediaWorkerController | None = None
            try:
                worker = MediaWorkerController([source.parent, output_path.parent])
                with self.media_edit_worker_lock:
                    self.media_edit_worker = worker
                clip_path = worker.trim_video(
                    source,
                    output_path,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    include_audio=include_audio,
                )
                if self.media_edit_cancelled.is_set():
                    self._post_event("single_clip_cancelled", {"action": action})
                else:
                    self._post_event(
                        "single_clip_ready",
                        {
                            "action": action,
                            "source": source,
                            "clip_path": clip_path,
                            "start": start_seconds,
                            "end": end_seconds,
                            "include_audio": include_audio,
                        },
                    )
            except (MediaWorkerError, OSError, ValueError) as error:
                if self.media_edit_cancelled.is_set():
                    self._post_event("single_clip_cancelled", {"action": action})
                else:
                    self._post_event(
                        "single_clip_error",
                        {"action": action, "source": source, "error": str(error)},
                    )
            finally:
                if worker is not None:
                    worker.close()
                with self.media_edit_worker_lock:
                    if self.media_edit_worker is worker:
                        self.media_edit_worker = None

        self.controller_thread = threading.Thread(
            target=trim, daemon=True, name="video-clip-controller"
        )
        self.controller_thread.start()

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

    def save_preset(self) -> tk.Toplevel | None:
        existing_dialog = getattr(self, "preset_name_dialog", None)
        if existing_dialog is not None:
            try:
                if existing_dialog.winfo_exists():
                    existing_dialog.deiconify()
                    existing_dialog.lift()
                    existing_dialog.qianyi_name_entry.focus_set()
                    return existing_dialog
            except tk.TclError:
                pass

        dialog = tk.Toplevel(self.root)
        self.preset_name_dialog = dialog
        dialog.withdraw()
        dialog.title("保存提示词预设")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.overrideredirect(True)
        dialog.configure(background=COLORS["border"])

        border = tk.Frame(dialog, background=COLORS["border"], padx=1, pady=1)
        border.pack(fill=tk.BOTH, expand=True)
        shell = ttk.Frame(border, style="Surface.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(
            shell, style="Surface.TFrame", padding=(22, 18, 16, 12)
        )
        header.pack(fill=tk.X)
        icon = tk.Label(
            header,
            text="✦",
            width=2,
            height=1,
            background=COLORS["semantic_violet"],
            foreground="#ffffff",
            font=("Segoe UI Symbol", 13, "bold"),
            anchor=tk.CENTER,
        )
        icon.pack(side=tk.LEFT, padx=(0, 12))
        title_copy = ttk.Frame(header, style="Surface.TFrame")
        title_copy.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_label = ttk.Label(
            title_copy, text="保存为提示词预设", style="DialogTitle.TLabel"
        )
        title_label.pack(anchor=tk.W)
        subtitle_label = ttk.Label(
            title_copy,
            text="给当前系统提示词一个清晰名称，之后可以随时复用",
            style="SurfaceMuted.TLabel",
        )
        subtitle_label.pack(anchor=tk.W, pady=(2, 0))

        name_var = tk.StringVar()
        feedback_var = tk.StringVar(value="名称会显示在提示词预设列表中")

        def close_dialog(_event=None) -> None:
            if getattr(self, "preset_name_dialog", None) is dialog:
                self.preset_name_dialog = None
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        close_button = ttk.Button(
            header,
            text="×",
            width=3,
            style="DialogClose.TButton",
            command=close_dialog,
        )
        close_button.pack(side=tk.RIGHT, padx=(12, 0))

        ttk.Separator(shell).pack(fill=tk.X, padx=22)
        body = ttk.Frame(
            shell, style="Surface.TFrame", padding=(22, 16, 22, 10)
        )
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="预设名称", style="DialogField.TLabel").pack(
            anchor=tk.W
        )
        name_entry = ttk.Entry(body, textvariable=name_var, width=46)
        name_entry.pack(fill=tk.X, pady=(7, 7))
        feedback_label = ttk.Label(
            body, textvariable=feedback_var, style="SurfaceMuted.TLabel"
        )
        feedback_label.pack(anchor=tk.W)

        footer = ttk.Frame(
            shell, style="Surface.TFrame", padding=(22, 10, 22, 20)
        )
        footer.pack(fill=tk.X)

        def save_name(_event=None) -> None:
            name = name_var.get().strip()
            prompt = self.system_prompt_text.get("1.0", tk.END).strip()
            if not name:
                feedback_var.set("请输入预设名称")
                feedback_label.configure(style="DialogError.TLabel")
                name_entry.focus_set()
                return
            if not prompt:
                feedback_var.set("当前系统提示词为空，无法保存")
                feedback_label.configure(style="DialogError.TLabel")
                return
            self.settings["prompt_presets"][name] = prompt
            self.settings["selected_preset"] = name
            self.preset_box["values"] = list(self.settings["prompt_presets"])
            self.preset_var.set(name)
            self.settings_store.save(self.settings)
            close_dialog()
            self.log(f"提示词预设已保存：{name}")

        save_button = ttk.Button(
            footer,
            text="保存预设",
            style="Primary.TButton",
            command=save_name,
        )
        save_button.pack(side=tk.RIGHT)
        cancel_button = ttk.Button(
            footer, text="取消", command=close_dialog
        )
        cancel_button.pack(side=tk.RIGHT, padx=(0, 8))

        def refresh_name_state(*_args) -> None:
            name = name_var.get().strip()
            if name and name in self.settings["prompt_presets"]:
                feedback_var.set("同名预设已存在，保存后将更新其内容")
                save_button.configure(text="更新预设")
            else:
                feedback_var.set("名称会显示在提示词预设列表中")
                save_button.configure(text="保存预设")
            feedback_label.configure(style="SurfaceMuted.TLabel")

        name_var.trace_add("write", refresh_name_state)

        drag_state = {"x": 0, "y": 0}

        def begin_drag(event) -> None:
            drag_state["x"] = event.x_root - dialog.winfo_x()
            drag_state["y"] = event.y_root - dialog.winfo_y()

        def drag_dialog(event) -> None:
            x = event.x_root - drag_state["x"]
            y = event.y_root - drag_state["y"]
            dialog.geometry(f"+{x}+{y}")

        for draggable in (header, icon, title_copy, title_label, subtitle_label):
            draggable.bind("<ButtonPress-1>", begin_drag)
            draggable.bind("<B1-Motion>", drag_dialog)

        dialog.qianyi_name_var = name_var
        dialog.qianyi_name_entry = name_entry
        dialog.qianyi_feedback_label = feedback_label
        dialog.qianyi_save_button = save_button
        dialog.qianyi_cancel_button = cancel_button
        dialog.qianyi_shell = shell
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", save_name)
        dialog.geometry("520x285")
        transparent_while_positioning = False
        try:
            dialog.attributes("-alpha", 0.0)
            transparent_while_positioning = True
        except tk.TclError:
            pass
        dialog.deiconify()
        dialog.update_idletasks()
        center_dialog(dialog, self.root)
        if transparent_while_positioning:
            dialog.attributes("-alpha", 1.0)
        dialog.grab_set()
        dialog.lift()
        name_entry.focus_set()
        return dialog

    def delete_preset(self) -> None:
        name = self.preset_var.get()
        if name in DEFAULT_PRESETS:
            messagebox.showinfo("提示", "内置预设不能删除", parent=self.root)
            return
        if not name or name not in self.settings["prompt_presets"]:
            return
        self.settings["prompt_presets"].pop(name, None)
        selected = next(iter(self.settings["prompt_presets"]), "")
        self.settings["selected_preset"] = selected
        self.preset_box["values"] = list(self.settings["prompt_presets"])
        self.preset_var.set(selected)
        if selected:
            self.apply_preset()
        else:
            self._set_system_prompt("")
        self.settings_store.save(self.settings)

    def open_platform_config(self) -> tk.Toplevel:
        self._set_nav_active("platform")
        dialog = tk.Toplevel(self.root)
        dialog.title("平台设置")
        dialog.transient(self.root)
        dialog.resizable(False, True)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        settings_heading = self._semantic_heading(
            frame, "☷", "平台设置", "blue", "运行后端、模型平台与安全凭据"
        )
        settings_heading.grid(row=0, column=0, columnspan=3, sticky=tk.EW)
        ttk.Label(
            frame,
            text="统一设置运行后端、模型与密钥。API Key 仅加密保存在当前 Windows 账户。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(4, 16))

        caption_style_var = tk.StringVar(value=self.caption_style_var.get())
        language_var = tk.StringVar(value=self.output_language_var.get())
        backend_var = tk.StringVar(value=self.backend_var.get())
        local_model_var = tk.StringVar(value=self.local_model_var.get())
        local_runtime_label_var = tk.StringVar(
            value=LOCAL_RUNTIME_LABELS.get(
                self.local_runtime_var.get(), "Hugging Face 本地目录"
            )
        )
        lmstudio_base_url_var = tk.StringVar(
            value=self.lmstudio_base_url_var.get()
        )
        lmstudio_model_var = tk.StringVar(value=self.lmstudio_model_var.get())
        lmstudio_load_profile_label_var = tk.StringVar(
            value=LMSTUDIO_LOAD_PROFILE_LABELS.get(
                self.lmstudio_load_profile_var.get(),
                LMSTUDIO_LOAD_PROFILE_LABELS[LMSTUDIO_LOAD_PROFILE_DEFAULT],
            )
        )
        llama_server_path_var = tk.StringVar(value=self.llama_server_path_var.get())
        llama_model_path_var = tk.StringVar(value=self.llama_model_path_var.get())
        llama_mmproj_path_var = tk.StringVar(value=self.llama_mmproj_path_var.get())
        llama_model_alias_var = tk.StringVar(value=self.llama_model_alias_var.get())
        llama_context_length_var = tk.IntVar(
            value=self.llama_context_length_var.get()
        )
        llama_gpu_layers_var = tk.IntVar(value=self.llama_gpu_layers_var.get())

        backend_heading = self._semantic_heading(
            frame, "◫", "运行后端与模型", "yellow", "API / LOCAL MODEL"
        )
        backend_heading.grid(
            row=3, column=0, columnspan=3, sticky=tk.EW, pady=(16, 10)
        )
        backend_frame = ttk.Frame(frame, style="Surface.TFrame", padding=(12, 10))
        backend_frame.grid(row=4, column=0, columnspan=3, sticky=tk.EW)
        backend_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            backend_frame, text="运行后端", style="Surface.TLabel"
        ).grid(row=0, column=0, sticky=tk.W)
        backend_controls = ttk.Frame(backend_frame, style="Surface.TFrame")
        backend_controls.grid(
            row=0, column=1, columnspan=2, sticky=tk.W, padx=(12, 0)
        )
        ttk.Radiobutton(
            backend_controls, text="外部 API", value="api", variable=backend_var,
            style="Surface.TRadiobutton",
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            backend_controls, text="本地模型", value="local", variable=backend_var,
            style="Surface.TRadiobutton",
        ).pack(side=tk.LEFT, padx=(16, 0))
        local_concurrency_note = ttk.Label(
            backend_controls,
            text="⚠ 并发越高，显存与内存占用越大；建议从 1 开始，确认资源充足后再提高",
            style="SurfaceMuted.TLabel",
        )

        ttk.Label(
            backend_frame, text="本地运行方式", style="Surface.TLabel"
        ).grid(row=1, column=0, sticky=tk.W, pady=(11, 0))
        local_runtime_box = ttk.Combobox(
            backend_frame,
            textvariable=local_runtime_label_var,
            state="readonly",
            values=list(LOCAL_RUNTIME_OPTIONS),
            width=42,
        )
        local_runtime_box.grid(
            row=1, column=1, columnspan=2, sticky=tk.EW,
            padx=(12, 0), pady=(11, 0),
        )

        local_model_label = ttk.Label(
            backend_frame, text="本地模型目录", style="Surface.TLabel"
        )
        local_model_label.grid(row=2, column=0, sticky=tk.W, pady=(11, 0))
        local_model_entry = ttk.Entry(
            backend_frame, textvariable=local_model_var, width=42
        )
        local_model_entry.grid(
            row=2, column=1, sticky=tk.EW, padx=(12, 8), pady=(11, 0)
        )

        def browse_draft_local_model() -> None:
            selected = filedialog.askdirectory(
                title="选择 Hugging Face 视觉语言模型目录",
                initialdir=local_model_var.get() or None,
                parent=dialog,
            )
            if selected:
                local_model_var.set(selected)

        local_model_button = ttk.Button(
            backend_frame, text="选择", command=browse_draft_local_model
        )
        local_model_button.grid(row=2, column=2, sticky=tk.E, pady=(11, 0))

        lmstudio_url_label = ttk.Label(
            backend_frame, text="LM Studio URL", style="Surface.TLabel"
        )
        lmstudio_url_label.grid(row=3, column=0, sticky=tk.W, pady=(11, 0))
        lmstudio_url_entry = ttk.Entry(
            backend_frame, textvariable=lmstudio_base_url_var, width=42
        )
        lmstudio_url_entry.grid(
            row=3, column=1, columnspan=2, sticky=tk.EW,
            padx=(12, 0), pady=(11, 0),
        )

        lmstudio_model_label = ttk.Label(
            backend_frame, text="LM Studio 模型", style="Surface.TLabel"
        )
        lmstudio_model_label.grid(row=4, column=0, sticky=tk.W, pady=(11, 0))
        lmstudio_model_box = ttk.Combobox(
            backend_frame,
            textvariable=lmstudio_model_var,
            state="readonly",
            values=(
                (lmstudio_model_var.get().strip(),)
                if lmstudio_model_var.get().strip()
                else ()
            ),
            width=42,
        )
        lmstudio_model_box.grid(
            row=4, column=1, sticky=tk.EW, padx=(12, 8), pady=(11, 0)
        )
        lmstudio_actions = ttk.Frame(
            backend_frame, style="Surface.TFrame"
        )
        lmstudio_actions.grid(
            row=4, column=2, sticky=tk.E, pady=(11, 0)
        )
        lmstudio_discover_button = ttk.Button(
            lmstudio_actions, text="刷新列表", width=10
        )
        lmstudio_discover_button.pack(side=tk.LEFT)
        lmstudio_load_button = ttk.Button(
            lmstudio_actions, text="加载模型", width=10
        )
        lmstudio_load_button.pack(side=tk.LEFT, padx=(6, 0))
        lmstudio_profile_label = ttk.Label(
            backend_frame, text="加载策略", style="Surface.TLabel"
        )
        lmstudio_profile_label.grid(row=5, column=0, sticky=tk.W, pady=(11, 0))
        lmstudio_profile_box = ttk.Combobox(
            backend_frame,
            textvariable=lmstudio_load_profile_label_var,
            state="readonly",
            values=list(LMSTUDIO_LOAD_PROFILE_OPTIONS),
            width=42,
        )
        lmstudio_profile_box.grid(
            row=5, column=1, columnspan=2, sticky=tk.EW,
            padx=(12, 0), pady=(11, 0),
        )
        lmstudio_state_var = tk.StringVar(
            value="启动 LM Studio 本地服务器后刷新列表；加载策略只管理资源，采样仍由 LM Studio 控制"
        )
        lmstudio_state_label = ttk.Label(
            backend_frame,
            textvariable=lmstudio_state_var,
            style="SurfaceMuted.TLabel",
        )
        lmstudio_state_label.grid(
            row=6, column=1, columnspan=2, sticky=tk.W,
            padx=(12, 0), pady=(6, 0),
        )

        llama_server_label = ttk.Label(
            backend_frame, text="llama-server 程序", style="Surface.TLabel"
        )
        llama_server_label.grid(row=7, column=0, sticky=tk.W, pady=(15, 0))
        llama_server_entry = ttk.Entry(
            backend_frame, textvariable=llama_server_path_var, width=42
        )
        llama_server_entry.grid(
            row=7, column=1, sticky=tk.EW, padx=(12, 8), pady=(15, 0)
        )

        def browse_llama_server() -> None:
            selected = filedialog.askopenfilename(
                title="选择 llama-server.exe",
                initialdir=(
                    str(Path(llama_server_path_var.get()).parent)
                    if llama_server_path_var.get().strip()
                    else None
                ),
                filetypes=[("llama-server", "llama-server.exe"), ("所有文件", "*.*")],
                parent=dialog,
            )
            if selected:
                llama_server_path_var.set(selected)

        llama_server_button = ttk.Button(
            backend_frame, text="选择", command=browse_llama_server
        )
        llama_server_button.grid(row=7, column=2, sticky=tk.E, pady=(15, 0))

        llama_model_label = ttk.Label(
            backend_frame, text="GGUF 主模型", style="Surface.TLabel"
        )
        llama_model_label.grid(row=8, column=0, sticky=tk.W, pady=(11, 0))
        llama_model_entry = ttk.Entry(
            backend_frame, textvariable=llama_model_path_var, width=42
        )
        llama_model_entry.grid(
            row=8, column=1, sticky=tk.EW, padx=(12, 8), pady=(11, 0)
        )

        def browse_llama_model() -> None:
            selected = filedialog.askopenfilename(
                title="选择 GGUF 主模型",
                initialdir=(
                    str(Path(llama_model_path_var.get()).parent)
                    if llama_model_path_var.get().strip()
                    else None
                ),
                filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")],
                parent=dialog,
            )
            if selected:
                llama_model_path_var.set(selected)
                if not llama_model_alias_var.get().strip():
                    llama_model_alias_var.set(Path(selected).stem)
                model_dir = Path(selected).parent
                if not llama_mmproj_path_var.get().strip():
                    candidates = sorted(model_dir.glob("mmproj*.gguf"))
                    if len(candidates) == 1:
                        llama_mmproj_path_var.set(str(candidates[0]))
                if not llama_server_path_var.get().strip():
                    for candidate in (
                        model_dir / "llama-server.exe",
                        model_dir.parent / "llama-server.exe",
                    ):
                        if candidate.is_file():
                            llama_server_path_var.set(str(candidate))
                            break

        llama_model_button = ttk.Button(
            backend_frame, text="选择", command=browse_llama_model
        )
        llama_model_button.grid(row=8, column=2, sticky=tk.E, pady=(11, 0))

        llama_mmproj_label = ttk.Label(
            backend_frame, text="mmproj 视觉投影", style="Surface.TLabel"
        )
        llama_mmproj_label.grid(row=9, column=0, sticky=tk.W, pady=(11, 0))
        llama_mmproj_entry = ttk.Entry(
            backend_frame, textvariable=llama_mmproj_path_var, width=42
        )
        llama_mmproj_entry.grid(
            row=9, column=1, sticky=tk.EW, padx=(12, 8), pady=(11, 0)
        )

        def browse_llama_mmproj() -> None:
            selected = filedialog.askopenfilename(
                title="选择 mmproj 视觉投影文件",
                initialdir=(
                    str(Path(llama_mmproj_path_var.get()).parent)
                    if llama_mmproj_path_var.get().strip()
                    else None
                ),
                filetypes=[("GGUF 投影文件", "*.gguf"), ("所有文件", "*.*")],
                parent=dialog,
            )
            if selected:
                llama_mmproj_path_var.set(selected)

        llama_mmproj_button = ttk.Button(
            backend_frame, text="选择", command=browse_llama_mmproj
        )
        llama_mmproj_button.grid(row=9, column=2, sticky=tk.E, pady=(11, 0))

        llama_alias_label = ttk.Label(
            backend_frame, text="模型别名", style="Surface.TLabel"
        )
        llama_alias_label.grid(row=10, column=0, sticky=tk.W, pady=(11, 0))
        llama_alias_entry = ttk.Entry(
            backend_frame, textvariable=llama_model_alias_var, width=42
        )
        llama_alias_entry.grid(
            row=10, column=1, columnspan=2, sticky=tk.EW,
            padx=(12, 0), pady=(11, 0),
        )

        llama_runtime_options = ttk.Frame(backend_frame, style="Surface.TFrame")
        llama_runtime_options.grid(
            row=11, column=1, columnspan=2, sticky=tk.W,
            padx=(12, 0), pady=(11, 0),
        )
        llama_runtime_label = ttk.Label(
            backend_frame, text="运行参数", style="Surface.TLabel"
        )
        llama_runtime_label.grid(row=11, column=0, sticky=tk.W, pady=(11, 0))
        ttk.Label(llama_runtime_options, text="上下文", style="SurfaceMuted.TLabel").pack(
            side=tk.LEFT
        )
        llama_context_box = ttk.Spinbox(
            llama_runtime_options,
            from_=512,
            to=131072,
            increment=512,
            textvariable=llama_context_length_var,
            width=9,
        )
        llama_context_box.pack(side=tk.LEFT, padx=(6, 18))
        ttk.Label(llama_runtime_options, text="GPU 层", style="SurfaceMuted.TLabel").pack(
            side=tk.LEFT
        )
        llama_gpu_box = ttk.Spinbox(
            llama_runtime_options,
            from_=-1,
            to=999,
            increment=1,
            textvariable=llama_gpu_layers_var,
            width=7,
        )
        llama_gpu_box.pack(side=tk.LEFT, padx=(6, 0))
        llama_state_var = tk.StringVar(
            value="原生 GGUF 模式：程序会按任务启动并关闭 llama-server；主模型与 mmproj 必须匹配"
        )
        llama_state_label = ttk.Label(
            backend_frame,
            textvariable=llama_state_var,
            style="SurfaceMuted.TLabel",
            wraplength=560,
            justify=tk.LEFT,
        )
        llama_state_label.grid(
            row=12, column=1, columnspan=2, sticky=tk.W,
            padx=(12, 0), pady=(6, 0),
        )
        llama_widgets = [
            llama_server_label, llama_server_entry, llama_server_button,
            llama_model_label, llama_model_entry, llama_model_button,
            llama_mmproj_label, llama_mmproj_entry, llama_mmproj_button,
            llama_runtime_label, llama_runtime_options,
            llama_alias_label, llama_alias_entry, llama_state_label,
        ]
        huggingface_widgets = [
            local_model_label, local_model_entry, local_model_button,
        ]
        lmstudio_widgets = [
            lmstudio_url_label, lmstudio_url_entry,
            lmstudio_model_label, lmstudio_model_box, lmstudio_actions,
            lmstudio_profile_label, lmstudio_profile_box, lmstudio_state_label,
        ]
        for widget in (*huggingface_widgets, *lmstudio_widgets, *llama_widgets):
            widget.grid_remove()

        ttk.Separator(frame).grid(
            row=5, column=0, columnspan=3, sticky=tk.EW, pady=(14, 0)
        )
        api_heading = self._semantic_heading(
            frame, "⌁", "API 平台与模型", "violet", "PROVIDER CONNECTION"
        )
        enable_mtp_var = tk.BooleanVar(value=bool(self.enable_mtp_var.get()))
        remove_thinking_tags_var = tk.BooleanVar(
            value=bool(self.remove_thinking_tags_var.get())
        )
        inference_switches = ttk.Frame(api_heading)
        inference_switches.pack(
            side=tk.RIGHT,
            padx=(18, 0),
            before=api_heading.copy_frame,
        )
        mtp_switch = SlideSwitch(
            inference_switches,
            "启用 MTP",
            enable_mtp_var,
        )
        mtp_switch.pack(side=tk.LEFT)
        thinking_switch = SlideSwitch(
            inference_switches,
            "移除思考标签",
            remove_thinking_tags_var,
        )
        thinking_switch.pack(side=tk.LEFT, padx=(18, 0))
        for widget in (mtp_switch, mtp_switch.label, mtp_switch.canvas):
            ToolTip(
                widget,
                "仅本地兼容模型生效；云 API 的 MTP 由服务商推理端管理。",
            )
        for widget in (
            thinking_switch,
            thinking_switch.label,
            thinking_switch.canvas,
        ):
            ToolTip(
                widget,
                "关闭兼容模型的思考输出，并清理最终标注中的思考区块。",
            )
        api_heading.grid(
            row=6, column=0, columnspan=3, sticky=tk.EW, pady=(12, 10)
        )
        current_provider_key = self._provider_key()
        if current_provider_key not in PUBLIC_PROVIDER_KEYS:
            current_provider_key = DEFAULT_PROVIDER_KEY
        provider_var = tk.StringVar(
            value=API_PROVIDERS[current_provider_key].label
        )
        api_frame = ttk.Frame(frame, style="Surface.TFrame", padding=(12, 10))
        api_frame.grid(row=7, column=0, columnspan=3, sticky=tk.EW)
        api_frame.grid_columnconfigure(0, weight=0, minsize=168)
        api_frame.grid_columnconfigure(1, weight=1, minsize=440)
        api_frame.grid_columnconfigure(2, weight=0, minsize=180)

        ttk.Label(
            api_frame, text="API 平台", style="Surface.TLabel"
        ).grid(row=0, column=0, sticky=tk.W)
        provider_host = ttk.Frame(
            api_frame,
            style="Surface.TFrame",
            width=420,
            height=48,
        )
        provider_host.grid(row=0, column=1, sticky=tk.W, padx=(12, 8))
        provider_host.grid_propagate(False)
        provider_button = ttk.Menubutton(
            provider_host,
            textvariable=provider_var,
            image=self.provider_icons.get(current_provider_key, ""),
            compound=tk.LEFT,
            style="Provider.TMenubutton",
        )
        provider_button.place(x=0, y=0, width=420, height=48)
        provider_menu = tk.Menu(
            provider_button,
            tearoff=False,
            background=COLORS["input_bg"],
            foreground=COLORS["text"],
            activebackground=COLORS["selection"],
            activeforeground=COLORS["text"],
            borderwidth=1,
            relief=tk.FLAT,
            activeborderwidth=0,
            font=(UI_FONT, UI_TEXT_SIZE),
        )
        provider_button.configure(menu=provider_menu)
        self._register_themed_menu(provider_menu, "input")

        portal_button = ttk.Button(api_frame, text="获取 API Key ↗", width=17)
        portal_button.grid(row=0, column=2, sticky=tk.E)

        ttk.Label(
            api_frame, text="模型", style="Surface.TLabel"
        ).grid(row=1, column=0, sticky=tk.W, pady=(12, 0))
        model_var = tk.StringVar()
        model_box = ttk.Combobox(
            api_frame, textvariable=model_var, state="readonly", width=44
        )
        model_box.grid(
            row=1, column=1, columnspan=2, sticky=tk.EW, padx=(12, 0), pady=(12, 0)
        )

        route_label = ttk.Label(
            api_frame, text="Base URL", style="Surface.TLabel"
        )
        route_label.grid(row=2, column=0, sticky=tk.W, pady=(12, 0))
        route_var = tk.StringVar()
        route_box = ttk.Combobox(
            api_frame, textvariable=route_var, state="readonly", width=44
        )
        route_box.grid(
            row=2, column=1, columnspan=2, sticky=tk.EW, padx=(12, 0), pady=(12, 0)
        )
        route_note_var = tk.StringVar()
        route_note_label = ttk.Label(
            api_frame,
            textvariable=route_note_var,
            style="SurfaceMuted.TLabel",
            wraplength=560,
            justify=tk.LEFT,
        )
        route_note_label.grid(
            row=3, column=1, columnspan=2, sticky=tk.W, padx=(12, 0), pady=(6, 0)
        )

        ttk.Separator(api_frame).grid(
            row=4, column=0, columnspan=3, sticky=tk.EW, pady=16
        )
        ttk.Label(
            api_frame, text="API Key", style="Surface.TLabel"
        ).grid(row=5, column=0, sticky=tk.W)
        api_key_var = tk.StringVar()
        entry = ttk.Entry(api_frame, textvariable=api_key_var, width=38, show="•")
        entry.grid(
            row=5, column=1, padx=(12, 8), sticky=tk.EW
        )
        connection_test_button = ttk.Button(
            api_frame,
            text="测试连接",
            image=self.provider_icons.get("connection", ""),
            compound=tk.LEFT,
            width=15,
        )
        connection_test_button.grid(row=5, column=2, sticky=tk.E)
        stored_var = tk.StringVar()
        ttk.Label(
            api_frame, textvariable=stored_var, style="SurfaceMuted.TLabel"
        ).grid(
            row=6, column=1, sticky=tk.W, padx=(12, 0), pady=(6, 0)
        )
        connection_state_var = tk.StringVar(value="●  尚未测试")
        connection_state_label = ttk.Label(
            api_frame,
            textvariable=connection_state_var,
            style="Muted.TLabel",
        )
        connection_state_label.grid(row=6, column=2, sticky=tk.E, pady=(6, 0))
        frame.grid_columnconfigure(1, weight=1)

        dialog.qianyi_provider_button = provider_button
        dialog.qianyi_provider_menu = provider_menu
        dialog.qianyi_provider_keys = PUBLIC_PROVIDER_KEYS
        dialog.qianyi_model_box = model_box
        dialog.qianyi_route_label = route_label
        dialog.qianyi_route_box = route_box
        dialog.qianyi_route_note_var = route_note_var
        dialog.qianyi_local_runtime_box = local_runtime_box
        dialog.qianyi_local_model_entry = local_model_entry
        dialog.qianyi_local_concurrency_note = local_concurrency_note
        dialog.qianyi_lmstudio_url_entry = lmstudio_url_entry
        dialog.qianyi_lmstudio_model_box = lmstudio_model_box
        dialog.qianyi_lmstudio_profile_box = lmstudio_profile_box
        dialog.qianyi_lmstudio_discover_button = lmstudio_discover_button
        dialog.qianyi_lmstudio_load_button = lmstudio_load_button
        dialog.qianyi_lmstudio_state_var = lmstudio_state_var
        dialog.qianyi_llama_server_entry = llama_server_entry
        dialog.qianyi_llama_model_entry = llama_model_entry
        dialog.qianyi_llama_mmproj_entry = llama_mmproj_entry
        dialog.qianyi_llama_context_box = llama_context_box
        dialog.qianyi_llama_gpu_box = llama_gpu_box
        dialog.qianyi_llama_state_var = llama_state_var
        dialog.qianyi_api_key_entry = entry
        dialog.qianyi_mtp_switch = mtp_switch
        dialog.qianyi_thinking_switch = thinking_switch

        selected_models = dict(self.api_model_by_provider)
        selected_models[DEFAULT_PROVIDER_KEY] = MODELS[
            self.settings.get("model_key", DEFAULT_MODEL_KEY)
        ].label
        if current_provider_key != DEFAULT_PROVIDER_KEY:
            selected_models[current_provider_key] = self.model_label_var.get()
        selected_endpoints = dict(self.api_endpoint_by_provider)
        current_endpoint = self.custom_endpoint_var.get().strip()
        if current_endpoint:
            selected_endpoints[current_provider_key] = current_endpoint
        state = {
            "provider_key": current_provider_key,
            "placeholder": "",
            "draft_keys": {},
            "selected_models": selected_models,
            "selected_endpoints": selected_endpoints,
            "lmstudio_models": {},
            "lmstudio_busy": False,
            "layout_ready": False,
        }

        def resize_dialog_to_content() -> None:
            """Fit dynamic provider fields without moving the selector unnecessarily."""
            if not state["layout_ready"] or not dialog.winfo_exists():
                return
            dialog.update_idletasks()
            target_width = max(dialog.winfo_width(), dialog.winfo_reqwidth())
            target_height = max(1, dialog.winfo_reqheight())
            dialog.geometry(f"{target_width}x{target_height}")
            dialog.update_idletasks()

        def capture_current() -> None:
            provider_key = state["provider_key"]
            value = api_key_var.get().strip()
            if value != state["placeholder"]:
                state["draft_keys"][provider_key] = value
            model = model_var.get().strip()
            if model:
                state["selected_models"][provider_key] = model
            endpoint = route_var.get().strip()
            if endpoint:
                state["selected_endpoints"][provider_key] = endpoint
            else:
                state["selected_endpoints"].pop(provider_key, None)

        def update_route(_event=None) -> None:
            provider_key = state["provider_key"]
            provider = API_PROVIDERS[provider_key]
            if provider_key == DEFAULT_PROVIDER_KEY:
                model_key = next(
                    (
                        key
                        for key, model in MODELS.items()
                        if model.label == model_var.get()
                    ),
                    DEFAULT_MODEL_KEY,
                )
                model = MODELS[model_key]
                billing = model.billing_label()
                endpoint = model.chat_url_for()
                route_var.set(endpoint)
                state["selected_endpoints"][provider_key] = endpoint
            elif provider_key == "custom":
                billing = provider.billing
                state["selected_endpoints"][provider_key] = route_var.get().strip()
            else:
                billing = provider.billing
                route_var.set(provider.chat_url)
                state["selected_endpoints"][provider_key] = provider.chat_url
            route_note_var.set(
                "请填写 OpenAI 兼容 Base URL 与模型 ID，例如 https://host/v1"
                if provider_key == "custom"
                else f"系统已内置 Base URL · {billing}"
            )

        def update_route_visibility() -> None:
            if state["provider_key"] == "custom":
                route_label.grid()
                route_box.grid()
                route_note_label.grid()
            else:
                route_label.grid_remove()
                route_box.grid_remove()
                route_note_label.grid_remove()
            resize_dialog_to_content()

        def load_provider_key() -> None:
            provider_key = state["provider_key"]
            provider = API_PROVIDERS[provider_key]
            dialog.qianyi_provider_key = provider_key
            provider_var.set(provider.label)
            provider_button.configure(
                image=self.provider_icons.get(provider_key, "")
            )
            connection_state_var.set("●  尚未测试")
            connection_state_label.configure(style="Muted.TLabel")
            connection_test_button.configure(text="测试连接")
            stored_key = self.settings_store.get_api_key(provider_key)
            has_key = bool(stored_key)
            if provider_key in state["draft_keys"]:
                placeholder = ""
                key_value = state["draft_keys"][provider_key]
            else:
                placeholder = "*" * 16 if has_key else ""
                key_value = placeholder
            state["placeholder"] = placeholder
            api_key_var.set(key_value)
            stored_var.set(
                "已使用 Windows DPAPI 安全保存（脱敏显示）"
                if has_key and provider_key not in state["draft_keys"]
                else (
                    "待保存"
                    if key_value
                    else ("可选，未设置" if provider_key == "custom" else "未设置")
                )
            )
            if provider_key == DEFAULT_PROVIDER_KEY:
                values = [model.label for model in MODELS.values()]
                selected = state["selected_models"].get(provider_key)
                if selected not in values:
                    selected = MODELS[
                        self.settings.get("model_key", DEFAULT_MODEL_KEY)
                    ].label
            else:
                values = list(provider.model_suggestions)
                selected = (
                    state["selected_models"].get(provider_key)
                    or provider.default_model
                )
            model_box.configure(values=values)
            model_var.set(selected)
            route_box.configure(values=provider.endpoint_suggestions)
            route_var.set(
                state["selected_endpoints"].get(provider_key)
                or self._default_endpoint(provider_key)
            )
            if provider_key == "custom":
                portal_button.configure(text="自定义接口", state=tk.DISABLED)
            else:
                portal_button.configure(
                    text=f"前往 {API_KEY_PORTALS[provider_key][0]} ↗"
                )
            update_route()
            update_route_visibility()
            update_backend_controls()
            if placeholder:
                entry.selection_range(0, tk.END)

        def choose_provider(provider_key: str) -> None:
            capture_current()
            state["provider_key"] = provider_key
            load_provider_key()

        for provider_key in PUBLIC_PROVIDER_KEYS:
            provider_menu.add_command(
                label=f"  {API_PROVIDERS[provider_key].label}",
                image=self.provider_icons.get(provider_key, ""),
                compound=tk.LEFT,
                command=lambda key=provider_key: choose_provider(key),
            )

        model_box.bind("<<ComboboxSelected>>", update_route)
        route_box.bind("<<ComboboxSelected>>", capture_current)
        route_box.bind("<FocusOut>", capture_current)

        def open_key_portal() -> None:
            provider_key = state["provider_key"]
            portal_url = API_KEY_PORTALS[provider_key][1]
            if not portal_url:
                return
            try:
                opened = webbrowser.open(portal_url, new=2)
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                messagebox.showerror(
                    "无法打开浏览器",
                    f"请在浏览器中访问：\n{portal_url}",
                    parent=dialog,
                )

        portal_button.configure(command=open_key_portal)

        def test_connection() -> None:
            provider_key = state["provider_key"]
            typed_value = api_key_var.get().strip()
            if typed_value and typed_value != state["placeholder"]:
                secret = typed_value
            else:
                secret = self.settings_store.get_api_key(provider_key)
            endpoint = route_var.get().strip()
            if not secret and provider_key != "custom":
                connection_state_var.set("●  请先填写 API Key")
                connection_state_label.configure(style="StatusError.TLabel")
                return
            if provider_key == "custom" and not endpoint:
                connection_state_var.set("●  请先填写 Base URL")
                connection_state_label.configure(style="StatusError.TLabel")
                return
            connection_test_button.configure(state=tk.DISABLED, text="测试中…")
            connection_state_var.set("●  正在连接")
            connection_state_label.configure(style="StatusPending.TLabel")

            def worker() -> None:
                try:
                    result = test_provider_connection(
                        provider_key,
                        secret,
                        api_endpoint=endpoint,
                    )
                    payload = {"ok": True, **result}
                except Exception as error:
                    payload = {"ok": False, "error": str(error)}
                self._post_event(
                    "provider_connection_result",
                    {
                        **payload,
                        "provider_key": provider_key,
                        "dialog": dialog,
                        "button": connection_test_button,
                        "label": connection_state_label,
                        "variable": connection_state_var,
                    },
                )

            threading.Thread(
                target=worker, daemon=True, name=f"provider-test-{provider_key}"
            ).start()

        def lmstudio_selected_info() -> dict:
            selected = lmstudio_model_var.get().strip()
            info = state["lmstudio_models"].get(selected)
            return info if isinstance(info, dict) else {}

        def lmstudio_config_summary(info: dict) -> str:
            instances = list(info.get("loaded_instances") or [])
            configs = info.get("loaded_configs") or {}
            config = (
                configs.get(instances[0], {})
                if instances and isinstance(configs, dict)
                else {}
            )
            if not isinstance(config, dict) or not config:
                return ""
            parts = []
            context = config.get("context_length")
            parallel = config.get("parallel")
            if context:
                parts.append(f"上下文 {context}")
            if parallel:
                parts.append(f"并行 {parallel}")
            if config.get("speculative_draft_mtp") is False:
                parts.append("MTP 关闭")
            return " · ".join(parts)

        def sync_lmstudio_action_button() -> None:
            is_lmstudio = (
                backend_var.get() == "local"
                and LOCAL_RUNTIME_OPTIONS.get(local_runtime_label_var.get())
                == "lmstudio"
            )
            if state["lmstudio_busy"] or not is_lmstudio:
                lmstudio_load_button.configure(state=tk.DISABLED)
                return
            selected = lmstudio_model_var.get().strip()
            loaded_instances = lmstudio_selected_info().get(
                "loaded_instances", []
            )
            lmstudio_load_button.configure(
                text="卸载模型" if loaded_instances else "加载模型",
                state=tk.NORMAL if selected else tk.DISABLED,
            )

        def apply_lmstudio_result(payload: dict) -> None:
            if not dialog.winfo_exists():
                return
            state["lmstudio_busy"] = False
            lmstudio_discover_button.configure(text="刷新列表")
            if payload.get("ok"):
                inventory = list(payload.get("inventory") or [])
                state["lmstudio_models"] = {
                    str(model.get("key") or ""): model
                    for model in inventory
                    if isinstance(model, dict) and model.get("key")
                }
                model_keys = list(state["lmstudio_models"])
                selected = lmstudio_model_var.get().strip()
                if selected not in model_keys:
                    selected = model_keys[0] if model_keys else ""
                    lmstudio_model_var.set(selected)
                lmstudio_model_box.configure(values=model_keys)
                action = str(payload.get("action") or "refresh")
                if action == "load":
                    instance_id = str(payload.get("instance_id") or "")
                    info = state["lmstudio_models"].get(selected)
                    if info is not None and instance_id:
                        instances = list(info.get("loaded_instances") or [])
                        if instance_id not in instances:
                            instances.append(instance_id)
                        info["loaded_instances"] = instances
                    config_summary = lmstudio_config_summary(
                        state["lmstudio_models"].get(selected, {})
                    )
                    lmstudio_state_var.set(
                        f"●  模型已加载 · {selected}"
                        f"{(' · ' + config_summary) if config_summary else ''}"
                    )
                elif action == "unload":
                    lmstudio_state_var.set(
                        f"●  模型已卸载 · {selected}"
                    )
                elif model_keys:
                    loaded_count = sum(
                        bool(model.get("loaded_instances"))
                        for model in state["lmstudio_models"].values()
                    )
                    selected_info = state["lmstudio_models"].get(selected, {})
                    config_summary = lmstudio_config_summary(selected_info)
                    lmstudio_state_var.set(
                        f"●  连接正常 · {len(model_keys)} 个视觉模型"
                        f" · {loaded_count} 个已加载"
                        f"{(' · ' + config_summary) if config_summary else ''}"
                    )
                else:
                    lmstudio_state_var.set(
                        "●  连接正常，但未发现支持图片输入的模型"
                    )
                lmstudio_state_label.configure(
                    style=(
                        "StatusSuccess.TLabel"
                        if model_keys else "StatusPending.TLabel"
                    )
                )
            else:
                error = str(payload.get("error") or "连接失败")
                summary = error.replace("\n", " ")[:54]
                lmstudio_state_var.set(f"●  操作失败 · {summary}")
                lmstudio_state_label.configure(style="StatusError.TLabel")
            update_backend_controls()
            sync_lmstudio_action_button()

        def validate_lmstudio_endpoint() -> str:
            endpoint = lmstudio_base_url_var.get().strip()
            if not endpoint:
                raise ValueError("请先填写 LM Studio Base URL")
            if not endpoint.casefold().startswith(("http://", "https://")):
                raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
            return endpoint

        def read_lmstudio_models() -> None:
            try:
                endpoint = validate_lmstudio_endpoint()
            except ValueError as error:
                lmstudio_state_var.set(f"●  {error}")
                lmstudio_state_label.configure(style="StatusError.TLabel")
                return
            state["lmstudio_busy"] = True
            lmstudio_discover_button.configure(state=tk.DISABLED, text="刷新中…")
            lmstudio_load_button.configure(state=tk.DISABLED)
            lmstudio_state_var.set("●  正在读取 LM Studio 模型列表")
            lmstudio_state_label.configure(style="StatusPending.TLabel")

            def worker() -> None:
                try:
                    inventory = list_lmstudio_models(endpoint)
                    payload = {
                        "ok": True,
                        "action": "refresh",
                        "inventory": inventory,
                    }
                except Exception as error:
                    payload = {"ok": False, "error": str(error)}
                self._post_event(
                    "lmstudio_models_result",
                    {**payload, "dialog": dialog, "apply": apply_lmstudio_result},
                )

            threading.Thread(
                target=worker, daemon=True, name="lmstudio-model-discovery"
            ).start()

        def toggle_lmstudio_model() -> None:
            try:
                endpoint = validate_lmstudio_endpoint()
            except ValueError as error:
                lmstudio_state_var.set(f"●  {error}")
                lmstudio_state_label.configure(style="StatusError.TLabel")
                return
            model_key = lmstudio_model_var.get().strip()
            if not model_key:
                lmstudio_state_var.set("●  请先从下拉框选择模型")
                lmstudio_state_label.configure(style="StatusError.TLabel")
                return
            loaded_instances = list(
                lmstudio_selected_info().get("loaded_instances") or []
            )
            action = "unload" if loaded_instances else "load"
            load_profile = LMSTUDIO_LOAD_PROFILE_OPTIONS.get(
                lmstudio_load_profile_label_var.get(),
                LMSTUDIO_LOAD_PROFILE_DEFAULT,
            )
            state["lmstudio_busy"] = True
            lmstudio_discover_button.configure(state=tk.DISABLED)
            lmstudio_load_button.configure(
                state=tk.DISABLED,
                text="卸载中…" if action == "unload" else "加载中…",
            )
            lmstudio_state_var.set(
                "●  正在卸载模型" if action == "unload" else "●  正在加载模型"
            )
            lmstudio_state_label.configure(style="StatusPending.TLabel")

            def worker() -> None:
                try:
                    instance_id = ""
                    if action == "unload":
                        for loaded_instance in loaded_instances:
                            unload_lmstudio_model(endpoint, loaded_instance)
                    else:
                        result = load_lmstudio_model(
                            endpoint,
                            model_key,
                            load_profile=load_profile,
                        )
                        instance_id = result["instance_id"]
                    inventory = list_lmstudio_models(endpoint)
                    payload = {
                        "ok": True,
                        "action": action,
                        "instance_id": instance_id,
                        "inventory": inventory,
                    }
                except Exception as error:
                    payload = {
                        "ok": False,
                        "action": action,
                        "error": str(error),
                    }
                self._post_event(
                    "lmstudio_models_result",
                    {**payload, "dialog": dialog, "apply": apply_lmstudio_result},
                )

            threading.Thread(
                target=worker,
                daemon=True,
                name=f"lmstudio-model-{action}",
            ).start()

        lmstudio_discover_button.configure(command=read_lmstudio_models)
        lmstudio_load_button.configure(command=toggle_lmstudio_model)
        lmstudio_model_box.bind(
            "<<ComboboxSelected>>", lambda _event: sync_lmstudio_action_button()
        )

        def update_backend_controls() -> None:
            is_local = backend_var.get() == "local"
            local_runtime = LOCAL_RUNTIME_OPTIONS.get(
                local_runtime_label_var.get(), "huggingface"
            )
            is_huggingface = is_local and local_runtime == "huggingface"
            is_lmstudio = is_local and local_runtime == "lmstudio"
            is_llamacpp = is_local and local_runtime == "llamacpp"
            visible_widgets = (
                huggingface_widgets if is_huggingface
                else lmstudio_widgets if is_lmstudio
                else llama_widgets if is_llamacpp
                else []
            )
            for widget in (*huggingface_widgets, *lmstudio_widgets, *llama_widgets):
                if widget in visible_widgets:
                    widget.grid()
                else:
                    widget.grid_remove()
            provider_key = state["provider_key"]
            provider = API_PROVIDERS[provider_key]
            api_state = tk.DISABLED if is_local else tk.NORMAL
            local_runtime_box.configure(
                state="readonly" if is_local else tk.DISABLED
            )
            local_model_entry.configure(
                state=tk.NORMAL if is_huggingface else tk.DISABLED
            )
            local_model_button.configure(
                state=tk.NORMAL if is_huggingface else tk.DISABLED
            )
            lmstudio_url_entry.configure(
                state=tk.NORMAL if is_lmstudio else tk.DISABLED
            )
            lmstudio_model_box.configure(
                state="readonly" if is_lmstudio else tk.DISABLED
            )
            lmstudio_profile_box.configure(
                state="readonly" if is_lmstudio else tk.DISABLED
            )
            lmstudio_discover_button.configure(
                state=(
                    tk.NORMAL
                    if is_lmstudio and not state["lmstudio_busy"]
                    else tk.DISABLED
                )
            )
            llama_state = tk.NORMAL if is_llamacpp else tk.DISABLED
            for widget in (
                llama_server_entry,
                llama_model_entry,
                llama_mmproj_entry,
                llama_alias_entry,
                llama_context_box,
                llama_gpu_box,
            ):
                widget.configure(state=llama_state)
            for button in (
                llama_server_button,
                llama_model_button,
                llama_mmproj_button,
            ):
                button.configure(state=llama_state)
            provider_button.configure(state=api_state)
            if is_local:
                if not local_concurrency_note.winfo_manager():
                    local_concurrency_note.pack(side=tk.LEFT, padx=(14, 0))
            else:
                local_concurrency_note.pack_forget()
            model_box.configure(
                state=(
                    tk.DISABLED
                    if is_local
                    else (tk.NORMAL if provider.allows_custom_endpoint else "readonly")
                )
            )
            route_box.configure(
                state=(
                    tk.DISABLED
                    if is_local
                    else (tk.NORMAL if provider.allows_custom_endpoint else "readonly")
                )
            )
            entry.configure(state=api_state)
            connection_test_button.configure(state=api_state)
            mtp_switch.set_enabled(is_huggingface)
            thinking_switch.set_enabled(True)
            portal_button.configure(
                state=(
                    tk.NORMAL
                    if not is_local and API_KEY_PORTALS[provider_key][1]
                    else tk.DISABLED
                )
            )
            resize_dialog_to_content()
            sync_lmstudio_action_button()

        local_runtime_box.bind(
            "<<ComboboxSelected>>", lambda _event: update_backend_controls()
        )

        for button in backend_controls.winfo_children():
            if isinstance(button, ttk.Radiobutton):
                button.configure(command=update_backend_controls)

        connection_test_button.configure(command=test_connection)
        load_provider_key()

        def clear_key() -> None:
            if messagebox.askyesno("清除 API Key", "确定清除已保存的 API Key？", parent=dialog):
                try:
                    self.settings_store.set_api_key("", state["provider_key"])
                except OSError as error:
                    messagebox.showerror("清除失败", str(error), parent=dialog)
                    return
                api_key_var.set("")
                state["placeholder"] = ""
                state["draft_keys"][state["provider_key"]] = ""
                stored_var.set("未设置")

        def close_dialog() -> None:
            dialog.destroy()
            self._set_nav_active(
                "video" if self.media_mode_var.get() == "video" else "image"
            )

        def save() -> None:
            capture_current()
            try:
                for provider_key, value in state["draft_keys"].items():
                    self.settings_store.set_api_key(value, provider_key)
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=dialog)
                return

            for provider_key, model_value in state["selected_models"].items():
                if provider_key == DEFAULT_PROVIDER_KEY:
                    model_key = next(
                        (
                            key
                            for key, model in MODELS.items()
                            if model.label == model_value
                        ),
                        DEFAULT_MODEL_KEY,
                    )
                    self.settings["model_key"] = model_key
                elif provider_key in PUBLIC_PROVIDER_KEYS and model_value:
                    self.api_model_by_provider[provider_key] = model_value

            self.api_endpoint_by_provider = {
                provider_key: endpoint.strip()
                for provider_key, endpoint in state["selected_endpoints"].items()
                if provider_key in PUBLIC_PROVIDER_KEYS and endpoint.strip()
            }

            provider = API_PROVIDERS[state["provider_key"]]
            self.provider_label_var.set(provider.label)
            self.settings["provider_key"] = provider.key
            self.settings["api_models"] = dict(self.api_model_by_provider)
            self.settings["api_endpoints"] = dict(self.api_endpoint_by_provider)
            self.settings["custom_api_endpoint"] = self.api_endpoint_by_provider.get(
                "custom", ""
            )
            self._active_provider_key = None
            self._provider_changed()
            self.caption_style_var.set(caption_style_var.get())
            self.output_language_var.set(language_var.get())
            self.backend_var.set(backend_var.get())
            self.local_runtime_var.set(
                LOCAL_RUNTIME_OPTIONS.get(
                    local_runtime_label_var.get(), "huggingface"
                )
            )
            self.local_model_var.set(local_model_var.get().strip())
            self.lmstudio_base_url_var.set(
                lmstudio_base_url_var.get().strip()
                or "http://localhost:1234/v1"
            )
            self.lmstudio_model_var.set(lmstudio_model_var.get().strip())
            self.lmstudio_load_profile_var.set(
                LMSTUDIO_LOAD_PROFILE_OPTIONS.get(
                    lmstudio_load_profile_label_var.get(),
                    LMSTUDIO_LOAD_PROFILE_DEFAULT,
                )
            )
            self.llama_server_path_var.set(llama_server_path_var.get().strip())
            self.llama_model_path_var.set(llama_model_path_var.get().strip())
            self.llama_mmproj_path_var.set(llama_mmproj_path_var.get().strip())
            self.llama_model_alias_var.set(llama_model_alias_var.get().strip())
            try:
                self.llama_context_length_var.set(int(llama_context_length_var.get()))
            except (TypeError, ValueError, tk.TclError):
                self.llama_context_length_var.set(LLAMA_CPP_DEFAULT_CONTEXT_LENGTH)
            try:
                self.llama_gpu_layers_var.set(int(llama_gpu_layers_var.get()))
            except (TypeError, ValueError, tk.TclError):
                self.llama_gpu_layers_var.set(LLAMA_CPP_DEFAULT_GPU_LAYERS)
            self.enable_mtp_var.set(bool(enable_mtp_var.get()))
            self.remove_thinking_tags_var.set(
                bool(remove_thinking_tags_var.get())
            )
            self._backend_changed()
            self.settings.update({
                "caption_style": self.caption_style_var.get(),
                "output_language": self.output_language_var.get(),
                "backend": self.backend_var.get(),
                "local_runtime": self.local_runtime_var.get(),
                "local_model_folder": self.local_model_var.get(),
                "lmstudio_base_url": self.lmstudio_base_url_var.get(),
                "lmstudio_model": self.lmstudio_model_var.get(),
                "lmstudio_load_profile": self.lmstudio_load_profile_var.get(),
                "llama_server_path": self.llama_server_path_var.get(),
                "llama_model_path": self.llama_model_path_var.get(),
                "llama_mmproj_path": self.llama_mmproj_path_var.get(),
                "llama_model_alias": self.llama_model_alias_var.get(),
                "llama_context_length": int(self.llama_context_length_var.get()),
                "llama_gpu_layers": int(self.llama_gpu_layers_var.get()),
                "enable_mtp": bool(self.enable_mtp_var.get()),
                "remove_thinking_tags": bool(
                    self.remove_thinking_tags_var.get()
                ),
            })
            self.settings_store.save(self.settings)
            close_dialog()
            self.log(f"平台设置已保存：{self._platform_log_summary()}")

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=3, sticky=tk.E, pady=(18, 0))
        ttk.Button(buttons, text="清除密钥", command=clear_key).pack(side=tk.LEFT)
        ttk.Button(buttons, text="取消", command=close_dialog).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            buttons, text="保存设置", style="Primary.TButton", command=save
        ).pack(side=tk.LEFT, padx=(6, 0))
        # The dialog creates its own controls after the main workbench has
        # already chosen a typography bucket. Apply the same readable input
        # size to these late-created controls before measuring the dialog.
        self._adaptive_typography(force=True)
        center_dialog(dialog, self.root)
        state["layout_ready"] = True
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.grab_set()
        dialog.lift()
        model_box.focus_set()
        return dialog

    def open_settings(self) -> tk.Toplevel:
        return self.open_platform_config()

    def _save_workspace_settings(self) -> None:
        folder = self.folder_var.get().strip()
        recent = [folder] + [value for value in self.settings.get("recent_folders", []) if value != folder] if folder else self.settings.get("recent_folders", [])
        provider_key = self._provider_key()
        if provider_key != DEFAULT_PROVIDER_KEY and self.model_label_var.get().strip():
            self.api_model_by_provider[provider_key] = self.model_label_var.get().strip()
        endpoint = self.custom_endpoint_var.get().strip()
        if endpoint:
            self.api_endpoint_by_provider[provider_key] = endpoint
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
            "local_runtime": self.local_runtime_var.get(),
            "local_model_folder": self.local_model_var.get().strip(),
            "lmstudio_base_url": self.lmstudio_base_url_var.get().strip(),
            "lmstudio_model": self.lmstudio_model_var.get().strip(),
            "lmstudio_load_profile": self.lmstudio_load_profile_var.get(),
            "llama_server_path": self.llama_server_path_var.get().strip(),
            "llama_model_path": self.llama_model_path_var.get().strip(),
            "llama_mmproj_path": self.llama_mmproj_path_var.get().strip(),
            "llama_model_alias": self.llama_model_alias_var.get().strip(),
            "llama_context_length": int(self.llama_context_length_var.get()),
            "llama_gpu_layers": int(self.llama_gpu_layers_var.get()),
            "labeling_focus": FOCUS_OPTIONS.get(
                self.focus_label_var.get(), "subject"
            ),
            "output_language": self.output_language_var.get(),
            "trigger_word": self.trigger_word_var.get().strip(),
            "user_prompt": self.user_prompt_text.get("1.0", tk.END).strip(),
            "auto_check_updates": bool(
                self.settings.get("auto_check_updates", True)
            ),
            "model_key": (
                self._model_key()
                if provider_key == DEFAULT_PROVIDER_KEY
                else self.settings.get("model_key", DEFAULT_MODEL_KEY)
            ),
            "provider_key": provider_key,
            "api_models": dict(self.api_model_by_provider),
            "api_endpoints": dict(self.api_endpoint_by_provider),
            "custom_api_endpoint": self.api_endpoint_by_provider.get("custom", ""),
            "sampling": self._sampling_values(),
            "enable_mtp": bool(self.enable_mtp_var.get()),
            "remove_thinking_tags": bool(
                self.remove_thinking_tags_var.get()
            ),
            "selected_preset": self.preset_var.get(),
            "theme": self.theme_key,
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

    def _validate_task(
        self,
        folder_override: Path | None = None,
        mode_override: str | None = None,
    ) -> tuple[Path, str, str] | None:
        folder = (
            Path(folder_override).resolve()
            if folder_override is not None
            else Path(self.folder_var.get().strip())
        )
        mode = mode_override if mode_override in {"image", "video"} else self.media_mode_var.get()
        system_prompt = self.system_prompt_text.get("1.0", tk.END).strip()
        user_prompt = self.user_prompt_text.get("1.0", tk.END).strip()
        provider_key = self._provider_key()
        provider = API_PROVIDERS[provider_key]
        api_key = self.settings_store.get_api_key(provider_key)
        custom_endpoint = self.custom_endpoint_var.get().strip()
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
            if mode != "image":
                messagebox.showwarning(
                    "本地模型",
                    "本地模型后端当前只支持图片；视频请选择外部 API",
                    parent=self.root,
                )
                return None
            if self.local_runtime_var.get() == "lmstudio":
                endpoint = self.lmstudio_base_url_var.get().strip()
                model_id = self.lmstudio_model_var.get().strip()
                if not endpoint.casefold().startswith(("http://", "https://")):
                    messagebox.showwarning(
                        "LM Studio",
                        "请填写有效的 LM Studio Base URL，例如 http://localhost:1234/v1",
                        parent=self.root,
                    )
                    return None
                if not model_id:
                    messagebox.showwarning(
                        "LM Studio",
                        "请在平台设置中读取并选择模型，或手动填写模型 ID",
                        parent=self.root,
                    )
                    return None
            elif self.local_runtime_var.get() == "llamacpp":
                server_path = Path(self.llama_server_path_var.get().strip())
                model_path = Path(self.llama_model_path_var.get().strip())
                mmproj_path = Path(self.llama_mmproj_path_var.get().strip())
                if not server_path.is_file():
                    messagebox.showwarning(
                        "llama.cpp",
                        "请选择有效的 llama-server.exe",
                        parent=self.root,
                    )
                    return None
                if not model_path.is_file() or model_path.suffix.casefold() != ".gguf":
                    messagebox.showwarning(
                        "llama.cpp",
                        "请选择有效的 GGUF 主模型文件",
                        parent=self.root,
                    )
                    return None
                if not mmproj_path.is_file() or mmproj_path.suffix.casefold() != ".gguf":
                    messagebox.showwarning(
                        "llama.cpp",
                        "请选择与主模型匹配的 mmproj GGUF 文件",
                        parent=self.root,
                    )
                    return None
                try:
                    context_length = int(self.llama_context_length_var.get())
                    gpu_layers = int(self.llama_gpu_layers_var.get())
                except (TypeError, ValueError, tk.TclError):
                    messagebox.showwarning(
                        "llama.cpp",
                        "上下文长度和 GPU 层数必须是数字",
                        parent=self.root,
                    )
                    return None
                if not 512 <= context_length <= 131072:
                    messagebox.showwarning(
                        "llama.cpp",
                        "上下文长度应在 512 到 131072 之间",
                        parent=self.root,
                    )
                    return None
                if not -1 <= gpu_layers <= 999:
                    messagebox.showwarning(
                        "llama.cpp",
                        "GPU 层数应在 -1 到 999 之间",
                        parent=self.root,
                    )
                    return None
            else:
                model_folder = Path(self.local_model_var.get().strip())
                if not model_folder.is_dir() or not (model_folder / "config.json").is_file():
                    messagebox.showwarning(
                        "本地模型",
                        "请选择包含 config.json 的 Hugging Face 视觉语言模型目录",
                        parent=self.root,
                    )
                    return None
            api_key = ""
        elif not self._api_model():
            messagebox.showwarning("提示", "请填写当前平台的模型 ID", parent=self.root)
            return None
        elif not custom_endpoint:
            messagebox.showwarning(
                "平台设置异常",
                "请在平台设置中重新选择服务商和模型",
                parent=self.root,
            )
            return None
        elif not custom_endpoint.casefold().startswith(
            ("http://", "https://")
        ):
            messagebox.showwarning(
                "提示", "模型接口地址必须以 http:// 或 https:// 开头", parent=self.root
            )
            return None
        elif mode == "video" and not provider.supports_video:
            messagebox.showwarning(
                "视频反推",
                f"{provider.label} 当前未启用视频输入，请切换火山引擎或兼容视频的平台",
                parent=self.root,
            )
            return None
        elif not api_key and not provider.allows_custom_endpoint:
            messagebox.showwarning(
                    "提示",
                    f"请先在平台设置中填写 {provider.label} API Key",
                    parent=self.root,
                )
            return None
        return folder, prompt, api_key

    def start_task(
        self,
        only_paths: list[Path] | None = None,
        force: bool = False,
        context: str = "batch",
        folder_override: Path | None = None,
        mode_override: str | None = None,
    ) -> None:
        if self.runner and self.runner.running:
            return
        context = "single" if context == "single" else "batch"
        validated = self._validate_task(folder_override, mode_override)
        if not validated:
            if context == "single":
                self._restore_single_controls(assume_idle=True)
            return
        folder, prompt, api_key = validated
        try:
            concurrency = max(
                1, min(MAX_CONCURRENCY, int(self.concurrency_var.get()))
            )
        except (TypeError, ValueError, tk.TclError):
            concurrency = 3
            self.concurrency_var.set(3)
        mode = mode_override if mode_override in {"image", "video"} else self.media_mode_var.get()
        model_key = self._model_key()
        provider_key = self._provider_key()
        provider = API_PROVIDERS[provider_key]
        api_model = self._api_model()
        api_endpoint = self.custom_endpoint_var.get().strip()
        sampling = self._sampling_values()
        caption_style = self.caption_style_var.get()
        subject_filter = self.subject_filter_var.get().strip()
        backend = self.backend_var.get()
        local_model_folder = self.local_model_var.get().strip()
        local_runtime = self.local_runtime_var.get()
        lmstudio_base_url = self.lmstudio_base_url_var.get().strip()
        lmstudio_model = self.lmstudio_model_var.get().strip()
        llama_server_path = self.llama_server_path_var.get().strip()
        llama_model_path = self.llama_model_path_var.get().strip()
        llama_mmproj_path = self.llama_mmproj_path_var.get().strip()
        llama_model_alias = self.llama_model_alias_var.get().strip()
        llama_context_length = int(
            self.llama_context_length_var.get()
        )
        llama_gpu_layers = int(self.llama_gpu_layers_var.get())
        labeling_focus = FOCUS_OPTIONS.get(self.focus_label_var.get(), "subject")
        output_language = self.output_language_var.get()
        trigger_word = self.trigger_word_var.get().strip()
        enable_mtp = bool(self.enable_mtp_var.get())
        remove_thinking_tags = bool(self.remove_thinking_tags_var.get())
        skip_existing = bool(self.skip_var.get()) and not force
        self._save_workspace_settings()
        self.active_task_context = context

        def emit(kind: str, payload: dict) -> None:
            tagged = dict(payload)
            tagged["_task_context"] = context
            self._post_event(kind, tagged)

        self.runner = BatchRunner(emit)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.retry_button.config(state=tk.DISABLED)
        self.selected_button.config(state=tk.DISABLED)
        self.batch_button.config(state=tk.DISABLED)
        if context == "single":
            for button in self.single_action_buttons:
                button.configure(state=tk.DISABLED)
            self.single_progress_var.set(max(8, float(self.single_progress_var.get())))
            self.single_progress_text_var.set("正在连接模型…")
            self.single_status_var.set("● 正在连接模型并准备输入…")
        else:
            self.progress_var.set(0)
            self._set_stage(2)
            self.progress_text_var.set("准备任务...")
        if backend == "local":
            if local_runtime == "lmstudio":
                profile_label = LMSTUDIO_LOAD_PROFILE_LABELS.get(
                    self.lmstudio_load_profile_var.get(),
                    LMSTUDIO_LOAD_PROFILE_LABELS[LMSTUDIO_LOAD_PROFILE_DEFAULT],
                )
                self.log(
                    f"开始任务：LM Studio / {lmstudio_model} / "
                    f"并发 {concurrency} / {profile_label}"
                )
                self.log(
                    "LM Studio 使用服务端采样设置 / "
                    f"输出安全上限 {LMSTUDIO_CAPTION_TOKEN_LIMIT} tokens / "
                    f"思考{'请求关闭' if remove_thinking_tags else '保留'}"
                )
            else:
                if local_runtime == "llamacpp":
                    self.log(
                        f"开始任务：llama.cpp / {Path(llama_model_path).name} / "
                        f"上下文 {llama_context_length} / GPU 层 {llama_gpu_layers} / "
                        f"并发 {concurrency}"
                    )
                else:
                    self.log(
                        f"开始任务：本地模型 {Path(local_model_folder).name} / "
                        f"并发 {concurrency} / "
                        f"MTP {'请求启用' if enable_mtp else '关闭'}"
                    )
        else:
            self.log(
                f"开始任务：{provider.label} / {api_model} / "
                f"{MODELS[model_key].billing_label() if provider_key == DEFAULT_PROVIDER_KEY else provider.billing}"
            )
            self.log(
                f"采样：max_tokens {sampling['max_tokens']} / "
                f"temperature {sampling['temperature']:.2f} / top_p {sampling['top_p']:.2f}"
            )
        self.log(
            f"打标策略：{self.focus_label_var.get()} / "
            f"{'中文' if output_language == 'zh' else 'English'}"
            f"{(' / 触发词 ' + trigger_word) if trigger_word else ''} / "
            f"思考标签{'移除' if remove_thinking_tags else '保留'}"
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
                local_runtime=local_runtime,
                lmstudio_base_url=lmstudio_base_url,
                lmstudio_model=lmstudio_model,
                llama_server_path=llama_server_path,
                llama_model_path=llama_model_path,
                llama_mmproj_path=llama_mmproj_path,
                llama_model_alias=llama_model_alias,
                llama_context_length=llama_context_length,
                llama_gpu_layers=llama_gpu_layers,
                labeling_focus=labeling_focus,
                output_language=output_language,
                trigger_word=trigger_word,
                provider_key=provider_key,
                api_model=api_model,
                api_endpoint=api_endpoint,
                sampling=sampling,
                only_paths=only_paths,
                video_preflight=bool(self.settings.get("video_preflight", True)),
                enable_mtp=enable_mtp,
                remove_thinking_tags=remove_thinking_tags,
                write_output=context != "single",
            )

        thread_name = "single-caption-controller" if context == "single" else "batch-controller"
        self.controller_thread = threading.Thread(target=run, daemon=True, name=thread_name)
        self.controller_thread.start()

    def stop_task(self) -> None:
        if self.runner:
            self.runner.cancel()
        if self.analysis_token:
            self.analysis_token.cancel()
        self.media_edit_cancelled.set()
        with self.media_edit_worker_lock:
            media_edit_worker = self.media_edit_worker
        if media_edit_worker is not None:
            media_edit_worker.close()
        self.stop_button.config(state=tk.DISABLED)
        self.progress_text_var.set("正在停止...")
        if self.active_task_context == "single":
            self.single_progress_text_var.set("正在停止单次反推…")
            self.single_status_var.set("● 正在停止当前任务…")
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
        self.caption_health_counts = {"missing": 0, "invalid": 0}
        self._sync_selection_widgets()
        self._update_stats()

    def _handle_scan(self, result: ScanResult) -> None:
        self._clear_items()
        missing = {str(path) for path in result.missing_captions}
        invalid = {str(path) for path in result.invalid_captions}
        self.orphan_caption_paths = list(result.orphan_captions)
        for path in result.files:
            key = str(path)
            caption_exists = key not in missing
            caption_usable = caption_exists and key not in invalid
            if path in result.conflicts:
                detail = result.conflicts[path]
                status = "failed"
            elif caption_usable:
                caption = caption_path_for(path).read_text(encoding="utf-8").strip()
                detail = caption
                status = "skipped"
            else:
                detail = (
                    "缺少对应 TXT"
                    if key in missing
                    else "TXT 为空或包含错误信息"
                    if key in invalid
                    else ""
                )
                status = "pending"
            self._set_item(
                path,
                status,
                detail,
                caption_exists=caption_exists,
                caption_usable=caption_usable,
            )
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

    def _set_item(
        self,
        path: Path,
        status: str,
        detail: str,
        elapsed_seconds: float | None = None,
        caption_exists: bool | None = None,
        caption_usable: bool | None = None,
    ) -> None:
        key = str(path)
        old = self.items.get(key)
        old_visible = self._visible(old) if old is not None else None
        if old:
            old_status = old["status"]
            self.counts[old_status] = max(0, self.counts[old_status] - 1)
            self._adjust_caption_health(old, -1)
        else:
            self.counts["total"] += 1
        if caption_exists is None:
            if status == "success":
                caption_exists = True
            elif old is not None and status in {"running", "failed", "cancelled"}:
                caption_exists = bool(old.get("caption_exists", False))
            else:
                caption_exists = caption_path_for(path).is_file()
        if caption_usable is None:
            if status == "success":
                caption_usable = True
            elif old is not None and status in {"running", "failed", "cancelled"}:
                caption_usable = bool(old.get("caption_usable", False))
            else:
                caption_usable = has_usable_caption(path)
        self.items[key] = {
            "path": path,
            "status": status,
            "detail": detail,
            "caption_exists": caption_exists,
            "caption_usable": caption_usable,
        }
        self._adjust_caption_health(self.items[key], 1)
        self.counts[status] += 1
        self._refresh_selected_result(
            path, status, detail, elapsed_seconds=elapsed_seconds
        )
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

    def _adjust_caption_health(self, item: dict, delta: int) -> None:
        if item.get("is_orphan", False):
            return
        if not item.get("caption_exists", False):
            self.caption_health_counts["missing"] = max(
                0, self.caption_health_counts["missing"] + delta
            )
        elif not item.get("caption_usable", False):
            self.caption_health_counts["invalid"] = max(
                0, self.caption_health_counts["invalid"] + delta
            )

    def _refresh_selected_result(
        self,
        path: Path,
        status: str,
        detail: str,
        elapsed_seconds: float | None = None,
    ) -> None:
        """Keep the inspector synchronized when a selected item is relabeled."""
        if self.selected_paths != {str(path)}:
            return
        self._update_selected_item_header(path, status)
        if status == "running":
            self._set_result_feedback("running")
            return
        if status == "failed":
            self._set_result_feedback("failed", elapsed_seconds)
            return
        if status == "cancelled":
            self._set_result_feedback("cancelled", elapsed_seconds)
            return
        if status != "success":
            return
        caption = detail.strip()
        caption_path = caption_path_for(path)
        if caption_path.is_file():
            try:
                caption = caption_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                pass
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", caption)
        self.result_text.mark_set(tk.INSERT, "1.0")
        self.result_text.see("1.0")
        self.save_result_button.configure(
            state=tk.NORMAL if caption else tk.DISABLED
        )
        self._set_result_feedback("success", elapsed_seconds)

    def _update_selected_item_header(self, path: Path, status: str) -> None:
        try:
            size_mb = path.stat().st_size / 1024 / 1024
            self.selected_item_var.set(
                f"{path.name}  ·  {size_mb:.2f} MB  ·  {STATUS_TEXT[status]}"
            )
        except OSError:
            self.selected_item_var.set(
                f"{path.name}  ·  {STATUS_TEXT[status]}"
            )

    def _set_result_feedback(
        self, state: str, elapsed_seconds: float | None = None
    ) -> None:
        if self._result_feedback_after_id is not None:
            try:
                self.root.after_cancel(self._result_feedback_after_id)
            except tk.TclError:
                pass
            self._result_feedback_after_id = None
        elapsed = (
            f" · {float(elapsed_seconds):.1f} 秒"
            if elapsed_seconds is not None
            else ""
        )
        messages = {
            "idle": ("● 等待选择素材", "SurfaceMuted.TLabel", "标注结果"),
            "empty": ("● 当前素材尚无标注", "SurfaceMuted.TLabel", "标注结果"),
            "loaded": ("● 已载入当前 TXT", "SurfaceMuted.TLabel", "标注结果"),
            "multiple": ("● 已选择多项素材", "SurfaceMuted.TLabel", "标注结果"),
            "running": (
                "● 正在生成新标注，完成后将自动覆盖",
                "SurfaceMuted.TLabel",
                "标注结果 · 生成中",
            ),
            "success": (
                f"● 标注结果已更新{elapsed}",
                "StatusSuccess.TLabel",
                "标注结果 · 已更新",
            ),
            "failed": (
                f"● 生成失败，已保留原标注{elapsed}",
                "StatusError.TLabel",
                "标注结果 · 失败",
            ),
            "cancelled": (
                f"● 任务已停止，已保留原标注{elapsed}",
                "SurfaceMuted.TLabel",
                "标注结果 · 已停止",
            ),
        }
        text, style, tab_text = messages.get(state, messages["idle"])
        self.result_state_var.set(text)
        self.result_state_label.configure(style=style)
        self.inspector_tabs.tab(self.result_tab, text=tab_text)
        if state not in {"success", "failed", "cancelled"}:
            return

        def settle_feedback() -> None:
            self._result_feedback_after_id = None
            if self.closing:
                return
            if len(self.selected_paths) == 1:
                self.result_state_var.set("● 已载入当前 TXT")
            else:
                self.result_state_var.set("● 等待选择素材")
            self.result_state_label.configure(style="SurfaceMuted.TLabel")
            self.inspector_tabs.tab(self.result_tab, text="标注结果")

        self._result_feedback_after_id = self.root.after(
            3500, settle_feedback
        )

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
        missing = self.caption_health_counts["missing"]
        invalid = self.caption_health_counts["invalid"]
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
                if payload.get("_task_context") == "single":
                    continue
                if payload.get("scan_generation") != self.scan_generation:
                    continue
                self._handle_scan(payload["result"])
            elif kind == "scan_done":
                if payload.get("scan_generation") != self.scan_generation:
                    continue
                self.progress_text_var.set("扫描完成")
                if self.pending_resume_paths:
                    self.root.after_idle(self._start_pending_resume)
            elif kind == "thumbnail":
                path = payload["path"]
                key = str(path)
                self.thumbnail_pending.discard(key)
                image = ImageTk.PhotoImage(payload["image"], master=self.root)
                self.thumbnail_cache[key] = image
                self.gallery.update_thumbnail(path, image)
            elif kind == "hardware":
                sample = payload.get("sample")
                if isinstance(sample, dict):
                    self._apply_hardware_sample(sample)
            elif kind == "engine":
                detail = str(payload.get("detail") or "媒体引擎状态已更新")
                self.log(detail)
                if payload.get("_task_context") == "single":
                    self.single_status_var.set(f"● {detail}")
                if hasattr(self, "system_maintenance_state_var"):
                    self.system_maintenance_state_var.set(detail)
            elif kind == "single_media_probe_result":
                self._apply_single_media_probe(payload)
            elif kind == "single_clip_ready":
                clip_path = Path(payload["clip_path"])
                action = str(payload.get("action") or "reverse")
                self._restore_idle_controls()
                if action == "preview":
                    self.single_progress_var.set(100)
                    self.single_progress_text_var.set("选区预览已生成")
                    self.single_status_var.set("● 正在使用系统播放器打开选区预览")
                    self.root.after(80, lambda: self._open_media_file(clip_path))
                    self._restore_single_controls(assume_idle=True)
                elif action == "save":
                    self.single_progress_var.set(100)
                    self.single_progress_text_var.set("片段已保存")
                    self.single_status_var.set(f"● 已保存片段：{clip_path.name}")
                    self._restore_single_controls(assume_idle=True)
                else:
                    self.single_task_kind = "media"
                    self.single_task_path = clip_path
                    self.single_progress_var.set(22)
                    self.single_progress_text_var.set("片段已生成，正在启动反推…")
                    self.single_status_var.set("● 片段已生成，正在连接模型…")
                    self.single_media_tabs.select(1)
                    self.log(
                        f"单次片段已生成：{clip_path.name} · "
                        f"{self._format_media_time(float(payload.get('start') or 0))}–"
                        f"{self._format_media_time(float(payload.get('end') or 0))}"
                    )
                    self.start_task(
                        [clip_path],
                        force=True,
                        context="single",
                        folder_override=clip_path.parent,
                        mode_override="video",
                    )
            elif kind == "single_clip_cancelled":
                self._restore_idle_controls()
                self.single_progress_var.set(0)
                self.single_progress_text_var.set("片段处理已取消")
                self.single_status_var.set("● 已取消片段处理")
                self._restore_single_controls(assume_idle=True)
            elif kind == "single_clip_error":
                self._restore_idle_controls()
                error = str(payload.get("error") or "未知错误")
                self.single_progress_var.set(0)
                self.single_progress_text_var.set("片段处理失败")
                self.single_status_var.set(f"● 片段处理失败：{error}")
                self.log(f"单次片段处理失败：{error}")
                self._restore_single_controls(assume_idle=True)
            elif kind == "maintenance_done":
                automatic = bool(payload.get("automatic"))
                output = Path(payload["path"])
                action = "项目状态备份" if payload.get("kind") == "backup" else "诊断包"
                if not automatic:
                    self.maintenance_running = False
                    self.backup_button.configure(state=tk.NORMAL)
                    self.diagnostic_button.configure(state=tk.NORMAL)
                    self.system_maintenance_state_var.set(
                        f"{action}已生成：{output}"
                    )
                    messagebox.showinfo(
                        "运行维护",
                        f"{action}已生成。\n\n{output}",
                        parent=self.root,
                    )
                else:
                    self.log(f"已完成每日项目状态备份：{output}")
            elif kind == "maintenance_error":
                automatic = bool(payload.get("automatic"))
                error = str(payload.get("error") or "未知错误")
                if automatic:
                    self.log(f"每日项目状态备份失败：{error}")
                else:
                    self.maintenance_running = False
                    self.backup_button.configure(state=tk.NORMAL)
                    self.diagnostic_button.configure(state=tk.NORMAL)
                    self.system_maintenance_state_var.set("备份或诊断导出失败")
                    messagebox.showerror(
                        "运行维护失败",
                        error,
                        parent=self.root,
                    )
            elif kind == "update_checked":
                self.update_check_running = False
                release = payload["release"]
                self.latest_release = release
                self._apply_release_to_system_info(release)
                if release.get("is_newer"):
                    self._show_update_banner(release)
                elif payload.get("manual"):
                    messagebox.showinfo(
                        "检查更新",
                        f"当前已是最新版本 v{APP_VERSION}。",
                        parent=self.root,
                    )
            elif kind == "update_error":
                self.update_check_running = False
                self.system_update_state_var.set("暂时无法连接 GitHub 检查更新")
                if payload.get("manual"):
                    messagebox.showwarning(
                        "检查更新失败",
                        "暂时无法访问 GitHub Releases。\n\n"
                        f"{payload.get('error', '')}",
                        parent=self.root,
                    )
            elif kind == "update_download_progress":
                received = payload.get("received", 0) / (1024 * 1024)
                total = payload.get("total", 0) / (1024 * 1024)
                percent = payload.get("percent", 0)
                if total:
                    state = f"正在下载更新 {percent}%  ·  {received:.1f}/{total:.1f} MB"
                else:
                    state = f"正在下载更新  ·  {received:.1f} MB"
                self.system_update_state_var.set(state)
                self.system_update_button.configure(text=f"下载中 {percent}%")
            elif kind == "update_download_ready":
                self.system_update_state_var.set("下载校验完成，正在安装并重启…")
                self.system_update_button.configure(text="正在安装…")
                self.root.after(
                    150,
                    lambda data=payload: self._start_update_installer(
                        Path(data["script"]), str(data.get("tag") or "")
                    ),
                )
            elif kind == "update_download_error":
                self.update_download_running = False
                self.system_update_state_var.set("更新下载或校验失败")
                self.system_update_button.configure(state=tk.NORMAL, text="重新下载")
                messagebox.showerror(
                    "更新失败",
                    f"无法完成自动更新。\n\n{payload.get('error', '')}",
                    parent=self.root,
                )
            elif kind == "provider_connection_result":
                dialog = payload.get("dialog")
                button = payload.get("button")
                label = payload.get("label")
                variable = payload.get("variable")
                if not dialog or not dialog.winfo_exists():
                    continue
                button.configure(state=tk.NORMAL, text="测试连接")
                if payload.get("ok"):
                    variable.set(f"●  连接正常 · {payload.get('latency_ms', 0)} ms")
                    label.configure(style="StatusSuccess.TLabel")
                else:
                    error = str(payload.get("error") or "连接失败")
                    summary = error.replace("\n", " ")[:38]
                    variable.set(f"●  连接失败 · {summary}")
                    label.configure(style="StatusError.TLabel")
            elif kind == "lmstudio_models_result":
                dialog = payload.get("dialog")
                apply_result = payload.get("apply")
                if callable(apply_result):
                    if dialog and dialog.winfo_exists():
                        apply_result(payload)
                    continue
                button = payload.get("button")
                label = payload.get("label")
                variable = payload.get("variable")
                model_box = payload.get("model_box")
                model_variable = payload.get("model_variable")
                if not dialog or not dialog.winfo_exists():
                    continue
                button.configure(state=tk.NORMAL, text="刷新列表")
                if payload.get("ok"):
                    models = list(payload.get("models") or [])
                    model_box.configure(values=models)
                    if models and not model_variable.get().strip():
                        model_variable.set(models[0])
                    if models:
                        variable.set(f"●  连接正常 · 已读取 {len(models)} 个模型")
                        label.configure(style="StatusSuccess.TLabel")
                    else:
                        variable.set("●  连接正常，但 LM Studio 尚未加载模型")
                        label.configure(style="StatusPending.TLabel")
                else:
                    error = str(payload.get("error") or "连接失败")
                    summary = error.replace("\n", " ")[:48]
                    variable.set(f"●  连接失败 · {summary}")
                    label.configure(style="StatusError.TLabel")
            elif kind == "status":
                if payload.get("_task_context") == "single":
                    self._handle_single_status(payload)
                    continue
                path = payload["path"]
                status = payload["status"]
                detail = payload.get("detail", "")
                elapsed = payload.get("elapsed_seconds")
                character_count = payload.get("character_count")
                speed = payload.get("characters_per_second")
                if status == "success" and character_count is None:
                    character_count = count_output_characters(detail)
                if (
                    status == "success"
                    and speed is None
                    and elapsed is not None
                ):
                    speed = float(character_count or 0) / max(
                        0.001, float(elapsed)
                    )
                self._set_item(
                    path,
                    status,
                    detail,
                    elapsed_seconds=elapsed,
                )
                metrics_text = self._format_generation_metrics(
                    status,
                    elapsed_seconds=elapsed,
                    character_count=character_count,
                    characters_per_second=speed,
                )
                self.log(
                    f"{STATUS_TEXT[status]}：{self._relative_path(path)}"
                    f"{metrics_text}"
                    f"{(' | ' + detail[:300]) if detail else ''}"
                )
            elif kind == "progress":
                completed = payload["completed"]
                total = payload["total"]
                eta = payload["eta"]
                if payload.get("_task_context") == "single":
                    self.single_progress_var.set(completed / total * 100 if total else 100)
                    self.single_progress_text_var.set(
                        f"{completed}/{total} · ETA {self._format_eta(eta)}"
                    )
                    continue
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
                if payload.get("_task_context") == "single":
                    self._handle_single_done(
                        payload["status"], payload["summary"], payload["journal_dir"]
                    )
                else:
                    self._handle_done(payload["status"], payload["summary"], payload["journal_dir"])
        next_poll_ms = 25 if processed >= 120 else 60 if processed else 160
        self.events_after_id = self.root.after(next_poll_ms, self._process_events)

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        return f"{seconds // 60} 分 {seconds % 60} 秒"

    @staticmethod
    def _format_generation_metrics(
        status: str,
        elapsed_seconds: float | None = None,
        character_count: int | None = None,
        characters_per_second: float | None = None,
    ) -> str:
        if status == "running":
            return ""
        parts = []
        if elapsed_seconds is not None:
            parts.append(f"耗时 {float(elapsed_seconds):.1f} 秒")
        if status == "success" and character_count is not None:
            parts.append(f"字数 {int(character_count)}")
        if status == "success" and characters_per_second is not None:
            parts.append(f"速度 {float(characters_per_second):.1f} 字/秒")
        return f" · {' · '.join(parts)}" if parts else ""

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
        self._restore_single_controls()

    def _handle_single_status(self, payload: dict) -> None:
        path = Path(payload["path"])
        status = str(payload.get("status") or "failed")
        detail = str(payload.get("detail") or "")
        elapsed = payload.get("elapsed_seconds")
        character_count = payload.get("character_count")
        speed = payload.get("characters_per_second")
        if status == "success" and character_count is None:
            character_count = count_output_characters(detail)
        if status == "success" and speed is None and elapsed is not None:
            speed = float(character_count or 0) / max(0.001, float(elapsed))
        metrics = self._format_generation_metrics(
            status,
            elapsed_seconds=elapsed,
            character_count=character_count,
            characters_per_second=speed,
        ).lstrip(" ·")
        if status == "running":
            self.single_status_var.set(f"● {detail or '正在请求模型'}")
            self.single_progress_text_var.set(detail or "正在请求模型…")
            self.single_progress_var.set(max(28, float(self.single_progress_var.get())))
        elif status == "success":
            widget = self._single_result_widget()
            widget.delete("1.0", tk.END)
            widget.insert("1.0", detail)
            self.single_status_var.set("● 反推完成，结果可继续编辑")
            self.single_metrics_var.set(metrics)
            self.single_progress_var.set(100)
            self.single_progress_text_var.set("反推完成")
            if self.single_task_kind == "media":
                self.single_media_tabs.select(1)
        elif status == "cancelled":
            self.single_status_var.set("● 单次反推已取消")
            self.single_metrics_var.set(metrics)
        else:
            widget = self._single_result_widget()
            widget.delete("1.0", tk.END)
            widget.insert("1.0", detail)
            self.single_status_var.set("● 反推失败，错误详情已显示在结果区")
            self.single_metrics_var.set(metrics)
            if self.single_task_kind == "media":
                self.single_media_tabs.select(1)
        log_metrics = f" · {metrics}" if metrics else ""
        self.log(
            f"单次反推 {STATUS_TEXT.get(status, status)}：{path.name}"
            f"{log_metrics}{(' | ' + detail[:300]) if detail else ''}"
        )

    def _handle_single_done(
        self,
        status: str,
        summary: BatchSummary,
        journal_dir: Path,
    ) -> None:
        self._restore_idle_controls()
        self.active_task_context = "batch"
        if status == "stopped":
            self.single_progress_text_var.set("单次反推已停止")
            self.single_status_var.set("● 单次反推已停止")
        elif summary.success:
            self.single_progress_var.set(100)
            self.single_progress_text_var.set("反推完成")
        elif summary.failed:
            self.single_progress_var.set(0)
            self.single_progress_text_var.set("反推失败")
        self.log(
            f"单次反推结束：成功 {summary.success}，失败 {summary.failed}，"
            f"耗时 {summary.elapsed_seconds:.1f} 秒；日志 {journal_dir}"
        )
        self._restore_single_controls(assume_idle=True)

    def _handle_done(self, status: str, summary: BatchSummary, journal_dir: Path) -> None:
        self.last_failed_paths = [path for path, _detail in summary.failures]
        self._restore_idle_controls()
        if status == "stopped":
            self.progress_text_var.set("已停止")
        else:
            self.progress_var.set(100)
            self.progress_text_var.set("任务完成")
            self._set_stage(3)
        average_speed = summary.characters / max(
            0.001, summary.elapsed_seconds
        )
        self.log(
            f"任务结束：成功 {summary.success}，跳过 {summary.skipped}，"
            f"失败 {summary.failed}，取消 {summary.cancelled}；"
            f"总耗时 {summary.elapsed_seconds:.1f} 秒，"
            f"总字数 {summary.characters}，"
            f"平均速度 {average_speed:.1f} 字/秒；日志 {journal_dir}"
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

    def _preview_resized(self, _event=None) -> None:
        if self.closing or self._window_suspended or len(self.selected_paths) != 1:
            return
        if _event is not None and (
            int(getattr(_event, "width", 0)) <= 1
            or int(getattr(_event, "height", 0)) <= 1
        ):
            return
        try:
            if str(self.root.state()) in {"iconic", "withdrawn"}:
                return
        except tk.TclError:
            return
        if self._preview_resize_after is not None:
            try:
                self.root.after_cancel(self._preview_resize_after)
            except tk.TclError:
                pass
        self._preview_resize_after = self.root.after(
            120, self._refresh_selected_preview
        )

    def _refresh_selected_preview(self) -> None:
        self._preview_resize_after = None
        if len(self.selected_paths) != 1:
            return
        path = Path(next(iter(self.selected_paths)))
        item = self.items.get(str(path), {})
        if item.get("is_orphan", False) or path.suffix.casefold() in {
            ".mp4", ".mov", ".avi",
        }:
            return
        try:
            stat = path.stat()
            source_key = (str(path), stat.st_mtime_ns, stat.st_size)
            max_width = max(120, self.preview_label.winfo_width() - 24)
            max_height = max(100, self.preview_label.winfo_height() - 24)
            render_key = (*source_key, max_width, max_height)
            if render_key == self._preview_render_key and self.preview_image is not None:
                return
            if source_key != self._preview_source_key or self._preview_source_image is None:
                source = open_image(path)
                try:
                    source_copy = source.copy()
                finally:
                    source.close()
                if self._preview_source_image is not None:
                    self._preview_source_image.close()
                self._preview_source_image = source_copy
                self._preview_source_key = source_key
            preview = self._preview_source_image.copy()
            preview.thumbnail(
                (max_width, max_height), Image.Resampling.LANCZOS
            )
            self.preview_image = ImageTk.PhotoImage(
                preview.copy(), master=self.root
            )
            preview.close()
            self._preview_render_key = render_key
            self.preview_label.config(image=self.preview_image, text="")
        except (OSError, RuntimeError, ValueError) as error:
            self.preview_label.config(image="", text=f"预览失败\n{error}")
            self.preview_image = None
            self._preview_render_key = None

    def _display_selection(self) -> None:
        self.result_text.delete("1.0", tk.END)
        self.save_result_button.config(state=tk.DISABLED)
        paths = [Path(value) for value in sorted(self.selected_paths)]
        if not paths:
            self.preview_label.config(image="", text="选择任务项")
            self.selected_item_var.set("尚未选择素材")
            self._set_result_feedback("idle")
            self.preview_image = None
            return
        if len(paths) > 1:
            self.preview_label.config(image="", text=f"已选择 {len(paths)} 项")
            self.selected_item_var.set(f"已选择 {len(paths)} 项，可使用底部任务栏批量处理")
            self.result_text.insert(
                "1.0", "孤立 TXT 不会进入打标或训练数据导出。"
            )
            self._set_result_feedback("multiple")
            self.preview_image = None
            return
        path = paths[0]
        item = self.items.get(str(path), {})
        try:
            size_mb = path.stat().st_size / 1024 / 1024
            self.selected_item_var.set(
                f"{path.name}  ·  {size_mb:.2f} MB  ·  "
                f"{STATUS_TEXT.get(item.get('status'), '待处理')}"
            )
        except OSError:
            self.selected_item_var.set(path.name)
        if item.get("is_orphan", False):
            self.preview_label.config(
                image="", text=f"孤立 TXT\n{path.name}\n无对应媒体文件"
            )
            try:
                self.result_text.insert("1.0", path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as error:
                self.result_text.insert("1.0", f"读取 TXT 失败：{error}")
            self._set_result_feedback("loaded")
            self.preview_image = None
            return
        caption_path = caption_path_for(path)
        if caption_path.exists():
            try:
                self.result_text.insert("1.0", caption_path.read_text(encoding="utf-8"))
                self.save_result_button.config(state=tk.NORMAL)
                self._set_result_feedback("loaded")
            except (OSError, UnicodeError) as error:
                self.result_text.insert("1.0", f"读取结果失败：{error}")
                self._set_result_feedback("failed")
        else:
            self._set_result_feedback("empty")
        if path.suffix.casefold() in {".mp4", ".mov", ".avi"}:
            size_mb = path.stat().st_size / 1024 / 1024
            self.preview_label.config(image="", text=f"{path.name}\n{size_mb:.1f} MB")
            self.preview_image = None
            return
        self._refresh_selected_preview()

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
        with self.media_edit_worker_lock:
            media_edit_worker = self.media_edit_worker
        active = bool(
            (self.runner and self.runner.running)
            or (self.analysis_token and self.controller_thread and self.controller_thread.is_alive())
            or media_edit_worker is not None
        )
        if active and not self.update_install_pending:
            if not messagebox.askyesno("确认退出", "任务正在运行，确定停止并退出？", parent=self.root):
                return
            if self.runner:
                self.runner.cancel()
            if self.analysis_token:
                self.analysis_token.cancel()
            self.media_edit_cancelled.set()
            if media_edit_worker is not None:
                media_edit_worker.close()
        elif media_edit_worker is not None:
            self.media_edit_cancelled.set()
            media_edit_worker.close()
        self.hardware_monitor.stop()
        try:
            self._save_workspace_settings()
        except (OSError, ValueError, tk.TclError):
            pass
        self.closing = True
        for after_id in (
            self.events_after_id,
            self.sash_after_id,
            self.splash_after_id,
            self.update_after_id,
            self._preview_resize_after,
            self._result_feedback_after_id,
            self._window_resume_after_id,
        ):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._cancel_theme_sync_jobs()
        self._cancel_text_sync_jobs()
        self.events_after_id = None
        self.sash_after_id = None
        self.splash_after_id = None
        self._preview_resize_after = None
        self._result_feedback_after_id = None
        self._window_resume_after_id = None
        if self._preview_source_image is not None:
            self._preview_source_image.close()
            self._preview_source_image = None
        if self.single_image_preview_source is not None:
            self.single_image_preview_source.close()
            self.single_image_preview_source = None
        if self.single_media_preview_source is not None:
            self.single_media_preview_source.close()
            self.single_media_preview_source = None
        for frame in self.single_media_preview_frames:
            frame.close()
        self.single_media_preview_frames.clear()
        self.root.destroy()


def main() -> None:
    if "--qianyi-worker" in sys.argv:
        raise SystemExit(run_worker_cli(sys.argv[1:]))
    enable_dpi_awareness()
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        root.withdraw()
    app = CaptionApp(root, show_splash=not smoke_test)
    if smoke_test:
        if app._compose_launch_background(640, 360).size != (640, 360):
            raise RuntimeError("启动背景无法渲染")
        expected_icons = {
            "project", "image", "video", "single", "platform", "system", "night", "day"
        }
        for theme_key in THEMES:
            if set(app.toolbar_icons.get(theme_key, {})) != expected_icons:
                raise RuntimeError(f"{theme_key} 顶栏图标资源不完整")
        expected_provider_icons = {*PUBLIC_PROVIDER_KEYS, "connection"}
        if set(app.provider_icons) != expected_provider_icons:
            raise RuntimeError("供应商图标资源不完整")
        for theme_key in ("day", "night", "day"):
            app._set_theme(theme_key)
            root.update_idletasks()
            for text_widget in (
                app.result_text,
                app.user_prompt_text,
                app.system_prompt_text,
                app.log_text,
                app.single_image_result_text,
                app.single_media_result_text,
            ):
                if text_widget.cget("background") != THEMES[theme_key]["input_bg"]:
                    raise RuntimeError(f"{theme_key} 文本区主题同步失败")
            for canvas, color_key in (
                (app.single_image_canvas, "media_bg"),
                (app.single_media_canvas, "media_bg"),
                (app.single_editor_preview_canvas, "media_bg"),
                (app.single_timeline_canvas, "input_readonly"),
            ):
                if canvas.cget("background") != THEMES[theme_key][color_key]:
                    raise RuntimeError(f"{theme_key} 单次反推画布主题同步失败")
            if (
                app.system_release_notes.cget("background")
                != THEMES[theme_key]["input_readonly"]
            ):
                raise RuntimeError(f"{theme_key} 只读文本区主题同步失败")
        root.update_idletasks()
        root.update()
        root.destroy()
        print("GUI_SMOKE_OK")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
