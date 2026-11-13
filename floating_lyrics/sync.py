from bisect import bisect_right
from time import monotonic


class ManualClock:
    """Playback time for a demo or a manually synchronized song."""

    def __init__(self):
        self._position_ms = 0
        self._started = None

    @property
    def running(self):
        return self._started is not None

    @property
    def position_ms(self):
        if self._started is None:
            return self._position_ms
        return self._position_ms + int((monotonic() - self._started) * 1000)

    def seek(self, position_ms):
        self._position_ms = max(0, position_ms)
        if self.running:
            self._started = monotonic()

    def set_running(self, running):
        self._position_ms = self.position_ms
        self._started = monotonic() if running else None


class LyricTimeline:
    def __init__(self, lyrics):
        self._timestamps = tuple(line.time_ms for line in lyrics.lines)

    def active_row(self, position_ms):
        return bisect_right(self._timestamps, position_ms) - 1

