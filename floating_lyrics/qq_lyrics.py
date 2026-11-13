from base64 import b64decode
from html import unescape
import json
import re
import unicodedata
from urllib.parse import urlencode

QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/search_cp"
QQ_LYRICS = "https://u.y.qq.com/cgi-bin/musicu.fcg"


def search_url(track):
    return QQ_SEARCH + "?" + urlencode({
        "w": f"{track.title} {track.artist}", "format": "json", "p": 1, "n": 30,
    })


def lyric_url(*, song_id="", mid=""):
    identity = {"songID": int(song_id)} if song_id else {"songMid": mid}
    payload = {
        "comm": {"ct": 24, "cv": 0},
        "req": {
            "module": "music.musichallSong.PlayLyricInfo",
            "method": "GetPlayLyricInfo",
            "param": {**identity, "crypt": 0, "qrc": 0, "trans": 1, "roma": 0,
                      "lrc_t": 0, "trans_t": 0},
        },
    }
    return QQ_LYRICS + "?" + urlencode({"format": "json", "data": json.dumps(payload)})


def normalized(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", unescape(value)).casefold().split())


def match_song(payload, state):
    data = json.loads(payload)
    if data["code"] != 0:
        return None
    track = state.track
    title, artist, album = map(normalized, (track.title, track.artist, track.album))
    names = set(re.split(r"\s*[/,、;]\s*|\s+&\s+", artist))
    matches = {}
    for record in data["data"]["song"]["list"]:
        mid = record.get("songmid", "")
        if not re.fullmatch(r"[A-Za-z0-9]{14}", mid):
            continue
        if title not in {normalized(record.get(key)) for key in ("songname", "title")}:
            continue
        singers = record.get("singer") or []
        artist_names = [normalized(singer.get("name")) for singer in singers]
        artists = set(artist_names)
        album_matches = bool(album and album == normalized(record.get("albumname")))
        duration = record.get("interval")
        if state.duration_ms and (
            type(duration) not in (int, float) or abs(duration * 1000 - state.duration_ms) > 2000
        ):
            continue
        if not album_matches and not state.duration_ms:
            continue
        full_artists = names == artists or artist == " & ".join(artist_names)
        first_artist = bool(artist_names and artist == artist_names[0])
        # Some QQ sessions only report the first credited artist.
        if not full_artists and not (first_artist and album_matches and state.duration_ms):
            continue
        matches[mid] = (album_matches, full_artists)
    if not matches:
        return None
    best = max(matches.values())
    candidates = [mid for mid, score in matches.items() if score == best]
    return candidates[0] if len(candidates) == 1 else None


def lyric_texts(payload, expected_id=""):
    response = json.loads(payload)
    request = response["req"]
    if response["code"] != 0 or request["code"] != 0:
        return "", ""
    data = request["data"]
    if expected_id and str(data.get("songID")) != expected_id:
        return "", ""
    return tuple(
        unescape(b64decode(data.get(field) or "", validate=True).decode("utf-8-sig"))
        for field in ("lyric", "trans")
    )
