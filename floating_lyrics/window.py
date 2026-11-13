from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QSpinBox,
    QToolButton, QVBoxLayout, QWidget, QSizePolicy,
)
from PySide6.QtGui import QColor

from .config import APPEARANCE_LIMITS, ROOT, save_appearance, save_window_state
from .lrc import Lyrics, load_lrc, parse_lrc
from .lyric_sources import PublicLyrics, local_lyrics
from .lyric_view import LyricView
from .sync import LyricTimeline, ManualClock
from .themes import THEMES
from .transport import TransportButton
from .window_effects import (
    FOREGROUND, WINDOW_FLAGS, exec_dialog, paint_background, set_click_through, show_message,
)


class SongLabel(QLabel):
    def __init__(self, role, max_lines=1):
        super().__init__(objectName=role)
        self.full_text = ""
        self.max_lines = max_lines
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_song_text(self, text):
        self.full_text = " ".join(text.split())
        self.setToolTip(self.full_text)
        self.setVisible(bool(self.full_text))
        self.fit_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_text()

    def fit_text(self):
        width = self.contentsRect().width()
        metrics = self.fontMetrics()
        rest = self.full_text
        if self.max_lines == 2 and metrics.horizontalAdvance(rest) > width:
            end = next((index for index in range(1, len(rest) + 1)
                        if metrics.horizontalAdvance(rest[:index]) > width), len(rest) + 1) - 1
            if end > 0:
                space = rest.rfind(" ", 0, end + 1)
                split = space if space > 0 else end
                self.setText(rest[:split] + "\n" + metrics.elidedText(
                    rest[split:].lstrip(), Qt.TextElideMode.ElideRight, width
                ))
                return
        self.setText(metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width))


class FloatingWindow(QWidget):
    playback_requested = Signal(str, object, object)
    player_selected = Signal(str)

    def __init__(self, appearance, shortcuts):
        super().__init__(None, WINDOW_FLAGS)
        self.remember_window = False
        self.player_preference = "auto"
        self.appearance = appearance
        self.shortcuts = shortcuts
        self.clock = ManualClock()
        self.media_enabled = False
        self.latest_media = None
        self.media_error = ""
        self.active_track = None
        self.calibrated = False
        self.public_lyrics = PublicLyrics(self)
        self.public_lyrics.finished.connect(self.apply_public_lyrics)
        self.lookup_status = ""
        self.waiting_for_qq_duration = False
        self.click_through = False
        self.can_click_through = False
        self.can_hide = False
        self.locked = False
        self.menu = None
        self.setWindowTitle("Floating Lyrics")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 215)
        self.resize(appearance.width, appearance.height)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 15, 17, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(5)
        metadata = QVBoxLayout()
        metadata.setSpacing(3)
        self.title = SongLabel("title", max_lines=2)
        self.artist = SongLabel("artist")
        self.album = SongLabel("album")
        for label in (self.title, self.artist, self.album):
            metadata.addWidget(label)
        header.addLayout(metadata, 1)
        menu_button = QToolButton(text="…", objectName="overlayMenu")
        menu_button.setProperty("overlayControl", True)
        menu_button.setToolTip("歌词和窗口选项")
        menu_button.setAccessibleName("歌词和窗口选项")
        menu_button.setFixedSize(27, 27)
        menu_button.clicked.connect(lambda: self.show_menu(menu_button))
        header.addWidget(menu_button, alignment=Qt.AlignmentFlag.AlignTop)
        close_button = QToolButton(text="×")
        close_button.setProperty("overlayControl", True)
        close_button.setToolTip("退出 Floating Lyrics")
        close_button.setAccessibleName("退出 Floating Lyrics")
        close_button.setFixedSize(27, 27)
        close_button.clicked.connect(self.close)
        header.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        divider = QWidget(objectName="divider")
        divider.setFixedHeight(1)
        divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(divider)
        self.status = QLabel(objectName="status")
        self.status.setWordWrap(True)
        self.status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.status)
        self.lyrics_view = LyricView(appearance)
        self.lyrics_view.seek_requested.connect(self.seek_from_lyric)
        layout.addWidget(self.lyrics_view, 1)
        self.follow_button = QToolButton(text="恢复自动跟随")
        self.follow_button.setProperty("overlayControl", True)
        follow_size = self.follow_button.sizePolicy()
        follow_size.setRetainSizeWhenHidden(True)
        self.follow_button.setSizePolicy(follow_size)
        self.follow_button.clicked.connect(self.lyrics_view.resume_follow)
        self.follow_button.hide()
        self.lyrics_view.follow_changed.connect(lambda following: self.follow_button.setVisible(not following))
        layout.addWidget(self.follow_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.transport_bar = QWidget()
        transport = QHBoxLayout(self.transport_bar)
        transport.setContentsMargins(0, 2, 0, 0)
        transport.setSpacing(15)
        self.previous_button = TransportButton("previous", "上一首")
        self.play_button = TransportButton("play", "播放", primary=True)
        self.next_button = TransportButton("next", "下一首")
        for button, action in (
            (self.previous_button, "previous"), (self.play_button, "toggle"),
            (self.next_button, "next"),
        ):
            button.clicked.connect(lambda checked=False, command=action: self.request_playback(command))
            transport.addWidget(button, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.transport_bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.update_transport()
        self.apply_theme()

        self.timer = QTimer(self, interval=40)
        self.timer.timeout.connect(self.update_position)
        self.open_sample()
        self.timer.start()
        self.window_state_timer = QTimer(self, singleShot=True, interval=350)
        self.window_state_timer.timeout.connect(self.save_window_preferences)

    def track_window_state(self):
        self.remember_window = True

    def save_window_preferences(self):
        if self.remember_window:
            try:
                save_window_state(self)
            except OSError:
                pass

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.remember_window:
            self.window_state_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.remember_window:
            self.window_state_timer.start()

    def paintEvent(self, event):
        paint_background(self, self.appearance, THEMES[self.appearance.theme])

    def apply_theme(self):
        theme = THEMES[self.appearance.theme]
        self.setStyleSheet(f"""
            QLabel#title {{ color: {theme.title}; font-size: 25px; font-weight: 700; }}
            QLabel#artist {{ color: {theme.artist}; font-size: 14px; font-weight: 600; }}
            QLabel#album, QLabel#status {{ color: {theme.secondary}; font-size: 12px; }}
            QWidget#divider {{ background: rgba({theme.chrome_rgb}, 47); }}
            QToolButton[overlayControl="true"] {{
                color: {theme.title}; background: rgba({theme.chrome_rgb}, 19);
                border: 0; border-radius: 7px; padding: 4px 7px;
            }}
            QToolButton[overlayControl="true"]:hover {{ background: rgba({theme.chrome_rgb}, 49); }}
            QToolButton#overlayMenu {{ font-size: 15px; padding: 0; }}
        """)
        for button in (self.previous_button, self.play_button, self.next_button):
            button.theme = theme
            button.update()
        self.lyrics_view.viewport().update()
        self.update()

    def closeEvent(self, event):
        self.window_state_timer.stop()
        self.save_window_preferences()
        self.public_lyrics.cancel()
        self.timer.stop()
        QApplication.quit()
        super().closeEvent(event)

    def set_lyrics(self, lyrics, *, autoplay=False):
        self.clock.set_running(False)
        self.clock.seek(0)
        self.display_lyrics(lyrics)
        self.clock.set_running(autoplay)

    def display_lyrics(self, lyrics):
        self.lyrics_view.set_empty_message("")
        self.current_lyrics = lyrics
        self.update_details()
        self.timeline = LyricTimeline(lyrics)
        self.lyrics_view.set_lyrics(lyrics)
        self.update_position(animated=False)

    def update_details(self):
        lyrics = self.current_lyrics
        track = self.active_track if self.media_enabled else None
        self.title.set_song_text(track.title if track else lyrics.title)
        artist = (track.artist or lyrics.artist) if track else lyrics.artist
        album = (track.album or lyrics.album) if track else lyrics.album
        self.artist.set_song_text(artist)
        self.album.set_song_text(album)

    def update_position(self, *, animated=True):
        position = self.clock.position_ms
        if self.media_enabled and not self.calibrated:
            position = self.latest_media.current_position() if self.latest_media else None
        row = self.timeline.active_row(position) if position is not None else -1
        self.lyrics_view.set_active(row, animated=animated)

    def open_sample(self):
        self.disable_media()
        self.set_lyrics(load_lrc(ROOT / "sample.lrc", source="示例计时"), autoplay=True)

    def disable_media(self):
        self.media_enabled = False
        self.active_track = None
        self.waiting_for_qq_duration = False
        self.public_lyrics.cancel()
        self.status.hide()
        self.update_transport()

    def enable_media(self):
        self.media_enabled = True
        self.active_track = None
        self.calibrated = False
        self.lookup_status = ""
        self.set_lyrics(Lyrics((), "Floating Lyrics", "", "", ""))
        self.update_media(self.latest_media, self.media_error)

    def update_media(self, state, error=""):
        self.latest_media, self.media_error = state, error
        self.update_transport()
        if not self.media_enabled:
            return
        if state is None:
            self.calibrated = False
            self.clock.set_running(False)
            self.lyrics_view.set_empty_message("")
            player = {"qq": "QQ 音乐", "netease": "网易云音乐"}.get(self.player_preference)
            self.set_status(error or f"请在{player or ' QQ 音乐或网易云音乐'}开启系统媒体控件并播放。")
            return
        if state.track != self.active_track:
            self.public_lyrics.cancel()
            self.active_track = state.track
            self.calibrated = False
            self.lookup_status = ""
            self.waiting_for_qq_duration = False
            lyrics = local_lyrics(state.track) or Lyrics((), state.track.title, "", "", "")
            self.set_lyrics(lyrics)
            if not lyrics.lines and (state.track.netease_id or state.track.qq_id or state.track.artist):
                if state.track.player == "qq" and not state.track.qq_id and not state.duration_ms and not state.track.album:
                    self.waiting_for_qq_duration = True
                    self.lookup_status = "正在确认歌曲版本…"
                else:
                    self.search_public_lyrics()
        if self.waiting_for_qq_duration and state.duration_ms:
            self.search_public_lyrics()
        if self.calibrated and self.clock.running != state.playing:
            self.clock.set_running(state.playing)
        self.refresh_media_status()

    def set_status(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def refresh_media_status(self):
        state = self.latest_media
        if state is None:
            return
        if not self.lyrics_view.lines and (
            self.lookup_status == "暂无同步歌词"
            or (self.current_lyrics.instrumental and not self.lookup_status)
        ):
            message = "纯音乐 · 请欣赏" if self.current_lyrics.instrumental else "静听此刻"
            self.lyrics_view.set_empty_message(message)
            self.set_status("")
            return
        self.lyrics_view.set_empty_message("")
        label = {
            "PLAYING": "", "PAUSED": "已暂停", "STOPPED": "已停止",
        }.get(state.status, "等待播放")
        if self.calibrated:
            label = " · ".join(filter(None, (label, "手动对齐")))
        elif state.position_ms is None:
            label = " · ".join(filter(None, (label, "无系统进度")))
        if self.lookup_status:
            label = self.lookup_status
        elif not self.lyrics_view.lines:
            label = "可在菜单中查找或导入歌词"
        elif state.position_ms is None and not self.calibrated:
            label = "请设置手动进度"
        self.set_status(label)

    def search_public_lyrics(self, *, refresh=False):
        state = self.latest_media
        if self.media_enabled and state and (state.track.netease_id or state.track.qq_id or state.track.artist):
            self.waiting_for_qq_duration = False
            self.lookup_status = "正在查找时间轴歌词…"
            self.refresh_media_status()
            self.public_lyrics.search(state, refresh=refresh)

    def apply_public_lyrics(self, track, lyrics, error):
        if not self.media_enabled or track != self.active_track:
            return
        self.lookup_status = error
        if lyrics is not None:
            self.display_lyrics(lyrics)
        self.refresh_media_status()

    def toggle_translation(self, enabled):
        self.lyrics_view.show_translation = enabled
        self.save_window_preferences()
        self.lyrics_view.reflow()

    def accept_import(self, lyrics):
        if self.media_enabled and self.latest_media:
            self.waiting_for_qq_duration = False
            self.public_lyrics.cancel()
            self.lookup_status = ""
            self.display_lyrics(lyrics)
            self.refresh_media_status()
        else:
            self.disable_media()
            self.set_lyrics(lyrics)

    def import_file(self):
        track = self.import_track()
        dialog = QFileDialog(self, "导入歌词", "", "LRC 歌词 (*.lrc);;所有文件 (*)")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        path = dialog.selectedFiles()[0] if exec_dialog(dialog) else ""
        if path and self.same_import_track(track):
            try:
                self.accept_import(load_lrc(Path(path)))
            except (OSError, ValueError) as error:
                show_message(self, "导入失败", str(error))

    def paste_lrc(self):
        track = self.import_track()
        text, accepted = QInputDialog.getMultiLineText(
            self, "粘贴 LRC", "粘贴带时间戳的歌词，例如 [00:12.500]歌词内容",
            QApplication.clipboard().text(),
            flags=FOREGROUND,
        )
        if accepted and self.same_import_track(track):
            try:
                self.accept_import(parse_lrc(text, source="粘贴 LRC"))
            except ValueError as error:
                show_message(self, "导入失败", str(error))

    def same_import_track(self, track):
        if track == self.import_track():
            return True
        show_message(self, "曲目已变化", "请为当前歌曲重新导入歌词。", QMessageBox.Icon.Information)
        return False

    def import_track(self):
        return self.latest_media.track if self.media_enabled and self.latest_media else None

    def set_manual_position(self):
        media = self.latest_media if self.media_enabled else None
        position_ms = self.clock.position_ms
        if media and not self.calibrated:
            position_ms = media.current_position() or 0
        position, accepted = QInputDialog.getDouble(
            self, "设置手动进度", "当前歌曲已播放秒数（例如 75.5）",
            position_ms / 1000, 0, 86400, 3,
            flags=FOREGROUND,
        )
        if accepted:
            if media:
                if not self.latest_media or self.latest_media.track != media.track:
                    return
                self.calibrated = True
                self.clock.set_running(self.latest_media.playing)
                self.refresh_media_status()
            self.clock.seek(round(position * 1000))
            self.lyrics_view.resume_follow(animated=False)
            self.update_position(animated=False)

    def seek_from_lyric(self, position_ms):
        state = self.latest_media
        if self.media_enabled and state and state.can_seek:
            self.playback_requested.emit("seek", state.track, position_ms)

    def seek_playback(self):
        state = self.latest_media
        if not self.media_enabled or state is None or not state.can_seek:
            return
        position, accepted = QInputDialog.getDouble(
            self, "跳转播放进度", "跳到第几秒？例如 1 分 15 秒输入 75。",
            (state.current_position() or 0) / 1000, 0, state.duration_ms / 1000, 3,
            flags=FOREGROUND,
        )
        if accepted and self.media_enabled:
            self.playback_requested.emit("seek", state.track, round(position * 1000))

    def playback_result(self, action, error):
        if error:
            show_message(self, "播放控制", error)
        elif action in ("seek", "restart") and self.media_enabled:
            self.calibrated = False
            self.clock.set_running(False)
            self.lyrics_view.resume_follow(animated=False)

    def update_transport(self):
        state = self.latest_media if self.media_enabled else None
        self.transport_bar.setVisible(self.media_enabled)
        self.previous_button.setEnabled(bool(state and state.can_previous))
        self.next_button.setEnabled(bool(state and state.can_next))
        self.play_button.setEnabled(bool(state and state.can_toggle))
        playing = bool(state and state.playing)
        self.play_button.set_symbol("pause" if playing else "play", "暂停" if playing else "播放")

    def request_playback(self, action):
        if self.media_enabled and self.latest_media is not None:
            self.playback_requested.emit(action, self.latest_media.track, None)

    def select_player(self, player):
        self.player_preference = player
        self.save_window_preferences()
        self.player_selected.emit(player)
        if self.media_enabled and self.latest_media and player not in (
            "auto", self.latest_media.track.player,
        ):
            self.latest_media = None
            self.enable_media()

    def toggle_visibility(self):
        if QApplication.activeModalWidget():
            return
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def toggle_click_through(self):
        if self.can_click_through and not QApplication.activeModalWidget():
            self.click_through = not self.click_through
            set_click_through(self, self.click_through)

    def toggle_lock(self):
        self.locked = not self.locked
        self.unsetCursor()

    def show_menu(self, button):
        if self.menu is not None:
            if self.menu.isVisible():
                self.menu.close()
                return
            self.menu.deleteLater()
        menu = QMenu(self)
        menu.setWindowFlag(FOREGROUND)
        self.menu = menu
        media = self.latest_media if self.media_enabled else None
        menu.addAction("从头播放", lambda: self.request_playback("restart")).setEnabled(bool(media and media.can_seek))
        menu.addAction("跳转播放进度…", self.seek_playback).setEnabled(bool(media and media.can_seek))
        menu.addSeparator()
        show_translation = menu.addAction("显示中文翻译")
        show_translation.setCheckable(True)
        show_translation.setChecked(self.lyrics_view.show_translation)
        show_translation.toggled.connect(self.toggle_translation)
        menu.addSeparator()
        menu.addAction("跟随播放器", self.enable_media).setEnabled(not self.media_enabled)
        player_menu = menu.addMenu("播放来源")
        for value, name in (("auto", "自动跟随"), ("qq", "QQ 音乐"), ("netease", "网易云音乐")):
            option = player_menu.addAction(name)
            option.setCheckable(True)
            option.setChecked(value == self.player_preference)
            option.triggered.connect(lambda checked=False, player=value: self.select_player(player))
        media = self.latest_media
        menu.addAction("重新在线查找歌词", lambda: self.search_public_lyrics(refresh=True)).setEnabled(
            bool(self.media_enabled and media and (media.track.netease_id or media.track.qq_id or media.track.artist))
        )
        menu.addAction("导入 LRC 文件…", self.import_file)
        menu.addAction("粘贴 LRC 文本…", self.paste_lrc)
        menu.addAction("重新播放示例歌词", self.open_sample)
        menu.addSeparator()
        menu.addAction(
            "暂停手动计时" if self.clock.running else "开始手动计时",
            lambda: self.clock.set_running(not self.clock.running),
        ).setEnabled(not self.media_enabled)
        menu.addAction("设置手动进度…", self.set_manual_position).setEnabled(
            not self.media_enabled or self.latest_media is not None
        )
        if self.media_enabled and self.calibrated:
            menu.addAction("恢复系统进度", self.resume_system_position).setEnabled(
                self.latest_media is not None and self.latest_media.position_ms is not None
            )
        menu.addAction(f"恢复自动跟随 ({self.shortcuts['follow']})", self.lyrics_view.resume_follow)
        menu.addSeparator()
        menu.addAction("调整外观…", self.edit_appearance)
        lock = menu.addAction("锁定窗口位置和大小", self.toggle_lock)
        lock.setCheckable(True)
        lock.setChecked(self.locked)
        through = menu.addAction(
            f"开启点击穿透 ({self.shortcuts['click_through']})", self.toggle_click_through,
        )
        through.setEnabled(self.can_click_through)
        menu.addAction(f"隐藏窗口 ({self.shortcuts['visibility']})", self.hide).setEnabled(
            self.can_hide
        )
        menu.addAction("退出 Floating Lyrics", self.close)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))
        menu.raise_()

    def resume_system_position(self):
        self.calibrated = False
        self.clock.set_running(False)
        self.refresh_media_status()
        self.update_position(animated=False)

    def edit_appearance(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("歌词外观")
        form = QFormLayout(dialog)
        original_theme = self.appearance.theme
        selector = QComboBox(dialog)
        for key, theme in THEMES.items():
            selector.addItem(theme.name, key)
        selector.setCurrentIndex(selector.findData(original_theme))
        form.addRow("主题", selector)
        controls = {}
        labels = {
            "opacity": "背景不透明度（%）",
            "font_size": "歌词字号（像素）",
            "line_spacing": "歌词行距（像素）",
            "follow_delay": "滚动后自动跟随（秒）",
        }
        for field, (minimum, maximum) in APPEARANCE_LIMITS.items():
            spin = QSpinBox(dialog, minimum=minimum, maximum=maximum)
            if field == "follow_delay":
                spin.setSpecialValueText("关闭自动恢复")
            spin.setValue(getattr(self.appearance, field))
            controls[field] = spin
            form.addRow(labels[field], spin)
        color = QToolButton(text=self.appearance.background)
        form.addRow("背景颜色", color)

        def preview_theme():
            self.appearance.theme = selector.currentData()
            color.setEnabled(self.appearance.theme == "classic")
            self.apply_theme()

        def choose_color():
            picker = QColorDialog(QColor(color.text()), dialog)
            picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
            picker.setWindowTitle("选择背景颜色")
            if exec_dialog(picker):
                color.setText(picker.selectedColor().name())

        color.clicked.connect(choose_color)
        selector.currentIndexChanged.connect(preview_theme)
        color.setEnabled(original_theme == "classic")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if exec_dialog(dialog):
            previous_delay = self.appearance.follow_delay
            for field, spin in controls.items():
                setattr(self.appearance, field, spin.value())
            self.appearance.background = color.text()
            self.lyrics_view.reflow()
            if self.appearance.follow_delay != previous_delay:
                self.lyrics_view.restart_follow_timer()
            self.update()
            try:
                save_appearance(self.appearance)
            except OSError:
                show_message(self, "无法保存外观", "当前窗口已应用设置，但下次启动可能无法恢复。")
        else:
            self.appearance.theme = original_theme
            self.apply_theme()

    def resize_edges(self, point):
        edges = Qt.Edge(0)
        if point.x() < 8:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - 8:
            edges |= Qt.Edge.RightEdge
        if point.y() < 8:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - 8:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        edges = self.resize_edges(event.position()) if not self.locked else Qt.Edge(0)
        horizontal = edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge)
        vertical = edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge)
        if horizontal and vertical:
            diagonal = edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge
            )
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor)
        elif horizontal:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vertical:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.locked:
            edges = self.resize_edges(event.position())
            if edges:
                self.windowHandle().startSystemResize(edges)
            else:
                self.windowHandle().startSystemMove()
        super().mousePressEvent(event)

