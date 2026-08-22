from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle

from .backend import AppBackend


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--smoke-test" in args:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("芊熠智能打标工作台")
    app.setApplicationVersion("3.6.demo")
    app.setOrganizationName("Qianyi Studio")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    QQuickStyle.setStyle("Basic")
    icon = Path(__file__).resolve().parent.parent / "assets" / "qianyi-app.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    engine = QQmlApplicationEngine()
    backend = AppBackend()
    engine.rootContext().setContextProperty("backend", backend)
    qml_path = Path(__file__).resolve().parent / "qml" / "App.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 2
    if "--smoke-test" in args:
        app.processEvents()
        root = engine.rootObjects()[0]
        assert root.property("visible") is True
        print("QML_SMOKE_OK")
        root.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

