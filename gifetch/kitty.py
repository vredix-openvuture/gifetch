"""kitty side: terminal size and `kitten icat` with Unicode placeholders."""
import base64
import fcntl
import os
import re
import signal
import struct
import subprocess
import termios
from dataclasses import dataclass

from .errors import GifetchError

APC = re.compile(r"\x1b_G[^\x1b]*\x1b\\")
FG = re.compile(r"\x1b\[38:2:[0-9:]+m")
PLACEHOLDER = "\U0010EEEE"
CONTROL = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]|\x1b[78]|\r")
# Assumed cell size when the terminal does not report pixels, and size when it reports nothing.
FALLBACK_CELL = (10, 20)
FALLBACK_WINDOW = (80, 24)
SHM_DIR = "/dev/shm"


class NoTerminalError(GifetchError):
    """No controlling terminal, e.g. under cron, setsid or a piped launcher."""


@dataclass(frozen=True)
class Window:
    cols: int
    rows: int
    px_w: int
    px_h: int

    @property
    def cell_w(self):
        return self.px_w / self.cols

    @property
    def cell_h(self):
        return self.px_h / self.rows


@dataclass(frozen=True)
class Image:
    transmit: str  # escapes that upload all frames
    animate: str  # escapes that start the loop; sent after the placeholders are on screen
    colour: str  # foreground colour escape that carries the image id
    rows: list  # placeholder rows, one string per cell row


def window():
    try:
        with open("/dev/tty") as tty:
            rows, cols, px_w, px_h = struct.unpack("HHHH", fcntl.ioctl(tty, termios.TIOCGWINSZ, b"\0" * 8))
    except OSError as e:
        raise NoTerminalError(f"needs a kitty terminal (/dev/tty: {e.strerror})") from e
    if not cols or not rows:  # a pty nobody sized, e.g. `script` without stty
        cols, rows = FALLBACK_WINDOW
    if not px_w or not px_h:
        px_w, px_h = cols * FALLBACK_CELL[0], rows * FALLBACK_CELL[1]
    return Window(cols, rows, px_w, px_h)


def _transfer_mode():
    # Shared memory hands kitty the decoded frames directly instead of megabytes of base64 through
    # the pty. Over ssh the remote host's memory is out of kitty's reach, so stream there.
    # GIFETCH_TRANSFER_MODE overrides, e.g. for checks in a pseudo terminal no kitty reads from.
    forced = os.environ.get("GIFETCH_TRANSFER_MODE")
    if forced:
        return forced
    if any(os.environ.get(v) for v in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return "stream"
    return "memory"


def placeholder_image(gif, cols, rows, win):
    """Encode `gif` to fit a cols x rows cell box, keeping its aspect ratio."""
    command = ["kitten", "icat", "--unicode-placeholder", f"--transfer-mode={_transfer_mode()}", "--stdin=no",
               "--scale-up", f"--place={cols}x{rows}@0x0",
               f"--use-window-size={win.cols},{win.rows},{win.px_w},{win.px_h}", gif]
    # icat ignores Ctrl-C, so it always finishes and hands over the names of every frame it put in
    # shared memory; on any failure those are released here instead of leaking.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    try:
        out, err = process.communicate()
    except KeyboardInterrupt:
        release(process.communicate()[0])
        raise
    if process.returncode != 0:
        release(out)
        raise subprocess.CalledProcessError(process.returncode, command, out, err)
    commands = APC.findall(out)
    image = Image(
        transmit="".join(c for c in commands if not c.startswith("\x1b_Ga=a,")),
        animate="".join(c for c in commands if c.startswith("\x1b_Ga=a,")),
        colour=(FG.search(out) or [""])[0],
        rows=[r for r in CONTROL.sub("", APC.sub("", out)).split("\n") if r],
    )
    if not image.colour or not image.rows or PLACEHOLDER not in image.rows[0]:
        release(out)
        raise GifetchError(f"unexpected kitten icat output for '{gif}' - is kitty 0.28 or newer installed?")
    return image


def release(escapes, shm_dir=SHM_DIR):
    """Remove the shared memory frames named in `escapes`, because kitty will never read them.

    kitty unlinks them after reading; without that they would sit in RAM (/dev/shm) until reboot.
    """
    for command in APC.findall(escapes or ""):
        control, _, payload = command[3:-2].partition(";")
        if "t=s" not in control.split(","):
            continue
        name = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode(errors="replace")  # sent unpadded
        try:
            os.unlink(os.path.join(shm_dir, os.path.basename(name)))
        except FileNotFoundError:
            pass
