"""Small, reusable controls used by the modern workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
    QVBoxLayout,
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


class EditableComboBox(QComboBox):
    """Editable combo that keeps the simple text API used by form state code."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)

    def text(self) -> str:
        return self.currentText()

    def setText(self, value: str) -> None:  # noqa: N802
        self.setEditText(value)

    def setPlaceholderText(self, value: str) -> None:  # noqa: N802
        if self.lineEdit() is not None:
            self.lineEdit().setPlaceholderText(value)


class SectionCard(QFrame):
    """Card with a custom heading, avoiding native QGroupBox paint artifacts."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        heading = QLabel(title)
        heading.setObjectName("cardHeading")
        outer.addWidget(heading)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(8)
        outer.addWidget(self.body)


class ContentCard(QFrame):
    """Card with a free-form body layout for prompts and media previews."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        heading = QLabel(title)
        heading.setObjectName("cardHeading")
        outer.addWidget(heading)
        self.body_layout = QVBoxLayout()
        outer.addLayout(self.body_layout)


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
