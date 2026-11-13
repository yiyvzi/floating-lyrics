import ctypes
from ctypes import wintypes
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from floating_lyrics.config import load_defaults, restore_window_state, visible_geometry
from floating_lyrics.hotkeys import GlobalHotkeys, WM_HOTKEY
from floating_lyrics.lrc import Lyrics, parse_lrc
from floating_lyrics.lyric_sources import PublicLyrics, parse_netease_lyrics, parse_public_lyrics
from floating_lyrics.media import MediaState, Track, is_netease, media_state, netease_song_id, player_for_source
from floating_lyrics.qq_lyrics import match_song
from floating_lyrics.sync import LyricTimeline
from floating_lyrics.ttml import parse_amll_ttml
from floating_lyrics.window import FloatingWindow


TIMED_TRANSLATION = json.dumps({
    "code": 200,
    "lrc": {"lyric": "[00:01]空"},
    "tlyric": {"lyric": "[00:01]天空"},
})


class LyricsTest(unittest.TestCase):
    def test_multistamp_offset_and_exact_boundaries(self):
        lyrics = parse_lrc(
            "\ufeff[ti:示例]\n[ar:歌手]\n[al:专辑]\n[offset:-20]\n"
            "[00:03.25][00:01.025]第一行\n[00:01.025]翻译\n[00:04.0]"
        )
        self.assertEqual((lyrics.title, lyrics.artist, lyrics.album), ("示例", "歌手", "专辑"))
        self.assertEqual([line.time_ms for line in lyrics.lines], [1005, 3230, 3980])
        self.assertEqual(lyrics.lines[0].text, "第一行\n翻译")
        timeline = LyricTimeline(lyrics)
        self.assertEqual(timeline.active_row(1004), -1)
        self.assertEqual(timeline.active_row(1005), 0)
        self.assertEqual(timeline.active_row(3980), 2)
        with self.assertRaises(ValueError):
            parse_lrc("缺少时间戳")


class MediaTest(unittest.TestCase):
    def test_qq_source_and_version_matching(self):
        self.assertEqual(player_for_source(r"C:\Tencent\QQMusic\QQMusic.exe"), "qq")
        self.assertIsNone(player_for_source("MSEdge"))
        state = MediaState(Track("歌曲", "歌手", "专辑", player="qq"), "PAUSED", 180000)
        record = {"songmid": "12345678901234", "songname": "歌曲", "albumname": "专辑",
                  "singer": [{"name": "歌手"}], "interval": 180}
        def payload(*rows):
            return json.dumps({"code": 0, "data": {"song": {"list": rows}}})
        wrong = dict(record, songmid="00000000000001", interval=191)
        alternate = dict(record, songmid="00000000000002", albumname="精选")
        self.assertEqual(match_song(payload(wrong, alternate, record), state), record["songmid"])
        self.assertIsNone(match_song(payload(wrong), state))
        self.assertIsNone(match_song(payload(record, dict(record, songmid="00000000000003")), state))

    def test_qq_lyrics_response_aligns_translation(self):
        app = QApplication.instance() or QApplication([])
        state = MediaState(Track("歌曲", "歌手", player="qq", qq_id="123"), "PAUSED", 120000)
        data = {"songID": 123}
        for field, text in (("lyric", "[00:01]空"), ("trans", "[00:01.08]天空")):
            data[field] = b64encode(text.encode("utf-8")).decode("ascii")
        reply = SimpleNamespace(
            error=lambda: QNetworkReply.NetworkError.NoError,
            readAll=lambda: json.dumps({"code": 0, "req": {"code": 0, "data": data}}).encode(),
        )
        lookup = PublicLyrics()
        with patch.object(lookup, "_publish") as publish:
            lookup._finish_qq(reply, state, "qq_lyrics")
        publish.assert_called_once()
        received_state, lyrics = publish.call_args.args
        self.assertEqual(received_state, state)
        self.assertEqual(lyrics.translation_source, "QQ 音乐")
        self.assertEqual((lyrics.lines[0].time_ms, lyrics.lines[0].translation), (1000, "天空"))

    def test_netease_uses_timed_translation_and_preserves_original_timing(self):
        track = Track("歌曲", "歌手", netease_id="123")
        data = {"code": 200, "lrc": {"lyric": "[00:01]空\n[00:03]夜"},
                "tlyric": {"lyric": "[00:01.080]天空\n[00:03]夜晚\n[00:08]无关"}}
        lyrics = parse_netease_lyrics(json.dumps(data), track)
        self.assertEqual([(line.time_ms, line.translation) for line in lyrics.lines],
                         [(1000, "天空"), (3000, "夜晚")])
        data["lrc"] = None
        data["yrc"] = {"lyric": "[1000,300](1000,150,0)天(1150,150,0)空"}
        self.assertEqual(parse_netease_lyrics(json.dumps(data), track).lines[0].text, "天空")

    def test_netease_distinguishes_missing_and_instrumental_lyrics(self):
        track = Track("歌曲", "歌手", netease_id="123")
        self.assertIsNone(parse_netease_lyrics('{"code": 200}', track))
        self.assertTrue(parse_netease_lyrics('{"code": 200, "nolyric": true}', track).instrumental)
        notice = {"code": 200, "lrc": {"lyric": "[00:00]纯音乐，请欣赏"}}
        self.assertTrue(parse_netease_lyrics(json.dumps(notice), track).instrumental)

    def test_instrumental_requires_matching_catalog_evidence(self):
        state = MediaState(Track("器乐曲", "歌手"), "PAUSED", 120000)
        record = {"trackName": "器乐曲", "artistName": "歌手", "duration": 120,
                  "instrumental": True, "syncedLyrics": None}
        self.assertTrue(parse_public_lyrics(json.dumps([record]), state).instrumental)
        with self.assertRaisesRegex(ValueError, "暂无同步歌词"):
            parse_public_lyrics(json.dumps([dict(record, duration=150)]), state)
        with self.assertRaisesRegex(ValueError, "暂无同步歌词"):
            parse_public_lyrics(json.dumps([record, dict(record, instrumental=False)]), state)

    def test_amll_inline_and_linked_translations(self):
        payload = """<tt xmlns="http://www.w3.org/ns/ttml"
            xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xmlns:i="urn:itunes">
            <head><metadata><translations>
            <translation xml:lang="zh-Hans"><text for="L2">夜晚</text></translation>
            </translations></metadata></head>
            <body><div>
            <p begin="00:03.5">空<span ttm:role="x-translation" xml:lang="en">Sky</span>
            <span ttm:role="x-translation" xml:lang="zh-CN">天空</span></p>
            <p begin="5s" i:key="L2">夜</p></div></body></tt>"""
        lyrics = parse_amll_ttml(payload, Track("测试"))
        self.assertEqual([(row.time_ms, row.text, row.translation) for row in lyrics.lines],
                         [(3500, "空", "天空"), (5000, "夜", "夜晚")])

    def test_inflink_song_id(self):
        self.assertEqual(netease_song_id(["Rock", "NCM-123456"]), "123456")
        self.assertEqual(netease_song_id(["NCM-123/../../", "NCM-0"]), "")
        self.assertEqual(netease_song_id(["NCM-123", "NCM-456"]), "")

    def test_unreported_progress_and_valid_anchor(self):
        status = Enum("Status", ["PLAYING"])
        info = SimpleNamespace(playback_status=status.PLAYING, playback_rate=None)
        now = datetime.now(timezone.utc)
        track = Track("歌曲", "歌手")
        timeline = SimpleNamespace(
            start_time=timedelta(), end_time=timedelta(), position=timedelta(),
            last_updated_time=datetime(1601, 1, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(is_netease(r"C:\Music\cloudmusic.exe"))
        self.assertFalse(is_netease("MSEdge"))
        self.assertIsNone(media_state(track, info, timeline, now).current_position())
        timeline.end_time = timedelta(seconds=180)
        timeline.position = timedelta(seconds=30)
        timeline.last_updated_time = now - timedelta(seconds=2)
        self.assertEqual(media_state(track, info, timeline, now).position_ms, 32000)

    def test_public_search_checks_track_and_rejects_ambiguous_versions(self):
        state = MediaState(Track("歌曲", "歌手"), "PLAYING")
        hit = {"trackName": "歌曲", "artistName": "歌手", "syncedLyrics": "[00:01]测试"}
        wrong_artist = dict(hit, artistName="另一个歌手")
        result = parse_public_lyrics(json.dumps([wrong_artist, hit]), state)
        self.assertEqual(result.lines[0].text, "测试")
        with self.assertRaises(ValueError):
            parse_public_lyrics(json.dumps([hit, dict(hit, syncedLyrics="[00:02]测试")]), state)


@unittest.skipUnless(sys.platform == "win32", "Windows desktop required")
class WindowTest(unittest.TestCase):
    def test_qq_lookup_waits_for_duration_when_album_is_missing(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        window.enable_media()
        track = Track("Lemon (Live)", "米津玄師", player="qq")
        ready = MediaState(track, "PAUSED", 248986, 2879)
        with patch.object(window.public_lyrics, "search") as search:
            window.update_media(MediaState(track, "OPENED"))
            search.assert_not_called()
            window.update_media(ready)
            window.update_media(ready)
            search.assert_called_once_with(ready, refresh=False)

    def test_main_window_requests_playback_control(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        window.enable_media()
        track = Track("歌曲", "歌手")
        state = MediaState(track, "PAUSED", 120000, 5000,
                           can_previous=True, can_next=True, can_seek=True, can_toggle=True)
        with patch.object(window.public_lyrics, "search"):
            window.update_media(state)
        requests = []
        window.playback_requested.connect(lambda *args: requests.append(args))
        window.show()
        for button in (window.previous_button, window.play_button, window.next_button):
            self.assertTrue(button.isVisible() and button.isEnabled())
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.play_button.accessibleName(), "播放")
        window.update_media(MediaState(track, "PLAYING", can_toggle=True))
        self.assertEqual(window.play_button.accessibleName(), "暂停")
        menu_button = window.findChild(QToolButton, "overlayMenu")
        window.show_menu(menu_button)
        restart = next(action for action in window.menu.actions() if action.text() == "从头播放")
        self.assertFalse(restart.isEnabled())
        window.menu.close()
        window.update_media(state)
        window.show_menu(menu_button)
        restart = next(action for action in window.menu.actions() if action.text() == "从头播放")
        self.assertTrue(restart.isEnabled())
        restart.trigger()
        window.menu.close()
        with patch("floating_lyrics.window.QInputDialog.getDouble", return_value=(12.345, True)):
            window.seek_playback()
        self.assertEqual(requests, [
            ("previous", track, None), ("toggle", track, None), ("next", track, None),
            ("restart", track, None),
            ("seek", track, 12345),
        ])

    def test_missing_id_automatically_searches_once(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        state = MediaState(Track("测试歌曲", "测试歌手"), "PAUSED", 120000)
        with patch.object(window.public_lyrics, "_get") as request:
            window.enable_media()
            window.update_media(state)
            window.update_media(state)
        request.assert_called_once()
        url, requested_state = request.call_args.args
        self.assertTrue(url.startswith("https://lrclib.net/api/search?"))
        self.assertEqual(requested_state.duration_ms, 120000)

    def test_online_lyrics_reload_without_network_and_can_refresh(self):
        app = QApplication.instance() or QApplication([])
        state = MediaState(Track("歌曲", "歌手"), "PAUSED", 120000)
        lyrics = parse_netease_lyrics(TIMED_TRANSLATION, state.track)
        with TemporaryDirectory() as folder, patch(
            "floating_lyrics.lyric_sources.app_data_path", return_value=Path(folder),
        ):
            first = PublicLyrics()
            first._publish(state, lyrics)
            second = PublicLyrics()
            received = []
            second.finished.connect(lambda *args: received.append(args))
            with patch.object(second, "_get") as request:
                second.search(state)
                request.assert_not_called()
                self.assertEqual(received, [(state.track, lyrics, "")])
                second.search(state, refresh=True)
                request.assert_called_once()
                request.reset_mock()
                second.cache_path(state).write_text("{", encoding="utf-8")
                second.search(state)
                request.assert_called_once()

    def test_window_geometry_and_translation_are_restored(self):
        app = QApplication.instance() or QApplication([])
        first = FloatingWindow(*load_defaults())
        second = FloatingWindow(*load_defaults())
        geometry = QRect(first.screen().availableGeometry().topLeft() + QPoint(30, 30),
                         first.size())
        with TemporaryDirectory() as folder, patch(
            "floating_lyrics.config.app_data_path", return_value=Path(folder),
        ):
            first.track_window_state()
            first.setGeometry(geometry)
            first.toggle_translation(False)
            first.close()
            restore_window_state(second)
            self.assertEqual(second.geometry(), geometry)
            self.assertFalse(second.lyrics_view.show_translation)
        second.close()
        primary = QRect(0, 0, 1280, 720)
        secondary = QRect(-1920, 0, 1920, 1080)
        saved = QRect(-1700, 80, 360, 580)
        self.assertEqual(visible_geometry(saved, [primary, secondary]), saved)
        self.assertTrue(primary.contains(visible_geometry(saved, [primary])))

    def test_source_translation_window_toggle_and_empty_states(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        window.enable_media()
        track = Track("歌曲", "歌手")
        with patch.object(window.public_lyrics, "search"):
            window.update_media(MediaState(track, "PAUSED", 120000, 1500))
        lyrics = parse_netease_lyrics(TIMED_TRANSLATION, track)
        window.apply_public_lyrics(track, lyrics, "")
        self.assertEqual(window.lyrics_view.active, 0)
        self.assertEqual(window.lyrics_view.documents[0].toPlainText(), "空\n天空")
        window.toggle_translation(False)
        self.assertEqual(window.lyrics_view.documents[0].toPlainText(), "空")
        window.toggle_translation(True)
        self.assertEqual(window.lyrics_view.documents[0].toPlainText(), "空\n天空")
        record = {"trackName": "歌曲", "artistName": "歌手", "duration": 120, "instrumental": True}
        state = window.latest_media
        window.apply_public_lyrics(track, parse_public_lyrics(json.dumps([record]), state), "")
        self.assertEqual(window.lyrics_view.empty_message, "纯音乐 · 请欣赏")
        self.assertFalse(window.status.text())
        window.display_lyrics(parse_lrc("[00:01]空"))
        window.apply_public_lyrics(track, None, "暂无同步歌词")
        self.assertFalse(window.lyrics_view.empty_message)
        window.display_lyrics(Lyrics((), track.title, track.artist, "", ""))
        window.apply_public_lyrics(track, None, "暂无同步歌词")
        self.assertEqual(window.lyrics_view.empty_message, "静听此刻")

    def test_inflink_track_loads_lyrics_and_follows_seek(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        window.enable_media()
        track = Track("歌曲", "歌手", netease_id="123456")
        first = MediaState(track, "PAUSED", 120000, 1000)
        with patch.object(window.public_lyrics, "search") as search:
            window.update_media(first)
            window.update_media(first)
        search.assert_called_once_with(first, refresh=False)
        window.apply_public_lyrics(track, parse_lrc("[00:00]第一句\n[00:03]第二句"), "")
        self.assertEqual(window.lyrics_view.active, 0)
        window.update_media(MediaState(track, "PAUSED", 120000, 4000))
        window.update_position(animated=False)
        self.assertEqual(window.lyrics_view.active, 1)

    def test_manual_alignment_uses_play_state_and_resets_on_track_change(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        self.addCleanup(window.close)
        window.enable_media()
        track = Track("歌曲", "歌手")
        window.update_media(MediaState(track, "PLAYING"))
        window.accept_import(parse_lrc("[00:00]第一句\n[00:03]第二句"))
        self.assertEqual(window.lyrics_view.active, -1)
        self.assertEqual("请设置手动进度", window.status.text())
        with patch("floating_lyrics.window.QInputDialog.getDouble", return_value=(3.5, True)):
            window.set_manual_position()
        self.assertEqual(window.lyrics_view.active, 1)
        self.assertTrue(window.clock.running)
        window.update_media(MediaState(track, "PAUSED"))
        self.assertFalse(window.clock.running)
        window.update_media(MediaState(track, "PLAYING"))
        self.assertTrue(window.clock.running)
        window.update_media(MediaState(Track("下一首", "歌手"), "PLAYING"))
        self.assertFalse(window.calibrated)
        self.assertFalse(window.lyrics_view.lines)

    def test_overlay_and_native_hotkeys(self):
        app = QApplication.instance() or QApplication([])
        window = FloatingWindow(*load_defaults())
        window.show()
        self.addCleanup(window.close)
        app.processEvents()
        self.assertGreater(window.lyrics_view.viewport().height(), 100)
        self.assertEqual(window.lyrics_view.active, 0)
        window.clock.set_running(False)
        window.clock.seek(19000)
        window.update_position(animated=False)
        view = window.lyrics_view
        self.assertEqual(view.active, 5)
        follow_position = view.verticalScrollBar().value()

        origin = QPointF(20, 20)
        wheel = QWheelEvent(
            origin, origin, QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        app.sendEvent(view.viewport(), wheel)
        self.assertFalse(view.following)
        self.assertTrue(window.follow_button.isVisible())
        self.assertEqual(view.verticalScrollBar().value(), follow_position)
        app.sendEvent(view.viewport(), wheel)
        self.assertEqual(view.animation.endValue(), follow_position + 104)
        QTest.qWait(200)
        self.assertEqual(view.verticalScrollBar().value(), follow_position + 104)

        hotkeys = GlobalHotkeys(app)
        self.addCleanup(hotkeys.close)
        hotkeys.register(1, "Ctrl+Alt+Shift+F9", window.toggle_visibility)
        hotkeys.register(2, "Ctrl+Alt+Shift+F10", window.toggle_click_through)
        hotkeys.register(3, "Ctrl+Alt+Shift+F11", view.resume_follow)
        window.can_click_through = True
        user32 = hotkeys.user32
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.PostMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )

        def dispatch(identifier):
            self.assertTrue(user32.PostMessageW(hotkeys.hwnd, WM_HOTKEY, identifier, 0))
            QTest.qWait(50)

        def extended_style():
            return user32.GetWindowLongPtrW(int(window.winId()), -20)

        self.assertTrue(extended_style() & 0x8)  # WS_EX_TOPMOST
        self.assertTrue(extended_style() & 0x80000)  # WS_EX_LAYERED
        dispatch(3)
        self.assertTrue(view.following)
        QTest.qWait(180)
        self.assertGreater(view.verticalScrollBar().value(), follow_position)
        self.assertLess(view.verticalScrollBar().value(), follow_position + 104)
        QTest.qWait(420)
        self.assertEqual(view.verticalScrollBar().value(), follow_position)
        dispatch(2)
        self.assertTrue(window.click_through)
        self.assertTrue(extended_style() & 0x20)  # WS_EX_TRANSPARENT
        dispatch(1)
        self.assertFalse(window.isVisible())
        dispatch(2)
        self.assertFalse(window.click_through)
        self.assertFalse(window.isVisible())
        dispatch(1)
        self.assertTrue(window.isVisible())
        self.assertFalse(extended_style() & 0x20)

        window.resize(220, 680)
        app.processEvents()
        self.assertEqual(window.width(), 220)
        self.assertEqual(view.active, 5)

        QTimer.singleShot(0, window.close)
        self.assertEqual(app.exec(), 0)
        self.assertFalse(window.timer.isActive())


if __name__ == "__main__":
    unittest.main()

