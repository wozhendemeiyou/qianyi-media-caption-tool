from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Iterable, Iterator

from PIL import Image, ImageOps
import pillow_heif
import requests


APP_NAME = "Media Caption Tool"
APP_VERSION = "3.3"
CODING_CHAT_URL = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
STANDARD_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
FILES_URL = "https://ark.cn-beijing.volces.com/api/v3/files"
RESPONSES_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
IGNORED_DIRECTORY_NAMES = {
    "$recycle.bin",
    "deliveryoptimization",
    "recovery",
    "system volume information",
    "windowsapps",
    "wpsystem",
}
INVALID_CAPTION_PREFIXES = (
    "error",
    "api error",
    "http error",
    "request failed",
    '{"error"',
    "{'error'",
    "请求失败",
    "调用失败",
    "余额不足",
    "欠费",
)
INVALID_CAPTION_MARKERS = (
    "insufficient balance",
    "account balance is insufficient",
    "余额不足",
    "账户欠费",
)
REQUEST_ID_HEADERS = ("x-request-id", "x-tt-logid", "request-id")
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
VIDEO_CHAT_LIMIT = 20 * 1024 * 1024
VIDEO_UPLOAD_LIMIT = 512 * 1024 * 1024
MAX_CONCURRENCY = 10
HEIF_DECODE_LOCK = threading.Lock()
SEED_2_0_PLAN_END_DATE = date(2026, 8, 8)
SEED_2_0_NOTICE_START_DATE = date(2026, 7, 24)


@dataclass(frozen=True)
class ModelOption:
    key: str
    label: str
    model_id: str
    chat_url: str
    billing: str
    plan_end_date: date | None = None
    post_plan_chat_url: str | None = None
    post_plan_billing: str | None = None

    def chat_url_for(self, today: date | None = None) -> str:
        current = today or date.today()
        if (
            self.plan_end_date is not None
            and current >= self.plan_end_date
            and self.post_plan_chat_url
        ):
            return self.post_plan_chat_url
        return self.chat_url

    def billing_label(self, today: date | None = None) -> str:
        current = today or date.today()
        if (
            self.plan_end_date is not None
            and current >= self.plan_end_date
            and self.post_plan_billing
        ):
            return self.post_plan_billing
        return self.billing


MODELS = {
    "seed-2.1-pro": ModelOption(
        "seed-2.1-pro",
        "豆包 Seed 2.1 Pro",
        "doubao-seed-2-1-pro-260628",
        STANDARD_CHAT_URL,
        "按量计费",
    ),
    "seed-2.1-pro-turbo": ModelOption(
        "seed-2.1-pro-turbo",
        "豆包 Seed 2.1 Pro Turbo",
        "doubao-seed-2-1-turbo-260628",
        CODING_CHAT_URL,
        "Coding Plan",
    ),
    "seed-2.0-pro": ModelOption(
        "seed-2.0-pro",
        "豆包 Seed 2.0 Pro",
        "doubao-seed-2-0-pro-260215",
        CODING_CHAT_URL,
        "Coding Plan（8月8日下线）",
        SEED_2_0_PLAN_END_DATE,
        STANDARD_CHAT_URL,
        "按量计费",
    ),
    "seed-1.6-vision": ModelOption(
        "seed-1.6-vision",
        "豆包 Seed 1.6 Vision",
        "doubao-seed-1-6-251015",
        CODING_CHAT_URL,
        "Coding Plan",
    ),
}
DEFAULT_MODEL_KEY = "seed-2.1-pro"


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(base or Path.home()) / "MediaCaptionTool"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temp.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    )


class SecretStore:
    def get(self) -> str:
        raise NotImplementedError

    def set(self, value: str) -> None:
        raise NotImplementedError


class DpapiSecretStore(SecretStore):
    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "credentials.bin"

    @classmethod
    def _blob_from_bytes(cls, value: bytes):
        buffer = ctypes.create_string_buffer(value)
        blob = cls._Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    @classmethod
    def _protect(cls, value: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is only available on Windows")
        source, source_buffer = cls._blob_from_bytes(value)
        target = cls._Blob()
        result = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), APP_NAME, None, None, None, 0, ctypes.byref(target)
        )
        del source_buffer
        if not result:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(target.pbData)

    @classmethod
    def _unprotect(cls, value: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is only available on Windows")
        source, source_buffer = cls._blob_from_bytes(value)
        target = cls._Blob()
        result = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
        )
        del source_buffer
        if not result:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(target.pbData)

    def get(self) -> str:
        try:
            encrypted = self.path.read_bytes()
            return self._unprotect(encrypted).decode("utf-8")
        except (OSError, UnicodeError):
            return ""

    def set(self, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not value:
            self.path.unlink(missing_ok=True)
            return
        encrypted = self._protect(value.encode("utf-8"))
        temp = self.path.with_suffix(".tmp")
        try:
            temp.write_bytes(encrypted)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)


DEFAULT_SETTINGS = {
    "version": 4,
    "model_key": DEFAULT_MODEL_KEY,
    "last_folder": "",
    "recent_folders": [],
    "concurrency": 3,
    "skip_existing": True,
    "media_mode": "image",
    "caption_style": "natural",
    "view_mode": "gallery",
    "subject_filter": "",
    "backend": "api",
    "local_model_folder": "",
    "labeling_focus": "subject",
    "output_language": "zh",
    "trigger_word": "",
    "selected_preset": "详细自然语言",
    "prompt_presets": {},
    "suppress_seed_2_0_shutdown_notice": False,
}


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Convert persisted numeric settings without letting bad config block startup."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def bounded_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return default


def model_key_from_legacy(value: str) -> str:
    normalized = (value or "").casefold()
    for key, model in MODELS.items():
        if normalized in {key.casefold(), model.label.casefold(), model.model_id.casefold()}:
            return key
    if "2-0-pro" in normalized or "2.0 pro" in normalized:
        return "seed-2.0-pro"
    if "2-1-turbo" in normalized or "2.1 pro turbo" in normalized:
        return "seed-2.1-pro-turbo"
    if "1-6" in normalized or "1.6" in normalized:
        return "seed-1.6-vision"
    return DEFAULT_MODEL_KEY


class SettingsStore:
    def __init__(
        self,
        settings_path: Path | None = None,
        legacy_path: Path | None = None,
        secret_store: SecretStore | None = None,
    ):
        self.settings_path = settings_path or app_data_dir() / "settings.json"
        self.legacy_path = legacy_path or executable_dir() / "config.json"
        self.secret_store = secret_store or DpapiSecretStore()

    def load(self) -> dict[str, Any]:
        stored = load_json(self.settings_path, {})
        legacy = load_json(self.legacy_path, {}) if self.legacy_path.exists() else {}
        if not isinstance(stored, dict):
            stored = {}
        if not isinstance(legacy, dict):
            legacy = {}
        source = stored or legacy
        settings = {**DEFAULT_SETTINGS, **source}
        legacy_model = source.get("model_key") or source.get("model") or source.get("model_id")
        settings["model_key"] = model_key_from_legacy(str(legacy_model or ""))
        settings["concurrency"] = bounded_int(
            settings.get("concurrency", 3), 3, 1, MAX_CONCURRENCY
        )
        recent_folders = settings.get("recent_folders")
        if isinstance(recent_folders, (list, tuple)):
            settings["recent_folders"] = [
                str(value).strip() for value in recent_folders if str(value).strip()
            ][:10]
        else:
            settings["recent_folders"] = []
        presets = settings.get("prompt_presets")
        settings["prompt_presets"] = dict(presets) if isinstance(presets, dict) else {}
        settings["backend"] = settings.get("backend") if settings.get("backend") in {"api", "local"} else "api"
        settings["labeling_focus"] = (
            settings.get("labeling_focus")
            if settings.get("labeling_focus") in {"subject", "style", "scene"}
            else "subject"
        )
        settings["output_language"] = (
            settings.get("output_language")
            if settings.get("output_language") in {"zh", "en"}
            else "zh"
        )
        settings["suppress_seed_2_0_shutdown_notice"] = bounded_bool(
            settings.get("suppress_seed_2_0_shutdown_notice"), False
        )
        plaintext_key = source.get("api_key") or legacy.get("api_key")
        if plaintext_key and not self.secret_store.get():
            self.secret_store.set(str(plaintext_key))
        settings.pop("api_key", None)
        settings.pop("chat_url", None)
        settings.pop("model", None)
        settings.pop("model_id", None)
        settings.pop("auto_check_updates", None)
        if source and (not stored or any(k in source for k in ("api_key", "chat_url", "model"))):
            self.save(settings)
        return settings

    def save(self, settings: dict[str, Any]) -> None:
        cleaned = {**DEFAULT_SETTINGS, **settings}
        for key in (
            "api_key",
            "chat_url",
            "model",
            "model_id",
            "auto_check_updates",
        ):
            cleaned.pop(key, None)
        cleaned["version"] = 4
        cleaned["concurrency"] = bounded_int(
            cleaned.get("concurrency", 3), 3, 1, MAX_CONCURRENCY
        )
        cleaned["suppress_seed_2_0_shutdown_notice"] = bounded_bool(
            cleaned.get("suppress_seed_2_0_shutdown_notice"), False
        )
        atomic_write_json(self.settings_path, cleaned)

    def get_api_key(self) -> str:
        return self.secret_store.get()

    def set_api_key(self, value: str) -> None:
        self.secret_store.set(value.strip())


def should_show_seed_2_0_shutdown_notice(
    settings: dict[str, Any], today: date | None = None
) -> bool:
    current = today or date.today()
    return (
        SEED_2_0_NOTICE_START_DATE <= current < SEED_2_0_PLAN_END_DATE
        and not bounded_bool(
            settings.get("suppress_seed_2_0_shutdown_notice"), False
        )
    )


def caption_path_for(media_path: Path) -> Path:
    return media_path.with_suffix(".txt")


def read_usable_caption(media_path: Path) -> str:
    try:
        caption = caption_path_for(media_path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    if not caption:
        return ""
    normalized = caption.casefold()
    if (
        normalized.startswith(INVALID_CAPTION_PREFIXES)
        or any(marker in normalized for marker in INVALID_CAPTION_MARKERS)
    ):
        return ""
    return caption


def has_usable_caption(media_path: Path) -> bool:
    return bool(read_usable_caption(media_path))


def iter_usable_captions(paths: Iterable[Path]) -> Iterator[tuple[Path, str]]:
    materialized = [Path(path) for path in paths]
    if len(materialized) < 64:
        captions = map(read_usable_caption, materialized)
        yield from (
            (path, caption)
            for path, caption in zip(materialized, captions)
            if caption
        )
        return
    with ThreadPoolExecutor(max_workers=min(8, len(materialized))) as executor:
        captions = executor.map(read_usable_caption, materialized)
        yield from (
            (path, caption)
            for path, caption in zip(materialized, captions)
            if caption
        )


def write_caption(media_path: Path, caption: str) -> None:
    cleaned = caption.strip()
    if not cleaned:
        raise ValueError("模型返回了空结果")
    atomic_write_text(caption_path_for(media_path), cleaned)


def prepend_trigger_word(
    caption: str,
    trigger_word: str,
    output_language: str = "zh",
) -> str:
    cleaned_caption = caption.strip()
    cleaned_trigger = trigger_word.strip().strip(",，;；")
    if not cleaned_caption or not cleaned_trigger:
        return cleaned_caption
    normalized_caption = cleaned_caption.casefold()
    normalized_trigger = cleaned_trigger.casefold()
    if normalized_caption == normalized_trigger:
        return cleaned_caption
    if normalized_caption.startswith(normalized_trigger):
        following = cleaned_caption[len(cleaned_trigger) : len(cleaned_trigger) + 1]
        if not following or following in {" ", ",", "，", ";", "；"}:
            return cleaned_caption
    separator = ", " if output_language == "en" else "，"
    return f"{cleaned_trigger}{separator}{cleaned_caption}"


def _is_heif_content(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(32)
    except OSError:
        return False
    return b"ftyphei" in header or b"ftypmif" in header


def open_image(path: Path) -> Image.Image:
    def decode() -> Image.Image:
        with Image.open(path) as source:
            source.load()
            transposed = ImageOps.exif_transpose(source)
            if transposed.mode in {"RGBA", "LA"} or "transparency" in transposed.info:
                rgba = transposed.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                return background
            return transposed.convert("RGB")

    try:
        return decode()
    except (OSError, SyntaxError, ValueError):
        if not _is_heif_content(path):
            raise
    with HEIF_DECODE_LOCK:
        previous = pillow_heif.options.DISABLE_SECURITY_LIMITS
        pillow_heif.options.DISABLE_SECURITY_LIMITS = True
        try:
            heif_file = pillow_heif.open_heif(path)
            return ImageOps.exif_transpose(heif_file.to_pillow()).convert("RGB")
        finally:
            pillow_heif.options.DISABLE_SECURITY_LIMITS = previous


def is_readable_image(path: Path) -> bool:
    try:
        image = open_image(path)
        image.close()
        return True
    except (OSError, RuntimeError, SyntaxError, ValueError):
        return False


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    attempts: int


def prepare_image(
    path: Path,
    size_limit: int = 80 * 1024,
    max_dimension: int = 2048,
    min_dimension: int = 256,
    max_attempts: int = 18,
) -> PreparedImage:
    if size_limit < 1024 or min_dimension < 32 or max_attempts < 1:
        raise ValueError("图片压缩参数无效")
    image = open_image(path)
    attempts = 0
    best = b""
    try:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        qualities = (92, 82, 72, 62, 52, 42, 35)
        while attempts < max_attempts:
            quality = qualities[min(attempts, len(qualities) - 1)]
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            best = buffer.getvalue()
            attempts += 1
            if len(best) <= size_limit:
                return PreparedImage(best, "image/jpeg", image.width, image.height, attempts)
            if attempts >= len(qualities):
                if min(image.size) <= min_dimension:
                    break
                scale = max(0.5, min(0.82, min_dimension / min(image.size)))
                new_size = (
                    max(min_dimension, int(image.width * scale)),
                    max(min_dimension, int(image.height * scale)),
                )
                if new_size == image.size:
                    break
                resized = image.resize(new_size, Image.Resampling.LANCZOS)
                image.close()
                image = resized
        raise ValueError(
            f"图片在 {attempts} 次有界压缩后仍为 {len(best)} 字节，超过 {size_limit} 字节限制"
        )
    finally:
        image.close()


@dataclass
class ScanResult:
    files: list[Path] = field(default_factory=list)
    unreadable: dict[Path, str] = field(default_factory=dict)
    conflicts: dict[Path, str] = field(default_factory=dict)
    missing_captions: list[Path] = field(default_factory=list)
    invalid_captions: list[Path] = field(default_factory=list)
    orphan_captions: list[Path] = field(default_factory=list)
    ignored_directories: int = 0


def detect_output_conflicts(paths: Iterable[Path]) -> dict[Path, str]:
    groups: dict[str, list[Path]] = {}
    for path in paths:
        key = str(caption_path_for(path)).casefold()
        groups.setdefault(key, []).append(path)
    conflicts: dict[Path, str] = {}
    for group in groups.values():
        if len(group) <= 1:
            continue
        names = "、".join(sorted(path.name for path in group))
        detail = f"输出冲突：{names} 会写入同一个 TXT，请重命名其中一个文件"
        for path in group:
            conflicts[path] = detail
    return conflicts


def scan_media(folder: Path, mode: str) -> ScanResult:
    extensions = IMAGE_EXTENSIONS if mode == "image" else VIDEO_EXTENSIONS
    result = ScanResult()
    stack = [folder]
    txt_files: list[Path] = []
    all_caption_keys: set[str] = set()
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name.casefold()
            if entry.is_dir(follow_symlinks=False):
                if name in IGNORED_DIRECTORY_NAMES:
                    result.ignored_directories += 1
                else:
                    stack.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            suffix = path.suffix.casefold()
            if suffix == ".txt":
                txt_files.append(path)
                continue
            if suffix in ALL_MEDIA_EXTENSIONS:
                all_caption_keys.add(str(caption_path_for(path)).casefold())
            if suffix not in extensions:
                continue
            if mode == "image" and not is_readable_image(path) and not has_usable_caption(path):
                result.unreadable[path] = "图片无法解码"
            else:
                result.files.append(path)

    result.conflicts = detect_output_conflicts(result.files)
    result.files.sort(key=lambda item: str(item).casefold())
    for path in result.files:
        caption_path = caption_path_for(path)
        if not caption_path.is_file():
            result.missing_captions.append(path)
        elif not has_usable_caption(path):
            result.invalid_captions.append(path)
    result.orphan_captions = sorted(
        (
            path
            for path in txt_files
            if str(path).casefold() not in all_caption_keys
        ),
        key=lambda item: str(item).casefold(),
    )
    return result


def video_mime_type(path: Path) -> str:
    explicit = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
    }
    return explicit.get(path.suffix.casefold(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


class CancelledError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self.cancelled:
            raise CancelledError("任务已取消")

    def wait(self, seconds: float) -> None:
        if self._event.wait(max(0.0, seconds)):
            raise CancelledError("任务已取消")


def image_difference_hash(path: Path) -> int:
    image = open_image(path)
    try:
        grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(grayscale.get_flattened_data())
    finally:
        image.close()
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return value


def find_similar_images(
    paths: Iterable[Path],
    threshold: int = 5,
    token: CancellationToken | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[list[Path]]:
    if not 0 <= threshold <= 16:
        raise ValueError("相似图阈值必须在 0 到 16 之间")
    token = token or CancellationToken()
    image_paths = sorted(
        {Path(path) for path in paths if Path(path).suffix.casefold() in IMAGE_EXTENSIONS},
        key=lambda path: str(path).casefold(),
    )
    parent = list(range(len(image_paths)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    tree: dict[str, Any] | None = None

    def search(node: dict[str, Any], value: int) -> list[int]:
        distance = (value ^ node["value"]).bit_count()
        matches = list(node["indices"]) if distance <= threshold else []
        lower, upper = distance - threshold, distance + threshold
        for edge, child in node["children"].items():
            if lower <= edge <= upper:
                matches.extend(search(child, value))
        return matches

    def insert(node: dict[str, Any], value: int, index: int) -> None:
        distance = (value ^ node["value"]).bit_count()
        if distance == 0:
            node["indices"].append(index)
            return
        child = node["children"].get(distance)
        if child is None:
            node["children"][distance] = {
                "value": value, "indices": [index], "children": {}
            }
        else:
            insert(child, value, index)

    for index, path in enumerate(image_paths):
        token.check()
        try:
            value = image_difference_hash(path)
        except (OSError, RuntimeError, SyntaxError, ValueError):
            if progress:
                progress(index + 1, len(image_paths))
            continue
        if tree is None:
            tree = {"value": value, "indices": [index], "children": {}}
        else:
            for match in search(tree, value):
                union(index, match)
            insert(tree, value, index)
        if progress:
            progress(index + 1, len(image_paths))

    groups: dict[int, list[Path]] = {}
    for index, path in enumerate(image_paths):
        groups.setdefault(find(index), []).append(path)
    return [group for group in groups.values() if len(group) > 1]


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, request_id: str = "", body: str = ""):
        self.status = status
        self.request_id = request_id
        self.body = body
        parts = [message]
        if status is not None:
            parts.append(f"HTTP {status}")
        if request_id:
            parts.append(f"Request ID: {request_id}")
        if body:
            parts.append(body)
        super().__init__(" | ".join(parts))


def sanitized_response_text(response: Any, api_key: str, limit: int = 800) -> str:
    try:
        text = response.text or ""
    except Exception:
        text = ""
    if api_key:
        text = text.replace(api_key, "***")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer ***", text)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def request_id_from(response: Any) -> str:
    headers = getattr(response, "headers", {}) or {}
    lowered = {str(key).casefold(): str(value) for key, value in headers.items()}
    return next((lowered[key] for key in REQUEST_ID_HEADERS if key in lowered), "")


def retry_delay(attempt: int, response: Any | None = None, maximum: float = 15.0) -> float:
    if response is not None:
        value = (getattr(response, "headers", {}) or {}).get("Retry-After")
        try:
            return min(maximum, max(0.0, float(value)))
        except (TypeError, ValueError):
            pass
    return min(maximum, float(2 ** min(attempt, 4)))


class HttpTransport:
    def __init__(self, sender: Callable[..., Any] | None = None):
        self.sender = sender
        self._sessions: set[requests.Session] = set()
        self._lock = threading.Lock()

    def cancel_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()

    def _send(self, method: str, url: str, **kwargs):
        if self.sender is not None:
            return self.sender(method, url, **kwargs)
        session = requests.Session()
        with self._lock:
            self._sessions.add(session)
        try:
            return session.request(method, url, **kwargs)
        finally:
            with self._lock:
                self._sessions.discard(session)
            session.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        token: CancellationToken,
        api_key: str,
        attempts: int = 1,
        timeout: tuple[float, float] = (10, 120),
        **kwargs,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            token.check()
            try:
                response = self._send(method, url, timeout=timeout, **kwargs)
            except requests.RequestException as error:
                last_error = error
                if attempt >= attempts:
                    if token.cancelled:
                        raise CancelledError("任务已取消") from error
                    raise ApiError(f"网络请求失败：{error}") from error
                token.wait(retry_delay(attempt))
                continue
            token.check()
            status = int(getattr(response, "status_code", 0))
            if status in RETRYABLE_STATUS_CODES and attempt < attempts:
                token.wait(retry_delay(attempt, response))
                continue
            if status < 200 or status >= 300:
                raise ApiError(
                    "接口请求失败",
                    status=status,
                    request_id=request_id_from(response),
                    body=sanitized_response_text(response, api_key),
                )
            return response
        raise ApiError(f"网络请求失败：{last_error}")


def _response_json(response: Any, api_key: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise ApiError(
            "接口返回的不是有效 JSON",
            status=getattr(response, "status_code", None),
            request_id=request_id_from(response),
            body=sanitized_response_text(response, api_key),
        ) from error
    if not isinstance(payload, dict):
        raise ApiError("接口返回结构无效", body=str(payload)[:400])
    return payload


def _chat_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ApiError("接口响应缺少 choices.message.content", body=json.dumps(payload, ensure_ascii=False)[:600]) from error
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict)).strip()
    return str(content).strip()


def _responses_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                text = str(content.get("text", "")).strip()
                if text:
                    return text
    if "choices" in payload:
        return _chat_text(payload)
    raise ApiError("接口响应缺少文本结果", body=json.dumps(payload, ensure_ascii=False)[:600])


class CaptionClient:
    def __init__(
        self,
        model: ModelOption,
        api_key: str,
        token: CancellationToken,
        transport: HttpTransport | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.token = token
        self.transport = transport or HttpTransport()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _billable_post(self, url: str, payload: dict[str, Any], timeout=(10, 180)) -> dict[str, Any]:
        # A timed-out generation may already have been billed. Do not retry it automatically.
        response = self.transport.request(
            "POST",
            url,
            token=self.token,
            api_key=self.api_key,
            attempts=1,
            timeout=timeout,
            headers=self.headers,
            json=payload,
        )
        return _response_json(response, self.api_key)

    def caption_image(self, path: Path, prompt: str) -> str:
        prepared = prepare_image(path)
        encoded = base64.b64encode(prepared.data).decode("ascii")
        payload = {
            "model": self.model.model_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{prepared.mime_type};base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 2000,
        }
        text = _chat_text(self._billable_post(self.model.chat_url_for(), payload))
        if not text:
            raise ApiError("模型返回了空结果")
        return text

    def caption_video(self, path: Path, prompt: str) -> str:
        size = path.stat().st_size
        if size > VIDEO_UPLOAD_LIMIT:
            raise ValueError("视频大于 512 MB，无法上传")
        if size <= VIDEO_CHAT_LIMIT:
            return self._caption_small_video(path, prompt)
        return self._caption_uploaded_video(path, prompt)

    def _caption_small_video(self, path: Path, prompt: str) -> str:
        self.token.check()
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        self.token.check()
        payload = {
            "model": self.model.model_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": f"data:{video_mime_type(path)};base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 2000,
        }
        text = _chat_text(self._billable_post(self.model.chat_url_for(), payload, timeout=(10, 240)))
        if not text:
            raise ApiError("模型返回了空结果")
        return text

    def _caption_uploaded_video(self, path: Path, prompt: str) -> str:
        file_id = self._upload_video(path)
        try:
            self._wait_for_file(file_id)
            payload = {
                "model": self.model.model_id,
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_video", "file_id": file_id},
                        {"type": "input_text", "text": prompt},
                    ],
                }],
            }
            return _responses_text(self._billable_post(RESPONSES_URL, payload, timeout=(10, 240)))
        finally:
            self._delete_file(file_id)

    def _upload_video(self, path: Path) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with path.open("rb") as stream:
            files = {
                "file": (path.name, stream, video_mime_type(path)),
                "purpose": (None, "user_data"),
                "preprocess_configs[video][fps]": (None, "0.3"),
            }
            response = self.transport.request(
                "POST", FILES_URL, token=self.token, api_key=self.api_key,
                attempts=1, timeout=(10, 300), headers=headers, files=files,
            )
        file_id = str(_response_json(response, self.api_key).get("id", ""))
        if not file_id:
            raise ApiError("视频上传成功，但接口未返回文件 ID")
        return file_id

    def _wait_for_file(self, file_id: str, timeout_seconds: float = 300) -> None:
        deadline = time.monotonic() + timeout_seconds
        headers = {"Authorization": f"Bearer {self.api_key}"}
        while time.monotonic() < deadline:
            self.token.check()
            response = self.transport.request(
                "GET", f"{FILES_URL}/{file_id}", token=self.token,
                api_key=self.api_key, attempts=3, timeout=(10, 30), headers=headers,
            )
            status = str(_response_json(response, self.api_key).get("status", ""))
            if status in {"active", "processed"}:
                return
            if status in {"error", "failed"}:
                raise ApiError("视频预处理失败")
            self.token.wait(2)
        raise ApiError("视频预处理超时")

    def _delete_file(self, file_id: str) -> None:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            self.transport.request(
                "DELETE", f"{FILES_URL}/{file_id}", token=CancellationToken(),
                api_key=self.api_key, attempts=2, timeout=(10, 30), headers=headers,
            )
        except (ApiError, requests.RequestException):
            pass


class LocalCaptionClient:
    """Optional Hugging Face vision-language backend loaded from a local folder."""

    def __init__(self, model_folder: Path, token: CancellationToken):
        self.model_folder = Path(model_folder)
        self.token = token
        self.processor = None
        self.model = None
        self.torch = None
        self.device = "cpu"
        self._load_error: RuntimeError | None = None
        if not self.model_folder.is_dir():
            raise ValueError("本地模型目录不存在")
        if not (self.model_folder / "config.json").is_file():
            raise ValueError("本地模型目录缺少 config.json，不是 Hugging Face 模型目录")

    def _ensure_loaded(self) -> None:
        if self.model is not None and self.processor is not None:
            return
        if self._load_error is not None:
            raise self._load_error
        self.token.check()
        try:
            import torch
            import transformers
            from transformers import AutoProcessor
        except ImportError as error:
            self._load_error = RuntimeError(
                "本地模型需要可选运行库 torch、transformers、accelerate 和 safetensors；"
                "源码环境请安装 requirements-local.txt。轻量单文件 EXE 不内置数 GB 的模型运行库。"
            )
            raise self._load_error from error

        model_class = (
            getattr(transformers, "AutoModelForImageTextToText", None)
            or getattr(transformers, "AutoModelForVision2Seq", None)
            or getattr(transformers, "AutoModelForCausalLM", None)
        )
        if model_class is None:
            self._load_error = RuntimeError("当前 transformers 版本不支持视觉语言模型自动加载")
            raise self._load_error
        try:
            self.processor = AutoProcessor.from_pretrained(
                self.model_folder,
                local_files_only=True,
                trust_remote_code=True,
            )
            self.model = model_class.from_pretrained(
                self.model_folder,
                local_files_only=True,
                trust_remote_code=True,
                torch_dtype="auto",
            )
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model.to(self.device)
            self.model.eval()
            self.torch = torch
        except Exception as error:
            self.processor = None
            self.model = None
            self._load_error = RuntimeError(f"本地视觉模型加载失败：{error}")
            raise self._load_error from error
        self.token.check()

    def caption_image(self, path: Path, prompt: str) -> str:
        self._ensure_loaded()
        self.token.check()
        image = open_image(path)
        try:
            if hasattr(self.processor, "apply_chat_template"):
                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": prompt},
                    ],
                }]
                rendered_prompt = self.processor.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            else:
                rendered_prompt = prompt
            inputs = self.processor(
                text=[rendered_prompt],
                images=[image],
                padding=True,
                return_tensors="pt",
            )
        finally:
            image.close()
        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        else:
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
        self.token.check()
        with self.torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=False,
            )
        self.token.check()
        input_ids = inputs.get("input_ids")
        if input_ids is not None and generated.shape[-1] > input_ids.shape[-1]:
            generated = generated[:, input_ids.shape[-1] :]
        text = self.processor.batch_decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        if not text:
            raise RuntimeError("本地模型返回了空结果")
        return text

    def caption_video(self, path: Path, prompt: str) -> str:
        raise ValueError("本地模型后端当前只支持图片；视频请使用外部 API")


def project_data_path(folder: Path, data_root: Path | None = None) -> Path:
    digest = hashlib.sha256(
        str(folder.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:16]
    return (data_root or app_data_dir()) / "projects" / digest


def load_project_summary(folder: Path, data_root: Path | None = None) -> dict[str, Any]:
    state = load_json(project_data_path(folder, data_root) / "state.json", {})
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}

    def count(name: str) -> int:
        try:
            return max(0, int(summary.get(name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "folder": str(folder),
        "name": folder.name or str(folder),
        "exists": folder.is_dir(),
        "status": str(state.get("status", "new")),
        "updated_at": str(state.get("finished_at") or state.get("updated_at") or ""),
        "total": count("total"),
        "success": count("success"),
        "skipped": count("skipped"),
        "failed": count("failed"),
    }


def delete_project_metadata(folder: Path, data_root: Path | None = None) -> bool:
    projects_root = Path(os.path.abspath((data_root or app_data_dir()) / "projects"))
    target = Path(os.path.abspath(project_data_path(folder, data_root)))
    if target.parent != projects_root:
        raise ValueError("项目元数据路径越界")
    is_junction = getattr(Path, "is_junction", lambda _path: False)
    if projects_root.exists() and (projects_root.is_symlink() or is_junction(projects_root)):
        raise ValueError("项目元数据根目录不能是链接")
    if not target.exists():
        return False
    if target.is_symlink():
        target.unlink()
    elif is_junction(target):
        target.rmdir()
    else:
        shutil.rmtree(target)
    return True


class ProjectJournal:
    checkpoint_interval = 1.0

    def __init__(self, folder: Path, data_root: Path | None = None):
        self.project_dir = project_data_path(folder, data_root)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.project_dir / "state.json"
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.events_path = self.project_dir / f"run-{self.run_id}.jsonl"
        self.log_path = self.project_dir / f"run-{self.run_id}.log"
        self.folder = folder
        self.state: dict[str, Any] = {}
        self.failures: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._last_checkpoint = 0.0

    def start(self, metadata: dict[str, Any]) -> None:
        self.state = {
            "folder": str(self.folder), "run_id": self.run_id,
            "status": "running", "started_at": datetime.now().isoformat(timespec="seconds"),
            "metadata": metadata, "items": {},
        }
        atomic_write_json(self.state_path, self.state)
        self._last_checkpoint = time.monotonic()

    def record(self, path: Path, status: str, detail: str = "") -> None:
        relative = str(path.relative_to(self.folder)) if path.is_relative_to(self.folder) else str(path)
        event = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "path": relative, "status": status, "detail": detail,
        }
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            self.state["items"][relative] = event
            self.state["updated_at"] = event["time"]
            if status == "failed":
                self.failures.append({"path": relative, "error": detail})
            now = time.monotonic()
            if now - self._last_checkpoint >= self.checkpoint_interval:
                atomic_write_json(self.state_path, self.state)
                self._last_checkpoint = now

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{stamp}] {message}\n")

    def finish(self, status: str, summary: dict[str, Any]) -> None:
        with self._lock:
            self.state["status"] = status
            self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self.state["summary"] = summary
            atomic_write_json(self.state_path, self.state)
            self._last_checkpoint = time.monotonic()
            atomic_write_json(self.project_dir / "last-failures.json", self.failures)
            csv_path = self.project_dir / "last-failures.csv"
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=["path", "error"])
            writer.writeheader()
            writer.writerows(self.failures)
            atomic_write_text(csv_path, buffer.getvalue())


@dataclass
class BatchSummary:
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)


class BatchRunner:
    def __init__(self, event_callback: Callable[[str, dict[str, Any]], None], transport: HttpTransport | None = None):
        self.event_callback = event_callback
        self.token = CancellationToken()
        self.transport = transport or HttpTransport()
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def cancel(self) -> None:
        self.token.cancel()
        self.transport.cancel_all()

    def _emit(self, kind: str, **payload) -> None:
        self.event_callback(kind, payload)

    def run(
        self,
        folder: Path,
        mode: str,
        prompt: str,
        model_key: str,
        api_key: str,
        concurrency: int = 3,
        skip_existing: bool = True,
        caption_style: str = "natural",
        subject_filter: str = "",
        backend: str = "api",
        local_model_folder: str | Path = "",
        labeling_focus: str = "subject",
        output_language: str = "zh",
        trigger_word: str = "",
        only_paths: Iterable[Path] | None = None,
    ) -> BatchSummary:
        self._running.set()
        model = MODELS.get(model_key, MODELS[DEFAULT_MODEL_KEY])
        concurrency = max(1, min(MAX_CONCURRENCY, int(concurrency)))
        if backend == "local":
            concurrency = 1
        journal = ProjectJournal(folder)
        journal.start({
            "mode": mode, "caption_style": caption_style,
            "subject_filter": subject_filter,
            "backend": backend,
            "model": (
                str(Path(local_model_folder).name or local_model_folder)
                if backend == "local"
                else model.key
            ),
            "labeling_focus": labeling_focus,
            "output_language": output_language,
            "trigger_word": trigger_word,
            "concurrency": concurrency,
        })
        summary = BatchSummary()
        started = time.monotonic()
        try:
            if only_paths is None:
                scan = scan_media(folder, mode)
            else:
                extensions = IMAGE_EXTENSIONS if mode == "image" else VIDEO_EXTENSIONS
                scan = ScanResult(files=sorted({
                    Path(path)
                    for path in only_paths
                    if Path(path).is_file() and Path(path).suffix.casefold() in extensions
                }))
                scan.conflicts = detect_output_conflicts(scan.files)
            summary.total = len(scan.files) + len(scan.unreadable)
            self._emit("scan", result=scan)
            pending: queue.Queue[Path] = queue.Queue()
            for path, detail in scan.unreadable.items():
                summary.failed += 1
                summary.failures.append((path, detail))
                journal.record(path, "failed", detail)
                self._emit("status", path=path, status="failed", detail=detail)
            for path in scan.files:
                if path in scan.conflicts:
                    detail = scan.conflicts[path]
                    summary.failed += 1
                    summary.failures.append((path, detail))
                    journal.record(path, "failed", detail)
                    self._emit("status", path=path, status="failed", detail=detail)
                elif skip_existing and has_usable_caption(path):
                    summary.skipped += 1
                    journal.record(path, "skipped", "已有有效 TXT")
                    self._emit("status", path=path, status="skipped", detail="已有有效 TXT")
                else:
                    pending.put(path)

            lock = threading.Lock()
            client = (
                LocalCaptionClient(Path(local_model_folder), self.token)
                if backend == "local"
                else CaptionClient(model, api_key, self.token, self.transport)
            )
            effective_prompt = compose_prompt(
                prompt,
                caption_style,
                subject_filter,
                labeling_focus,
                output_language,
            )

            def worker() -> None:
                while not self.token.cancelled:
                    try:
                        path = pending.get_nowait()
                    except queue.Empty:
                        return
                    self._emit("status", path=path, status="running", detail="正在请求模型")
                    journal.record(path, "running", "正在请求模型")
                    try:
                        caption = client.caption_image(path, effective_prompt) if mode == "image" else client.caption_video(path, effective_prompt)
                        self.token.check()
                        caption = prepend_trigger_word(
                            caption,
                            trigger_word,
                            output_language,
                        )
                        write_caption(path, caption)
                    except CancelledError:
                        with lock:
                            summary.cancelled += 1
                        journal.record(path, "cancelled", "任务已取消")
                        self._emit("status", path=path, status="cancelled", detail="任务已取消")
                    except Exception as error:
                        detail = str(error)
                        with lock:
                            summary.failed += 1
                            summary.failures.append((path, detail))
                        journal.record(path, "failed", detail)
                        self._emit("status", path=path, status="failed", detail=detail)
                    else:
                        with lock:
                            summary.success += 1
                        journal.record(path, "success", caption[:240])
                        self._emit("status", path=path, status="success", detail=caption)
                    finally:
                        pending.task_done()
                        with lock:
                            completed = summary.success + summary.skipped + summary.failed + summary.cancelled
                        elapsed = max(0.001, time.monotonic() - started)
                        remaining = max(0, summary.total - completed)
                        eta = remaining * elapsed / max(1, completed)
                        self._emit("progress", completed=completed, total=summary.total, eta=eta)

            workers = [threading.Thread(target=worker, daemon=True, name=f"caption-worker-{index}") for index in range(concurrency)]
            for thread in workers:
                thread.start()
            for thread in workers:
                while thread.is_alive():
                    thread.join(timeout=0.1)

            while True:
                try:
                    path = pending.get_nowait()
                except queue.Empty:
                    break
                summary.cancelled += 1
                journal.record(path, "cancelled", "任务已取消")
                self._emit("status", path=path, status="cancelled", detail="任务已取消")
                pending.task_done()
            status = "stopped" if self.token.cancelled else "completed"
            journal.finish(status, {
                "total": summary.total, "success": summary.success,
                "skipped": summary.skipped, "failed": summary.failed,
                "cancelled": summary.cancelled,
            })
            self._emit("done", status=status, summary=summary, journal_dir=journal.project_dir)
            return summary
        finally:
            self._running.clear()


def compose_prompt(
    prompt: str,
    caption_style: str,
    subject_filter: str = "",
    labeling_focus: str = "subject",
    output_language: str = "zh",
) -> str:
    if output_language == "en":
        instruction = (
            "Final format: output one line of concise English phrase tags separated by commas. "
            "Do not explain your reasoning."
            if caption_style == "phrases"
            else "Final format: output one coherent English natural-language caption only. "
            "Do not explain your reasoning."
        )
    else:
        instruction = (
            "最终格式要求：只输出一行中文词组标签，以中文逗号分隔，不要输出解释。"
            if caption_style == "phrases"
            else "最终格式要求：只输出一段连贯的中文自然语言描述，不要输出分析过程。"
        )
    focus_instruction = {
        "subject": (
            "打标侧重点：用于训练主体 LoRA。优先描述主体身份特征、脸部、发型、体型、服装、"
            "姿态和稳定可识别特征，弱化无关背景与偶然风格。"
        ),
        "style": (
            "打标侧重点：用于训练风格 LoRA。优先描述画面媒介、艺术风格、色彩、材质、笔触、"
            "光影、构图、镜头和渲染特征，避免把偶然出现的主体身份作为核心。"
        ),
        "scene": (
            "打标侧重点：用于训练风景或场景。优先描述环境类型、地貌、建筑、空间关系、"
            "天气、季节、时间、光照和氛围，人物仅作为场景元素。"
        ),
    }.get(labeling_focus, "")
    subject_instruction = ""
    if subject_filter.strip():
        subject_instruction = (
            f"\n主体过滤要求：只保留并描述与“{subject_filter.strip()}”相关的主体信息，"
            "忽略背景中的无关人物或物体。"
        )
    return f"{prompt.strip()}\n\n{focus_instruction}\n{instruction}{subject_instruction}"


def export_jsonl(paths: Iterable[Path], destination: Path, root: Path) -> int:
    lines = []
    for path, caption in iter_usable_captions(paths):
        relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        lines.append(json.dumps({"file_name": relative, "text": caption}, ensure_ascii=False))
    atomic_write_text(destination, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def export_csv(paths: Iterable[Path], destination: Path, root: Path) -> int:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["file_name", "text"])
    count = 0
    for path, caption in iter_usable_captions(paths):
        relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        writer.writerow([relative, caption])
        count += 1
    atomic_write_text(destination, buffer.getvalue())
    return count
