"""Make ui/brand/runner-dark.css: the dark theme of the Run reports page (ui/index.html).

The page's own stylesheets (ui/styles.css, ui/runner.css) are written with fixed light colours. This script reads every
rule that sets a colour and writes the same rule for dark mode with the lightness of each colour turned around (light
backgrounds become dark, dark text becomes light; hues stay), so every part of the page stays readable without
keeping two stylesheets by hand. Run it again after changing those two files:

    python tools/theme/make_runner_dark.py
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "ui" / "styles.css", ROOT / "ui" / "runner.css"]
TARGET = ROOT / "ui" / "brand" / "runner-dark.css"
DARK = ':root[data-theme="dark"]'
AUTO = ':root:not([data-theme="light"])'

COLOR_PROPS = re.compile(r"^(--[\w-]+|color|background(-color|-image)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|fill|stroke|"
                         r"box-shadow|text-shadow|caret-color|accent-color|text-decoration(-color)?|column-rule(-color)?)$")
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(white|black)\b")
NAMED = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0)}


def parse(token: str) -> tuple[int, int, int, float] | None:
    token = token.strip().lower()
    if token in NAMED:
        return NAMED[token]
    if token.startswith("#"):
        h = token[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha
    numbers = re.findall(r"[\d.]+%?", token)
    if len(numbers) < 3:
        return None
    r, g, b = (int(float(n.rstrip("%")) * (2.55 if n.endswith("%") else 1)) for n in numbers[:3])
    alpha = 1.0
    if len(numbers) > 3:
        a = numbers[3]
        alpha = float(a.rstrip("%")) / 100 if a.endswith("%") else float(a)
    return r, g, b, alpha


def flip(rgba: tuple[int, int, int, float], shadow: bool, text: bool = False) -> str:
    r, g, b, alpha = rgba
    if shadow:                                       # shadows stay shadows: black, a little stronger
        return f"rgb(0 0 0 / {min(0.6, alpha * 2.2 + 0.08):.2f})"
    h, light, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    new_light = 0.07 + (1 - light) * 0.86              # 0.97 → 0.10 (page), 0.20 → 0.76 (text)
    if light >= 0.995:                                # white cards and fields sit a little above the dark page
        new_light = 0.145
    if 0.35 < light < 0.65 and s > 0.35:              # strong mid colours (accents, status) keep their strength
        new_light = min(0.72, light + 0.12)
    if text and 0.3 < light < 0.95:                  # muted text (light greys on white) stays readable on dark cards
        new_light = max(new_light, 0.66)
    s = s * 0.85 if new_light < 0.3 else s
    if s < 0.06:                                      # greys and white: a cool slate, like the other dark pages
        h, s = 0.6, 0.28 if new_light < 0.3 else 0.12
    nr, ng, nb = colorsys.hls_to_rgb(h, new_light, s)
    hex_ = f"#{round(nr * 255):02x}{round(ng * 255):02x}{round(nb * 255):02x}"
    return hex_ if alpha >= 0.999 else f"{hex_}{round(alpha * 255):02x}"


def convert(prop: str, value: str) -> str | None:
    shadow = prop in ("box-shadow", "text-shadow")
    changed = False

    def swap(match: re.Match[str]) -> str:
        nonlocal changed
        rgba = parse(match.group(0))
        if rgba is None:
            return match.group(0)
        changed = True
        return flip(rgba, shadow, text=prop == "color")

    out = COLOR.sub(swap, value)
    return out if changed else None


def rules(css: str):
    """(media or '', selector, declarations) for every plain rule; @media blocks are walked into."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i = 0
    stack: list[str] = []
    while i < len(css):
        brace = css.find("{", i)
        close = css.find("}", i)
        if close != -1 and (brace == -1 or close < brace):
            if stack:
                stack.pop()
            i = close + 1
            continue
        if brace == -1:
            break
        head = css[i:brace].strip()
        if head.startswith("@media") or head.startswith("@supports"):
            stack.append(head)
            i = brace + 1
            continue
        if head.startswith("@"):                     # @keyframes, @font-face: skip the whole block
            depth, j = 1, brace + 1
            while depth and j < len(css):
                depth += {"{": 1, "}": -1}.get(css[j], 0)
                j += 1
            i = j
            continue
        end = css.find("}", brace)
        yield (stack[-1] if stack else ""), head, css[brace + 1:end]
        i = end + 1


def prefixed(selector: str, prefix: str) -> str:
    parts = []
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if part in (":root", "html"):
            parts.append(prefix)
        elif part.startswith(("html ", ":root ")):
            parts.append(prefix + part[part.index(" "):])
        else:
            parts.append(f"{prefix} {part}")
    return ", ".join(parts)


def build() -> str:
    blocks: dict[str, list[str]] = {}
    for source in SOURCES:
        for media, selector, body in rules(source.read_text(encoding="utf-8")):
            out = []
            for declaration in body.split(";"):
                if ":" not in declaration:
                    continue
                prop, value = declaration.split(":", 1)
                prop = prop.strip().lower()
                if COLOR_PROPS.match(prop) and "url(" not in value:
                    new = convert(prop, value.strip())
                    if new is not None:
                        out.append(f"{prop}:{new}")
            if out:
                blocks.setdefault(media, []).append((selector, ";".join(out)))
    lines = ["/* GENERATED by tools/theme/make_runner_dark.py from ui/styles.css and ui/runner.css: do not edit by hand. */",
             "/* The Run reports page in dark mode (ui/brand/theme.js sets html[data-theme]; Auto follows the computer). */",
             f"{DARK}{{color-scheme:dark}}",
             f"@media (prefers-color-scheme:dark){{{AUTO}{{color-scheme:dark}}}}"]
    for media, items in blocks.items():
        dark = "\n".join(f"{prefixed(sel, DARK)}{{{decl}}}" for sel, decl in items)
        auto = "\n".join(f"{prefixed(sel, AUTO)}{{{decl}}}" for sel, decl in items)
        lines.append(f"{media}{{\n{dark}\n}}" if media else dark)
        if media:
            lines.append(f"@media (prefers-color-scheme:dark) and {media[len('@media'):].strip()}{{\n{auto}\n}}")
        else:
            lines.append(f"@media (prefers-color-scheme:dark){{\n{auto}\n}}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    TARGET.write_text(build(), encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes)")
