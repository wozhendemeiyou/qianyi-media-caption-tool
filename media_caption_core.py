from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import date, datetime
import gc
import hashlib
import io
import json
import mimetypes
import os
import platform
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlsplit, urlunsplit
import zipfile

from PIL import Image, ImageOps
import pillow_heif
import requests

from media_caption_worker import (
    MediaWorkerController,
    MediaWorkerError,
    media_tool_status,
)


APP_NAME = "Media Caption Tool"
APP_VERSION = "3.6.6"
GITHUB_REPOSITORY = "wozhendemeiyou/qianyi-media-caption-tool"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
GITHUB_LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
CODING_CHAT_URL = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
STANDARD_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
FILES_URL = "https://ark.cn-beijing.volces.com/api/v3/files"
RESPONSES_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
PROVIDER_TEST_URLS = {
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3/models",
    "openai": "https://api.openai.com/v1/models",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/models",
    "moonshot": "https://api.moonshot.cn/v1/models",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
    "siliconflow": "https://api.siliconflow.cn/v1/models",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
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
LMSTUDIO_CAPTION_TOKEN_LIMIT = 768
LMSTUDIO_REASONING_TOKEN_LIMIT = 1536
LMSTUDIO_READ_TIMEOUT = 600
LMSTUDIO_IMAGE_MAX_DIMENSION = 1280
LMSTUDIO_LOAD_CONTEXT_LENGTH = 8192
LMSTUDIO_LOAD_PARALLEL = 1
LMSTUDIO_LOAD_PROFILE_DEFAULT = "low_vram"
LMSTUDIO_LOAD_PROFILES = frozenset({"low_vram", "cpu", "inherit"})
LLAMA_CPP_READ_TIMEOUT = 600
LLAMA_CPP_START_TIMEOUT = 180
LLAMA_CPP_DEFAULT_CONTEXT_LENGTH = 4096
LLAMA_CPP_DEFAULT_GPU_LAYERS = 99
LLAMA_CPP_IMAGE_MAX_DIMENSION = 1280
LLAMA_CPP_IMAGE_SIZE_LIMIT = 512 * 1024
HEIF_DECODE_LOCK = threading.Lock()
SEED_2_0_PLAN_END_DATE = date(2026, 8, 8)


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


@dataclass(frozen=True)
class ApiProviderOption:
    key: str
    label: str
    chat_url: str
    default_model: str
    model_suggestions: tuple[str, ...]
    billing: str
    supported_parameters: frozenset[str]
    supports_video: bool = False
    allows_custom_endpoint: bool = False
    endpoint_suggestions: tuple[str, ...] = ()


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
        "豆包 Seed 2.1 Turbo",
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
    "minimax-m3": ModelOption(
        "minimax-m3",
        "MiniMax M3",
        "MiniMax-M3",
        CODING_CHAT_URL,
        "Coding Plan",
    ),
}
DEFAULT_MODEL_KEY = "seed-2.1-pro"

API_PROVIDERS = {
    "volcengine": ApiProviderOption(
        "volcengine",
        "火山引擎",
        "",
        MODELS[DEFAULT_MODEL_KEY].model_id,
        tuple(model.model_id for model in MODELS.values()),
        "按所选模型计费",
        frozenset({
            "max_tokens", "temperature", "top_p", "frequency_penalty",
            "presence_penalty", "seed",
        }),
        supports_video=True,
        endpoint_suggestions=(CODING_CHAT_URL, STANDARD_CHAT_URL),
    ),
    "openai": ApiProviderOption(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1/chat/completions",
        "gpt-5.6-terra",
        (
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.6",
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "gpt-4.1",
            "gpt-4.1-mini",
        ),
        "按量计费",
        frozenset({
            "max_tokens", "temperature", "top_p", "frequency_penalty",
            "presence_penalty", "seed",
        }),
        endpoint_suggestions=("https://api.openai.com/v1/chat/completions",),
    ),
    "google": ApiProviderOption(
        "google",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "gemini-3.7-flash",
        (
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-3.1-flash-lite",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ),
        "按量计费",
        frozenset({"max_tokens", "temperature", "top_p", "seed"}),
        endpoint_suggestions=(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
    ),
    "moonshot": ApiProviderOption(
        "moonshot",
        "月之暗面 Kimi",
        "https://api.moonshot.cn/v1/chat/completions",
        "kimi-k3",
        (
            "kimi-k3",
            "kimi-k2.7-code-highspeed",
            "kimi-k2.7-code",
            "kimi-k2.6",
        ),
        "按量计费",
        frozenset({
            "max_tokens", "temperature", "top_p",
            "frequency_penalty", "presence_penalty",
        }),
        endpoint_suggestions=("https://api.moonshot.cn/v1/chat/completions",),
    ),
    "qwen": ApiProviderOption(
        "qwen",
        "阿里云百炼 · 千问",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen3.8-max",
        (
            "qwen3.8-max",
            "qwen3.7-plus",
            "qwen3.7-flash",
            "qwen3.6-plus",
            "qwen3.6-flash",
            "qwen3.5-omni-plus",
            "qwen3.5-omni-flash",
            "qwen3-vl-plus",
            "qwen3-vl-flash",
        ),
        "按量计费",
        frozenset({"max_tokens", "temperature", "top_p", "top_k", "seed"}),
        supports_video=True,
        endpoint_suggestions=(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
    ),
    "siliconflow": ApiProviderOption(
        "siliconflow",
        "硅基流动 SiliconFlow",
        "https://api.siliconflow.cn/v1/chat/completions",
        "Qwen/Qwen3.6-35B-A3B",
        (
            "Qwen/Qwen3.6-35B-A3B",
            "Qwen/Qwen3.6-27B",
            "Qwen/Qwen3.5-397B-A17B",
            "Qwen/Qwen3-VL-32B-Instruct",
            "moonshotai/Kimi-K2.7-Code",
            "Pro/moonshotai/Kimi-K2.6",
            "nex-agi/Nex-N2-Pro",
        ),
        "按量计费",
        frozenset({"max_tokens", "temperature", "top_p", "top_k", "seed"}),
        endpoint_suggestions=("https://api.siliconflow.cn/v1/chat/completions",),
    ),
    "custom": ApiProviderOption(
        "custom",
        "自定义 OpenAI 兼容接口",
        "",
        "",
        (),
        "由服务商决定",
        frozenset({
            "max_tokens", "temperature", "top_p", "top_k",
            "frequency_penalty", "presence_penalty", "seed",
        }),
        supports_video=True,
        allows_custom_endpoint=True,
        endpoint_suggestions=(),
    ),
}
DEFAULT_PROVIDER_KEY = "volcengine"

DEFAULT_SAMPLING = {
    "max_tokens": 2000,
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "seed": None,
}


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


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _backup_files(data_root: Path, include_credentials: bool) -> Iterator[Path]:
    allowed_top_level = {"settings.json", "projects"}
    if include_credentials:
        allowed_top_level.add("credentials.bin")
    for path in data_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(data_root)
        if not relative.parts or relative.parts[0] not in allowed_top_level:
            continue
        if (
            relative.parts[0] == "projects"
            and path.suffix.casefold() not in {".json", ".jsonl", ".log", ".csv"}
        ):
            continue
        yield path
    if include_credentials:
        for path in data_root.glob("credentials-*.bin"):
            if path.is_file() and not path.is_symlink():
                yield path


def create_app_backup(
    destination: Path | None = None,
    data_root: Path | None = None,
    include_credentials: bool = False,
) -> Path:
    """Create an atomic metadata backup without copying user media files."""
    source_root = Path(data_root or app_data_dir()).resolve(strict=False)
    default_dir = source_root / "backups"
    target = Path(destination or default_dir / f"qianyi-backup-{_timestamp_slug()}.zip")
    if target.suffix.casefold() != ".zip":
        target = target / f"qianyi-backup-{_timestamp_slug()}.zip"
    target = target.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    manifest = {
        "application": APP_NAME,
        "version": APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "include_credentials": bool(include_credentials),
    }
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                "backup-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            if source_root.is_dir():
                for path in _backup_files(source_root, include_credentials):
                    if path.resolve(strict=False) == target:
                        continue
                    relative = path.relative_to(source_root)
                    archive_name = Path("data") / relative
                    if relative == Path("settings.json"):
                        safe_settings = _redact_diagnostic(load_json(path, {}))
                        archive.writestr(
                            str(archive_name).replace("\\", "/"),
                            json.dumps(safe_settings, ensure_ascii=False, indent=2),
                        )
                    else:
                        archive.write(path, archive_name)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def maybe_create_automatic_backup(
    data_root: Path | None = None,
    interval_hours: float = 24.0,
    keep: int = 7,
) -> Path | None:
    root = Path(data_root or app_data_dir()).resolve(strict=False)
    backup_dir = root / "backups"
    backups = sorted(
        backup_dir.glob("qianyi-backup-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if backup_dir.is_dir() else []
    interval_seconds = max(60.0, float(interval_hours) * 3600.0)
    if backups and time.time() - backups[0].stat().st_mtime < interval_seconds:
        return None
    created = create_app_backup(data_root=root)
    backups = sorted(
        backup_dir.glob("qianyi-backup-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for expired in backups[max(1, int(keep)):]:
        expired.unlink(missing_ok=True)
    return created


def _redact_diagnostic(value: Any, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in ("api_key", "token", "secret", "password")):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_diagnostic(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_diagnostic(item, key) for item in value]
    return value


def _diagnostic_project_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    sanitized = _redact_diagnostic(payload)
    folder = str(sanitized.get("folder") or "")
    sanitized["folder"] = Path(folder).name if folder else ""
    items = sanitized.get("items")
    if isinstance(items, dict):
        sanitized["items"] = {
            Path(str(path)).name: {
                "time": str(event.get("time") or ""),
                "status": str(event.get("status") or ""),
                "detail": "<omitted>" if event.get("detail") else "",
            }
            for path, event in items.items()
            if isinstance(event, dict)
        }
    return sanitized


def create_diagnostic_bundle(
    destination: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    """Export redacted runtime metadata; credentials and media never enter the bundle."""
    root = Path(data_root or app_data_dir()).resolve(strict=False)
    default_dir = root / "diagnostics"
    target = Path(
        destination or default_dir / f"qianyi-diagnostics-{_timestamp_slug()}.zip"
    )
    if target.suffix.casefold() != ".zip":
        target = target / f"qianyi-diagnostics-{_timestamp_slug()}.zip"
    target = target.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tools = media_tool_status()
    system_info = {
        "application": APP_NAME,
        "version": APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "media_tools": {
            name: {"available": bool(path), "file": Path(path).name if path else ""}
            for name, path in tools.items()
        },
    }
    settings = _redact_diagnostic(load_json(root / "settings.json", {}))
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                "system.json", json.dumps(system_info, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                "settings-redacted.json",
                json.dumps(settings, ensure_ascii=False, indent=2),
            )
            projects_root = root / "projects"
            if projects_root.is_dir():
                for state_path in projects_root.glob("*/state.json"):
                    project_id = state_path.parent.name
                    state = _diagnostic_project_state(load_json(state_path, {}))
                    archive.writestr(
                        f"projects/{project_id}/state-redacted.json",
                        json.dumps(state, ensure_ascii=False, indent=2),
                    )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


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
    "version": 14,
    "model_key": DEFAULT_MODEL_KEY,
    "provider_key": DEFAULT_PROVIDER_KEY,
    "api_models": {},
    "api_endpoints": {},
    "custom_api_endpoint": "",
    "sampling": dict(DEFAULT_SAMPLING),
    "last_folder": "",
    "recent_folders": [],
    "concurrency": 3,
    "skip_existing": True,
    "media_mode": "image",
    "caption_style": "natural",
    "view_mode": "gallery",
    "subject_filter": "",
    "backend": "api",
    "local_runtime": "huggingface",
    "local_model_folder": "",
    "lmstudio_base_url": "http://localhost:1234/v1",
    "lmstudio_model": "",
    "lmstudio_load_profile": LMSTUDIO_LOAD_PROFILE_DEFAULT,
    "llama_server_path": "",
    "llama_model_path": "",
    "llama_mmproj_path": "",
    "llama_model_alias": "",
    "llama_context_length": LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
    "llama_gpu_layers": LLAMA_CPP_DEFAULT_GPU_LAYERS,
    "labeling_focus": "subject",
    "output_language": "zh",
    "trigger_word": "",
    "user_prompt": "",
    "selected_preset": "",
    "prompt_presets": {},
    "theme": "night",
    "auto_check_updates": True,
    "video_preflight": True,
    "enable_mtp": False,
    "remove_thinking_tags": True,
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


def bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_lmstudio_load_profile(value: Any) -> str:
    profile = str(value or "").strip().casefold()
    return (
        profile
        if profile in LMSTUDIO_LOAD_PROFILES
        else LMSTUDIO_LOAD_PROFILE_DEFAULT
    )


def normalize_sampling(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    seed_value = source.get("seed")
    try:
        seed = int(seed_value) if seed_value not in {None, ""} else None
    except (TypeError, ValueError):
        seed = None
    if seed is not None:
        seed = max(0, min(2_147_483_647, seed))
    return {
        "max_tokens": bounded_int(source.get("max_tokens"), 2000, 64, 32768),
        "temperature": bounded_float(source.get("temperature"), 0.2, 0.0, 2.0),
        "top_p": bounded_float(source.get("top_p"), 0.9, 0.0, 1.0),
        "top_k": bounded_int(source.get("top_k"), 0, 0, 500),
        "frequency_penalty": bounded_float(
            source.get("frequency_penalty"), 0.0, -2.0, 2.0
        ),
        "presence_penalty": bounded_float(
            source.get("presence_penalty"), 0.0, -2.0, 2.0
        ),
        "seed": seed,
    }


def version_tuple(value: Any) -> tuple[int, ...]:
    """Return a comparable numeric version without trusting release labels."""
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){0,3})", str(value or ""))
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: Any, current: Any = APP_VERSION) -> bool:
    latest = version_tuple(candidate)
    installed = version_tuple(current)
    if not latest or not installed:
        return False
    width = max(len(latest), len(installed))
    return latest + (0,) * (width - len(latest)) > installed + (0,) * (
        width - len(installed)
    )


def check_latest_release(
    transport: "HttpTransport | None" = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Read the public GitHub release feed and select a Windows update asset."""
    sender = transport or HttpTransport()
    response = sender.request(
        "GET",
        GITHUB_LATEST_RELEASE_API,
        token=CancellationToken(),
        api_key="",
        attempts=1,
        timeout=timeout,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Qianyi-Media-Caption-Tool/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"GitHub Release 查询失败（HTTP {response.status_code}）")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub Release 返回格式无效")
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not version_tuple(tag):
        raise RuntimeError("GitHub Release 缺少有效版本号")
    assets = []
    for asset in payload.get("assets") or ():
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        digest = str(asset.get("digest") or "").strip().casefold()
        assets.append({
            "name": name,
            "url": url,
            "size": max(0, int(asset.get("size") or 0)),
            "content_type": str(asset.get("content_type") or "").strip(),
            "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
        })
    windows_asset = next(
        (
            asset
            for asset in assets
            if asset["name"].casefold().endswith((".exe", ".zip"))
            and any(
                marker in asset["name"].casefold()
                for marker in ("windows", "win64", "win-x64", "x64")
            )
        ),
        None,
    )
    return {
        "tag": tag,
        "name": str(payload.get("name") or tag).strip(),
        "url": str(payload.get("html_url") or GITHUB_RELEASES_URL).strip(),
        "published_at": str(payload.get("published_at") or "").strip(),
        "notes": str(payload.get("body") or "").strip(),
        "is_newer": is_newer_version(tag),
        "assets": assets,
        "windows_asset": windows_asset,
    }


def download_release_asset(
    asset: dict[str, Any],
    destination_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    timeout: tuple[float, float] = (10, 180),
) -> Path:
    """Download and validate a release asset without executing it."""
    url = str(asset.get("url") or "").strip()
    name = Path(str(asset.get("name") or "")).name
    if not url.startswith("https://github.com/") or not name:
        raise ValueError("更新包地址无效")
    target_dir = Path(destination_dir or tempfile.mkdtemp(prefix="qianyi-update-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    response = requests.get(
        url,
        stream=True,
        timeout=timeout,
        headers={"User-Agent": f"Qianyi-Media-Caption-Tool/{APP_VERSION}"},
    )
    response.raise_for_status()
    total = max(0, int(response.headers.get("Content-Length") or asset.get("size") or 0))
    digest = hashlib.sha256()
    received = 0
    try:
        with target.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, total)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    expected_size = max(0, int(asset.get("size") or 0))
    if expected_size and received != expected_size:
        target.unlink(missing_ok=True)
        raise RuntimeError("更新包大小校验失败")
    expected_hash = str(asset.get("sha256") or "").strip().casefold()
    if expected_hash and digest.hexdigest().casefold() != expected_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError("更新包 SHA-256 校验失败")
    if target.suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(target) as archive:
                bad_file = archive.testzip()
        except zipfile.BadZipFile as error:
            target.unlink(missing_ok=True)
            raise RuntimeError("下载的更新包不是有效 ZIP") from error
        if bad_file:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"更新包文件损坏：{bad_file}")
    elif target.suffix.casefold() == ".exe" and target.read_bytes()[:2] != b"MZ":
        target.unlink(missing_ok=True)
        raise RuntimeError("下载的更新程序格式无效")
    return target


def extract_update_executable(asset_path: Path, destination_dir: Path) -> Path:
    """Extract the most likely application executable from an update package."""
    source = Path(asset_path)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if source.suffix.casefold() == ".exe":
        target = destination / source.name
        shutil.copy2(source, target)
        return target
    if source.suffix.casefold() != ".zip":
        raise RuntimeError("更新资产必须是 EXE 或 ZIP")
    with zipfile.ZipFile(source) as archive:
        candidates = [
            info
            for info in archive.infolist()
            if not info.is_dir() and Path(info.filename).suffix.casefold() == ".exe"
        ]
        if not candidates:
            raise RuntimeError("更新包中没有找到可执行程序")
        candidate = max(candidates, key=lambda item: item.file_size)
        safe_name = Path(candidate.filename).name
        target = destination / safe_name
        with archive.open(candidate) as source_file, target.open("wb") as output:
            shutil.copyfileobj(source_file, output)
    if target.read_bytes()[:2] != b"MZ":
        target.unlink(missing_ok=True)
        raise RuntimeError("更新包内的可执行程序格式无效")
    return target


def provider_models_url(provider_key: str, api_endpoint: str = "") -> str:
    if provider_key in PROVIDER_TEST_URLS:
        return PROVIDER_TEST_URLS[provider_key]
    if provider_key != "custom":
        raise ValueError("当前平台不支持连接测试")
    endpoint = str(api_endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("请先填写自定义 Base URL")
    if not endpoint.casefold().startswith(("http://", "https://")):
        raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
    endpoint = endpoint.removesuffix("/chat/completions")
    return f"{endpoint}/models"


def test_provider_connection(
    provider_key: str,
    api_key: str,
    transport: "HttpTransport | None" = None,
    timeout: float = 10.0,
    api_endpoint: str = "",
) -> dict[str, Any]:
    """Validate credentials using a non-generating model-list request."""
    secret = str(api_key or "").strip()
    if not secret and provider_key != "custom":
        raise ValueError("请先填写 API Key")
    headers = {
        "Accept": "application/json",
        "User-Agent": f"Qianyi-Media-Caption-Tool/{APP_VERSION}",
    }
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    started = time.monotonic()
    response = (transport or HttpTransport()).request(
        "GET",
        provider_models_url(provider_key, api_endpoint),
        token=CancellationToken(),
        api_key=secret,
        attempts=1,
        timeout=(5, timeout),
        headers=headers,
    )
    return {
        "ok": True,
        "status": int(getattr(response, "status_code", 200)),
        "latency_ms": max(1, round((time.monotonic() - started) * 1000)),
    }


def lmstudio_native_url(base_url: str, path: str) -> str:
    """Build an LM Studio native REST URL from its OpenAI-compatible base."""
    endpoint = str(base_url or "").strip()
    if not endpoint:
        raise ValueError("请先填写 LM Studio Base URL")
    parsed = urlsplit(endpoint)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LM Studio Base URL 必须以 http:// 或 https:// 开头")
    normalized_path = "/" + str(path or "").lstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def is_local_lmstudio_endpoint(base_url: str) -> bool:
    parsed = urlsplit(str(base_url or "").strip())
    host = (parsed.hostname or "").casefold()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} and (
        parsed.port in {None, 1234}
    )


def find_lmstudio_cli() -> Path | None:
    discovered = shutil.which("lms")
    candidates = [
        Path(discovered) if discovered else None,
        Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _clean_cli_output(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _is_memory_failure(value: Any) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in (
        "outofdevicememory",
        "out of device memory",
        "cuda out of memory",
        "out of memory",
        "failed to allocate",
        "not enough memory",
    ))


def _lmstudio_load_command(model_key: str, profile: str) -> list[str]:
    command = [
        "load",
        model_key,
        "--context-length",
        str(LMSTUDIO_LOAD_CONTEXT_LENGTH),
        "--parallel",
        str(LMSTUDIO_LOAD_PARALLEL),
        "--no-speculative-draft-mtp",
        "-y",
    ]
    if profile == "cpu":
        command.extend(("--gpu", "off"))
    elif profile == "low_vram":
        command.extend(("--gpu", "0.10"))
    return command


def list_lmstudio_models(
    base_url: str,
    transport: "HttpTransport | None" = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Return downloaded vision-capable models and their loaded instances."""
    response = (transport or HttpTransport()).request(
        "GET",
        lmstudio_native_url(base_url, "/api/v1/models"),
        token=CancellationToken(),
        api_key="",
        attempts=1,
        timeout=(5, timeout),
        headers={
            "Accept": "application/json",
            "User-Agent": f"Qianyi-Media-Caption-Tool/{APP_VERSION}",
        },
    )
    payload = _response_json(response, "")
    data = payload.get("models")
    if not isinstance(data, list):
        raise ApiError("LM Studio 模型列表响应缺少 models 数组")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_key = str(item.get("key") or "").strip()
        if not model_key or model_key in seen:
            continue
        if str(item.get("type") or "llm").casefold() != "llm":
            continue
        capabilities = (
            item.get("capabilities")
            if isinstance(item.get("capabilities"), dict)
            else {}
        )
        # Older LM Studio versions may omit capability metadata. Include those
        # entries, but exclude models that explicitly report no vision input.
        if capabilities.get("vision") is False:
            continue
        loaded_instances = []
        loaded_configs: dict[str, dict[str, Any]] = {}
        raw_instances = item.get("loaded_instances")
        if isinstance(raw_instances, list):
            for instance in raw_instances:
                if not isinstance(instance, dict):
                    continue
                instance_id = str(instance.get("id") or "").strip()
                if not instance_id:
                    continue
                loaded_instances.append(instance_id)
                config = instance.get("config")
                loaded_configs[instance_id] = (
                    dict(config) if isinstance(config, dict) else {}
                )
        reasoning = capabilities.get("reasoning")
        seen.add(model_key)
        models.append({
            "key": model_key,
            "display_name": str(item.get("display_name") or model_key).strip(),
            "format": str(item.get("format") or "").strip(),
            "vision": capabilities.get("vision"),
            "reasoning": dict(reasoning) if isinstance(reasoning, dict) else {},
            "size_bytes": bounded_int(item.get("size_bytes"), 0, 0, 2 ** 63 - 1),
            "params_string": str(item.get("params_string") or "").strip(),
            "loaded_instances": loaded_instances,
            "loaded_configs": loaded_configs,
        })
    return models


def discover_lmstudio_models(
    base_url: str,
    transport: "HttpTransport | None" = None,
    timeout: float = 10.0,
) -> list[str]:
    """Return selectable LM Studio model keys."""
    return [
        model["key"]
        for model in list_lmstudio_models(base_url, transport, timeout)
    ]


def load_lmstudio_model(
    base_url: str,
    model_key: str,
    transport: "HttpTransport | None" = None,
    timeout: float = 600.0,
    *,
    load_profile: str = LMSTUDIO_LOAD_PROFILE_DEFAULT,
) -> dict[str, Any]:
    selected = str(model_key or "").strip()
    if not selected:
        raise ValueError("请先选择 LM Studio 模型")
    profile = normalize_lmstudio_load_profile(load_profile)
    if (
        transport is None
        and profile != "inherit"
        and is_local_lmstudio_endpoint(base_url)
    ):
        executable = find_lmstudio_cli()
        if executable is None:
            raise RuntimeError(
                "未找到 LM Studio 命令行工具 lms，无法应用安全 GPU 加载策略。"
                "请在 LM Studio 的 Developer 页面安装 CLI，或选择“沿用 LM Studio 预设”。"
            )
        command = [str(executable), *_lmstudio_load_command(selected, profile)]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30.0, float(timeout)),
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"LM Studio 模型加载超过 {int(timeout)} 秒，已停止等待"
            ) from error
        except OSError as error:
            raise RuntimeError(f"无法启动 LM Studio CLI：{error}") from error
        output = _clean_cli_output(
            "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        )
        if completed.returncode != 0:
            if _is_memory_failure(output):
                raise RuntimeError(
                    "LM Studio 模型加载失败：显存或内存不足。请关闭占用显存的软件，"
                    "改用“纯 CPU（最稳定）”，或换用更小的量化模型。"
                )
            raise RuntimeError(f"LM Studio 模型加载失败：{output or 'lms 返回未知错误'}")
        inventory = list_lmstudio_models(base_url, timeout=min(15.0, timeout))
        selected_info = next(
            (item for item in inventory if item.get("key") == selected), {}
        )
        instances = list(selected_info.get("loaded_instances") or [])
        instance_id = str(instances[0] if instances else selected)
        loaded_configs = selected_info.get("loaded_configs") or {}
        return {
            "instance_id": instance_id,
            "status": "loaded",
            "loader": "cli",
            "profile": profile,
            "load_config": dict(loaded_configs.get(instance_id) or {}),
        }

    request_body: dict[str, Any] = {
        "model": selected,
        "echo_load_config": True,
    }
    if profile != "inherit":
        request_body.update({
            "context_length": LMSTUDIO_LOAD_CONTEXT_LENGTH,
            "eval_batch_size": 512,
            "flash_attention": True,
            "offload_kv_cache_to_gpu": False,
        })
    response = (transport or HttpTransport()).request(
        "POST",
        lmstudio_native_url(base_url, "/api/v1/models/load"),
        token=CancellationToken(),
        api_key="",
        attempts=1,
        timeout=(5, timeout),
        headers={"Content-Type": "application/json"},
        json=request_body,
    )
    payload = _response_json(response, "")
    instance_id = str(payload.get("instance_id") or "").strip()
    if not instance_id:
        raise ApiError("LM Studio 加载响应缺少 instance_id")
    return {
        "instance_id": instance_id,
        "status": str(payload.get("status") or "loaded").strip(),
        "loader": "rest",
        "profile": profile,
        "load_config": dict(
            payload.get("load_config")
            if isinstance(payload.get("load_config"), dict)
            else {}
        ),
    }


def unload_lmstudio_model(
    base_url: str,
    instance_id: str,
    transport: "HttpTransport | None" = None,
    timeout: float = 120.0,
) -> dict[str, str]:
    selected = str(instance_id or "").strip()
    if not selected:
        raise ValueError("LM Studio 模型实例 ID 为空")
    response = (transport or HttpTransport()).request(
        "POST",
        lmstudio_native_url(base_url, "/api/v1/models/unload"),
        token=CancellationToken(),
        api_key="",
        attempts=1,
        timeout=(5, timeout),
        headers={"Content-Type": "application/json"},
        json={"instance_id": selected},
    )
    payload = _response_json(response, "")
    return {
        "instance_id": selected,
        "status": str(payload.get("status") or "unloaded").strip(),
    }


def create_windows_update_script(
    update_executable: Path,
    installed_executable: Path,
    parent_pid: int,
    directory: Path | None = None,
    *,
    restart: bool = True,
    bootloader_pid: int | None = None,
) -> Path:
    """Create a one-shot PowerShell updater that runs after this process exits."""
    source = Path(update_executable).resolve()
    target = Path(installed_executable).resolve()
    if source == target or source.suffix.casefold() != ".exe":
        raise ValueError("更新程序路径无效")
    if source.read_bytes()[:2] != b"MZ":
        raise RuntimeError("更新程序格式无效")

    def quoted(path: Path) -> str:
        return str(path).replace("'", "''")

    script_dir = Path(directory or source.parent)
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "install-qianyi-update.ps1"
    log_path = script_dir / "update-install.log"
    staged = target.with_name(f".{target.stem}-update-{int(parent_pid)}.exe")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
    target_parent = target.parent
    wait_pids = [int(parent_pid)]
    if bootloader_pid is not None:
        candidate = int(bootloader_pid)
        if candidate > 0 and candidate not in wait_pids:
            wait_pids.append(candidate)
    wait_commands = "\n".join(
        f"    Wait-Process -Id {pid} -ErrorAction SilentlyContinue"
        for pid in wait_pids
    )
    wait_label = ", ".join(str(pid) for pid in wait_pids)
    restart_command = (
        "    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
        "    Get-ChildItem Env: | Where-Object { $_.Name -like '_PYI_*' } | "
        "Remove-Item -ErrorAction SilentlyContinue\n"
        f"    Start-Process -FilePath $target -WorkingDirectory '{quoted(target_parent)}'\n"
        if restart
        else ""
    )
    body = f"""$ErrorActionPreference = 'Stop'
$source = '{quoted(source)}'
$target = '{quoted(target)}'
$staged = '{quoted(staged)}'
$log = '{quoted(log_path)}'
$expectedHash = '{expected_hash}'
$succeeded = $false
$exitCode = 0
function Get-Sha256([string] $path) {{
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {{
        $stream = [System.IO.File]::OpenRead($path)
        try {{
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '')
        }} finally {{
            $stream.Dispose()
        }}
    }} finally {{
        $sha.Dispose()
    }}
}}
try {{
    "[$(Get-Date -Format o)] Waiting for application processes {wait_label}" | Set-Content -LiteralPath $log -Encoding UTF8
{wait_commands}
    Start-Sleep -Milliseconds 750
    Copy-Item -LiteralPath $source -Destination $staged -Force
    $stagedHash = Get-Sha256 $staged
    if ($stagedHash -ne $expectedHash) {{
        throw "Staged executable SHA-256 mismatch"
    }}
    Move-Item -LiteralPath $staged -Destination $target -Force
    $installedHash = Get-Sha256 $target
    if ($installedHash -ne $expectedHash) {{
        throw "Installed executable SHA-256 mismatch"
    }}
{restart_command.rstrip()}
    "[$(Get-Date -Format o)] SUCCESS $installedHash" | Add-Content -LiteralPath $log -Encoding UTF8
    $succeeded = $true
}} catch {{
    $exitCode = 1
    "[$(Get-Date -Format o)] FAILURE`n$($_ | Out-String)" | Add-Content -LiteralPath $log -Encoding UTF8
}} finally {{
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    if ($succeeded) {{
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    }}
}}
exit $exitCode
"""
    script.write_text(body, encoding="utf-8-sig")
    return script


def launch_windows_update_installer(script: Path) -> subprocess.Popen:
    """Launch the updater hidden but attached long enough to execute reliably."""
    updater = Path(script).resolve()
    if not updater.is_file() or updater.suffix.casefold() != ".ps1":
        raise ValueError("更新脚本路径无效")
    clean_environment = os.environ.copy()
    clean_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return subprocess.Popen(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(updater),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=clean_environment,
    )


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
        provider_secret_stores: dict[str, SecretStore] | None = None,
    ):
        self.settings_path = settings_path or app_data_dir() / "settings.json"
        self.legacy_path = legacy_path or executable_dir() / "config.json"
        self.secret_store = secret_store or DpapiSecretStore()
        self.provider_secret_stores = dict(provider_secret_stores or {})
        self._volatile_provider_secrets: dict[str, str] = {}

    def _provider_secret_store(self, provider_key: str) -> SecretStore | None:
        normalized = provider_key if provider_key in API_PROVIDERS else DEFAULT_PROVIDER_KEY
        if normalized == DEFAULT_PROVIDER_KEY:
            return self.secret_store
        if normalized in self.provider_secret_stores:
            return self.provider_secret_stores[normalized]
        if isinstance(self.secret_store, DpapiSecretStore):
            store = DpapiSecretStore(
                self.secret_store.path.with_name(f"credentials-{normalized}.bin")
            )
            self.provider_secret_stores[normalized] = store
            return store
        return None

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
        user_prompt = settings.get("user_prompt")
        settings["user_prompt"] = user_prompt if isinstance(user_prompt, str) else ""
        provider_key = str(settings.get("provider_key", DEFAULT_PROVIDER_KEY))
        settings["provider_key"] = (
            provider_key if provider_key in API_PROVIDERS else DEFAULT_PROVIDER_KEY
        )
        api_models = settings.get("api_models")
        settings["api_models"] = {
            key: str(model).strip()
            for key, model in (api_models.items() if isinstance(api_models, dict) else ())
            if key in API_PROVIDERS and str(model).strip()
        }
        api_endpoints = settings.get("api_endpoints")
        settings["api_endpoints"] = {
            key: str(endpoint).strip()
            for key, endpoint in (
                api_endpoints.items() if isinstance(api_endpoints, dict) else ()
            )
            if key in API_PROVIDERS and str(endpoint).strip()
        }
        endpoint = settings.get("custom_api_endpoint")
        settings["custom_api_endpoint"] = endpoint.strip() if isinstance(endpoint, str) else ""
        if settings["custom_api_endpoint"] and "custom" not in settings["api_endpoints"]:
            settings["api_endpoints"]["custom"] = settings["custom_api_endpoint"]
        settings["sampling"] = normalize_sampling(settings.get("sampling"))
        settings["backend"] = settings.get("backend") if settings.get("backend") in {"api", "local"} else "api"
        settings["local_runtime"] = (
            settings.get("local_runtime")
            if settings.get("local_runtime") in {"huggingface", "lmstudio", "llamacpp"}
            else "huggingface"
        )
        lmstudio_base_url = settings.get("lmstudio_base_url")
        settings["lmstudio_base_url"] = (
            lmstudio_base_url.strip()
            if isinstance(lmstudio_base_url, str) and lmstudio_base_url.strip()
            else DEFAULT_SETTINGS["lmstudio_base_url"]
        )
        lmstudio_model = settings.get("lmstudio_model")
        settings["lmstudio_model"] = (
            lmstudio_model.strip() if isinstance(lmstudio_model, str) else ""
        )
        settings["lmstudio_load_profile"] = normalize_lmstudio_load_profile(
            settings.get("lmstudio_load_profile")
        )
        for key in (
            "llama_server_path", "llama_model_path", "llama_mmproj_path",
            "llama_model_alias",
        ):
            value = settings.get(key)
            settings[key] = value.strip() if isinstance(value, str) else ""
        settings["llama_context_length"] = bounded_int(
            settings.get("llama_context_length"),
            LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
            512,
            131072,
        )
        settings["llama_gpu_layers"] = bounded_int(
            settings.get("llama_gpu_layers"),
            LLAMA_CPP_DEFAULT_GPU_LAYERS,
            -1,
            999,
        )
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
        settings["theme"] = (
            settings.get("theme")
            if settings.get("theme") in {"night", "day"}
            else "night"
        )
        settings["auto_check_updates"] = bounded_bool(
            settings.get("auto_check_updates"), True
        )
        settings["video_preflight"] = bounded_bool(
            settings.get("video_preflight"), True
        )
        settings["enable_mtp"] = bounded_bool(
            settings.get("enable_mtp"), False
        )
        settings["remove_thinking_tags"] = bounded_bool(
            settings.get("remove_thinking_tags"), True
        )
        plaintext_key = source.get("api_key")
        if plaintext_key and not self.secret_store.get():
            self.secret_store.set(str(plaintext_key))
            try:
                self._remove_legacy_api_key()
            except OSError:
                pass
        settings.pop("api_key", None)
        settings.pop("chat_url", None)
        settings.pop("model", None)
        settings.pop("model_id", None)
        settings.pop("suppress_seed_2_0_shutdown_notice", None)
        if source and (
            not stored
            or any(
                key in source
                for key in (
                    "api_key",
                    "chat_url",
                    "model",
                    "suppress_seed_2_0_shutdown_notice",
                )
            )
        ):
            self.save(settings)
        return settings

    def save(self, settings: dict[str, Any]) -> None:
        cleaned = {**DEFAULT_SETTINGS, **settings}
        for key in (
            "api_key",
            "chat_url",
            "model",
            "model_id",
            "suppress_seed_2_0_shutdown_notice",
        ):
            cleaned.pop(key, None)
        cleaned["version"] = DEFAULT_SETTINGS["version"]
        cleaned["theme"] = (
            cleaned.get("theme")
            if cleaned.get("theme") in {"night", "day"}
            else "night"
        )
        cleaned["auto_check_updates"] = bounded_bool(
            cleaned.get("auto_check_updates"), True
        )
        cleaned["video_preflight"] = bounded_bool(
            cleaned.get("video_preflight"), True
        )
        cleaned["concurrency"] = bounded_int(
            cleaned.get("concurrency", 3), 3, 1, MAX_CONCURRENCY
        )
        cleaned["local_runtime"] = (
            cleaned.get("local_runtime")
            if cleaned.get("local_runtime") in {"huggingface", "lmstudio", "llamacpp"}
            else "huggingface"
        )
        lmstudio_base_url = cleaned.get("lmstudio_base_url")
        cleaned["lmstudio_base_url"] = (
            lmstudio_base_url.strip()
            if isinstance(lmstudio_base_url, str) and lmstudio_base_url.strip()
            else DEFAULT_SETTINGS["lmstudio_base_url"]
        )
        lmstudio_model = cleaned.get("lmstudio_model")
        cleaned["lmstudio_model"] = (
            lmstudio_model.strip() if isinstance(lmstudio_model, str) else ""
        )
        cleaned["lmstudio_load_profile"] = normalize_lmstudio_load_profile(
            cleaned.get("lmstudio_load_profile")
        )
        for key in (
            "llama_server_path", "llama_model_path", "llama_mmproj_path",
            "llama_model_alias",
        ):
            value = cleaned.get(key)
            cleaned[key] = value.strip() if isinstance(value, str) else ""
        cleaned["llama_context_length"] = bounded_int(
            cleaned.get("llama_context_length"),
            LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
            512,
            131072,
        )
        cleaned["llama_gpu_layers"] = bounded_int(
            cleaned.get("llama_gpu_layers"),
            LLAMA_CPP_DEFAULT_GPU_LAYERS,
            -1,
            999,
        )
        provider_key = str(cleaned.get("provider_key", DEFAULT_PROVIDER_KEY))
        cleaned["provider_key"] = (
            provider_key if provider_key in API_PROVIDERS else DEFAULT_PROVIDER_KEY
        )
        api_models = cleaned.get("api_models")
        cleaned["api_models"] = {
            key: str(model).strip()
            for key, model in (api_models.items() if isinstance(api_models, dict) else ())
            if key in API_PROVIDERS and str(model).strip()
        }
        api_endpoints = cleaned.get("api_endpoints")
        cleaned["api_endpoints"] = {
            key: str(endpoint).strip()
            for key, endpoint in (
                api_endpoints.items() if isinstance(api_endpoints, dict) else ()
            )
            if key in API_PROVIDERS and str(endpoint).strip()
        }
        endpoint = cleaned.get("custom_api_endpoint")
        cleaned["custom_api_endpoint"] = endpoint.strip() if isinstance(endpoint, str) else ""
        if cleaned["custom_api_endpoint"]:
            cleaned["api_endpoints"]["custom"] = cleaned["custom_api_endpoint"]
        cleaned["sampling"] = normalize_sampling(cleaned.get("sampling"))
        user_prompt = cleaned.get("user_prompt")
        cleaned["user_prompt"] = user_prompt if isinstance(user_prompt, str) else ""
        atomic_write_json(self.settings_path, cleaned)

    def _remove_legacy_api_key(self) -> None:
        if not self.legacy_path.exists():
            return
        legacy = load_json(self.legacy_path, {})
        if not isinstance(legacy, dict) or "api_key" not in legacy:
            return
        legacy.pop("api_key", None)
        if legacy:
            atomic_write_json(self.legacy_path, legacy)
        else:
            self.legacy_path.unlink(missing_ok=True)

    def get_api_key(self, provider_key: str = DEFAULT_PROVIDER_KEY) -> str:
        normalized = provider_key if provider_key in API_PROVIDERS else DEFAULT_PROVIDER_KEY
        store = self._provider_secret_store(normalized)
        if store is not None:
            return store.get()
        return self._volatile_provider_secrets.get(normalized, "")

    def set_api_key(self, value: str, provider_key: str = DEFAULT_PROVIDER_KEY) -> None:
        normalized = value.strip()
        normalized_provider = (
            provider_key if provider_key in API_PROVIDERS else DEFAULT_PROVIDER_KEY
        )
        store = self._provider_secret_store(normalized_provider)
        if store is not None:
            store.set(normalized)
        elif normalized:
            self._volatile_provider_secrets[normalized_provider] = normalized
        else:
            self._volatile_provider_secrets.pop(normalized_provider, None)
        if not normalized and normalized_provider == DEFAULT_PROVIDER_KEY:
            self._remove_legacy_api_key()


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


def count_output_characters(text: str) -> int:
    """Count visible output characters without inflating metrics with whitespace."""
    return sum(1 for character in text if not character.isspace())


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
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict)
            and str(item.get("type", "text")).casefold()
            not in {"reasoning", "analysis", "thinking"}
        ).strip()
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


_THINKING_NAMES = r"think|thinking|analysis|reasoning"
_THINKING_BLOCK_PATTERN = re.compile(
    rf"<\s*({_THINKING_NAMES})\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_THINKING_CLOSE_PATTERN = re.compile(
    rf"<\s*/\s*(?:{_THINKING_NAMES})\s*>",
    re.IGNORECASE,
)
_THINKING_TAG_PATTERN = re.compile(
    rf"<\s*/?\s*(?:{_THINKING_NAMES})\b[^>]*>",
    re.IGNORECASE,
)
_THINKING_SQUARE_BLOCK_PATTERN = re.compile(
    rf"\[\s*({_THINKING_NAMES})\s*].*?\[\s*/\s*\1\s*]",
    re.IGNORECASE | re.DOTALL,
)
_THINKING_SQUARE_TAG_PATTERN = re.compile(
    rf"\[\s*/?\s*(?:{_THINKING_NAMES})\s*]",
    re.IGNORECASE,
)


def strip_thinking_sections(value: Any) -> str:
    """Remove common reasoning blocks without touching the final caption."""
    text = str(value or "").strip()
    if not text:
        return ""
    for _index in range(4):
        cleaned = _THINKING_BLOCK_PATTERN.sub("", text)
        cleaned = _THINKING_SQUARE_BLOCK_PATTERN.sub("", cleaned)
        if cleaned == text:
            break
        text = cleaned
    # Some compatible endpoints omit the opening tag but retain </think>.
    closing_tags = list(_THINKING_CLOSE_PATTERN.finditer(text))
    if closing_tags and not _THINKING_TAG_PATTERN.match(text.lstrip()):
        text = text[closing_tags[-1].end() :]
    text = _THINKING_TAG_PATTERN.sub("", text)
    text = _THINKING_SQUARE_TAG_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def custom_chat_url(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        return ""
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return f"{endpoint}/chat/completions"


class CaptionClient:
    def __init__(
        self,
        model: ModelOption,
        api_key: str,
        token: CancellationToken,
        transport: HttpTransport | None = None,
        provider_key: str = DEFAULT_PROVIDER_KEY,
        api_model: str = "",
        api_endpoint: str = "",
        sampling: dict[str, Any] | None = None,
        remove_thinking_tags: bool = True,
    ):
        self.model = model
        self.api_key = api_key
        self.token = token
        self.transport = transport or HttpTransport()
        self.provider = API_PROVIDERS.get(
            provider_key, API_PROVIDERS[DEFAULT_PROVIDER_KEY]
        )
        self.api_model = api_model.strip() or (
            model.model_id
            if self.provider.key == DEFAULT_PROVIDER_KEY
            else self.provider.default_model
        )
        self.api_endpoint = api_endpoint.strip()
        self.sampling = normalize_sampling(sampling)
        self.remove_thinking_tags = bool(remove_thinking_tags)

    def _clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        return strip_thinking_sections(text) if self.remove_thinking_tags else text

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _billable_post(self, url: str, payload: dict[str, Any], timeout=(10, 180)) -> dict[str, Any]:
        # A timed-out generation may already have been billed. Do not retry it automatically.
        def send_once() -> dict[str, Any]:
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

        request_lock = getattr(self, "_request_lock", None)
        if request_lock is None:
            return send_once()
        # A single local server/model may expose only one context slot.  Keep
        # preprocessing concurrent, but serialize billable generations so a
        # user-selected worker count cannot corrupt a local KV cache.
        with request_lock:
            return send_once()

    def _chat_url(self) -> str:
        if self.api_endpoint:
            return custom_chat_url(self.api_endpoint)
        if self.provider.key == DEFAULT_PROVIDER_KEY:
            return self.model.chat_url_for()
        if self.provider.allows_custom_endpoint:
            endpoint = custom_chat_url(self.api_endpoint)
            if not endpoint:
                raise ValueError("请填写自定义 Chat Completions 地址")
            return endpoint
        return self.provider.chat_url

    def _sampling_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in self.provider.supported_parameters:
            value = self.sampling.get(key)
            if key == "seed" and value is None:
                continue
            if key == "top_k" and not value:
                continue
            target_key = (
                "max_completion_tokens"
                if self.provider.key == "openai"
                and self.api_model.startswith("gpt-5")
                and key == "max_tokens"
                else key
            )
            payload[target_key] = value
        if self.provider.key == "openai" and self.api_model.startswith("gpt-5"):
            payload["reasoning_effort"] = "none"
        if self.remove_thinking_tags and self.provider.key == "qwen":
            payload["enable_thinking"] = False
        return payload

    def _responses_sampling_payload(self) -> dict[str, Any]:
        payload = {"max_output_tokens": self.sampling["max_tokens"]}
        for key in ("temperature", "top_p"):
            if key in self.provider.supported_parameters:
                payload[key] = self.sampling[key]
        return payload

    def caption_image(self, path: Path, prompt: str) -> str:
        prepared = prepare_image(path)
        encoded = base64.b64encode(prepared.data).decode("ascii")
        payload = {
            "model": self.api_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{prepared.mime_type};base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        payload.update(self._sampling_payload())
        text = self._clean_text(
            _chat_text(self._billable_post(self._chat_url(), payload))
        )
        if not text:
            raise ApiError("模型返回了空结果")
        return text

    def caption_video(self, path: Path, prompt: str) -> str:
        if not self.provider.supports_video:
            raise ValueError(f"{self.provider.label} 当前未启用视频输入，请切换火山引擎或兼容视频的平台")
        size = path.stat().st_size
        if size > VIDEO_UPLOAD_LIMIT:
            raise ValueError("视频大于 512 MB，无法上传")
        if size <= VIDEO_CHAT_LIMIT:
            return self._caption_small_video(path, prompt)
        if self.provider.key != DEFAULT_PROVIDER_KEY:
            raise ValueError("当前平台仅支持 20 MB 以内的视频直传；大视频请切换火山引擎")
        return self._caption_uploaded_video(path, prompt)

    def _caption_small_video(self, path: Path, prompt: str) -> str:
        self.token.check()
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        self.token.check()
        payload = {
            "model": self.api_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": f"data:{video_mime_type(path)};base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        payload.update(self._sampling_payload())
        text = self._clean_text(
            _chat_text(
                self._billable_post(self._chat_url(), payload, timeout=(10, 240))
            )
        )
        if not text:
            raise ApiError("模型返回了空结果")
        return text

    def _caption_uploaded_video(self, path: Path, prompt: str) -> str:
        file_id = self._upload_video(path)
        try:
            self._wait_for_file(file_id)
            payload = {
                "model": self.api_model,
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_video", "file_id": file_id},
                        {"type": "input_text", "text": prompt},
                    ],
                }],
            }
            payload.update(self._responses_sampling_payload())
            text = _responses_text(
                self._billable_post(RESPONSES_URL, payload, timeout=(10, 240))
            )
            text = self._clean_text(text)
            if not text:
                raise ApiError("模型返回了空结果")
            return text
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


class LmStudioCaptionClient(CaptionClient):
    """Image caption client for an LM Studio OpenAI-compatible local server."""

    def __init__(
        self,
        base_url: str,
        model_id: str,
        token: CancellationToken,
        transport: HttpTransport | None = None,
        *,
        sampling: dict[str, Any] | None = None,
        remove_thinking_tags: bool = True,
    ):
        endpoint = str(base_url or "").strip()
        selected_model = str(model_id or "").strip()
        if not endpoint:
            raise ValueError("请填写 LM Studio Base URL")
        if not endpoint.casefold().startswith(("http://", "https://")):
            raise ValueError("LM Studio Base URL 必须以 http:// 或 https:// 开头")
        if not selected_model:
            raise ValueError("请选择或填写 LM Studio 模型 ID")
        self._request_lock = threading.RLock()
        super().__init__(
            MODELS[DEFAULT_MODEL_KEY],
            "",
            token,
            transport,
            provider_key="custom",
            api_model=selected_model,
            api_endpoint=endpoint,
            sampling=sampling,
            remove_thinking_tags=remove_thinking_tags,
        )

    def _sampling_payload(self) -> dict[str, Any]:
        # Keep LM Studio's sampling configuration as the source of truth. A
        # caption-specific output ceiling is still required because the
        # OpenAI-compatible endpoint does not inherit the Chat UI's preset and
        # some models otherwise generate indefinitely without an EOS token.
        payload: dict[str, Any] = {
            "max_tokens": (
                LMSTUDIO_CAPTION_TOKEN_LIMIT
                if self.remove_thinking_tags
                else LMSTUDIO_REASONING_TOKEN_LIMIT
            )
        }
        if self.remove_thinking_tags:
            # LM Studio exposes model reasoning independently from message
            # content. This disables it before generation instead of merely
            # deleting <think> blocks after the output budget is consumed.
            payload["reasoning_effort"] = "none"
        return payload

    @staticmethod
    def _translate_request_error(error: ApiError) -> None:
        detail = f"{error} {error.body}".casefold()
        if "terminated" in detail or "channel error" in detail:
            raise RuntimeError(
                "LM Studio 模型进程在处理图片时退出。通常是 GPU Offload、上下文或并行槽位"
                "占用过高导致显存不足；请卸载模型后改用低显存安全或纯 CPU 策略重新加载。"
            ) from error
        if _is_memory_failure(detail):
            raise RuntimeError(
                "LM Studio 推理显存或内存不足。请降低 GPU Offload、上下文与并行数，"
                "并关闭占用显存的软件后重试。"
            ) from error
        if any(marker in detail for marker in (
            "connection refused",
            "actively refused",
            "failed to establish a new connection",
            "无法连接",
        )):
            raise RuntimeError(
                "无法连接 LM Studio 本地服务器。请先启动 LM Studio Server，再刷新模型列表。"
            ) from error
        raise error

    def _billable_post(
        self,
        url: str,
        payload: dict[str, Any],
        timeout=(10, LMSTUDIO_READ_TIMEOUT),
    ) -> dict[str, Any]:
        try:
            return super()._billable_post(url, payload, timeout=timeout)
        except ApiError as error:
            detail = f"{error} {error.body}".casefold()
            unsupported_reasoning = (
                error.status == 400
                and "reasoning_effort" in payload
                and "reasoning" in detail
                and any(marker in detail for marker in (
                    "unknown", "unsupported", "unrecognized", "extra", "invalid"
                ))
            )
            if unsupported_reasoning:
                fallback = dict(payload)
                fallback.pop("reasoning_effort", None)
                try:
                    return super()._billable_post(url, fallback, timeout=timeout)
                except ApiError as fallback_error:
                    self._translate_request_error(fallback_error)
            self._translate_request_error(error)
        raise RuntimeError("LM Studio 请求失败")

    @staticmethod
    def _degenerate_output(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if len(compact) < 16:
            return False
        invalid = compact.count("?") + compact.count("�")
        if invalid / len(compact) >= 0.5:
            return True
        frequencies: dict[str, int] = {}
        for character in compact:
            frequencies[character] = frequencies.get(character, 0) + 1
        return max(frequencies.values(), default=0) / len(compact) >= 0.9

    def caption_image(self, path: Path, prompt: str) -> str:
        prepared = prepare_image(
            path,
            size_limit=256 * 1024,
            max_dimension=LMSTUDIO_IMAGE_MAX_DIMENSION,
        )
        encoded = base64.b64encode(prepared.data).decode("ascii")
        payload = {
            "model": self.api_model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{prepared.mime_type};base64,{encoded}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        payload.update(self._sampling_payload())
        response = self._billable_post(self._chat_url(), payload)
        choices = response.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        message = message if isinstance(message, dict) else {}
        reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        ).strip()
        text = self._clean_text(_chat_text(response))
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        details = (
            usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), dict)
            else {}
        )
        reasoning_tokens = bounded_int(
            details.get("reasoning_tokens"), 0, 0, 10_000_000
        )
        finish_reason = str(choice.get("finish_reason") or "").casefold()
        if finish_reason == "length":
            if reasoning_tokens or reasoning:
                raise RuntimeError(
                    "LM Studio 的输出预算被思考内容占用，正文未完整生成。"
                    "请开启“移除思考标签”后重试；工作台会在请求阶段关闭思考。"
                )
            raise RuntimeError(
                "LM Studio 输出达到安全上限，结果可能不完整，因此没有写入 TXT。"
            )
        if not text:
            if reasoning_tokens or reasoning:
                raise RuntimeError(
                    "LM Studio 只返回了思考内容，没有生成最终标注。"
                    "请开启“移除思考标签”后重试。"
                )
            raise ApiError("LM Studio 模型返回了空结果")
        if not self.remove_thinking_tags and reasoning:
            text = f"<think>{reasoning}</think>\n{text}"
        if self._degenerate_output(text):
            raise RuntimeError(
                "LM Studio 模型返回了连续重复的异常字符。请求格式和图片读取正常，"
                "请先在 LM Studio 中用纯文本测试，并恢复默认或已验证的加载与采样参数；"
                "若仍异常，再检查模型、Tokenizer、量化文件及配套 mmproj 的兼容性。"
            )
        return text


def _find_free_local_port() -> int:
    """Reserve a short-lived localhost port for an owned llama.cpp server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LlamaCppCaptionClient(CaptionClient):
    """Run a local GGUF vision model through an owned llama.cpp server."""

    def __init__(
        self,
        server_path: str | Path,
        model_path: str | Path,
        mmproj_path: str | Path,
        token: CancellationToken,
        transport: HttpTransport | None = None,
        *,
        model_alias: str = "",
        context_length: int = LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
        gpu_layers: int = LLAMA_CPP_DEFAULT_GPU_LAYERS,
        sampling: dict[str, Any] | None = None,
        remove_thinking_tags: bool = True,
    ):
        self.server_path = Path(server_path).expanduser()
        self.model_path = Path(model_path).expanduser()
        self.mmproj_path = Path(mmproj_path).expanduser()
        self.model_alias = str(model_alias or self.model_path.stem).strip()
        self.context_length = bounded_int(
            context_length,
            LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
            512,
            131072,
        )
        self.gpu_layers = bounded_int(
            gpu_layers,
            LLAMA_CPP_DEFAULT_GPU_LAYERS,
            -1,
            999,
        )
        self._server_process: subprocess.Popen | None = None
        self._server_log_handle = None
        self._server_log_path: Path | None = None
        self._server_base_url = ""
        self._request_lock = threading.RLock()
        super().__init__(
            MODELS[DEFAULT_MODEL_KEY],
            "",
            token,
            transport,
            provider_key="custom",
            api_model=self.model_alias,
            api_endpoint="",
            sampling=sampling,
            remove_thinking_tags=remove_thinking_tags,
        )
        try:
            self._validate_files()
            self._start_server()
            self.api_endpoint = self._server_base_url
        except Exception:
            self.close()
            raise

    def _validate_files(self) -> None:
        if not self.server_path.is_file():
            raise ValueError(
                "未找到 llama-server 可执行文件，请在平台设置中选择 llama-server.exe"
            )
        if not self.model_path.is_file():
            raise ValueError("GGUF 主模型文件不存在，请重新选择")
        if self.model_path.suffix.casefold() != ".gguf":
            raise ValueError("GGUF 主模型必须是 .gguf 文件")
        if not self.mmproj_path.is_file():
            raise ValueError("mmproj 视觉投影文件不存在，请重新选择")
        if self.mmproj_path.suffix.casefold() != ".gguf":
            raise ValueError("mmproj 文件必须是 .gguf 文件")

    def _server_command(self, port: int) -> list[str]:
        command = [
            str(self.server_path),
            "-m", str(self.model_path),
            "--mmproj", str(self.mmproj_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "-c", str(self.context_length),
        ]
        if self.model_alias:
            command.extend(("--alias", self.model_alias))
        if self.gpu_layers >= 0:
            command.extend(("-ngl", str(self.gpu_layers)))
        return command

    def _read_server_log_tail(self) -> str:
        path = self._server_log_path
        if path is None or not path.is_file():
            return ""
        try:
            handle = self._server_log_handle
            if handle is not None:
                handle.flush()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        text = text.strip()
        return text[-1200:] if text else ""

    def _start_server(self) -> None:
        port = _find_free_local_port()
        self._server_base_url = f"http://127.0.0.1:{port}/v1"
        log_dir = app_data_dir() / "logs" / "llama-cpp"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._server_log_path = log_dir / f"llama-{port}.log"
        self._server_log_handle = self._server_log_path.open(
            "a", encoding="utf-8", errors="replace"
        )
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        try:
            self._server_process = subprocess.Popen(
                self._server_command(port),
                stdin=subprocess.DEVNULL,
                stdout=self._server_log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                close_fds=True,
            )
        except OSError as error:
            raise RuntimeError(f"无法启动 llama-server：{error}") from error

        health_url = self._server_base_url.removesuffix("/v1") + "/health"
        deadline = time.monotonic() + LLAMA_CPP_START_TIMEOUT
        last_error = ""
        while time.monotonic() < deadline:
            self.token.check()
            process = self._server_process
            if process is not None and process.poll() is not None:
                detail = self._read_server_log_tail()
                if detail:
                    raise RuntimeError(
                        "llama.cpp 启动失败，服务器已退出：\n" + detail
                    )
                raise RuntimeError(
                    f"llama.cpp 启动失败，退出码 {process.returncode}"
                )
            try:
                response = requests.get(health_url, timeout=(1.0, 2.0))
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as error:
                last_error = str(error)
            self.token.wait(0.25)
        detail = self._read_server_log_tail()
        message = "llama.cpp 启动超时，请检查模型、mmproj、显存和 llama-server 版本"
        if last_error:
            message += f"（最近状态：{last_error}）"
        if detail:
            message += "\n\n服务器日志：\n" + detail
        raise RuntimeError(message)

    def _billable_post(
        self,
        url: str,
        payload: dict[str, Any],
        timeout=(10, LLAMA_CPP_READ_TIMEOUT),
    ) -> dict[str, Any]:
        try:
            return super()._billable_post(url, payload, timeout=timeout)
        except ApiError as error:
            detail = f"{error} {error.body}".casefold()
            unsupported_markers = any(marker in detail for marker in (
                "unknown", "unknown field", "unsupported", "unrecognized",
                "unexpected", "additional properties", "extra", "invalid",
            ))
            unsupported_fields = (
                error.status in {400, 422, 500, 501}
                and unsupported_markers
                and any(
                    field in payload
                    for field in (
                        "reasoning_effort",
                        "frequency_penalty",
                        "presence_penalty",
                    )
                )
            )
            if unsupported_fields:
                fallback = dict(payload)
                fallback.pop("reasoning_effort", None)
                fallback.pop("frequency_penalty", None)
                fallback.pop("presence_penalty", None)
                try:
                    return super()._billable_post(url, fallback, timeout=timeout)
                except ApiError as fallback_error:
                    error = fallback_error
                    detail = f"{error} {error.body}".casefold()
            if _is_memory_failure(detail):
                raise RuntimeError(
                    "llama.cpp 推理显存或内存不足，请降低 GPU 层数、上下文长度或并发数"
                ) from error
            if any(marker in detail for marker in (
                "connection refused", "actively refused", "failed to establish",
            )):
                raise RuntimeError(
                    "llama.cpp 本地服务已退出或无法连接，请检查服务器日志"
                ) from error
            server_log = self._read_server_log_tail()
            if server_log:
                raise RuntimeError(
                    "llama.cpp 请求失败。请确认主 GGUF 与 mmproj 属于同一视觉模型，"
                    "并检查以下服务器日志：\n\n" + server_log
                ) from error
            raise

    def _sampling_payload(self) -> dict[str, Any]:
        # Send the complete local sampling state first so compatible builds
        # receive every user choice.  _billable_post has a compatibility
        # fallback that removes hosted-only fields when a stricter llama-server
        # rejects them; thinking cleanup is handled on returned text.
        payload: dict[str, Any] = {
            "max_tokens": self.sampling["max_tokens"],
            "temperature": self.sampling["temperature"],
            "top_p": self.sampling["top_p"],
            "frequency_penalty": self.sampling["frequency_penalty"],
            "presence_penalty": self.sampling["presence_penalty"],
        }
        if self.sampling["top_k"]:
            payload["top_k"] = self.sampling["top_k"]
        if self.sampling["seed"] is not None:
            payload["seed"] = self.sampling["seed"]
        # Qwen-VL chat templates used by current llama.cpp builds accept
        # xhigh/medium/low, not OpenAI's none/default values.  ``low`` keeps
        # the remove-thinking switch useful while avoiding the 500 Jinja
        # exception that previously made every request look like a model
        # failure.  Older templates are handled by the compatibility retry.
        payload["reasoning_effort"] = (
            "low" if self.remove_thinking_tags else "xhigh"
        )
        return payload

    def caption_image(self, path: Path, prompt: str) -> str:
        # Vision GGUF models can consume a large number of image tokens.  The
        # generic API path's 2K/80-KB image policy is too aggressive for a
        # 4K-context llama-server and often leaves no room for the caption.
        prepared = prepare_image(
            path,
            size_limit=LLAMA_CPP_IMAGE_SIZE_LIMIT,
            max_dimension=LLAMA_CPP_IMAGE_MAX_DIMENSION,
        )
        encoded = base64.b64encode(prepared.data).decode("ascii")
        payload = {
            "model": self.api_model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{prepared.mime_type};base64,{encoded}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        payload.update(self._sampling_payload())
        response = self._billable_post(self._chat_url(), payload)
        text = self._clean_text(_chat_text(response))
        if not text:
            raise ApiError("llama.cpp 模型返回了空结果")
        return text

    def close(self) -> None:
        process = self._server_process
        self._server_process = None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        handle = self._server_log_handle
        self._server_log_handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


class LocalCaptionClient:
    """Optional Hugging Face vision-language backend loaded from a local folder."""

    def __init__(
        self,
        model_folder: Path,
        token: CancellationToken,
        *,
        enable_mtp: bool = False,
        remove_thinking_tags: bool = True,
    ):
        self.model_folder = Path(model_folder)
        self.token = token
        self.processor = None
        self.model = None
        self.torch = None
        self.device = "cpu"
        self.sampling = normalize_sampling(None)
        self.enable_mtp = bool(enable_mtp)
        self.mtp_active = False
        self.remove_thinking_tags = bool(remove_thinking_tags)
        self._load_error: RuntimeError | None = None
        self._load_lock = threading.Lock()
        self._generation_lock = threading.Lock()
        if not self.model_folder.is_dir():
            raise ValueError("本地模型目录不存在")
        if not (self.model_folder / "config.json").is_file():
            raise ValueError("本地模型目录缺少 config.json，不是 Hugging Face 模型目录")

    def _supports_native_mtp(self) -> bool:
        config = getattr(self.model, "config", None)
        generation_config = getattr(self.model, "generation_config", None)
        mtp_layers = 0
        for key in (
            "num_nextn_predict_layers",
            "num_mtp_layers",
            "mtp_num_hidden_layers",
        ):
            try:
                mtp_layers = max(
                    mtp_layers,
                    int(getattr(config, key, 0) or 0),
                )
            except (TypeError, ValueError):
                continue
        return bool(
            mtp_layers > 0
            and generation_config is not None
            and hasattr(generation_config, "use_mtp")
        )

    def _ensure_loaded(self) -> None:
        if self.model is not None and self.processor is not None:
            return
        if self._load_error is not None:
            raise self._load_error
        with self._load_lock:
            if self.model is not None and self.processor is not None:
                return
            if self._load_error is not None:
                raise self._load_error
            self._load_model()

    def _load_model(self) -> None:
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
            use_cuda = bool(torch.cuda.is_available())
            self.processor = AutoProcessor.from_pretrained(
                self.model_folder,
                local_files_only=True,
                trust_remote_code=True,
            )
            load_options: dict[str, Any] = {
                "local_files_only": True,
                "trust_remote_code": True,
                "torch_dtype": "auto",
                "low_cpu_mem_usage": True,
            }
            if use_cuda:
                # Let Accelerate distribute oversized models instead of first
                # materializing the whole checkpoint in RAM and then moving it
                # wholesale to an 8 GB-class GPU.
                load_options["device_map"] = "auto"
            try:
                self.model = model_class.from_pretrained(
                    self.model_folder,
                    **load_options,
                )
            except TypeError:
                # Older remote-code loaders may not accept low_cpu_mem_usage
                # even when the installed Transformers version does.
                load_options.pop("low_cpu_mem_usage", None)
                try:
                    self.model = model_class.from_pretrained(
                        self.model_folder,
                        **load_options,
                    )
                except TypeError:
                    load_options.pop("device_map", None)
                    self.model = model_class.from_pretrained(
                        self.model_folder,
                        **load_options,
                    )
                    if use_cuda:
                        self.model.to("cuda")
            self.device = "cuda" if use_cuda else "cpu"
            model_device = getattr(self.model, "device", None)
            if model_device is not None and str(model_device) != "meta":
                self.device = model_device
            self.model.eval()
            self.torch = torch
            self.mtp_active = bool(
                self.enable_mtp and self._supports_native_mtp()
            )
        except Exception as error:
            self.processor = None
            self.model = None
            if bool(getattr(torch, "cuda", None)) and torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            if _is_memory_failure(error):
                self._load_error = RuntimeError(
                    "本地视觉模型加载失败：显存或内存不足。请关闭占用显存的软件、"
                    "降低并发，或改用 LM Studio 的低显存/纯 CPU 加载策略。"
                )
            else:
                self._load_error = RuntimeError(f"本地视觉模型加载失败：{error}")
            raise self._load_error from error
        self.token.check()

    def close(self) -> None:
        model = self.model
        self.model = None
        self.processor = None
        torch = self.torch
        self.torch = None
        del model
        gc.collect()
        if torch is not None and bool(getattr(torch, "cuda", None)):
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

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
                template_options = {
                    "tokenize": False,
                    "add_generation_prompt": True,
                    "enable_thinking": not self.remove_thinking_tags,
                }
                try:
                    rendered_prompt = self.processor.apply_chat_template(
                        messages,
                        **template_options,
                    )
                except TypeError:
                    template_options.pop("enable_thinking", None)
                    rendered_prompt = self.processor.apply_chat_template(
                        messages,
                        **template_options,
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
        generation = {
            "max_new_tokens": self.sampling["max_tokens"],
            "do_sample": self.sampling["temperature"] > 0,
        }
        if self.mtp_active:
            generation["use_mtp"] = True
        if generation["do_sample"]:
            generation.update({
                "temperature": max(0.01, self.sampling["temperature"]),
                "top_p": self.sampling["top_p"],
            })
            if self.sampling["top_k"]:
                generation["top_k"] = self.sampling["top_k"]
        try:
            # Most Transformers VLM implementations are not safe to call from
            # several Python threads on one model instance. Serializing only
            # generate() still allows file decoding and preprocessing to run
            # concurrently without multiplying KV-cache allocations.
            with self._generation_lock:
                self.token.check()
                if self.sampling["seed"] is not None:
                    self.torch.manual_seed(self.sampling["seed"])
                with self.torch.inference_mode():
                    try:
                        generated = self.model.generate(
                            **inputs,
                            **generation,
                        )
                    except (TypeError, ValueError, NotImplementedError):
                        if not self.mtp_active:
                            raise
                        # Model/config combinations can advertise MTP before
                        # their custom generation implementation supports it.
                        self.mtp_active = False
                        generation.pop("use_mtp", None)
                        generated = self.model.generate(
                            **inputs,
                            **generation,
                        )
        except Exception as error:
            if _is_memory_failure(error):
                try:
                    if self.torch.cuda.is_available():
                        self.torch.cuda.empty_cache()
                except Exception:
                    pass
                raise RuntimeError(
                    "本地模型推理显存不足。请降低并发或输出长度，关闭其他显存任务后重试。"
                ) from error
            raise
        self.token.check()
        input_ids = inputs.get("input_ids")
        is_encoder_decoder = bool(
            getattr(getattr(self.model, "config", None), "is_encoder_decoder", False)
        )
        if (
            not is_encoder_decoder
            and input_ids is not None
            and generated.shape[-1] > input_ids.shape[-1]
        ):
            generated = generated[:, input_ids.shape[-1] :]
        text = self.processor.batch_decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        if self.remove_thinking_tags:
            text = strip_thinking_sections(text)
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


def process_is_running(pid: Any) -> bool:
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        open_process = ctypes.windll.kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        close_handle = ctypes.windll.kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(
            process_query_limited_information, False, process_id
        )
        if not handle:
            return False
        close_handle(handle)
        return True
    try:
        os.kill(process_id, 0)
    except (OSError, PermissionError):
        return False
    return True


def project_status_from_state(state: Any) -> str:
    if not isinstance(state, dict):
        return "new"
    status = str(state.get("status") or "new")
    if status == "running" and not process_is_running(state.get("owner_pid")):
        return "interrupted"
    return status


def load_incomplete_paths(
    folder: Path,
    mode: str,
    data_root: Path | None = None,
) -> list[Path]:
    state = load_json(project_data_path(folder, data_root) / "state.json", {})
    if not isinstance(state, dict) or not isinstance(state.get("items"), dict):
        return []
    extensions = IMAGE_EXTENSIONS if mode == "image" else VIDEO_EXTENSIONS
    result: list[Path] = []
    for relative, event in state["items"].items():
        if not isinstance(event, dict) or event.get("status") not in {
            "failed", "cancelled", "running", "pending",
        }:
            continue
        path = Path(relative)
        path = path if path.is_absolute() else folder / path
        if (
            path.is_file()
            and path.suffix.casefold() in extensions
            and not has_usable_caption(path)
        ):
            result.append(path)
    return sorted(set(result))


def load_project_summary(folder: Path, data_root: Path | None = None) -> dict[str, Any]:
    state = load_json(project_data_path(folder, data_root) / "state.json", {})
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}

    def count(name: str) -> int:
        try:
            return max(0, int(summary.get(name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "folder": str(folder),
        "name": folder.name or str(folder),
        "exists": folder.is_dir(),
        "status": project_status_from_state(state),
        "updated_at": str(state.get("finished_at") or state.get("updated_at") or ""),
        "total": count("total"),
        "success": count("success"),
        "skipped": count("skipped"),
        "failed": count("failed"),
        "mode": str(metadata.get("mode") or "image"),
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
        previous = load_json(self.state_path, {})
        previous_run_id = ""
        previous_status = ""
        if isinstance(previous, dict) and previous:
            previous_run_id = str(previous.get("run_id") or "")
            previous_status = project_status_from_state(previous)
            history_dir = self.project_dir / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            archive_name = previous_run_id or _timestamp_slug()
            atomic_write_json(history_dir / f"state-{archive_name}.json", previous)
            history = sorted(
                history_dir.glob("state-*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for expired in history[30:]:
                expired.unlink(missing_ok=True)
        self.state = {
            "folder": str(self.folder), "run_id": self.run_id,
            "status": "running", "started_at": datetime.now().isoformat(timespec="seconds"),
            "owner_pid": os.getpid(),
            "metadata": metadata, "items": {},
        }
        if previous_run_id:
            self.state["previous_run"] = {
                "run_id": previous_run_id,
                "status": previous_status,
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
    characters: int = 0
    elapsed_seconds: float = 0.0
    failures: list[tuple[Path, str]] = field(default_factory=list)


class BatchRunner:
    def __init__(
        self,
        event_callback: Callable[[str, dict[str, Any]], None],
        transport: HttpTransport | None = None,
        media_worker_factory: Callable[[Iterable[Path]], MediaWorkerController] | None = None,
    ):
        self.event_callback = event_callback
        self.token = CancellationToken()
        self.transport = transport or HttpTransport()
        self.media_worker_factory = media_worker_factory or MediaWorkerController
        self._media_worker: MediaWorkerController | None = None
        self._media_worker_lock = threading.Lock()
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def cancel(self) -> None:
        self.token.cancel()
        self.transport.cancel_all()
        with self._media_worker_lock:
            worker = self._media_worker
        if worker is not None:
            worker.close()

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
        local_runtime: str = "huggingface",
        lmstudio_base_url: str = "http://localhost:1234/v1",
        lmstudio_model: str = "",
        llama_server_path: str | Path = "",
        llama_model_path: str | Path = "",
        llama_mmproj_path: str | Path = "",
        llama_model_alias: str = "",
        llama_context_length: int = LLAMA_CPP_DEFAULT_CONTEXT_LENGTH,
        llama_gpu_layers: int = LLAMA_CPP_DEFAULT_GPU_LAYERS,
        labeling_focus: str = "subject",
        output_language: str = "zh",
        trigger_word: str = "",
        provider_key: str = DEFAULT_PROVIDER_KEY,
        api_model: str = "",
        api_endpoint: str = "",
        sampling: dict[str, Any] | None = None,
        only_paths: Iterable[Path] | None = None,
        video_preflight: bool = True,
        enable_mtp: bool = False,
        remove_thinking_tags: bool = True,
        write_output: bool = True,
    ) -> BatchSummary:
        self._running.set()
        model = MODELS.get(model_key, MODELS[DEFAULT_MODEL_KEY])
        provider = API_PROVIDERS.get(
            provider_key, API_PROVIDERS[DEFAULT_PROVIDER_KEY]
        )
        sampling = normalize_sampling(sampling)
        enable_mtp = bool(enable_mtp)
        remove_thinking_tags = bool(remove_thinking_tags)
        concurrency = max(1, min(MAX_CONCURRENCY, int(concurrency)))
        local_runtime = (
            local_runtime
            if local_runtime in {"huggingface", "lmstudio", "llamacpp"}
            else "huggingface"
        )
        effective_mtp = bool(
            enable_mtp and backend == "local" and local_runtime == "huggingface"
        )
        journal = ProjectJournal(folder)
        journal.start({
            "mode": mode, "caption_style": caption_style,
            "subject_filter": subject_filter,
            "backend": backend,
            "provider": (
                provider.key
                if backend == "api"
                else (
                    "lmstudio" if local_runtime == "lmstudio"
                    else ("llamacpp" if local_runtime == "llamacpp" else "local")
                )
            ),
            "model": (
                (
                    str(lmstudio_model).strip()
                    if local_runtime == "lmstudio"
                    else (
                        str(Path(llama_model_path).name or llama_model_path)
                        if local_runtime == "llamacpp"
                        else str(Path(local_model_folder).name or local_model_folder)
                    )
                )
                if backend == "local"
                else (model.key if provider.key == DEFAULT_PROVIDER_KEY else api_model)
            ),
            "labeling_focus": labeling_focus,
            "output_language": output_language,
            "trigger_word": trigger_word,
            "concurrency": concurrency,
            "sampling": (
                {}
                if backend == "local" and local_runtime == "lmstudio"
                else sampling
            ),
            "sampling_source": (
                "lmstudio"
                if backend == "local" and local_runtime == "lmstudio"
                else "application"
            ),
            "lmstudio_output_safety_limit": (
                LMSTUDIO_CAPTION_TOKEN_LIMIT
                if backend == "local" and local_runtime == "lmstudio"
                else None
            ),
            "video_preflight": bool(video_preflight),
            "enable_mtp": effective_mtp,
            "remove_thinking_tags": remove_thinking_tags,
            "write_output": bool(write_output),
        })
        summary = BatchSummary()
        started = time.monotonic()
        worker_capabilities: dict[str, Any] = {}
        client: Any | None = None
        try:
            if mode == "video" and video_preflight:
                worker_cache = app_data_dir() / "worker-cache" / journal.run_id
                worker_cache.mkdir(parents=True, exist_ok=True)
                try:
                    media_worker = self.media_worker_factory([folder, worker_cache])
                    with self._media_worker_lock:
                        self._media_worker = media_worker
                    health = media_worker.health()
                    worker_capabilities = (
                        health.get("capabilities")
                        if isinstance(health.get("capabilities"), dict)
                        else {}
                    )
                    if worker_capabilities.get("probe"):
                        detail = "媒体引擎已按需启动，视频将先进行完整性检查"
                    else:
                        detail = "未检测到 FFprobe，将继续使用平台原生视频输入"
                    journal.log(detail)
                    self._emit("engine", status="ready", detail=detail, health=health)
                    if not worker_capabilities.get("probe"):
                        media_worker.close()
                        with self._media_worker_lock:
                            self._media_worker = None
                except (MediaWorkerError, OSError, ValueError) as error:
                    detail = f"媒体引擎不可用，继续使用平台原生视频输入：{error}"
                    journal.log(detail)
                    self._emit("engine", status="unavailable", detail=detail)
                    with self._media_worker_lock:
                        self._media_worker = None
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
                    journal.record(path, "pending", "等待处理")
                    pending.put(path)

            lock = threading.Lock()
            if backend == "local":
                if local_runtime == "lmstudio":
                    client = LmStudioCaptionClient(
                        lmstudio_base_url,
                        lmstudio_model,
                        self.token,
                        self.transport,
                        sampling=sampling,
                        remove_thinking_tags=remove_thinking_tags,
                    )
                elif local_runtime == "llamacpp":
                    client = LlamaCppCaptionClient(
                        llama_server_path,
                        llama_model_path,
                        llama_mmproj_path,
                        self.token,
                        self.transport,
                        model_alias=llama_model_alias,
                        context_length=llama_context_length,
                        gpu_layers=llama_gpu_layers,
                        sampling=sampling,
                        remove_thinking_tags=remove_thinking_tags,
                    )
                else:
                    try:
                        client = LocalCaptionClient(
                            Path(local_model_folder),
                            self.token,
                            enable_mtp=effective_mtp,
                            remove_thinking_tags=remove_thinking_tags,
                        )
                    except TypeError as error:
                        if "unexpected keyword argument" not in str(error):
                            raise
                        client = LocalCaptionClient(
                            Path(local_model_folder), self.token
                        )
                        client.enable_mtp = effective_mtp
                        client.remove_thinking_tags = remove_thinking_tags
            else:
                client = CaptionClient(
                    model,
                    api_key,
                    self.token,
                    self.transport,
                    provider_key=provider.key,
                    api_model=api_model,
                    api_endpoint=api_endpoint,
                    sampling=sampling,
                    remove_thinking_tags=remove_thinking_tags,
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
                    item_started = time.monotonic()
                    self._emit("status", path=path, status="running", detail="正在请求模型")
                    journal.record(path, "running", "正在请求模型")
                    try:
                        if mode == "video" and worker_capabilities.get("probe"):
                            with self._media_worker_lock:
                                media_worker = self._media_worker
                            if media_worker is not None:
                                self.token.check()
                                self._emit(
                                    "status",
                                    path=path,
                                    status="running",
                                    detail="正在检查视频完整性",
                                )
                                media_info = media_worker.probe(path)
                                if not media_info.get("video_streams"):
                                    raise MediaWorkerError("文件中没有可用的视频流")
                        caption = client.caption_image(path, effective_prompt) if mode == "image" else client.caption_video(path, effective_prompt)
                        self.token.check()
                        caption = prepend_trigger_word(
                            caption,
                            trigger_word,
                            output_language,
                        )
                        if write_output:
                            write_caption(path, caption)
                    except CancelledError:
                        item_elapsed = time.monotonic() - item_started
                        with lock:
                            summary.cancelled += 1
                        journal.record(path, "cancelled", "任务已取消")
                        self._emit(
                            "status",
                            path=path,
                            status="cancelled",
                            detail="任务已取消",
                            elapsed_seconds=item_elapsed,
                        )
                    except Exception as error:
                        item_elapsed = time.monotonic() - item_started
                        detail = str(error)
                        with lock:
                            summary.failed += 1
                            summary.failures.append((path, detail))
                        journal.record(path, "failed", detail)
                        self._emit(
                            "status",
                            path=path,
                            status="failed",
                            detail=detail,
                            elapsed_seconds=item_elapsed,
                        )
                    else:
                        item_elapsed = time.monotonic() - item_started
                        character_count = count_output_characters(caption)
                        with lock:
                            summary.success += 1
                            summary.characters += character_count
                        journal.record(path, "success", caption[:240])
                        self._emit(
                            "status",
                            path=path,
                            status="success",
                            detail=caption,
                            elapsed_seconds=item_elapsed,
                            character_count=character_count,
                            characters_per_second=(
                                character_count / max(0.001, item_elapsed)
                            ),
                        )
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
            summary.elapsed_seconds = max(0.0, time.monotonic() - started)
            journal.finish(status, {
                "total": summary.total, "success": summary.success,
                "skipped": summary.skipped, "failed": summary.failed,
                "cancelled": summary.cancelled,
                "characters": summary.characters,
                "elapsed_seconds": summary.elapsed_seconds,
                "characters_per_second": (
                    summary.characters / max(0.001, summary.elapsed_seconds)
                ),
            })
            self._emit("done", status=status, summary=summary, journal_dir=journal.project_dir)
            return summary
        finally:
            close_client = getattr(client, "close", None)
            if callable(close_client):
                try:
                    close_client()
                except Exception:
                    pass
            with self._media_worker_lock:
                media_worker = self._media_worker
                self._media_worker = None
            if media_worker is not None:
                media_worker.close()
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
