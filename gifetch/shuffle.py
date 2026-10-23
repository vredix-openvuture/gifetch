"""Random order without repeats for folder sources: a shuffled queue per folder, kept between starts.

Every file comes up once before any comes up again, and a new round never starts with the file
that ended the last one. State lives in ~/.cache/gifetch/shuffle/<hash>.json, one per folder and
file kind, so -rg and -rc on the same folder keep separate orders.
"""
import fcntl
import hashlib
import json
import os
import random
import tempfile


def _state_file(folder, suffixes):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    key = os.path.abspath(folder) + "\0" + ",".join(suffixes)
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(base, "gifetch", "shuffle", f"{digest}.json")


def _read(path):
    """Queue and last pick; anything missing or damaged only means a fresh round."""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return [], None
    if not isinstance(state, dict):
        return [], None
    queue, last = state.get("queue"), state.get("last")
    if not isinstance(queue, list) or not all(isinstance(n, str) for n in queue):
        queue = []
    return queue, last if isinstance(last, str) else None


def _write(path, queue, last):
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        json.dump({"queue": queue, "last": last}, f)
    os.replace(temporary, path)


def _advance(path, names):
    queue, last = _read(path)
    queue = [n for n in queue if n in names]
    if not queue:
        queue = random.sample(names, len(names))
        if len(queue) > 1 and queue[0] == last:
            queue.append(queue.pop(0))
    chosen = queue.pop(0)
    _write(path, queue, chosen)
    return chosen


def next_file(folder, files, suffixes):
    """The next of `files` (full paths inside `folder`) in this folder's shuffled order."""
    names = sorted(os.path.basename(f) for f in files)
    path = _state_file(folder, suffixes)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Several kitty windows opening at once start gifetch in parallel; the lock keeps them
        # from drawing the same file or losing each other's progress.
        with open(path + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            chosen = _advance(path, names)
    except OSError:
        # An unwritable cache costs the no-repeat order, not the start: the pick is still random.
        chosen = random.choice(names)
    return os.path.join(folder, chosen)
