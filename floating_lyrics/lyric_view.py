from bisect import bisect_right

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QRectF, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QPainter, QPalette, QTextCharFormat,
    QTextCursor, QTextDocument, QTextOption,
)
from PySide6.QtWidgets import QAbstractScrollArea, QFrame

from .themes import THEMES


class LyricView(QAbstractScrollArea):
    follow_changed = Signal(bool)
    seek_requested = Signal(int)

    def __init__(self, appearance):
        super().__init__()
        self.appearance = appearance
        self.lines = ()
        self.empty_message = ""
        self.active = -1
        self.following = True
        self.follow_timer = QTimer(self, singleShot=True)
        self.follow_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.follow_timer.timeout.connect(self.resume_follow)
        self.show_translation = True
        self.documents = []
        self.highlight = None
        self.row_starts = [0]
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent;")
        self.viewport().setAutoFillBackground(False)
        self.animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)

    def set_lyrics(self, lyrics):
        self.animation.stop()
        self.lines = lyrics.lines
        self.active = -1
        self.verticalScrollBar().setValue(0)
        self.reflow()
        self.resume_follow(animated=False)

    def set_empty_message(self, message):
        if message != self.empty_message:
            self.empty_message = message
            self.setAccessibleDescription(message)
            self.viewport().update()

    def reflow(self):
        self.animation.stop()
        bar = self.verticalScrollBar()
        old_row = max(0, bisect_right(self.row_starts, bar.value()) - 1)
        self.documents = [self.make_document(line) for line in self.lines]
        self.remeasure()
        if self.following:
            self.center_active(animated=False)
        else:
            bar.setValue(round(self.row_starts[min(old_row, len(self.lines))]))
        self.viewport().update()

    def make_document(self, line, *, active=False):
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(self.appearance.font_size + (5 if active else 0))
        if active:
            font.setWeight(QFont.Weight.DemiBold)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(font)
        document.setDefaultTextOption(option)
        document.setPlainText(line.text or " ")
        if self.show_translation and line.translation:
            cursor = QTextCursor(document)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertBlock()
            font.setWeight(QFont.Weight.Normal)
            font.setPixelSize(max(10, round(self.appearance.font_size * 0.8)) + (2 if active else 0))
            style = QTextCharFormat()
            style.setFont(font)
            cursor.insertText(line.translation, style)
        document.setTextWidth(max(1, self.viewport().width() - 34))
        return document

    def remeasure(self):
        self.highlight = None
        if self.active >= 0 and self.lines[self.active].text:
            self.highlight = self.make_document(self.lines[self.active], active=True)
        self.row_starts = [0]
        for row, normal in enumerate(self.documents):
            document = self.highlight if row == self.active and self.highlight is not None else normal
            self.row_starts.append(
                self.row_starts[-1] + document.size().height() + self.appearance.line_spacing
            )
        bar = self.verticalScrollBar()
        bar.setPageStep(self.viewport().height())
        last_center = (self.row_starts[-2] + self.row_starts[-1]) / 2 if self.lines else 0
        bar.setRange(0, round(last_center))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def set_active(self, row, *, animated=True):
        if row == self.active:
            return
        bar = self.verticalScrollBar()
        anchor = max(0, bisect_right(self.row_starts, bar.value()) - 1)
        offset = bar.value() - self.row_starts[anchor]
        wheel_target = self.pending_wheel_target()
        if wheel_target is not None:
            target_row = bisect_right(self.row_starts, wheel_target) - 1
            target_offset = wheel_target - self.row_starts[target_row]
        self.active = row
        self.remeasure()
        bar.setValue(round(self.row_starts[anchor] + offset))
        if self.following:
            self.center_active(animated=animated)
        elif wheel_target is not None:
            remaining = max(40, self.animation.duration() - self.animation.currentTime())
            self.scroll_to(round(self.row_starts[target_row] + target_offset), remaining)
        self.viewport().update()

    def center_active(self, *, animated, returning=False):
        row = max(0, self.active)
        target = round((self.row_starts[row] + self.row_starts[row + 1]) / 2) if self.lines else 0
        duration = 360
        if returning:
            distance = abs(target - self.verticalScrollBar().value())
            duration = min(850, 430 + distance // 2)
        curve = QEasingCurve.Type.InOutCubic if returning else QEasingCurve.Type.OutCubic
        self.scroll_to(target, duration if animated else 0, curve)

    def scroll_to(self, target, duration, curve=QEasingCurve.Type.OutCubic):
        bar = self.verticalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), target))
        self.animation.stop()
        if duration and target != bar.value():
            self.animation.setDuration(duration)
            self.animation.setEasingCurve(curve)
            self.animation.setStartValue(bar.value())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            bar.setValue(target)

    def resume_follow(self, *, animated=True):
        self.follow_timer.stop()
        returning = not self.following
        if returning:
            self.following = True
            self.follow_changed.emit(True)
        self.center_active(animated=animated, returning=returning)

    def pending_wheel_target(self):
        if not self.following and self.animation.state() == QAbstractAnimation.State.Running:
            return self.animation.endValue()
        return None

    def restart_follow_timer(self):
        self.follow_timer.stop()
        if not self.following and self.appearance.follow_delay:
            self.follow_timer.start(self.appearance.follow_delay * 1000)

    def wheelEvent(self, event):
        if not self.lines:
            event.ignore()
            return
        pixels = event.pixelDelta().y()
        delta = pixels or event.angleDelta().y() / 120 * 52
        if not delta:
            event.ignore()
            return
        bar = self.verticalScrollBar()
        pending = self.pending_wheel_target()
        start = bar.value()
        if not pixels and pending is not None and (pending - start) * delta < 0:
            start = pending
        if self.following:
            self.following = False
            self.follow_changed.emit(False)
        self.restart_follow_timer()
        # Trackpads already supply continuous pixel deltas and momentum.
        self.scroll_to(start - round(delta), 0 if pixels else 165)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y() - self.viewport().height() / 2 + self.verticalScrollBar().value()
            row = bisect_right(self.row_starts, y) - 1
            if 0 <= row < len(self.lines) and self.lines[row].text:
                self.seek_requested.emit(self.lines[row].time_ms)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.lines and self.empty_message:
            self.paint_empty_message(painter)
            return
        top = self.viewport().height() / 2 - self.verticalScrollBar().value()
        theme = THEMES[self.appearance.theme]
        first = max(0, bisect_right(self.row_starts, -top) - 1)
        for row in range(first, len(self.documents)):
            y = self.row_starts[row] + top
            if y > self.viewport().height():
                break
            document = self.documents[row]
            color = QColor(theme.past if row < self.active else theme.lyric)
            if row == self.active and self.highlight is not None:
                document = self.highlight
                color = QColor(theme.highlight)
                painter.setPen(Qt.PenStyle.NoPen)
                tint = QColor(theme.accent)
                tint.setAlpha(theme.highlight_alpha)
                painter.setBrush(tint)
                painter.drawRoundedRect(
                    QRectF(0, y + 1, self.viewport().width(), self.row_starts[row + 1] - self.row_starts[row] - 2),
                    8, 8,
                )
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(6, y + self.appearance.line_spacing / 2 + 4, 2, 15), 1, 1)

            context = QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QPalette.ColorRole.Text, color)
            painter.save()
            painter.translate(17, y + self.appearance.line_spacing / 2)
            document.documentLayout().draw(painter, context)
            painter.restore()

    def paint_empty_message(self, painter):
        theme = THEMES[self.appearance.theme]
        width = self.viewport().width()
        height = self.viewport().height()
        painter.translate(width / 2, height / 2)
        if height >= 72:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.accent))
            for x, size in ((-9, 8), (-1, 16), (7, 11)):
                painter.drawRoundedRect(QRectF(x, -19 - size / 2, 2, size), 1, 1)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(theme.lyric))
        painter.drawText(QRectF(-width / 2, 0 if height >= 72 else -13, width, 26),
                         Qt.AlignmentFlag.AlignCenter, self.empty_message)

