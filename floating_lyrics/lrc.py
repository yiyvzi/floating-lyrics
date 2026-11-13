from dataclasses import dataclass
from pathlib import Path
import re

TIMESTAMP = re.compile(r"\[(\d+):([0-5]?\d)(?:[.:](\d{1,3}))?\]")
TAG = re.compile(r"^\[([a-z]+):([^\]]*)\]$", re.IGNORECASE)
WORD_TIMESTAMP = re.compile(r"<\d+:[0-5]?\d(?:[.:]\d{1,3})?>")


@dataclass(frozen=True)
class LyricLine:
    time_ms: int
    text: str
    translation: str = ""


@dataclass(frozen=True)
class Lyrics:
    lines: tuple[LyricLine, ...]
    title: str
    artist: str
    album: str
    source: str
    instrumental: bool = False
    translation_source: str = ""


def parse_lrc(text: str, *, title: str = "未命名歌曲", source: str = "") -> Lyrics:
    tags = {}
    entries = []
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        tag = TAG.fullmatch(line)
        if tag:
            tags[tag[1].lower()] = tag[2].strip()
            continue

        stamps = []
        while stamp := TIMESTAMP.match(line):
            minutes, seconds, fraction = stamp.groups()
            ms = (int(minutes) * 60 + int(seconds)) * 1000
            stamps.append(ms + int((fraction or "").ljust(3, "0")))
            line = line[stamp.end():].lstrip()
        lyric = WORD_TIMESTAMP.sub("", line).strip()
        entries.extend((time_ms, lyric) for time_ms in stamps)

    if not any(lyric for _, lyric in entries):
        raise ValueError("找不到带有效时间戳的歌词。示例：[00:12.500]歌词内容")

    offset_text = tags.get("offset", "0")
    offset = int(offset_text) if re.fullmatch(r"[+-]?\d+", offset_text) else 0
    grouped: dict[int, list[str]] = {}
    for time_ms, lyric in entries:
        same_time = grouped.setdefault(time_ms + offset, [])
        if lyric and lyric not in same_time:
            same_time.append(lyric)

    lines = tuple(LyricLine(time_ms, "\n".join(grouped[time_ms])) for time_ms in sorted(grouped))
    return Lyrics(
        lines,
        tags.get("ti") or title,
        tags.get("ar", ""),
        tags.get("al", ""),
        tags.get("source") or source,
    )


def load_lrc(path: Path, *, source: str = "本地 LRC") -> Lyrics:
    raw = path.read_bytes()
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else (
        "utf-8-sig", "gb18030"
    )
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        return parse_lrc(text, title=path.stem, source=source)
    raise ValueError("无法读取歌词编码。请将文件另存为 UTF-8 后重试。")

