"""Instant, token-based Qt theme styles."""

from __future__ import annotations

from typing import Mapping

THEMES: dict[str, dict[str, str]] = {
    "day": {
        "window": "#f2eee5",
        "surface": "#fbf8f1",
        "surface_alt": "#e9e3d7",
        "input": "#fffdf8",
        "border": "#c8bda9",
        "text": "#282522",
        "muted": "#6f675b",
        "accent": "#d75537",
        "accent_soft": "#f2d5c7",
        "success": "#3b8061",
    },
    "night": {
        "window": "#20242b",
        "surface": "#292f37",
        "surface_alt": "#343b45",
        "input": "#252b33",
        "border": "#4b5664",
        "text": "#eef0eb",
        "muted": "#aeb6b2",
        "accent": "#f27756",
        "accent_soft": "#553b38",
        "success": "#80c49a",
    },
}


def build_stylesheet(theme: Mapping[str, str]) -> str:
    """Build one complete stylesheet so theme changes are atomic and instant."""
    return f"""
    QWidget#root, QMainWindow {{ background: {theme['window']}; color: {theme['text']}; }}
    QFrame#header, QFrame#card, QGroupBox#card {{ background: {theme['surface']}; border: 1px solid {theme['border']}; border-radius: 12px; }}
    QGroupBox#card {{ margin-top: 12px; padding: 18px 10px 10px 10px; }}
    QGroupBox#card::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {theme['text']}; }}
    QLabel {{ color: {theme['text']}; }}
    QLabel#muted {{ color: {theme['muted']}; }}
    QLabel#badge {{ background: {theme['accent_soft']}; color: {theme['accent']}; border-radius: 9px; padding: 5px 10px; font-weight: 600; }}
    QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {theme['input']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 8px; padding: 7px 9px; selection-background-color: {theme['accent']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {theme['accent']}; padding: 6px 8px; }}
    QComboBox::drop-down {{ border: 0; width: 28px; }}
    QPushButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 8px; padding: 8px 12px; }}
    QPushButton:hover {{ border-color: {theme['accent']}; }}
    QPushButton#primary {{ background: {theme['accent']}; color: #fffaf4; border: 0; font-weight: 700; }}
    QPushButton:disabled {{ color: {theme['muted']}; background: {theme['surface_alt']}; }}
    QListWidget {{ background: {theme['input']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 10px; padding: 6px; }}
    QListWidget::item {{ padding: 9px 8px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {theme['accent_soft']}; color: {theme['text']}; }}
    QTabWidget::pane {{ border: 1px solid {theme['border']}; border-radius: 8px; background: {theme['input']}; }}
    QTabBar::tab {{ background: {theme['surface_alt']}; color: {theme['text']}; padding: 8px 14px; margin-right: 2px; border-radius: 6px; }}
    QTabBar::tab:selected {{ background: {theme['accent']}; color: #fffaf4; }}
    QToolBar {{ background: {theme['surface']}; border: 0; spacing: 6px; padding: 5px; }}
    QToolButton {{ color: {theme['text']}; padding: 6px 9px; border-radius: 6px; }}
    QToolButton:hover {{ background: {theme['accent_soft']}; }}
    QProgressBar {{ background: {theme['surface_alt']}; border: 0; border-radius: 6px; text-align: center; color: {theme['text']}; }}
    QProgressBar::chunk {{ background: {theme['accent']}; border-radius: 6px; }}
    QScrollBar:vertical {{ background: {theme['surface_alt']}; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {theme['border']}; min-height: 32px; border-radius: 6px; }}
    QSplitter::handle {{ background: {theme['border']}; width: 5px; }}
    QStatusBar {{ background: {theme['surface']}; color: {theme['muted']}; border-top: 1px solid {theme['border']}; }}
    """
