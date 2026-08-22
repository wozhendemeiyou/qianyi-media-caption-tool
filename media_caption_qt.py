"""Optional modern Qt workbench.

This is the first migration slice of the desktop UI.  The existing Tk workbench
remains the release fallback; this module reuses the same core, settings store,
model clients and BatchRunner, so it can be introduced without duplicating
inference or changing the on-disk format.

Install the optional runtime with ``pip install -r requirements-qt.txt`` and
run ``python media_caption_qt.py``.  ``--smoke-test`` creates the full three
column shell off-screen and exits without contacting a provider.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    from PySide6.QtCore import Qt, Slot
    from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSlider,
        QSpinBox,
        QSplitter,
        QStatusBar,
        QTabWidget,
        QTextBrowser,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - exercised only without the optional extra
    QApplication = None  # type: ignore[assignment]

import media_caption_core as core
from qt_ui import (
    BatchWorker,
    DropListWidget,
    FunctionWorker,
    LmStudioInventoryWorker,
    LmStudioLoadWorker,
    LmStudioUnloadWorker,
    ProviderTestWorker,
    SectionCard,
    THEMES,
    UpdateCheckWorker,
    build_stylesheet,
)


APP_VERSION = core.APP_VERSION
APP_TITLE = "芊熠智能打标工作台 · Modern"

def _ensure_qt() -> None:
    if QApplication is None:
        raise RuntimeError(
            "现代 Qt 界面需要可选依赖 PySide6，请先执行："
            "python -m pip install -r requirements-qt.txt"
        )


def _font(size: int = 14, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont("Microsoft YaHei UI", size)
    font.setWeight(weight)
    return font


class SingleMediaDialog(QDialog):
    """Modern clip editor that reuses the existing FFmpeg media worker."""

    def __init__(self, on_ready, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_ready = on_ready
        self.source: Path | None = None
        self.probe_info: dict[str, Any] = {}
        self._worker: FunctionWorker | None = None
        self.setWindowTitle("单次反推 · 音视频片段编辑器")
        self.setMinimumSize(680, 430)
        layout = QVBoxLayout(self)
        heading = QLabel("✂ 音视频片段编辑器")
        heading.setFont(_font(19, QFont.Weight.Bold))
        layout.addWidget(heading)
        intro = QLabel("手工选择起止时间，截取后可直接加入单次反推；原文件不会被修改。")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)
        form = QFormLayout()
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("选择视频或音频文件")
        choose = QPushButton("选择…")
        choose.clicked.connect(self.choose_file)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(choose)
        file_host = QWidget()
        file_host.setLayout(file_row)
        form.addRow("媒体文件", file_host)
        self.probe_label = QLabel("尚未读取媒体信息")
        self.probe_label.setObjectName("muted")
        form.addRow("轨道信息", self.probe_label)
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0, 86400)
        self.start_spin.setDecimals(3)
        self.start_spin.setSuffix(" 秒")
        form.addRow("开始", self.start_spin)
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(0, 86400)
        self.end_spin.setDecimals(3)
        self.end_spin.setSuffix(" 秒")
        form.addRow("结束", self.end_spin)
        self.audio_check = QCheckBox("保留音轨")
        self.audio_check.setChecked(True)
        form.addRow("音频", self.audio_check)
        layout.addLayout(form)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.probe_button = QPushButton("读取媒体信息")
        self.probe_button.clicked.connect(self.probe_media)
        self.trim_button = QPushButton("截取并加入单次反推")
        self.trim_button.setObjectName("primary")
        self.trim_button.setEnabled(False)
        self.trim_button.clicked.connect(self.trim_media)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.probe_button)
        buttons.addWidget(self.trim_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def choose_file(self) -> None:
        value, _ = QFileDialog.getOpenFileName(
            self,
            "选择音视频",
            self.file_edit.text(),
            "媒体文件 (*.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        )
        if value:
            self.file_edit.setText(value)
            self.probe_media()

    @staticmethod
    def _probe(path: Path) -> dict[str, Any]:
        worker = core.MediaWorkerController([path.parent])
        try:
            return worker.probe(path)
        finally:
            worker.close()

    @staticmethod
    def _trim(path: Path, output: Path, start: float, end: float, include_audio: bool) -> Path:
        worker = core.MediaWorkerController([path.parent, output.parent])
        try:
            return worker.trim_video(path, output, start, end, include_audio)
        finally:
            worker.close()

    def _run(self, function, *args, success, failure) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.progress.show()
        self.probe_button.setEnabled(False)
        self.trim_button.setEnabled(False)
        worker = FunctionWorker(function, *args)
        worker.succeeded.connect(success)
        worker.failed.connect(failure)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _worker_finished(self) -> None:
        self.progress.hide()
        self.probe_button.setEnabled(True)
        self.trim_button.setEnabled(bool(self.probe_info))

    def probe_media(self) -> None:
        source = Path(self.file_edit.text().strip())
        if not source.is_file():
            self.probe_label.setText("文件不存在")
            return
        self.source = source
        self._run(
            self._probe,
            source,
            success=self._probe_success,
            failure=lambda message: self.probe_label.setText(f"读取失败：{message}"),
        )

    def _probe_success(self, info: dict[str, Any]) -> None:
        self.probe_info = info
        duration = max(0.0, float(info.get("duration") or 0.0))
        self.start_spin.setRange(0.0, duration)
        self.end_spin.setRange(0.0, duration)
        self.end_spin.setValue(duration)
        video = len(info.get("video_streams") or [])
        audio = len(info.get("audio_streams") or [])
        self.probe_label.setText(
            f"时长 {duration:.3f} 秒 · 视频轨 {video} · 音频轨 {audio} · {info.get('format', '')}"
        )
        self.trim_button.setEnabled(duration >= 0.25)

    def trim_media(self) -> None:
        if self.source is None or not self.probe_info:
            return
        start = self.start_spin.value()
        end = self.end_spin.value()
        if end <= start:
            self.probe_label.setText("结束时间必须大于开始时间")
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "保存截取片段", str(self.source.with_name(f"{self.source.stem}-clip.mp4")),
            "MP4 视频 (*.mp4)",
        )
        if not output:
            return
        self._run(
            self._trim,
            self.source,
            Path(output),
            start,
            end,
            self.audio_check.isChecked(),
            success=self._trim_success,
            failure=lambda message: self.probe_label.setText(f"截取失败：{message}"),
        )

    def _trim_success(self, output: Path) -> None:
        self.on_ready(Path(output))
        self.accept()


class ModernMainWindow(QMainWindow):
    """Three-column Qt shell backed by the existing stable core."""

    def __init__(self) -> None:
        super().__init__()
        self.settings_store = core.SettingsStore()
        self.settings = self.settings_store.load()
        self.current_theme = str(self.settings.get("theme") or "night")
        self.current_folder: Path | None = None
        self.media_paths: list[Path] = []
        self.worker: BatchWorker | None = None
        self._aux_workers: set[object] = set()
        self._provider_model_memory: dict[str, str] = dict(
            self.settings.get("api_models") or {}
        )
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        self.setMinimumSize(1180, 760)
        self.resize(1440, 900)
        self._build_shell()
        self._load_settings_into_widgets()
        self.apply_theme(self.current_theme)

    # ----- shell ---------------------------------------------------------
    def _build_shell(self) -> None:
        self.setStatusBar(QStatusBar(self))
        self.statusBar().setFont(_font(12))

        toolbar = QToolBar("工作区导航", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        nav_items = (
            ("项目中心", "选择目录并扫描素材", self.focus_project_center),
            ("图像打标", "图片批量反推", lambda: self.select_mode("image")),
            ("视频反推", "视频批量反推", lambda: self.select_mode("video")),
            ("单次反推", "单张/单个媒体，不改写原 TXT", self.focus_single_reverse),
            ("平台设置", "运行后端与模型设置", self.focus_platform_settings),
            ("系统说明", "功能说明与版本检查", self.focus_system_info),
        )
        for label, hint, handler in nav_items:
            action = QAction(label, self)
            action.setToolTip(hint)
            action.triggered.connect(handler)
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.theme_action = QAction("切换日光/夜光", self)
        self.theme_action.triggered.connect(self.toggle_theme)
        toolbar.addAction(self.theme_action)
        self.legacy_action = QAction("打开经典工作台", self)
        self.legacy_action.setToolTip("迁移期间保留 Tk 版全部细节功能")
        self.legacy_action.triggered.connect(self.open_legacy_workbench)
        toolbar.addAction(self.legacy_action)

        root = QWidget()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(12)
        root_layout.addWidget(self._build_header())
        self.columns = QSplitter(Qt.Orientation.Horizontal)
        self.columns.setChildrenCollapsible(False)
        self.columns.addWidget(self._build_left_column())
        self.columns.addWidget(self._build_material_column())
        self.columns.addWidget(self._build_result_column())
        self.columns.setStretchFactor(0, 2)
        self.columns.setStretchFactor(1, 4)
        self.columns.setStretchFactor(2, 3)
        root_layout.addWidget(self.columns, 1)
        self.setCentralWidget(root)

    def select_mode(self, mode: str) -> None:
        index = self.mode_box.findData(mode)
        if index >= 0:
            self.mode_box.setCurrentIndex(index)
        self.statusBar().showMessage(
            "已切换到图像打标" if mode == "image" else "已切换到视频反推", 2500
        )

    def focus_project_center(self) -> None:
        self.folder_edit.setFocus()
        self.statusBar().showMessage("项目中心：选择目录后扫描素材", 3000)

    def focus_single_reverse(self) -> None:
        self.material_list.setFocus()
        if self.material_list.count() and not self.material_list.selectedItems():
            self.material_list.setCurrentRow(0)
        self.statusBar().showMessage("单次反推：选择一个素材后点击单次反推", 3000)

    def focus_platform_settings(self) -> None:
        self.provider_box.setFocus()
        self.statusBar().showMessage("平台设置已定位到左侧", 2500)

    def focus_system_info(self) -> None:
        self.tabs.setCurrentWidget(self.system_info)
        self.statusBar().showMessage("系统说明与版本检查", 2500)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("芊熠智能打标工作台")
        title.setFont(_font(21, QFont.Weight.Bold))
        subtitle = QLabel("现代 Qt 迁移试用版 · 核心能力与现有工作台共用")
        subtitle.setObjectName("muted")
        subtitle.setFont(_font(13))
        text = QVBoxLayout()
        text.addWidget(title)
        text.addWidget(subtitle)
        layout.addLayout(text)
        layout.addStretch(1)
        self.backend_badge = QLabel("外部 API")
        self.backend_badge.setObjectName("badge")
        self.version_badge = QLabel(f"v{APP_VERSION}")
        self.version_badge.setObjectName("badge")
        layout.addWidget(self.backend_badge)
        layout.addWidget(self.version_badge)
        return frame

    def _scroll(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _group(self, title: str, object_name: str = "card") -> SectionCard:
        group = SectionCard(title)
        group.setObjectName(object_name)
        group.setFont(_font(15, QFont.Weight.Bold))
        return group

    def _build_left_column(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_platform_group())
        layout.addWidget(self._build_sampling_group())
        layout.addStretch(1)
        return self._scroll(content)

    def _build_platform_group(self) -> QWidget:
        group = self._group("◈ 平台设置")
        form = group.form
        self.backend_box = QComboBox()
        self.backend_box.addItem("外部 API", "api")
        self.backend_box.addItem("本地模型", "local")
        self.backend_box.currentIndexChanged.connect(self._backend_changed)
        form.addRow("运行后端", self.backend_box)

        self.provider_box = QComboBox()
        for key, provider in core.API_PROVIDERS.items():
            self.provider_box.addItem(provider.label, key)
        self.provider_box.currentIndexChanged.connect(self._provider_changed)
        form.addRow("供应商", self.provider_box)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("模型 ID，例如 gpt-5.6 / qwen3.8-max")
        form.addRow("模型", self.model_edit)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("仅保存在当前 Windows 账户")
        form.addRow("API KEY", self.api_key_edit)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("仅自定义接口需要填写")
        form.addRow("Base URL", self.endpoint_edit)
        self.provider_test_button = QPushButton("测试连接")
        self.provider_test_button.clicked.connect(self.test_provider)
        form.addRow("API 平台", self.provider_test_button)

        self.runtime_box = QComboBox()
        self.runtime_box.addItem("Hugging Face 本地目录", "huggingface")
        self.runtime_box.addItem("LM Studio 本地服务", "lmstudio")
        self.runtime_box.addItem("llama.cpp 原生 GGUF", "llamacpp")
        self.runtime_box.currentIndexChanged.connect(self._runtime_changed)
        form.addRow("本地运行方式", self.runtime_box)

        self.local_folder_edit = QLineEdit()
        local_browse = QPushButton("选择…")
        local_browse.clicked.connect(self._choose_local_folder)
        form.addRow("模型目录", self._with_button(self.local_folder_edit, local_browse))
        self.lm_url_edit = QLineEdit()
        form.addRow("LM Base URL", self.lm_url_edit)
        self.lm_model_edit = QLineEdit()
        form.addRow("LM 模型", self.lm_model_edit)
        self.lm_profile_box = QComboBox()
        self.lm_profile_box.addItem("低显存安全", "low_vram")
        self.lm_profile_box.addItem("纯 CPU", "cpu")
        self.lm_profile_box.addItem("沿用预设", "inherit")
        form.addRow("LM 加载策略", self.lm_profile_box)
        lm_actions = QHBoxLayout()
        self.lm_refresh_button = QPushButton("刷新模型")
        self.lm_refresh_button.clicked.connect(self.refresh_lm_models)
        self.lm_load_button = QPushButton("加载模型")
        self.lm_load_button.clicked.connect(self.load_lm_model)
        self.lm_unload_button = QPushButton("卸载模型")
        self.lm_unload_button.clicked.connect(self.unload_lm_model)
        lm_actions.addWidget(self.lm_refresh_button)
        lm_actions.addWidget(self.lm_load_button)
        lm_actions.addWidget(self.lm_unload_button)
        lm_host = QWidget()
        lm_host.setLayout(lm_actions)
        form.addRow("LM Studio", lm_host)
        self.lm_instance_edit = QLineEdit()
        self.lm_instance_edit.setPlaceholderText("加载后自动填入实例 ID")
        form.addRow("实例 ID", self.lm_instance_edit)
        self.llama_server_edit = QLineEdit()
        self.llama_model_edit = QLineEdit()
        self.llama_mmproj_edit = QLineEdit()
        form.addRow("llama-server", self._path_row(self.llama_server_edit, False))
        form.addRow("GGUF 主模型", self._path_row(self.llama_model_edit, False))
        form.addRow("视觉 mmproj", self._path_row(self.llama_mmproj_edit, False))
        self.llama_context = self._spin(512, 131072, core.LLAMA_CPP_DEFAULT_CONTEXT_LENGTH)
        self.llama_gpu_layers = self._spin(-1, 999, core.LLAMA_CPP_DEFAULT_GPU_LAYERS)
        form.addRow("GGUF 上下文", self.llama_context)
        form.addRow("GPU 层数", self.llama_gpu_layers)

        self.mtp_check = QCheckBox("启用 MTP（仅兼容的 Hugging Face 模型）")
        self.thinking_check = QCheckBox("移除思考标签")
        form.addRow("推理控制", self.mtp_check)
        form.addRow("输出清理", self.thinking_check)
        self.platform_save = QPushButton("保存平台设置")
        self.platform_save.clicked.connect(self.save_settings)
        form.addRow("", self.platform_save)
        return group

    def _build_sampling_group(self) -> QWidget:
        group = self._group("◌ 采样参数")
        form = group.form
        self.preset_box = QComboBox()
        self.preset_box.addItems(["稳定反推", "平衡反推", "创意反推"])
        self.preset_box.currentTextChanged.connect(self.apply_preset)
        form.addRow("预设", self.preset_box)
        self.max_tokens = self._spin(1, 131072, 2000)
        form.addRow("Max tokens", self.max_tokens)
        self.temperature = self._double(0.0, 2.0, 0.2, 2)
        form.addRow("Temperature", self.temperature)
        self.top_p = self._double(0.0, 1.0, 0.9, 2)
        form.addRow("Top P", self.top_p)
        self.top_k = self._spin(0, 999, 0)
        form.addRow("Top K", self.top_k)
        self.frequency = self._double(-2.0, 2.0, 0.0, 2)
        form.addRow("频率惩罚", self.frequency)
        self.presence = self._double(-2.0, 2.0, 0.0, 2)
        form.addRow("存在惩罚", self.presence)
        seed_row = QHBoxLayout()
        self.seed = QLineEdit()
        self.seed.setPlaceholderText("留空为随机")
        seed_row.addWidget(self.seed, 1)
        dice = QPushButton("🎲")
        dice.setToolTip("生成随机 Seed")
        dice.setFixedWidth(42)
        dice.clicked.connect(self.randomize_seed)
        seed_row.addWidget(dice)
        seed_host = QWidget()
        seed_host.setLayout(seed_row)
        form.addRow("Seed", seed_host)
        self.concurrency = self._spin(1, core.MAX_CONCURRENCY, 3)
        form.addRow("并发数", self.concurrency)
        self.sampling_save = QPushButton("保存采样参数")
        self.sampling_save.clicked.connect(self.save_settings)
        form.addRow("", self.sampling_save)
        return group

    def _build_material_column(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        title_row = QHBoxLayout()
        title = QLabel("▣ 素材区")
        title.setFont(_font(16, QFont.Weight.Bold))
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.mode_box = QComboBox()
        self.mode_box.addItem("图像打标", "image")
        self.mode_box.addItem("视频反推", "video")
        self.mode_box.currentIndexChanged.connect(self.scan_current_folder)
        title_row.addWidget(self.mode_box)
        layout.addLayout(title_row)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("选择项目目录，或将单个文件拖到下方")
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("选择目录")
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(browse)
        scan = QPushButton("扫描")
        scan.clicked.connect(self.scan_current_folder)
        folder_row.addWidget(scan)
        layout.addLayout(folder_row)
        self.material_list = DropListWidget()
        self.material_list.files_dropped.connect(self.add_dropped_files)
        self.material_list.setMinimumHeight(280)
        layout.addWidget(self.material_list, 1)
        self.material_hint = QLabel("拖入图片/视频可直接进入单次反推；扫描目录后可批量处理。")
        self.material_hint.setObjectName("muted")
        self.material_hint.setWordWrap(True)
        layout.addWidget(self.material_hint)
        prompt_group = QGroupBox("✦ 用户提示词")
        prompt_group.setObjectName("card")
        prompt_group.setFont(_font(15, QFont.Weight.Bold))
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("输入你希望模型重点描述的内容…")
        self.prompt_edit.setMinimumHeight(115)
        prompt_layout.addWidget(self.prompt_edit)
        layout.addWidget(prompt_group)
        actions = QHBoxLayout()
        self.single_button = QPushButton("单次反推")
        self.single_button.clicked.connect(self.run_single)
        self.clip_editor_button = QPushButton("音视频编辑器")
        self.clip_editor_button.clicked.connect(self.open_media_editor)
        self.start_button = QPushButton("开始批量反推")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.run_batch)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_batch)
        actions.addWidget(self.single_button)
        actions.addWidget(self.clip_editor_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        return frame

    def _build_result_column(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        title = QLabel("◈ 结果与运行日志")
        title.setFont(_font(16, QFont.Weight.Bold))
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.result_edit = QPlainTextEdit()
        self.result_edit.setPlaceholderText("单次反推结果和当前选中素材的标注会显示在这里…")
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.system_info = QTextBrowser()
        self.system_info.setOpenExternalLinks(True)
        self.system_info.setHtml(
            f"<h2>系统说明</h2><p>当前版本：<b>{APP_VERSION}</b></p>"
            "<p>这是现代 Qt 迁移试用版。推理、设置保存、模型路由、日志和更新数据仍由稳定核心提供。</p>"
            "<p>本地模型：Hugging Face、LM Studio、llama.cpp 原生 GGUF。</p>"
            "<p>迁移期间可点击顶部“打开经典工作台”，使用旧版完整界面。</p>"
        )
        self.tabs.addTab(self.result_edit, "结果")
        self.tabs.addTab(self.log_edit, "运行日志")
        self.tabs.addTab(self.system_info, "系统说明")
        layout.addWidget(self.tabs, 1)
        action_row = QHBoxLayout()
        self.save_result_button = QPushButton("保存当前结果")
        self.save_result_button.clicked.connect(self.save_current_result)
        self.export_json_button = QPushButton("导出 JSONL")
        self.export_json_button.clicked.connect(lambda: self.export_captions("jsonl"))
        self.export_csv_button = QPushButton("导出 CSV")
        self.export_csv_button.clicked.connect(lambda: self.export_captions("csv"))
        self.update_button = QPushButton("检查更新")
        self.update_button.clicked.connect(self.check_updates)
        action_row.addWidget(self.save_result_button)
        action_row.addWidget(self.export_json_button)
        action_row.addWidget(self.export_csv_button)
        action_row.addStretch(1)
        action_row.addWidget(self.update_button)
        layout.addLayout(action_row)
        self.metrics = QLabel("尚未开始任务")
        self.metrics.setObjectName("muted")
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        return frame

    # ----- settings and controls ----------------------------------------
    @staticmethod
    def _with_button(field: QWidget, button: QPushButton) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        row.addWidget(button)
        return host

    def _path_row(self, field: QLineEdit, directory: bool) -> QWidget:
        button = QPushButton("选择…")
        button.clicked.connect(lambda: self.choose_path(field, directory))
        return self._with_button(field, button)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        return widget

    @staticmethod
    def _double(minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setSingleStep(0.05)
        widget.setValue(value)
        return widget

    def _load_settings_into_widgets(self) -> None:
        settings = self.settings
        self.backend_box.setCurrentIndex(max(0, self.backend_box.findData(settings.get("backend", "api"))))
        self.provider_box.setCurrentIndex(max(0, self.provider_box.findData(settings.get("provider_key", core.DEFAULT_PROVIDER_KEY))))
        self.model_edit.setText(self._model_for_provider(self.provider_box.currentData()))
        self.endpoint_edit.setText(str((settings.get("api_endpoints") or {}).get("custom", "")))
        self.runtime_box.setCurrentIndex(max(0, self.runtime_box.findData(settings.get("local_runtime", "huggingface"))))
        self.local_folder_edit.setText(str(settings.get("local_model_folder", "")))
        self.lm_url_edit.setText(str(settings.get("lmstudio_base_url", core.DEFAULT_SETTINGS["lmstudio_base_url"])))
        self.lm_model_edit.setText(str(settings.get("lmstudio_model", "")))
        self.lm_profile_box.setCurrentIndex(
            max(0, self.lm_profile_box.findData(settings.get("lmstudio_load_profile", "low_vram")))
        )
        self.llama_server_edit.setText(str(settings.get("llama_server_path", "")))
        self.llama_model_edit.setText(str(settings.get("llama_model_path", "")))
        self.llama_mmproj_edit.setText(str(settings.get("llama_mmproj_path", "")))
        self.llama_context.setValue(int(settings.get("llama_context_length", core.LLAMA_CPP_DEFAULT_CONTEXT_LENGTH)))
        self.llama_gpu_layers.setValue(int(settings.get("llama_gpu_layers", core.LLAMA_CPP_DEFAULT_GPU_LAYERS)))
        self.mtp_check.setChecked(bool(settings.get("enable_mtp", False)))
        self.thinking_check.setChecked(bool(settings.get("remove_thinking_tags", True)))
        sampling = core.normalize_sampling(settings.get("sampling"))
        self.max_tokens.setValue(int(sampling["max_tokens"]))
        self.temperature.setValue(float(sampling["temperature"]))
        self.top_p.setValue(float(sampling["top_p"]))
        self.top_k.setValue(int(sampling["top_k"]))
        self.frequency.setValue(float(sampling["frequency_penalty"]))
        self.presence.setValue(float(sampling["presence_penalty"]))
        self.seed.setText("" if sampling["seed"] is None else str(sampling["seed"]))
        self.concurrency.setValue(int(settings.get("concurrency", 3)))
        self.prompt_edit.setPlainText(str(settings.get("user_prompt", "")))
        self._backend_changed()

    def _model_for_provider(self, provider_key: str) -> str:
        saved = self._provider_model_memory.get(provider_key, "")
        if saved:
            return saved
        provider = core.API_PROVIDERS.get(provider_key)
        if provider_key == core.DEFAULT_PROVIDER_KEY:
            model = core.MODELS.get(self.settings.get("model_key", core.DEFAULT_MODEL_KEY))
            return model.model_id if model else provider.default_model
        return provider.default_model if provider else ""

    def _backend_changed(self) -> None:
        is_api = self.backend_box.currentData() == "api"
        for widget in (
            self.provider_box, self.model_edit, self.api_key_edit,
            self.endpoint_edit, self.provider_test_button,
        ):
            widget.setEnabled(is_api)
        for widget in (
            self.runtime_box, self.local_folder_edit, self.lm_url_edit,
            self.lm_model_edit, self.lm_profile_box, self.lm_refresh_button,
            self.lm_load_button, self.lm_unload_button, self.lm_instance_edit,
            self.llama_server_edit, self.llama_model_edit, self.llama_mmproj_edit,
            self.llama_context, self.llama_gpu_layers, self.mtp_check,
        ):
            widget.setEnabled(not is_api)
        self.backend_badge.setText("外部 API" if is_api else "本地模型")
        self._provider_changed()
        self._runtime_changed()

    def _provider_changed(self) -> None:
        key = str(self.provider_box.currentData() or core.DEFAULT_PROVIDER_KEY)
        if self.provider_box.isEnabled():
            self.model_edit.setText(self._model_for_provider(key))
        custom = key == "custom"
        self.endpoint_edit.setEnabled(custom and self.backend_box.currentData() == "api")
        self.endpoint_edit.setVisible(custom)

    def _runtime_changed(self) -> None:
        local = self.backend_box.currentData() == "local"
        runtime = self.runtime_box.currentData()
        self.local_folder_edit.setEnabled(local and runtime == "huggingface")
        self.lm_url_edit.setEnabled(local and runtime == "lmstudio")
        self.lm_model_edit.setEnabled(local and runtime == "lmstudio")
        self.lm_profile_box.setEnabled(local and runtime == "lmstudio")
        self.lm_refresh_button.setEnabled(local and runtime == "lmstudio")
        self.lm_load_button.setEnabled(local and runtime == "lmstudio")
        self.lm_unload_button.setEnabled(local and runtime == "lmstudio")
        self.lm_instance_edit.setEnabled(local and runtime == "lmstudio")
        for widget in (self.llama_server_edit, self.llama_model_edit, self.llama_mmproj_edit):
            widget.setEnabled(local and runtime == "llamacpp")
        self.llama_context.setEnabled(local and runtime == "llamacpp")
        self.llama_gpu_layers.setEnabled(local and runtime == "llamacpp")
        self.mtp_check.setEnabled(local and runtime == "huggingface")

    def _sampling(self) -> dict[str, Any]:
        try:
            seed: int | None = int(self.seed.text().strip()) if self.seed.text().strip() else None
        except ValueError:
            seed = None
        return core.normalize_sampling({
            "max_tokens": self.max_tokens.value(),
            "temperature": self.temperature.value(),
            "top_p": self.top_p.value(),
            "top_k": self.top_k.value(),
            "frequency_penalty": self.frequency.value(),
            "presence_penalty": self.presence.value(),
            "seed": seed,
        })

    def save_settings(self) -> None:
        provider_key = str(self.provider_box.currentData() or core.DEFAULT_PROVIDER_KEY)
        self._provider_model_memory[provider_key] = self.model_edit.text().strip()
        settings = dict(self.settings)
        settings.update({
            "backend": self.backend_box.currentData(),
            "provider_key": provider_key,
            "model_key": settings.get("model_key", core.DEFAULT_MODEL_KEY),
            "api_models": dict(self._provider_model_memory),
            "api_endpoints": {**(settings.get("api_endpoints") or {}), "custom": self.endpoint_edit.text().strip()},
            "custom_api_endpoint": self.endpoint_edit.text().strip(),
            "local_model_folder": self.local_folder_edit.text().strip(),
            "local_runtime": self.runtime_box.currentData(),
            "lmstudio_base_url": self.lm_url_edit.text().strip(),
            "lmstudio_model": self.lm_model_edit.text().strip(),
            "lmstudio_load_profile": self.lm_profile_box.currentData(),
            "llama_server_path": self.llama_server_edit.text().strip(),
            "llama_model_path": self.llama_model_edit.text().strip(),
            "llama_mmproj_path": self.llama_mmproj_edit.text().strip(),
            "llama_context_length": self.llama_context.value(),
            "llama_gpu_layers": self.llama_gpu_layers.value(),
            "enable_mtp": self.mtp_check.isChecked(),
            "remove_thinking_tags": self.thinking_check.isChecked(),
            "sampling": self._sampling(),
            "concurrency": self.concurrency.value(),
            "user_prompt": self.prompt_edit.toPlainText(),
            "theme": self.current_theme,
        })
        self.settings_store.set_api_key(self.api_key_edit.text(), provider_key)
        self.settings_store.save(settings)
        self.settings = self.settings_store.load()
        self.log("设置已保存；API KEY 使用本机安全存储。")
        self.statusBar().showMessage("设置已保存", 3000)

    def _track_aux_worker(self, worker) -> None:
        self._aux_workers.add(worker)
        worker.finished.connect(lambda: self._aux_workers.discard(worker))
        worker.start()

    def test_provider(self) -> None:
        provider_key = str(self.provider_box.currentData() or core.DEFAULT_PROVIDER_KEY)
        worker = ProviderTestWorker(
            provider_key,
            self.api_key_edit.text().strip(),
            self.endpoint_edit.text().strip() if provider_key == "custom" else "",
        )
        worker.succeeded.connect(
            lambda result: self._provider_test_succeeded(provider_key, result)
        )
        worker.failed.connect(lambda message: self._provider_test_failed(message))
        self.provider_test_button.setEnabled(False)
        worker.finished.connect(lambda: self.provider_test_button.setEnabled(True))
        self.log(f"正在测试 {core.API_PROVIDERS[provider_key].label} 连接…")
        self._track_aux_worker(worker)

    def _provider_test_succeeded(self, provider_key: str, result: dict[str, Any]) -> None:
        label = core.API_PROVIDERS[provider_key].label
        latency = result.get("latency_ms", "?")
        self.log(f"连接正常：{label}，HTTP {result.get('status', 200)}，{latency} ms")
        self.statusBar().showMessage("连接测试正常", 3000)

    def _provider_test_failed(self, message: str) -> None:
        self.log(f"连接测试失败：{message}")
        self.statusBar().showMessage("连接测试失败", 3000)

    def refresh_lm_models(self) -> None:
        worker = LmStudioInventoryWorker(self.lm_url_edit.text().strip())
        worker.succeeded.connect(self._lm_models_loaded)
        worker.failed.connect(lambda message: self.log(f"LM Studio 刷新失败：{message}"))
        self.lm_refresh_button.setEnabled(False)
        worker.finished.connect(lambda: self.lm_refresh_button.setEnabled(True))
        self.log("正在读取 LM Studio 模型列表…")
        self._track_aux_worker(worker)

    def _lm_models_loaded(self, models: list[dict[str, Any]]) -> None:
        keys = [str(item.get("key") or "").strip() for item in models if item.get("key")]
        if keys and not self.lm_model_edit.text().strip():
            self.lm_model_edit.setText(keys[0])
        loaded = []
        for item in models:
            loaded.extend(str(value) for value in item.get("loaded_instances") or [])
        if loaded and not self.lm_instance_edit.text().strip():
            self.lm_instance_edit.setText(loaded[0])
        self.log(f"LM Studio 已发现 {len(keys)} 个视觉模型。")

    def load_lm_model(self) -> None:
        worker = LmStudioLoadWorker(
            self.lm_url_edit.text().strip(),
            self.lm_model_edit.text().strip(),
            str(self.lm_profile_box.currentData() or "low_vram"),
        )
        worker.succeeded.connect(self._lm_model_loaded)
        worker.failed.connect(lambda message: self.log(f"LM Studio 加载失败：{message}"))
        self.lm_load_button.setEnabled(False)
        worker.finished.connect(lambda: self.lm_load_button.setEnabled(True))
        self.log("正在加载 LM Studio 模型…")
        self._track_aux_worker(worker)

    def _lm_model_loaded(self, result: dict[str, Any]) -> None:
        self.lm_instance_edit.setText(str(result.get("instance_id") or ""))
        self.log(f"LM Studio 模型已加载：{result.get('instance_id', '')}")

    def unload_lm_model(self) -> None:
        worker = LmStudioUnloadWorker(
            self.lm_url_edit.text().strip(), self.lm_instance_edit.text().strip()
        )
        worker.succeeded.connect(lambda _result: self._lm_model_unloaded())
        worker.failed.connect(lambda message: self.log(f"LM Studio 卸载失败：{message}"))
        self.lm_unload_button.setEnabled(False)
        worker.finished.connect(lambda: self.lm_unload_button.setEnabled(True))
        self.log("正在卸载 LM Studio 模型…")
        self._track_aux_worker(worker)

    def _lm_model_unloaded(self) -> None:
        self.lm_instance_edit.clear()
        self.log("LM Studio 模型已卸载。")

    def apply_preset(self, label: str) -> None:
        presets = {
            "稳定反推": {"temperature": 0.1, "top_p": 0.8, "top_k": 20},
            "平衡反推": {"temperature": 0.2, "top_p": 0.9, "top_k": 0},
            "创意反推": {"temperature": 0.65, "top_p": 0.95, "top_k": 40},
        }
        values = presets.get(label)
        if values:
            self.temperature.setValue(values["temperature"])
            self.top_p.setValue(values["top_p"])
            self.top_k.setValue(values["top_k"])

    def randomize_seed(self) -> None:
        import secrets
        self.seed.setText(str(secrets.randbelow(2_147_483_648)))

    # ----- media and batch flow -----------------------------------------
    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择媒体目录", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self.scan_current_folder()

    def choose_path(self, field: QLineEdit, directory: bool) -> None:
        if directory:
            value = QFileDialog.getExistingDirectory(self, "选择目录")
        else:
            value, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if value:
            field.setText(value)

    def _choose_local_folder(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "选择 Hugging Face 模型目录")
        if value:
            self.local_folder_edit.setText(value)

    def scan_current_folder(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            return
        mode = str(self.mode_box.currentData())
        scan = core.scan_media(folder, mode)
        self.current_folder = folder
        self.media_paths = list(scan.files)
        self.material_list.clear()
        for path in self.media_paths:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.material_list.addItem(item)
        self.log(f"扫描完成：{len(self.media_paths)} 个{('图片' if mode == 'image' else '视频')}素材。")
        self.progress.setValue(0)

    def add_dropped_files(self, paths: list[Path]) -> None:
        valid = [path for path in paths if path.suffix.casefold() in (
            core.IMAGE_EXTENSIONS if self.mode_box.currentData() == "image" else core.VIDEO_EXTENSIONS
        )]
        for path in valid:
            if path not in self.media_paths:
                self.media_paths.append(path)
                item = QListWidgetItem(path.name)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                self.material_list.addItem(item)
        if valid:
            self.current_folder = valid[0].parent
            self.folder_edit.setText(str(self.current_folder))
            self.log(f"已加入 {len(valid)} 个素材，可使用“单次反推”处理。")

    def open_media_editor(self) -> None:
        dialog = SingleMediaDialog(self._accept_single_clip, self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def _accept_single_clip(self, path: Path) -> None:
        self.select_mode("video")
        self.add_dropped_files([path])
        self.focus_single_reverse()
        self.log(f"片段已准备完成：{path.name}，可点击“单次反推”。")

    def _batch_kwargs(self, paths: list[Path], write_output: bool) -> dict[str, Any]:
        self.save_settings()
        provider_key = str(self.provider_box.currentData() or core.DEFAULT_PROVIDER_KEY)
        runtime = str(self.runtime_box.currentData() or "huggingface")
        backend = str(self.backend_box.currentData() or "api")
        model_key = str(self.settings.get("model_key", core.DEFAULT_MODEL_KEY))
        folder = self.current_folder or Path(self.folder_edit.text().strip())
        if not folder.is_dir() and paths:
            folder = paths[0].parent
        return {
            "folder": folder,
            "mode": str(self.mode_box.currentData()),
            "prompt": self.prompt_edit.toPlainText().strip() or "请准确描述素材内容。",
            "model_key": model_key,
            "api_key": self.settings_store.get_api_key(provider_key),
            "concurrency": self.concurrency.value(),
            "skip_existing": write_output,
            "caption_style": "natural",
            "subject_filter": "",
            "backend": backend,
            "local_model_folder": self.local_folder_edit.text().strip(),
            "local_runtime": runtime,
            "lmstudio_base_url": self.lm_url_edit.text().strip(),
            "lmstudio_model": self.lm_model_edit.text().strip(),
            "llama_server_path": self.llama_server_edit.text().strip(),
            "llama_model_path": self.llama_model_edit.text().strip(),
            "llama_mmproj_path": self.llama_mmproj_edit.text().strip(),
            "llama_context_length": self.llama_context.value(),
            "llama_gpu_layers": self.llama_gpu_layers.value(),
            "labeling_focus": str(self.settings.get("labeling_focus", "subject")),
            "output_language": str(self.settings.get("output_language", "zh")),
            "trigger_word": str(self.settings.get("trigger_word", "")),
            "provider_key": provider_key,
            "api_model": self.model_edit.text().strip(),
            "api_endpoint": self.endpoint_edit.text().strip() if provider_key == "custom" else "",
            "sampling": self._sampling(),
            "only_paths": paths or None,
            "video_preflight": True,
            "enable_mtp": self.mtp_check.isChecked(),
            "remove_thinking_tags": self.thinking_check.isChecked(),
            "write_output": write_output,
        }

    def run_batch(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.media_paths:
            self.scan_current_folder()
        if not self.media_paths:
            QMessageBox.information(self, "没有素材", "请先选择目录并扫描素材。")
            return
        self._start_worker(self._batch_kwargs([], True))

    def run_single(self) -> None:
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.material_list.selectedItems()]
        paths = [Path(value) for value in selected if value]
        if not paths and self.media_paths:
            paths = [self.media_paths[0]]
        if not paths:
            QMessageBox.information(self, "没有素材", "请拖入或选择一个图片/视频文件。")
            return
        self._start_worker(self._batch_kwargs(paths[:1], False))

    def _start_worker(self, kwargs: dict[str, Any]) -> None:
        self.log("任务开始：核心推理由 BatchRunner 后台线程执行。")
        self.progress.setValue(0)
        self.start_button.setEnabled(False)
        self.single_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker = BatchWorker(kwargs)
        self.worker.event_received.connect(self.handle_event)
        self.worker.completed.connect(self.task_completed)
        self.worker.failed.connect(self.task_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def stop_batch(self) -> None:
        if self.worker is not None:
            self.log("正在请求停止任务…")
            self.worker.cancel()

    @Slot(str, object)
    def handle_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "progress":
            total = max(1, int(payload.get("total", 1)))
            self.progress.setValue(round(100 * int(payload.get("completed", 0)) / total))
        elif kind == "status":
            path = Path(payload.get("path", ""))
            detail = str(payload.get("detail", ""))
            elapsed = payload.get("elapsed_seconds")
            if payload.get("status") == "success":
                self.result_edit.setPlainText(detail)
                self.metrics.setText(
                    f"{path.name} · {payload.get('character_count', 0)} 字 · "
                    f"{float(payload.get('characters_per_second', 0.0)):.1f} 字/秒 · "
                    f"耗时 {float(elapsed or 0):.2f} 秒"
                )
            self.log(f"{payload.get('status', '')} · {path.name} · {detail}")
        elif kind in {"engine", "scan"}:
            self.log(f"{kind}：{payload}")

    @Slot(object)
    def task_completed(self, summary: core.BatchSummary) -> None:
        self.progress.setValue(100)
        self.log(
            f"任务完成：成功 {summary.success}，跳过 {summary.skipped}，"
            f"失败 {summary.failed}，耗时 {summary.elapsed_seconds:.2f} 秒。"
        )
        self.metrics.setText(
            f"完成 · {summary.characters} 字 · "
            f"{summary.characters / max(0.001, summary.elapsed_seconds):.1f} 字/秒"
        )

    @Slot(str)
    def task_failed(self, message: str) -> None:
        self.log(f"任务异常：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def worker_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.single_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def log(self, text: str) -> None:
        self.log_edit.appendPlainText(text)

    def _selected_paths(self) -> list[Path]:
        values = [item.data(Qt.ItemDataRole.UserRole) for item in self.material_list.selectedItems()]
        selected = [Path(value) for value in values if value]
        return selected or list(self.media_paths[:1])

    def save_current_result(self) -> None:
        paths = self._selected_paths()
        caption = self.result_edit.toPlainText().strip()
        if not paths:
            QMessageBox.information(self, "没有目标素材", "请先扫描或选择一个素材。")
            return
        if not caption:
            QMessageBox.information(self, "结果为空", "当前结果为空，无法保存。")
            return
        core.write_caption(paths[0], caption)
        self.log(f"已保存标注：{paths[0].name}（TXT 已覆盖写入）")
        self.statusBar().showMessage("标注结果已保存", 3000)

    def export_captions(self, kind: str) -> None:
        if not self.media_paths or self.current_folder is None:
            QMessageBox.information(self, "没有素材", "请先选择目录并扫描素材。")
            return
        suffix = ".jsonl" if kind == "jsonl" else ".csv"
        destination, _ = QFileDialog.getSaveFileName(
            self, "导出标注", str(self.current_folder / f"captions{suffix}"),
            "JSONL (*.jsonl)" if kind == "jsonl" else "CSV (*.csv)",
        )
        if not destination:
            return
        count = (
            core.export_jsonl(self.media_paths, Path(destination), self.current_folder)
            if kind == "jsonl"
            else core.export_csv(self.media_paths, Path(destination), self.current_folder)
        )
        self.log(f"已导出 {count} 条标注：{destination}")

    def check_updates(self) -> None:
        worker = UpdateCheckWorker()
        worker.succeeded.connect(self._update_checked)
        worker.failed.connect(lambda message: self.log(f"检查更新失败：{message}"))
        self.update_button.setEnabled(False)
        worker.finished.connect(lambda: self.update_button.setEnabled(True))
        self.log("正在检查 GitHub 最新版本…")
        self._track_aux_worker(worker)

    def _update_checked(self, release: dict[str, Any]) -> None:
        tag = str(release.get("tag") or release.get("name") or "未知版本")
        if release.get("is_newer"):
            self.system_info.setHtml(
                f"<h2>发现新版本 {tag}</h2>"
                "<p>当前是演示架构版本，更新前请先保存任务和设置。</p>"
                "<p>稳定版仍可通过经典工作台执行应用内覆盖安装。</p>"
            )
            self.log(f"发现新版本：{tag}，请在经典工作台执行覆盖安装。")
        else:
            self.log(f"当前已是最新版本（当前 {APP_VERSION}，远端 {tag}）。")
        self.statusBar().showMessage("更新检查完成", 3000)

    # ----- theme / migration --------------------------------------------
    def _qss(self, theme: dict[str, str]) -> str:
        return build_stylesheet(theme)

    def apply_theme(self, theme_name: str) -> None:
        self.current_theme = theme_name if theme_name in THEMES else "night"
        self.setStyleSheet(self._qss(THEMES[self.current_theme]))
        self.statusBar().showMessage(
            "夜光模式" if self.current_theme == "night" else "日光模式", 1800
        )

    def toggle_theme(self) -> None:
        self.apply_theme("day" if self.current_theme == "night" else "night")
        self.settings["theme"] = self.current_theme
        self.settings_store.save(self.settings)

    def open_legacy_workbench(self) -> None:
        """Keep all legacy-only dialogs available during incremental migration."""
        source = Path(__file__).with_name("media_caption_tool_v3.py")
        if not source.is_file():
            QMessageBox.warning(self, "经典工作台不可用", "找不到 Tk 版入口文件。")
            return
        import subprocess
        subprocess.Popen([sys.executable, str(source)], cwd=str(source.parent))
        self.statusBar().showMessage("已打开经典完整工作台", 3000)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2500)
        try:
            self.save_settings()
        except Exception:
            pass
        event.accept()


def main(argv: list[str] | None = None) -> int:
    _ensure_qt()
    args = argv if argv is not None else sys.argv[1:]
    if "--smoke-test" in args:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("Qianyi Media Caption Tool")
    app.setFont(_font(14))
    window = ModernMainWindow()
    if "--smoke-test" in args:
        window.show()
        app.processEvents()
        assert window.columns.count() == 3
        assert window.backend_box.count() == 2
        assert window.runtime_box.count() == 3
        print("QT_SMOKE_OK")
        window.close()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
