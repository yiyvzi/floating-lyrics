from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    background: tuple[str, ...]
    title: str
    artist: str
    secondary: str
    lyric: str
    past: str
    highlight: str
    accent: str
    on_accent: str
    chrome_rgb: str
    highlight_alpha: int = 24


THEMES = {
    "classic": Theme(
        "经典暖金（自定义背景）", (), "#f5f2ee", "#ddd9d4", "#b9bdcb",
        "#d6d7e2", "#9899a8", "#ffe0a1", "#e7bf82", "#25201a", "255,255,255",
    ),
    "sea": Theme(
        "深海青", ("#254b58", "#1a2f44", "#101e30"),
        "#f0fffc", "#c6ebe5", "#aed1d4", "#cee6eb", "#9ebbc7",
        "#8af2de", "#62d2c2", "#102e36", "146,233,222", 31,
    ),
    "porcelain": Theme(
        "奶油蓝", ("#faf2e0", "#f2f3ee", "#c4ddef"),
        "#173f63", "#2c5777", "#45647a", "#284b63", "#506779",
        "#104c83", "#0e61ac", "#faf2e0", "14,65,106", 24,
    ),
    "olive": Theme(
        "雾橄榄", ("#31452b", "#233728", "#11201e"),
        "#f7f3ec", "#e9e2d6", "#cec6ba", "#ede6db", "#c9c1b5",
        "#faf0e4", "#c2c08a", "#11201e", "194,192,138", 35,
    ),
    "berry": Theme(
        "深莓红", ("#65020a", "#490f22", "#281722"),
        "#fff2f5", "#f2cbd5", "#d6b5c3", "#f6dfe7", "#c9a5b5",
        "#6bebf3", "#18d5e7", "#3a0f21", "136,232,240", 25,
    ),
}
