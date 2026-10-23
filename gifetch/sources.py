"""Where an image or a fastfetch config comes from: one file, or a folder picked from at random.

A source is (kind, path) with kind "file" or "folder". The bundled defaults live in defaults/.
"""
import os
from dataclasses import dataclass

from . import fastfetch, images, shuffle
from .errors import GifetchError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GIF = os.path.join(ROOT, "defaults", "default.gif")
DEFAULT_CONFIG = os.path.join(ROOT, "defaults", "default.jsonc")

IMAGE_SUFFIXES = images.SUFFIXES
CONFIG_SUFFIXES = (".jsonc", ".json")


@dataclass(frozen=True)
class Source:
    kind: str  # "file" or "folder"
    path: str

    @property
    def is_folder(self):
        return self.kind == "folder"


def default_gif():
    return Source("file", DEFAULT_GIF)


def default_config():
    return Source("file", DEFAULT_CONFIG)


def candidates(folder, suffixes):
    """Files in `folder` with one of `suffixes`, sorted; hidden files are skipped."""
    try:
        entries = os.listdir(folder)
    except OSError as e:
        raise GifetchError(f"cannot read folder '{folder}': {e.strerror}") from e
    found = sorted(
        os.path.join(folder, e) for e in entries
        if not e.startswith(".") and e.lower().endswith(suffixes) and os.path.isfile(os.path.join(folder, e))
    )
    if not found:
        raise GifetchError(f"no {'/'.join(suffixes)} files in '{folder}'")
    return found


def pick(source, suffixes):
    """The file to use this run: the file itself, or the next one in the folder's shuffled order."""
    if source.is_folder:
        return shuffle.next_file(source.path, candidates(source.path, suffixes), suffixes)
    return source.path


def _refuse_folder(path, random_flag):
    if os.path.isdir(path):
        raise GifetchError(f"'{path}' is a folder; for a random pick from it use {random_flag} {path}")


def check_image_file(path):
    _refuse_folder(path, "-rg")
    images.check(path)


def check_config_file(path):
    """Let fastfetch load the config. Returns layout warnings; raises if fastfetch rejects it."""
    _refuse_folder(path, "-rc")
    if not os.path.isfile(path):
        raise GifetchError(f"cannot read '{path}': no such file")
    error = fastfetch.config_error(path)
    if error:
        raise GifetchError(f"fastfetch rejects '{path}': {error}")
    return [f"{path}: {w}" for w in fastfetch.layout_warnings(path)]


def check_image(source):
    for path in candidates(source.path, IMAGE_SUFFIXES) if source.is_folder else [source.path]:
        check_image_file(path)


def check_config(source):
    """Check every config the source can yield. Returns all layout warnings."""
    paths = candidates(source.path, CONFIG_SUFFIXES) if source.is_folder else [source.path]
    return [w for path in paths for w in check_config_file(path)]
