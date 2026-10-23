"""Animated GIF next to fastfetch, robust against window resizes.

The GIF is drawn with kitty's Unicode placeholders: every image cell is a real
character in the text, so it reflows together with the fastfetch lines instead of
floating at a fixed screen position. The gap between image and text is made of real
spaces for the same reason.
"""
import re
import sys
import unicodedata

from . import fastfetch, images, kitty

GAP = 3  # cells between image and text
MAX_WIDTH_SHARE = 0.4  # the image never takes more than this share of the window width
MIN_ROWS = 8  # a very short fetch still gets a recognisable image
MIN_WIDTH_SHARE = 0.3  # in narrow windows the image makes room for the text, down to this share
MIN_COLS = 12  # floor of that shrink; the 40 % cap still wins in windows under 30 cells
# Escape sequences take no cells: OSC (e.g. hyperlinks, ended by BEL or ST), CSI, and two-byte ones.
ESCAPE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;:?]*[A-Za-z]|\x1b[@-Z\\-_]")
TOKEN = re.compile(f"({ESCAPE.pattern})")
# DECAWM off/on: in a window too narrow for a line, cut it at the edge instead of wrapping it
# onto the next row, which would push the image rows apart.
NO_WRAP, WRAP = "\x1b[?7l", "\x1b[?7h"


def char_cells(char):
    """Terminal cells a character takes: 0 for combining marks, 2 for wide CJK and emoji."""
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def visible_width(line):
    return sum(char_cells(c) for c in ESCAPE.sub("", line))


def fit_line(line, width):
    """Cut `line` to `width` cells, ending in an ellipsis; escape sequences are kept whole."""
    if width <= 0:
        return ""
    if visible_width(line) <= width:
        return line
    kept, used, cut = [], 0, False
    for part in TOKEN.split(line):
        if ESCAPE.fullmatch(part):
            kept.append(part)  # later escapes still run, so colours and links are closed
            continue
        for char in "" if cut else part:
            cells = char_cells(char)
            if used + cells > width - 1:  # one cell stays for the ellipsis
                kept.append("\u2026")
                cut = True
                break
            kept.append(char)
            used += cells
    return "".join(kept)


def image_box(gif_px, text_rows, win, text_cols=0):
    """Cell box for the image: as tall as the text, unless that would make it too wide.

    Too wide means more than MAX_WIDTH_SHARE of the window, or leaving less room than the
    `text_cols` widest text line needs; then it shrinks down to MIN_WIDTH_SHARE (at least MIN_COLS)
    and lines that still do not fit are cut at the window edge.
    """
    gif_w, gif_h = gif_px
    rows = max(1, min(max(MIN_ROWS, text_rows), win.rows - 1))  # never taller than the window
    cols = round(rows * win.cell_h * gif_w / (gif_h * win.cell_w))
    room_beside_text = win.cols - 1 - GAP - text_cols
    smallest = max(MIN_COLS, int(win.cols * MIN_WIDTH_SHARE))
    max_cols = max(1, min(int(win.cols * MAX_WIDTH_SHARE), max(smallest, room_beside_text)))
    if cols > max_cols:
        cols = max_cols
        rows = max(1, round(cols * win.cell_w * gif_h / (gif_w * win.cell_h)))
    return max(cols, 1), rows


def show(gif, config, fastfetch_args):
    """Clear the screen and draw the animated GIF next to the fastfetch output."""
    info = fastfetch.fetch_lines(config, fastfetch_args)
    win = kitty.window()
    text_cols = max((visible_width(line) for line in info), default=0)
    cols, rows = image_box(images.size(gif), len(info), win, text_cols)
    image = kitty.placeholder_image(gif, cols, rows, win)
    try:
        sys.stdout.write(layout(image, cols, info, win.cols))
        sys.stdout.flush()
    except BaseException:
        kitty.release(image.transmit)
        try:  # the output may have stopped after NO_WRAP; give the terminal its line wrap back
            sys.stdout.write(WRAP)
            sys.stdout.flush()
        except OSError:
            pass
        raise


def layout(image, cols, info, window_cols):
    """Screen clear, frame upload, image and text side by side, then the loop start."""
    blank = " " * cols
    # icat may round the box down by a cell; centre what it produced in the slot.
    width = image.rows[0].count(kitty.PLACEHOLDER)
    left = " " * ((cols - width) // 2)
    right = " " * max(0, cols - width - len(left))
    image_rows = [f"{left}{image.colour}{row}\x1b[39m{right}" for row in image.rows]
    top = max(0, (len(info) - len(image_rows)) // 2)
    image_rows = [blank] * top + image_rows
    lines = []
    for i in range(max(len(image_rows), len(info))):
        cell = image_rows[i] if i < len(image_rows) else blank
        text = fit_line(info[i], window_cols - 1 - cols - GAP) if i < len(info) else ""
        lines.append(f" {cell}{' ' * GAP}{text}\x1b[0m")
    return "\x1b[H\x1b[2J\x1b[3J" + image.transmit + NO_WRAP + "\n".join(lines) + "\n" + WRAP + image.animate
