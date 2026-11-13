from collections import defaultdict
import re
from xml.etree import ElementTree

from .lrc import LyricLine, Lyrics

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ROLE = "{http://www.w3.org/ns/ttml#metadata}role"


def chinese(language):
    return language.lower().split("-", 1)[0] in {"zh", "cmn"}


def time_ms(value):
    if re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s)?", value):
        if value.endswith("ms"):
            return round(float(value[:-2]))
        return round(float(value.removesuffix("s")) * 1000)
    if re.fullmatch(r"(?:\d+:)?\d+:\d+(?:\.\d+)?", value):
        result = 0.0
        for part in value.split(":"):
            result = result * 60 + float(part)
        return round(result * 1000)
    raise ValueError("不支持的 TTML 时间戳")


def line_text(node):
    parts = [node.text or ""]
    for child in node:
        if child.get(ROLE) not in {"x-bg", "x-translation", "x-roman"}:
            parts.append(line_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def parse_amll_ttml(payload, track):
    root = ElementTree.fromstring(payload)
    linked = {}
    for group in root.findall(".//{*}translations/{*}translation"):
        if chinese(group.get(XML_LANG, "")):
            for item in group.findall("{*}text"):
                linked.setdefault(item.get("for", ""), " ".join(line_text(item).split()))

    rows = defaultdict(list)
    for node in root.findall(".//{*}body//{*}p"):
        begin = node.get("begin")
        original = " ".join(line_text(node).split())
        if begin is None or not original:
            continue
        key = next((value for attr, value in node.attrib.items() if attr.endswith("}key")), "")
        translation = next(
            (" ".join(line_text(child).split()) for child in node
             if child.get(ROLE) == "x-translation" and chinese(child.get(XML_LANG, ""))),
            "",
        ) or linked.get(key, "")
        rows[time_ms(begin)].append((original, translation))

    lines = tuple(
        LyricLine(stamp, "\n".join(text for text, _ in pairs),
                  "\n".join(translation for _, translation in pairs if translation))
        for stamp, pairs in sorted(rows.items())
    )
    if not any(line.translation for line in lines):
        return None
    return Lyrics(lines, track.title, track.artist, track.album, "AMLL", translation_source="AMLL")
