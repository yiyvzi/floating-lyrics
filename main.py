import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from floating_lyrics import __version__
from floating_lyrics.config import ROOT, load_appearance, load_defaults, restore_window_state
from floating_lyrics.hotkeys import GlobalHotkeys
from floating_lyrics.media import WindowsMediaMonitor
from floating_lyrics.window import FloatingWindow
from floating_lyrics.window_effects import show_message


def main():
    if sys.platform != "win32":
        raise SystemExit("Floating Lyrics 需要 Windows。")

    app = QApplication(sys.argv)
    app.setApplicationName("Floating Lyrics")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ROOT / "assets" / "floating-lyrics.ico")))
    # The overlay is a Qt tool window, so closing a dialog can otherwise
    # trigger automatic quit while the overlay is still visible.
    app.setQuitOnLastWindowClosed(False)
    appearance, shortcuts = load_defaults()
    window = FloatingWindow(load_appearance(appearance), shortcuts)
    restore_window_state(window)
    window.track_window_state()
    window.enable_media()
    media = WindowsMediaMonitor(window)
    media.select_player(window.player_preference)
    media.updated.connect(window.update_media)
    window.player_selected.connect(media.select_player)
    window.playback_requested.connect(media.control)
    media.control_finished.connect(window.playback_result)
    app.aboutToQuit.connect(media.close)
    hotkeys = GlobalHotkeys(app)
    actions = {
        "visibility": window.toggle_visibility,
        "click_through": window.toggle_click_through,
        "follow": window.lyrics_view.resume_follow,
    }
    unavailable = []
    for identifier, (name, callback) in enumerate(actions.items(), 1):
        try:
            hotkeys.register(identifier, shortcuts[name], callback)
        except (OSError, ValueError) as error:
            unavailable.append(f"{shortcuts[name]}（{error}）")
        else:
            if name == "visibility":
                window.can_hide = True
            if name == "click_through":
                window.can_click_through = True

    window.show()
    if unavailable:
        show_message(
            window, "部分快捷键不可用",
            "以下快捷键注册失败，可以修改 defaults.json 后重启。\n\n" + "\n".join(unavailable),
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

