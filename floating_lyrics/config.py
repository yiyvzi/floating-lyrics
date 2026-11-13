from dataclasses import dataclass, replace
import json
from pathlib import Path
import re

from PySide6.QtCore import QIODevice, QRect, QSaveFile, QStandardPaths
from PySide6.QtGui import QGuiApplication

from .themes import THEMES

ROOT = Path(__file__).resolve().parent.parent
APPEARANCE_LIMITS = {
    "opacity": (1, 100),
    "font_size": (11, 42),
    "line_spacing": (2, 32),
    "follow_delay": (0, 60),
}


@dataclass
class Appearance:
    width: int
    height: int
    background: str
    opacity: int
    font_size: int
    line_spacing: int
    follow_delay: int = 5
    theme: str = "classic"


def load_defaults():
    data = json.loads((ROOT / "defaults.json").read_text(encoding="utf-8"))
    hotkeys = data.pop("hotkeys")
    if not isinstance(data.get("theme"), str) or data["theme"] not in THEMES:
        data["theme"] = "classic"
    return Appearance(**data), hotkeys


def app_data_path():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))


def appearance_path():
    return app_data_path() / "appearance.json"


def load_appearance(defaults):
    try:
        saved = json.loads(appearance_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(saved, dict):
        return defaults

    values = {}
    for field, (minimum, maximum) in APPEARANCE_LIMITS.items():
        value = saved.get(field)
        if type(value) is int and minimum <= value <= maximum:
            values[field] = value
    color = saved.get("background")
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        values["background"] = color
    theme = saved.get("theme")
    if theme == "dusk":
        theme = "olive"
    if isinstance(theme, str) and theme in THEMES:
        values["theme"] = theme
    return replace(defaults, **values)


def save_appearance(appearance):
    values = {field: getattr(appearance, field) for field in APPEARANCE_LIMITS}
    values["background"] = appearance.background
    values["theme"] = appearance.theme
    save_json(appearance_path(), values)


def save_json(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(values, indent=2) + "\n").encode("utf-8")
    file = QSaveFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(file.errorString())
    if file.write(payload) != len(payload):
        file.cancelWriting()
        raise OSError(file.errorString())
    if not file.commit():
        raise OSError(file.errorString())


def visible_geometry(rect, screens):
    def overlap(screen):
        intersection = screen.intersected(rect)
        return intersection.width() * intersection.height()

    screen = max(screens, key=overlap)
    width = min(rect.width(), screen.width())
    height = min(rect.height(), screen.height())
    if not overlap(screen):
        return QRect(screen.center().x() - width // 2, screen.center().y() - height // 2, width, height)
    x = max(screen.left(), min(rect.x(), screen.right() - width + 1))
    y = max(screen.top(), min(rect.y(), screen.bottom() - height + 1))
    return QRect(x, y, width, height)


def restore_window_state(window):
    try:
        data = json.loads((app_data_path() / "window.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("player_preference") in ("auto", "qq", "netease"):
        window.player_preference = data["player_preference"]
    if type(data.get("show_translation")) is bool:
        window.lyrics_view.show_translation = data["show_translation"]
        window.lyrics_view.reflow()
    bounds = data.get("geometry")
    if not isinstance(bounds, list) or len(bounds) != 4 or not all(
        type(value) is int and -100_000 <= value <= 100_000 for value in bounds
    ):
        return
    rect = QRect(*bounds)
    if rect.isValid():
        rect.setSize(rect.size().expandedTo(window.minimumSize()))
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            screens = [primary.availableGeometry()]
            screens.extend(screen.availableGeometry() for screen in QGuiApplication.screens()
                           if screen != primary)
            window.setGeometry(visible_geometry(rect, screens))


def save_window_state(window):
    rect = window.geometry()
    save_json(app_data_path() / "window.json", {
        "geometry": list(rect.getRect()),
        "show_translation": window.lyrics_view.show_translation,
        "player_preference": window.player_preference,
    })

