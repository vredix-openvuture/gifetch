"""Named presets in ~/.config/gifetch: which GIF and which fastfetch config to show together.

Layout:
    presets/<name>/preset.json      {"gif": {"kind", "path"}, "config": {"kind", "path"}}
    presets/<name>/image.<ext>      copy of a single image, so the original may move or vanish;
                                    the suffix follows the detected format
    presets/<name>/fastfetch.jsonc  copy of a single config, edit it freely

A single file is copied; a folder is stored as its absolute path, so files added to it later
take part in the random pick. The bundled defaults are stored as their path too, so replacing
them in defaults/ also changes presets saved without -g or -c.
"""
import json
import os
import re
import shutil

from . import images, sources
from .errors import GifetchError
from .sources import Source

NAME = re.compile(r"^[A-Za-z0-9._-]+$")
PRESET_FILE = "preset.json"
CONFIG_COPY = "fastfetch.jsonc"


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "gifetch")


def _presets_dir():
    return os.path.join(config_dir(), "presets")


def preset_dir(name):
    return os.path.join(_presets_dir(), name)


def _preset_file(name):
    return os.path.join(preset_dir(name), PRESET_FILE)


def _check_name(name):
    # A leading '-' would be read as an option by -p/-d, a leading '.' marks unfinished saves.
    if not NAME.fullmatch(name) or name[0] in ".-":
        raise GifetchError(
            f"invalid preset name '{name}': use letters, digits, '.', '_' or '-', not starting with '.' or '-'"
        )


def names():
    try:
        entries = os.listdir(_presets_dir())
    except FileNotFoundError:
        return []
    return sorted(e for e in entries if not e.startswith(".") and os.path.isfile(_preset_file(e)))


def _require(name):
    if name not in names():
        known = ", ".join(names()) or "none"
        raise GifetchError(f"no preset named '{name}' (saved: {known})")


def load(name):
    """Return (gif source, config source) of a saved preset, with absolute paths."""
    _require(name)
    try:
        with open(_preset_file(name), encoding="utf-8") as f:
            stored = json.load(f)
        return tuple(_loaded(name, stored[key]) for key in ("gif", "config"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise GifetchError(f"preset '{name}' is damaged ({_preset_file(name)}): {e}") from e


def _loaded(name, entry):
    if entry["kind"] not in ("file", "folder") or not isinstance(entry["path"], str):
        raise ValueError(f"bad entry {entry!r}")
    # Copied files are stored relative to the preset folder, so the config dir may move.
    # An absolute path (folder or bundled default) is kept as it is by os.path.join.
    return Source(entry["kind"], os.path.join(preset_dir(name), entry["path"]))


def _stored(source, copy_name, target_dir):
    """Copy a single file into the preset; keep a folder or a bundled default as its absolute path."""
    if source.is_folder or source in (sources.default_gif(), sources.default_config()):
        return {"kind": source.kind, "path": os.path.abspath(source.path)}
    shutil.copyfile(source.path, os.path.join(target_dir, copy_name))
    return {"kind": "file", "path": copy_name}


def save(name, gif, config):
    """Store a new preset. The sources must already be checked."""
    _check_name(name)
    if name in names():
        raise GifetchError(f"preset '{name}' already exists; remove it first with -d {name}")
    # Build under a dot name that names() ignores, then rename, so a failed save leaves no preset.
    partial = os.path.join(_presets_dir(), f".{name}.partial")
    try:
        shutil.rmtree(partial, ignore_errors=True)
        os.makedirs(partial)
        image_copy = "image" + images.suffix(gif.path) if not gif.is_folder else None
        stored = {"gif": _stored(gif, image_copy, partial), "config": _stored(config, CONFIG_COPY, partial)}
        with open(os.path.join(partial, PRESET_FILE), "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
            f.write("\n")
        os.replace(partial, preset_dir(name))
    except OSError as e:
        shutil.rmtree(partial, ignore_errors=True)
        if os.path.exists(preset_dir(name)):
            raise GifetchError(f"cannot store preset: {preset_dir(name)} already exists without "
                               f"{PRESET_FILE}; remove that folder first") from e
        raise GifetchError(f"cannot store preset in {_presets_dir()}: {e.strerror}") from e


def delete(name):
    _require(name)
    try:
        shutil.rmtree(preset_dir(name))
    except OSError as e:
        raise GifetchError(f"cannot delete {preset_dir(name)}: {e.strerror}") from e
