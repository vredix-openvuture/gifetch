"""Image formats gifetch shows (GIF, PNG, JPEG, WebP): recognise a file and read its pixel size.

Only headers are read, with the standard library; decoding is left to `kitten icat`. The size is
the one icat draws, so a JPEG turned by its EXIF orientation reports width and height swapped.
"""
import struct

from .errors import GifetchError

SUFFIXES = (".gif", ".png", ".jpg", ".jpeg", ".webp")
FORMATS = "GIF, PNG, JPEG or WebP"
SUFFIX_OF = {"gif": ".gif", "png": ".png", "jpeg": ".jpg", "webp": ".webp"}
HEAD_BYTES = 32  # enough for every fixed-position header below
# JPEG start-of-frame markers; C4 (Huffman table), C8 and CC (arithmetic coding) are not frames.
JPEG_FRAME_MARKERS = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers that stand alone, without a length: TEM, RST0-7, SOI.
JPEG_STANDALONE = {0x01, *range(0xD0, 0xD9)}
JPEG_MAX_SEGMENTS = 4096  # a real file reaches its frame after a few dozen
EXIF_ORIENTATION_TAG = 0x0112


def _format(head):
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _gif(head):
    return struct.unpack("<HH", head[6:10])


def _png(head):
    if head[12:16] != b"IHDR":  # the spec makes IHDR the first chunk; anything else is not plain PNG
        return None
    return struct.unpack(">II", head[16:24])


def _webp(head):
    chunk = head[12:16]
    if chunk == b"VP8X" and len(head) >= 30:  # extended (animation, alpha): canvas size minus one
        return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
    if chunk == b"VP8 " and len(head) >= 30 and head[23:26] == b"\x9d\x01\x2a":  # lossy key frame
        width, height = struct.unpack("<HH", head[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:  # lossless: 14 bit sizes minus one
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _exif_orientation(segment):
    """Orientation (1-8) from an APP1 segment body, or None."""
    if segment[:6] != b"Exif\0\0":
        return None
    tiff = segment[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return None
    first_ifd = struct.unpack(order + "I", tiff[4:8])[0]
    count = struct.unpack(order + "H", tiff[first_ifd:first_ifd + 2])[0]
    for i in range(count):
        entry = tiff[first_ifd + 2 + 12 * i:first_ifd + 14 + 12 * i]
        if len(entry) < 12:
            break
        if struct.unpack(order + "H", entry[:2])[0] == EXIF_ORIENTATION_TAG:
            return struct.unpack(order + "H", entry[8:10])[0]
    return None


def _jpeg(f):
    """Walk the JPEG segments up to the frame header. Big EXIF/ICC blocks are skipped with seek."""
    f.seek(2)
    orientation = 1
    for _ in range(JPEG_MAX_SEGMENTS):
        if f.read(1) != b"\xff":
            return None
        marker = 0xFF
        while marker == 0xFF:  # any number of 0xFF fill bytes may precede the marker
            byte = f.read(1)
            if not byte:
                return None
            marker = byte[0]
        if marker in JPEG_STANDALONE:
            continue
        if marker in (0xD9, 0xDA):  # end of image or start of scan before any frame header
            return None
        raw = f.read(2)
        if len(raw) < 2 or int.from_bytes(raw, "big") < 2:
            return None
        body_length = int.from_bytes(raw, "big") - 2
        if marker in JPEG_FRAME_MARKERS:
            frame = f.read(5)
            if len(frame) < 5:
                return None
            height, width = struct.unpack(">HH", frame[1:5])
            # 5-8 are the orientations turned by 90 degrees; icat shows them upright.
            return (height, width) if orientation in (5, 6, 7, 8) else (width, height)
        if marker == 0xE1:
            orientation = _exif_orientation(f.read(body_length)) or orientation
        else:
            f.seek(body_length, 1)
    return None


def _open(path):
    try:
        return open(path, "rb")
    except OSError as e:
        raise GifetchError(f"cannot read '{path}': {e.strerror}") from e


def detect(path):
    """The format name ("gif", "png", "jpeg", "webp"); raises for anything else."""
    with _open(path) as f:
        found = _format(f.read(HEAD_BYTES))
    if found is None:
        raise GifetchError(f"'{path}' is not a {FORMATS} file")
    return found


def size(path):
    """Width and height in pixels as drawn; raises for unsupported or damaged files."""
    with _open(path) as f:
        head = f.read(HEAD_BYTES)
        found = _format(head)
        if found is None:
            raise GifetchError(f"'{path}' is not a {FORMATS} file")
        try:
            dimensions = _jpeg(f) if found == "jpeg" else {"gif": _gif, "png": _png, "webp": _webp}[found](head)
        except (struct.error, OSError):
            dimensions = None
    if not dimensions or not all(dimensions):
        raise GifetchError(f"'{path}' has no image size in its header (damaged or cut off?)")
    return dimensions


def check(path):
    """Raise unless `path` is a supported image whose size can be read."""
    size(path)


def suffix(path):
    """File suffix matching the detected format, for copies of the image."""
    return SUFFIX_OF[detect(path)]
