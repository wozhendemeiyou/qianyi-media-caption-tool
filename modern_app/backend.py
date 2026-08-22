from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot

import media_caption_core as core


class AppBackend(QObject):
    """Small QML-facing application service.

    The QML layer only deals with user intent and view state.  All persistence,
    scanning and inference orchestration stays here so the UI can be replaced
    without touching the stable media/model core.
    """

    pageChanged = Signal()
    themeChanged = Signal()
    toast = Signal(str, str)
    filesChanged = Signal()
    logsChanged = Signal()
    taskChanged = Signal()
    progressChanged = Signal()
    settingsChanged = Signal()
    providerChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.store = core.SettingsStore()
        self.settings: dict[str, Any] = self.store.load()
        self._page = "home"
        self._files: list[dict[str, Any]] = []
        self._selected_files: list[Path] = []
        self._logs: list[str] = []
        self._task = {"status": "idle", "label": "准备就绪", "current": 0, "total": 0}
        self._thread: threading.Thread | None = None
        self._runner: core.BatchRunner | None = None

    def _emit_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._logs.append(f"{stamp}  {message}")
        self._logs = self._logs[-200:]
        self.logsChanged.emit()

    def _notify(self, message: str, kind: str = "info") -> None:
        self._emit_log(message)
        self.toast.emit(message, kind)

    @Property(str, notify=pageChanged)
    def page(self) -> str:
        return self._page

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        return str(self.settings.get("theme", "night"))

    @Property(str, notify=taskChanged)
    def taskStatus(self) -> str:
        return str(self._task.get("status", "idle"))

    @Property(str, notify=taskChanged)
    def taskLabel(self) -> str:
        return str(self._task.get("label", "准备就绪"))

    @Property(int, notify=progressChanged)
    def progress(self) -> int:
        return int(self._task.get("current", 0))

    @Property(int, notify=progressChanged)
    def total(self) -> int:
        return int(self._task.get("total", 0))

    @Property(str, notify=filesChanged)
    def folder(self) -> str:
        return str(self.settings.get("last_folder", ""))

    @Property("QVariantList", notify=filesChanged)
    def mediaFiles(self) -> list[dict[str, Any]]:
        return self._files

    @Property("QVariantList", notify=logsChanged)
    def logs(self) -> list[str]:
        return self._logs

    @Property("QVariantList", constant=True)
    def providerOptions(self) -> list[dict[str, str]]:
        return [
            {"key": key, "name": option.name, "endpoint": option.endpoint}
            for key, option in core.API_PROVIDERS.items()
        ]

    @Property("QVariantList", notify=providerChanged)
    def modelOptions(self) -> list[dict[str, str]]:
        provider = str(self.settings.get("provider_key", core.DEFAULT_PROVIDER_KEY))
        option = core.API_PROVIDERS.get(provider)
        if option is None:
            return []
        return [{"key": model, "name": model} for model in option.models]

    @Property(str, notify=providerChanged)
    def providerKey(self) -> str:
        return str(self.settings.get("provider_key", core.DEFAULT_PROVIDER_KEY))

    @Property(str, notify=settingsChanged)
    def modelKey(self) -> str:
        return str(self.settings.get("model_key", core.DEFAULT_MODEL_KEY))

    @Property(str, notify=settingsChanged)
    def backendMode(self) -> str:
        return str(self.settings.get("backend", "api"))

    @Property(str, notify=settingsChanged)
    def localRuntime(self) -> str:
        return str(self.settings.get("local_runtime", "huggingface"))

    @Property(str, notify=settingsChanged)
    def prompt(self) -> str:
        return str(self.settings.get("user_prompt", ""))

    @Property(int, notify=settingsChanged)
    def concurrency(self) -> int:
        return int(self.settings.get("concurrency", 3))

    @Property(bool, notify=settingsChanged)
    def skipExisting(self) -> bool:
        return bool(self.settings.get("skip_existing", True))

    @Property(bool, notify=settingsChanged)
    def mtp(self) -> bool:
        return bool(self.settings.get("enable_mtp", False))

    @Property(bool, notify=settingsChanged)
    def removeThinking(self) -> bool:
        return bool(self.settings.get("remove_thinking_tags", True))

    @Slot(str)
    def navigate(self, page: str) -> None:
        if page not in {"home", "task", "projects", "platform", "about"}:
            return
        self._page = page
        self.pageChanged.emit()

    @Slot()
    def toggleTheme(self) -> None:
        self.settings["theme"] = "day" if self.theme == "night" else "night"
        self.store.save(self.settings)
        self.themeChanged.emit()
        self.settingsChanged.emit()

    @Slot(str)
    def setFolder(self, folder: str) -> None:
        value = str(folder or "").replace("file:///", "")
        if not value:
            return
        self.settings["last_folder"] = value
        recent = [value] + [x for x in self.settings.get("recent_folders", []) if x != value]
        self.settings["recent_folders"] = recent[:10]
        self.store.save(self.settings)
        self.filesChanged.emit()
        self._notify(f"已选择素材目录：{Path(value).name or value}")
        self.scan()

    @Slot("QVariantList")
    def addFiles(self, urls: list[Any]) -> None:
        paths: list[Path] = []
        for value in urls or []:
            text = str(value)
            if text.startswith("file:///"):
                text = text[8:]
            path = Path(text)
            if path.is_file():
                paths.append(path)
        self._selected_files = list(dict.fromkeys(paths))
        self._files = [self._file_item(path, "待处理") for path in self._selected_files]
        self.filesChanged.emit()
        if paths:
            self._notify(f"已加入 {len(paths)} 个素材")

    def _file_item(self, path: Path, status: str) -> dict[str, Any]:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {"name": path.name, "path": str(path), "status": status, "size": size}

    @Slot()
    def scan(self) -> None:
        folder = Path(self.folder)
        if not folder.is_dir():
            self._files = []
            self.filesChanged.emit()
            return
        mode = str(self.settings.get("media_mode", "image"))
        result = core.scan_media(folder, mode)
        self._selected_files = list(result.files)
        self._files = [
            self._file_item(path, "已有标注" if core.has_usable_caption(path) else "待处理")
            for path in result.files
        ]
        self.filesChanged.emit()
        self._notify(f"扫描完成：发现 {len(result.files)} 个{ '图片' if mode == 'image' else '视频' }素材")

    @Slot(str)
    def setMediaMode(self, mode: str) -> None:
        if mode not in {"image", "video"}:
            return
        self.settings["media_mode"] = mode
        self.store.save(self.settings)
        self.settingsChanged.emit()
        self.scan()

    @Slot(str)
    def setPrompt(self, value: str) -> None:
        self.settings["user_prompt"] = str(value)
        self.settingsChanged.emit()

    @Slot(str)
    def setBackendMode(self, value: str) -> None:
        if value not in {"api", "local"}:
            return
        self.settings["backend"] = value
        self.settingsChanged.emit()

    @Slot(str)
    def setLocalRuntime(self, value: str) -> None:
        if value not in {"huggingface", "lmstudio", "llamacpp"}:
            return
        self.settings["local_runtime"] = value
        self.settingsChanged.emit()

    @Slot(str)
    def setProvider(self, value: str) -> None:
        if value not in core.API_PROVIDERS:
            return
        self.settings["provider_key"] = value
        option = core.API_PROVIDERS[value]
        self.settings["model_key"] = core.model_key_from_legacy(option.default_model)
        self.providerChanged.emit()
        self.settingsChanged.emit()

    @Slot(str)
    def setModel(self, value: str) -> None:
        provider = self.providerKey
        if provider == "volcengine" and value in core.MODELS:
            self.settings["model_key"] = value
        else:
            self.settings.setdefault("api_models", {})[provider] = value
        self.settingsChanged.emit()

    @Slot(int)
    def setConcurrency(self, value: int) -> None:
        self.settings["concurrency"] = max(1, min(core.MAX_CONCURRENCY, int(value)))
        self.settingsChanged.emit()

    @Slot(bool)
    def setSkipExisting(self, value: bool) -> None:
        self.settings["skip_existing"] = bool(value)
        self.settingsChanged.emit()

    @Slot(bool)
    def setMtp(self, value: bool) -> None:
        self.settings["enable_mtp"] = bool(value)
        self.settingsChanged.emit()

    @Slot(bool)
    def setRemoveThinking(self, value: bool) -> None:
        self.settings["remove_thinking_tags"] = bool(value)
        self.settingsChanged.emit()

    @Slot(str)
    def setApiKey(self, value: str) -> None:
        self.store.set_api_key(value, self.providerKey)
        self._notify("API Key 已保存到系统凭据存储")

    @Slot()
    def saveSettings(self) -> None:
        self.store.save(self.settings)
        self.settingsChanged.emit()
        self._notify("设置已保存", "success")

    @Slot()
    def testConnection(self) -> None:
        provider = self.providerKey
        option = core.API_PROVIDERS.get(provider)
        if option is None:
            return
        self._notify(f"正在测试 {option.name} 连接…")

        def worker() -> None:
            try:
                result = core.test_provider_connection(
                    provider,
                    self.store.get_api_key(provider),
                    endpoint=self.settings.get("api_endpoints", {}).get(provider, ""),
                )
                self._notify(str(result.get("detail") or "连接正常"), "success" if result.get("ok") else "error")
            except Exception as exc:  # noqa: BLE001
                self._notify(f"连接测试失败：{exc}", "error")

        threading.Thread(target=worker, daemon=True).start()

    @Slot()
    def runSingle(self) -> None:
        if not self._selected_files:
            self._notify("请先拖入或选择至少一个素材", "warning")
            return
        self._start_job(only_paths=self._selected_files[:1])

    @Slot()
    def runBatch(self) -> None:
        if not self.folder and not self._selected_files:
            self._notify("请先选择素材目录或拖入文件", "warning")
            return
        self._start_job(only_paths=self._selected_files or None)

    def _start_job(self, only_paths: list[Path] | None) -> None:
        if self._thread and self._thread.is_alive():
            self._notify("已有任务正在运行", "warning")
            return
        folder = Path(self.folder) if self.folder else (only_paths[0].parent if only_paths else Path.cwd())
        mode = str(self.settings.get("media_mode", "image"))
        self._task = {"status": "running", "label": "正在准备任务…", "current": 0, "total": len(only_paths or self._files)}
        self.taskChanged.emit()
        self.progressChanged.emit()

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            if kind == "status":
                current = int(self._task.get("current", 0)) + 1
                self._task.update(current=current, label=f"{current}/{self._task.get('total', 0)}  {payload.get('status', '处理中')}")
                self.progressChanged.emit()
                self.taskChanged.emit()
            elif kind == "log":
                self._emit_log(str(payload.get("message") or payload.get("detail") or ""))
            elif kind == "scan":
                result = payload.get("result")
                total = len(getattr(result, "files", []) or [])
                self._task["total"] = max(total, int(self._task.get("total", 0)))
                self.progressChanged.emit()
            elif kind == "error":
                self._emit_log(str(payload.get("detail") or "任务错误"))

        def worker() -> None:
            try:
                self._runner = core.BatchRunner(on_event)
                model_key = self.modelKey
                provider = self.providerKey
                api_model = self.settings.get("api_models", {}).get(provider, "")
                summary = self._runner.run(
                    folder=folder,
                    mode=mode,
                    prompt=self.prompt,
                    model_key=model_key,
                    api_key=self.store.get_api_key(provider),
                    concurrency=self.concurrency,
                    skip_existing=self.skipExisting,
                    backend=self.backendMode,
                    local_model_folder=self.settings.get("local_model_folder", ""),
                    local_runtime=self.localRuntime,
                    lmstudio_base_url=self.settings.get("lmstudio_base_url", "http://localhost:1234/v1"),
                    lmstudio_model=self.settings.get("lmstudio_model", ""),
                    llama_server_path=self.settings.get("llama_server_path", ""),
                    llama_model_path=self.settings.get("llama_model_path", ""),
                    llama_mmproj_path=self.settings.get("llama_mmproj_path", ""),
                    llama_model_alias=self.settings.get("llama_model_alias", ""),
                    llama_context_length=self.settings.get("llama_context_length", 8192),
                    llama_gpu_layers=self.settings.get("llama_gpu_layers", -1),
                    labeling_focus=self.settings.get("labeling_focus", "subject"),
                    output_language=self.settings.get("output_language", "zh"),
                    trigger_word=self.settings.get("trigger_word", ""),
                    provider_key=provider,
                    api_model=api_model,
                    api_endpoint=self.settings.get("api_endpoints", {}).get(provider, ""),
                    sampling=self.settings.get("sampling", {}),
                    only_paths=only_paths,
                    video_preflight=bool(self.settings.get("video_preflight", True)),
                    enable_mtp=self.mtp,
                    remove_thinking_tags=self.removeThinking,
                )
                self._task.update(status="success", label=f"完成：成功 {summary.successful}，跳过 {summary.skipped}，失败 {summary.failed}", current=summary.total)
                self._notify(self._task["label"], "success" if not summary.failed else "warning")
            except Exception as exc:  # noqa: BLE001
                self._task.update(status="error", label=f"任务失败：{exc}")
                self._notify(str(self._task["label"]), "error")
            finally:
                self.taskChanged.emit()
                self.progressChanged.emit()
                self.scan()

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    @Slot()
    def stopTask(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._notify("正在取消任务…", "warning")

    @Slot()
    def clearLogs(self) -> None:
        self._logs = []
        self.logsChanged.emit()

