from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
IGNORED_DIRECTORIES = {
    ".git",
    ".venv312",
    ".analysis_tools",
    "__pycache__",
    "analysis",
    "build",
    "dist",
    "MediaCaptionTool.exe_extracted",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_FILES = {
    "config.json",
    "credentials.bin",
    "settings.json",
}
SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Volcengine access key": re.compile(r"\bAKLT[A-Za-z0-9]{16,}\b"),
    "Bearer credential": re.compile(r"Bearer\s+[A-Za-z0-9._~-]{20,}"),
    "Private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "Personal Windows path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s/]+"),
    "Personal macOS path": re.compile(r"/Users/[^/\s]+"),
}


def iter_public_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
            continue
        yield path


def main() -> int:
    failures: list[str] = []
    checked = 0
    for path in iter_public_files():
        relative = path.relative_to(ROOT)
        lowered_name = path.name.casefold()
        if lowered_name in FORBIDDEN_FILES or (
            lowered_name.startswith("credentials-")
            and lowered_name.endswith(".bin")
        ):
            failures.append(f"禁止发布的本地数据文件：{relative}")
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        checked += 1
        if path.resolve() == Path(__file__).resolve():
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label}：{relative}")

    from media_caption_core import DEFAULT_SETTINGS
    from media_caption_tool_v3 import DEFAULT_PRESETS

    for key in (
        "api_models",
        "api_endpoints",
        "custom_api_endpoint",
        "prompt_presets",
        "selected_preset",
        "user_prompt",
    ):
        if DEFAULT_SETTINGS.get(key):
            failures.append(f"公开默认设置未清空：DEFAULT_SETTINGS[{key!r}]")
    if DEFAULT_PRESETS:
        failures.append("公开构建仍包含内置提示词模板")

    if failures:
        print("PUBLIC_RELEASE_CHECK_FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"PUBLIC_RELEASE_CHECK_OK · checked {checked} text files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
