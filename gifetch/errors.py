"""The one exception type that ends a run with a plain message instead of a traceback."""


class GifetchError(Exception):
    """A user mistake or missing precondition, phrased for the user."""
