"""Calls into the installed fastfetch binary. gifetch never ships its own copy, so a fastfetch
update brings new modules and options without any change here."""
import ctypes
import os
import re
import subprocess

# Options that print something other than a fetch (or loop forever): no image, fastfetch runs alone.
# fastfetch matches long options case-insensitively, so this does too. (-h is gifetch's own.)
STANDALONE = re.compile(
    r"^(-v|--version|-w|--watch|--dynamic-interval|--help|--list-.*|--print-.*|--gen-config.*)$",
    re.IGNORECASE,
)
# These depend on their value: `--format default` and `--json false` still print a normal fetch.
NORMAL_VALUE = {"--format": {"default"}, "--json": {"false", "0", "no", "off"}, "-j": {"false", "0", "no", "off"}}
# Private Use Area code points, where Nerd Font icons live: literal, or as a JSON \uXXXX escape.
NERD_FONT_ICON = re.compile("[\ue000-\uf8ff\U000f0000-\U0010fffd]|\\\\u(e[0-9a-f]{3}|f[0-8][0-9a-f]{2})", re.IGNORECASE)
KEY_WIDTH = re.compile(r'"key"\s*:\s*\{[^{}]*"width"')


def is_standalone(args):
    for i, arg in enumerate(args):
        name, _, inline_value = arg.partition("=")
        if STANDALONE.match(name):
            return True
        normal_values = NORMAL_VALUE.get(name.lower())
        if normal_values is None:
            continue
        following = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else ""
        if (inline_value or following).lower() not in normal_values:
            return True
    return False


def config_error(config):
    """Let fastfetch parse the config; return its error text, or None if it loads."""
    result = subprocess.run(
        ["fastfetch", "--config", config, "--logo", "none", "--structure", "none", "--pipe", "true"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout).strip() or f"fastfetch exit {result.returncode}"


def layout_warnings(config):
    """Settings that are valid fastfetch but break the column next to the image."""
    # fastfetch already accepted the file; a stray non-UTF-8 byte must not crash the check.
    with open(config, encoding="utf-8", errors="replace") as f:
        text = f.read()
    warnings = []
    if KEY_WIDTH.search(text):
        warnings.append("display.key.width shifts text with absolute column jumps; pad keys by hand instead")
    if NERD_FONT_ICON.search(text):
        warnings.append("Nerd Font icons render at uneven widths and can misalign the text column")
    return warnings


def _hide_from_shell_detection():
    """Rename this process to "fastfetch" in the kernel's process table.

    fastfetch finds the shell by walking up its parent processes and skips the ones it knows as
    wrappers, its own name among them. Without this the shell line reads "gifetch" instead of
    fish or bash. Only `ps`/`top` show the new name; on failure the line just stays wrong.
    """
    try:
        zero = ctypes.c_ulong(0)
        ctypes.CDLL(None).prctl(15, b"fastfetch", zero, zero, zero)  # 15 = PR_SET_NAME
    except (OSError, AttributeError):
        pass


def fetch_lines(config, args):
    """fastfetch output as lines, colours kept, logo forced off since gifetch draws the GIF."""
    _hide_from_shell_detection()
    out = subprocess.run(
        ["fastfetch", "--config", config, *args, "--logo", "none", "--pipe", "false"],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.rstrip("\n").split("\n")


def replace_process(config, args):
    """Become plain fastfetch with this config, for standalone options or a non-kitty terminal."""
    try:
        os.execvp("fastfetch", ["fastfetch", "--config", config, *args])
    except FileNotFoundError as e:
        raise FileNotFoundError(e.errno, e.strerror, "fastfetch") from e
