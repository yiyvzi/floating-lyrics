from bisect import bisect_left
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode
from xml.etree.ElementTree import ParseError

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__, qq_lyrics
from .config import ROOT, app_data_path, save_json
from .lrc import LyricLine, Lyrics, load_lrc, parse_lrc
from .ttml import parse_amll_ttml

AMLL_LYRICS = "https://raw.githubusercontent.com/amll-dev/amll-ttml-db/refs/heads/main/ncm-lyrics"
NETEASE_LYRICS = "https://music.163.com/api/song/lyric"
YRC_LINE = re.compile(r"^\[(\d+),\d+\](.*)$")
YRC_WORD = re.compile(r"\(\d+,\d+,\d+\)")
INSTRUMENTAL_NOTICE = re.compile(
    r"(?:纯音乐[，, ]*(?:请欣赏|无(?:文字)?歌词)|无(?:文字)?歌词)[。！!]*"
)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def local_lyrics(track):
    folders = []
    for variable in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(variable)
        if base and track.player == "netease":
            player = Path(base) / "NetEase" / "CloudMusic"
            folders.extend((player / "Lyric", player / "Cache" / "Lyric"))
    folders.append(ROOT / "lyrics")

    title = normalize(track.title)
    artist = normalize(track.artist)
    if not title or not artist:
        return None
    for folder in dict.fromkeys(folders):
        try:
            candidates = sorted(folder.glob("*.lrc"))
        except OSError:
            continue
        matches = []
        for path in candidates:
            try:
                if path.stat().st_size > 256_000:
                    continue
                lyrics = load_lrc(path, source="本地歌词库" if folder.parent == ROOT else "网易云本地歌词")
            except (OSError, ValueError):
                continue
            if lyrics.artist:
                matches_track = normalize(lyrics.title) == title and normalize(lyrics.artist) == artist
            else:
                matches_track = normalize(path.stem) == f"{artist} - {title}"
            if matches_track and not (
                track.album and lyrics.album and normalize(track.album) != normalize(lyrics.album)
            ):
                matches.append(lyrics)
        if matches:
            return matches[0] if all(match.lines == matches[0].lines for match in matches) else None
    return None


def parse_public_lyrics(payload, state):
    records = json.loads(payload)
    if not isinstance(records, list):
        raise ValueError("公开歌词服务返回了无法识别的结果。")
    track = state.track
    matches = []
    instrumental = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title, artist, album, lrc = (
            record.get(key) for key in ("trackName", "artistName", "albumName", "syncedLyrics")
        )
        if not all(isinstance(value, str) for value in (title, artist)):
            continue
        if normalize(title) != normalize(track.title) or normalize(artist) != normalize(track.artist):
            continue
        if track.album and (not isinstance(album, str) or normalize(album) != normalize(track.album)):
            continue
        duration = record.get("duration")
        if state.duration_ms and (
            not isinstance(duration, (int, float)) or abs(duration * 1000 - state.duration_ms) > 2000
        ):
            continue
        instrumental.append(record.get("instrumental") is True)
        if instrumental[-1] or not isinstance(lrc, str):
            continue
        try:
            lyrics = parse_lrc(lrc, title=track.title, source="LRCLIB")
        except ValueError:
            continue
        matches.append(replace(lyrics, title=track.title, artist=track.artist, album=track.album))
    if not matches:
        if instrumental and all(instrumental):
            return Lyrics((), track.title, track.artist, track.album, "LRCLIB", instrumental=True)
        raise ValueError("暂无同步歌词")
    if any(match.lines != matches[0].lines for match in matches):
        raise ValueError("歌词版本无法确认")
    return matches[0]


def parse_netease_lyrics(payload, track, *, source="网易云歌词"):
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("code") != 200:
        return None
    base = Lyrics((), track.title, track.artist, track.album, source)
    if data.get("nolyric") is True:
        return replace(base, instrumental=True)

    def text_for(field):
        entry = data.get(field)
        text = entry.get("lyric") if isinstance(entry, dict) else None
        return text if isinstance(text, str) else ""

    return _parse_timed_lyrics(text_for("lrc"), text_for("tlyric"), base, yrc=text_for("yrc"))


def _parse_timed_lyrics(original, translation, base, *, yrc=""):
    try:
        lines = parse_lrc(original).lines
    except ValueError:
        rows = {}
        for row in yrc.splitlines():
            if match := YRC_LINE.fullmatch(row.strip()):
                text = YRC_WORD.sub("", match[2]).strip()
                if text:
                    rows[int(match[1])] = text
        lines = tuple(LyricLine(stamp, rows[stamp]) for stamp in sorted(rows))
    if not lines:
        return None
    texts = [line.text.strip() for line in lines if line.text.strip()]
    if len(texts) == 1 and INSTRUMENTAL_NOTICE.fullmatch(texts[0]):
        return replace(base, instrumental=True)

    try:
        translated = parse_lrc(translation).lines
    except ValueError:
        return replace(base, lines=lines)

    stamps = [line.time_ms for line in lines if line.text.strip()]
    aligned = {}
    for line in translated:
        if not line.text.strip() or not stamps:
            continue
        index = bisect_left(stamps, line.time_ms)
        candidates = stamps[max(0, index - 1):index + 1]
        candidates = sorted((abs(stamp - line.time_ms), stamp) for stamp in candidates)
        distance, stamp = candidates[0]
        if distance <= 250 and (len(candidates) == 1 or distance < candidates[1][0]):
            if stamp not in aligned or distance < aligned[stamp][0]:
                aligned[stamp] = (distance, line.text)
    return replace(
        base,
        lines=tuple(replace(line, translation=aligned.get(line.time_ms, (0, ""))[1]) for line in lines),
        translation_source=base.source if aligned else "",
    )


class PublicLyrics(QObject):
    finished = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.cache = {}

    def cache_path(self, state):
        track = state.track
        identity = ["qq", track.qq_id] if track.qq_id else ["netease", track.netease_id] if track.netease_id else [
            "metadata", normalize(track.title), normalize(track.artist),
            normalize(track.album), state.duration_ms,
        ]
        if track.player == "qq" and not track.qq_id:
            identity.insert(0, "qq")
        digest = sha256(json.dumps(identity).encode("utf-8")).hexdigest()
        return app_data_path() / "lyrics-cache" / f"{digest}.json"

    def load_cached(self, path):
        try:
            if path.stat().st_size > 1_000_000:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["version"] != 1:
                return None
            lyrics = data["lyrics"]
            lines = tuple(LyricLine(**line) for line in lyrics["lines"])
            if any(
                type(line.time_ms) is not int or not isinstance(line.text, str)
                or not isinstance(line.translation, str)
                or (index > 0 and line.time_ms <= lines[index - 1].time_ms)
                for index, line in enumerate(lines)
            ) or not any(line.text.strip() for line in lines):
                return None
            result = Lyrics(**{**lyrics, "lines": lines})
            if result.instrumental or not all(isinstance(field, str) for field in (
                result.title, result.artist, result.album, result.source, result.translation_source,
            )):
                return None
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def search(self, state, *, refresh=False):
        self.cancel()
        track = state.track
        path = self.cache_path(state)
        cached = None if refresh else self.cache.get(path) or self.load_cached(path)
        if cached is not None:
            self.finished.emit(track, cached, "")
        elif track.player == "qq" and track.qq_id:
            self._get(qq_lyrics.lyric_url(song_id=track.qq_id), state, format="qq_lyrics")
        elif track.player == "qq" and track.artist:
            self._get(qq_lyrics.search_url(track), state, format="qq_search")
        elif re.fullmatch(r"[1-9][0-9]{0,19}", track.netease_id):
            query = urlencode({"id": track.netease_id, "lv": 1, "tv": 1, "yv": 1})
            self._get(f"{NETEASE_LYRICS}?{query}", state, format="netease")
        else:
            self._search_lrclib(state)

    def _search_lrclib(self, state):
        if not state.track.artist:
            self.finished.emit(state.track, None, "未找到当前歌曲的时间轴歌词")
            return
        query = {"track_name": state.track.title, "artist_name": state.track.artist}
        self._get("https://lrclib.net/api/search?" + urlencode(query), state)

    def _get(self, url, state, *, format="lrclib"):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"Floating Lyrics/{__version__}".encode("ascii"))
        if format in {"netease", "qq_search", "qq_lyrics"}:
            referer = b"https://music.163.com/" if format == "netease" else b"https://y.qq.com/portal/player.html"
            request.setRawHeader(b"Referer", referer)
            for attribute in (QNetworkRequest.Attribute.CookieLoadControlAttribute,
                              QNetworkRequest.Attribute.CookieSaveControlAttribute):
                request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy,
        )
        reply = self.reply = self.network.get(request)
        timeout = QTimer(reply, singleShot=True, interval=8000)
        timeout.timeout.connect(reply.abort)
        timeout.start()
        reply.finished.connect(lambda: self._finish(reply, state, format))

    def _finish(self, reply, state, format):
        try:
            if reply is not self.reply:
                return
            self.reply = None
            if format in {"qq_search", "qq_lyrics"}:
                self._finish_qq(reply, state, format)
                return
            if format == "netease":
                lyrics = None
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    try:
                        lyrics = parse_netease_lyrics(bytes(reply.readAll()), state.track)
                    except ValueError:
                        pass
                if lyrics is not None:
                    self._publish(state, lyrics)
                else:
                    self._get(f"{AMLL_LYRICS}/{state.track.netease_id}.ttml", state, format="ttml")
                return
            if format == "ttml":
                lyrics = None
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    try:
                        lyrics = parse_amll_ttml(bytes(reply.readAll()), state.track)
                    except (ValueError, ParseError, OverflowError):
                        pass
                if lyrics is not None:
                    self._publish(state, lyrics)
                else:
                    self._get(f"{AMLL_LYRICS}/{state.track.netease_id}.lrc", state, format="lrc")
                return
            if format == "lrc":
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    try:
                        lyrics = parse_lrc(bytes(reply.readAll()).decode("utf-8-sig"), source="AMLL")
                    except ValueError:
                        pass
                    else:
                        self._publish(state, lyrics)
                        return
                self._search_lrclib(state)
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.finished.emit(state.track, None, "歌词查询失败")
                return
            try:
                lyrics = parse_public_lyrics(bytes(reply.readAll()), state)
            except (ValueError, UnicodeError) as error:
                self.finished.emit(state.track, None, str(error))
            else:
                self._publish(state, lyrics)
        finally:
            reply.deleteLater()

    def _finish_qq(self, reply, state, format):
        track = state.track
        if reply.error() == QNetworkReply.NetworkError.NoError:
            try:
                payload = bytes(reply.readAll())
                if format == "qq_search":
                    mid = qq_lyrics.match_song(payload, state)
                    if mid:
                        self._get(qq_lyrics.lyric_url(mid=mid), state, format="qq_lyrics")
                        return
                else:
                    original, translation = qq_lyrics.lyric_texts(payload, track.qq_id)
                    base = Lyrics((), track.title, track.artist, track.album, "QQ 音乐")
                    lyrics = _parse_timed_lyrics(original, translation, base)
                    if lyrics is not None:
                        self._publish(state, lyrics)
                        return
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
        self._search_lrclib(state)

    def _publish(self, state, lyrics):
        path = self.cache_path(state)
        if len(self.cache) >= 32:
            del self.cache[next(iter(self.cache))]
        self.cache[path] = lyrics
        if lyrics.lines and not lyrics.instrumental:
            try:
                save_json(path, {"version": 1, "lyrics": asdict(lyrics)})
            except OSError:
                pass
        elif lyrics.instrumental:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self.finished.emit(state.track, lyrics, "")

    def cancel(self):
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()

