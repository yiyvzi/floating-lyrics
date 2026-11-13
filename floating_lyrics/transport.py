from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolButton, QToolTip

from .themes import THEMES


class TransportButton(QToolButton):
    def __init__(self, symbol, label, *, primary=False):
        super().__init__()
        self.symbol = symbol
        self.primary = primary
        self.theme = THEMES["classic"]
        size = 50 if primary else 42
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.hover_fade = QVariantAnimation(self, duration=140)
        self.hover_fade.setStartValue(0.0)
        self.hover_fade.setEndValue(1.0)
        self.hover_fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.hover_fade.valueChanged.connect(self.update)
        self.set_symbol(symbol, label)

    def set_symbol(self, symbol, label):
        if self.symbol != symbol:
            self.symbol = symbol
            self.update()
        if self.accessibleName() != label:
            self.setAccessibleName(label)
            self.setToolTip(label)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.animate_hover(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.animate_hover(False)

    def animate_hover(self, hovering):
        direction = QVariantAnimation.Direction.Forward if hovering else QVariantAnimation.Direction.Backward
        self.hover_fade.setDirection(direction)
        self.hover_fade.start()

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.toolTip():
            # Keep the tooltip above the hit area, including at the bottom of the desktop.
            anchor = self.mapToGlobal(QPoint(0, -QFontMetrics(QToolTip.font()).height() - 18))
            QToolTip.showText(anchor, self.toolTip(), self, self.rect())
            return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        bounds = QRectF(1 - self.width() / 2, 1 - self.height() / 2,
                        self.width() - 2, self.height() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        enabled = self.isEnabled()
        theme = self.theme
        hover = self.hover_fade.currentValue()
        if self.primary:
            fill = QColor(theme.accent)
            if not enabled:
                fill.setAlpha(48)
            elif self.isDown():
                fill = fill.darker(120)
            else:
                fill = fill.lighter(100 + round(10 * hover))
            painter.setBrush(fill)
            painter.drawEllipse(bounds)
        elif enabled and (hover or self.isDown()):
            tint = QColor(theme.accent)
            tint.setAlpha(88 if self.isDown() else round(62 * hover))
            painter.setBrush(tint)
            painter.drawRoundedRect(bounds, 8, 8)
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.highlight), 1))
            painter.drawLine(QPointF(-6, 18), QPointF(6, 18))
            painter.setPen(Qt.PenStyle.NoPen)

        icon = QColor(theme.on_accent if self.primary and enabled else theme.artist)
        if not enabled:
            icon.setAlpha(120)
        painter.setBrush(icon)
        painter.scale(1.25, 1.25)
        if self.symbol == "pause":
            for x in (-5, 2):
                painter.drawRoundedRect(QRectF(x, -6, 3, 12), 1, 1)
        elif self.symbol == "play":
            painter.drawPolygon(QPolygonF([QPointF(-4, -6), QPointF(6, 0), QPointF(-4, 6)]))
        else:
            if self.symbol == "previous":
                painter.scale(-1, 1)
            painter.drawPolygon(QPolygonF([QPointF(-6, -5.5), QPointF(3, 0), QPointF(-6, 5.5)]))
            painter.drawRoundedRect(QRectF(4, -5.5, 2, 11), 0.75, 0.75)
