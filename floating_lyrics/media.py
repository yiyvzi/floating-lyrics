import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import ntpath
import re
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(frozen=True)
class Track:
    title: str
    artist: str = ""
    album: str = ""
    netease_id: str = ""
    player: str = "netease"
    qq_id: str = ""


@dataclass(frozen=True)
class MediaState:
    track: Track
    status: str
    duration_ms: int | None = None
    position_ms: int | None = None
    sampled_at: float = 0
    rate: float = 1
    can_previous: bool = False
    can_next: bool = False
    can_seek: bool = False
    can_toggle: bool = False

    @property
    def playing(self):
        return self.status == "PLAYING"

    def current_position(self):
        if self.position_ms is None:
            return None
        position = self.position_ms
        if self.playing:
            position += round((monotonic() - self.sampled_at) * self.rate * 1000)
        return min(position, self.duration_ms) if self.duration_ms is not None else position


def is_netease(source):
    name = ntpath.basename(source).casefold()
    return name in {"cloudmusic", "cloudmusic.exe"} or (
        "!" in name and name.split("!", 1)[0].startswith(("netease.cloudmusic_", "neteasecloudmusic_"))
    )


def player_for_source(source):
    if is_netease(source):
        return "netease"
    name = ntpath.basename(source).casefold()
    package = name.split("!", 1)[0]
    if name in {"qqmusic", "qqmusic.exe"} or (
        "!" in name and package.startswith(("tencent.qqmusic_", "qqmusic_"))
    ):
        return "qq"
    return None


def netease_song_id(genres):
    ids = {
        genre[4:] for genre in genres if re.fullmatch(r"NCM-[1-9][0-9]{0,19}", genre)
    }
    return ids.pop() if len(ids) == 1 else ""


def track_from_properties(properties, player="netease"):
    qq_ids = {genre[3:] for genre in properties.genres if re.fullmatch(r"QQ-[1-9][0-9]{0,19}", genre)}
    return Track(
        properties.title.strip(), properties.artist.strip(), properties.album_title.strip(),
        netease_song_id(properties.genres) if player == "netease" else "",
        player, qq_ids.pop() if player == "qq" and len(qq_ids) == 1 else "",
    )


def media_state(track, info, timeline, now):
    status = info.playback_status.name
    rate = info.playback_rate
    rate = rate if rate and math.isfinite(rate) and rate > 0 else 1.0
    duration = round((timeline.end_time - timeline.start_time).total_seconds() * 1000)
    position = round((timeline.position - timeline.start_time).total_seconds() * 1000)
    age = (now - timeline.last_updated_time).total_seconds()

    # A default FILETIME or an empty/out-of-range timeline gives no playback anchor.
    valid = duration > 0 and 0 <= position <= duration and age >= -2
    if status == "PLAYING" and position + max(0, age) * rate * 1000 > duration + 2000:
        valid = False
    if not valid or timeline.last_updated_time.year < 1970:
        return MediaState(track, status, duration if duration > 0 else None)

    if status == "PLAYING":
        position = min(duration, position + round(max(0, age) * rate * 1000))
    return MediaState(track, status, duration, position, monotonic(), rate)


class WindowsMediaMonitor(QObject):
    updated = Signal(object, str)
    control_finished = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = asyncio.new_event_loop()
        self.manager = None
        self.task = None
        self.command = None
        self.session = None
        self.preferred_player = "auto"
        self.previous_track = None
        self.previous_timeline = None
        self.previous_status = None
        self.blocked_timeline = None
        self.poll = QTimer(self, interval=650)
        self.poll.timeout.connect(self._request)
        self.pump = QTimer(self, interval=25)
        self.pump.timeout.connect(self._pump)
        self.poll.start()
        self._request()

    def _request(self):
        if self.task is None:
            self.task = self.loop.create_task(self._read())
            self.pump.start()

    def _pump(self):
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if self.task is not None and self.task.done():
            task, self.task = self.task, None
            try:
                state = task.result()
            except (ImportError, OSError, RuntimeError, asyncio.TimeoutError,
                    ValueError, OverflowError) as error:
                self.manager = None
                self.session = None
                self.updated.emit(None, f"Windows 媒体会话读取失败（{type(error).__name__}）")
            else:
                self.updated.emit(state, "")
        if self.command is not None and self.command.done():
            task, self.command = self.command, None
            action, error = task.result()
            self.control_finished.emit(action, error)
            self._request()
        if self.task is None and self.command is None:
            self.pump.stop()

    def control(self, action, track, position_ms):
        if self.command is not None:
            return
        if self.session is None:
            self.control_finished.emit(action, "播放器暂不可控制")
            return
        self.command = self.loop.create_task(self._control(self.session, action, track, position_ms))
        self.pump.start()

    def select_player(self, player):
        self.preferred_player = player
        self.session = None
        self._request()

    async def _control(self, session, action, track, position_ms):
        try:
            player = player_for_source(session.source_app_user_model_id)
            if player != track.player:
                return action, "播放来源已变化，请重试"
            controls = session.get_playback_info().controls
            if action in ("seek", "restart"):
                if not controls.is_playback_position_enabled:
                    return action, "当前歌曲不支持跳转"
                properties = await asyncio.wait_for(session.try_get_media_properties_async(), 2)
                if properties is None or track_from_properties(properties, player) != track:
                    return action, "曲目已切换，请重新选择跳转位置"
                timeline = session.get_timeline_properties()
                duration = round((timeline.end_time - timeline.start_time).total_seconds() * 1000)
                if duration <= 0:
                    return action, "当前歌曲没有可用的播放进度"
                target = 0 if action == "restart" else min(duration, max(0, position_ms))
                start_ticks = round(timeline.start_time.total_seconds() * 10_000_000)
                request = session.try_change_playback_position_async(start_ticks + target * 10_000)
            elif action == "previous" and controls.is_previous_enabled:
                request = session.try_skip_previous_async()
            elif action == "next" and controls.is_next_enabled:
                request = session.try_skip_next_async()
            elif action == "toggle" and controls.is_play_pause_toggle_enabled:
                request = session.try_toggle_play_pause_async()
            elif action == "toggle":
                if session.get_playback_info().playback_status.name == "PLAYING" and controls.is_pause_enabled:
                    request = session.try_pause_async()
                elif session.get_playback_info().playback_status.name != "PLAYING" and controls.is_play_enabled:
                    request = session.try_play_async()
                else:
                    return action, "播放器暂不支持播放或暂停"
            else:
                return action, "播放器暂不支持此操作"
            if await asyncio.wait_for(request, 3):
                if action == "restart":
                    info = session.get_playback_info()
                    if info.playback_status.name != "PLAYING":
                        properties = await asyncio.wait_for(session.try_get_media_properties_async(), 2)
                        if properties is None or track_from_properties(properties, player) != track:
                            return action, "曲目已切换，请重试"
                        info = session.get_playback_info()
                        if info.playback_status.name != "PLAYING":
                            if info.controls.is_play_enabled:
                                play = session.try_play_async()
                            elif info.controls.is_play_pause_toggle_enabled:
                                play = session.try_toggle_play_pause_async()
                            else:
                                return action, "已回到曲首，请在播放器中开始播放"
                            if not await asyncio.wait_for(play, 3):
                                return action, "已回到曲首，播放器未能开始播放"
                return action, ""
        except (OSError, RuntimeError, asyncio.TimeoutError, ValueError, OverflowError):
            pass
        return action, "播放器未能执行此操作"

    async def _read(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

        if self.manager is None:
            self.manager = await asyncio.wait_for(
                GlobalSystemMediaTransportControlsSessionManager.request_async(), 3
            )
        current = self.manager.get_current_session()
        candidates = [current, *self.manager.get_sessions()]
        sessions = []
        for item in candidates:
            if item is None:
                continue
            player = player_for_source(item.source_app_user_model_id)
            if player and self.preferred_player in ("auto", player):
                sessions.append(item)
        session = next(
            (item for item in sessions if item.get_playback_info().playback_status.name == "PLAYING"),
            sessions[0] if sessions else None,
        )
        if session is None:
            self.session = None
            self.previous_track = self.previous_timeline = self.blocked_timeline = None
            self.previous_status = None
            return None
        properties = await asyncio.wait_for(session.try_get_media_properties_async(), 3)
        player = player_for_source(session.source_app_user_model_id)
        if self.preferred_player not in ("auto", player):
            return None
        if properties is None or not properties.title.strip():
            self.session = None
            return None
        self.session = session
        track = track_from_properties(properties, player)
        info = session.get_playback_info()
        timeline = session.get_timeline_properties()
        status = info.playback_status.name
        signature = (timeline.position, timeline.end_time, timeline.last_updated_time)
        if track != self.previous_track or (
            status == "PLAYING" and self.previous_status != "PLAYING"
        ):
            self.blocked_timeline = self.previous_timeline
        self.previous_track, self.previous_timeline = track, signature
        self.previous_status = status
        if signature == self.blocked_timeline:
            state = MediaState(track, status)
        else:
            self.blocked_timeline = None
            state = media_state(track, info, timeline, datetime.now(timezone.utc))
        controls = info.controls
        return replace(
            state, can_previous=controls.is_previous_enabled, can_next=controls.is_next_enabled,
            can_seek=controls.is_playback_position_enabled and bool(state.duration_ms),
            can_toggle=controls.is_play_pause_toggle_enabled or (
                controls.is_pause_enabled if state.playing else controls.is_play_enabled
            ),
        )

    def close(self):
        self.poll.stop()
        self.pump.stop()
        pending = [task for task in (self.task, self.command) if task is not None]
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.task = self.command = self.session = None
        self.manager = None
        self.loop.close()

