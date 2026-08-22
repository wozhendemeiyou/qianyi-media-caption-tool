"""Small, reusable controls used by the modern workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)


class DropListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        paths = [path for path in paths if path.is_file()]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()


class SectionCard(QGroupBox):
    """Card with consistent heading metrics and form spacing."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.setObjectName("card")
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(8)


class PathPicker(QWidget):
    """A line edit with a native file/folder picker and stable geometry."""

    changed = Signal(str)

    def __init__(self, *, directory: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.directory = directory
        self.edit = QLineEdit()
        self.button = QPushButton("选择…")
        self.button.clicked.connect(self.choose)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.edit.setEnabled(enabled)
        self.button.setEnabled(enabled)

    def choose(self) -> None:
        if self.directory:
            value = QFileDialog.getExistingDirectory(self, "选择目录", self.text())
        else:
            value, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if value:
            self.setText(value)
            self.changed.emit(value)
