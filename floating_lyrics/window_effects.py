from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QMessageBox

WINDOW_FLAGS = (
    Qt.WindowType.Tool
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
)
FOREGROUND = Qt.WindowType.WindowStaysOnTopHint


def exec_dialog(dialog):
    dialog.setWindowFlag(FOREGROUND)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog.exec()


def show_message(parent, title, text, icon=QMessageBox.Icon.Warning):
    dialog = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    exec_dialog(dialog)


def set_click_through(window, enabled):
    # Qt maps this flag to the layered-window input transparency on Windows.
    visible = window.isVisible()
    geometry = window.geometry()
    window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
    window.setGeometry(geometry)
    if visible:
        window.show()


def paint_background(window, appearance, theme):
    painter = QPainter(window)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    opacity = max(0.01, appearance.opacity / 100)
    if theme.background:
        gradient = QLinearGradient(window.width(), 0, 0, window.height())
        for position, value in zip((0, 0.56, 1), theme.background):
            color = QColor(value)
            color.setAlphaF(opacity)
            gradient.setColorAt(position, color)
        painter.setBrush(gradient)
    else:
        background = QColor(appearance.background)
        background.setAlphaF(opacity)
        painter.setBrush(background)
    border = QColor(theme.accent)
    border.setAlpha(76 if theme.background else 35)
    painter.setPen(QPen(border, 1))
    painter.drawRoundedRect(window.rect().adjusted(1, 1, -1, -1), 13, 13)

