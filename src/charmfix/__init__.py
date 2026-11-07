"""charmfix: repair EXIF metadata of Kodak Charmera JPEGs without touching image data."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("charmfix")
except PackageNotFoundError:  # run from a source tree that was never installed
    __version__ = "0+unknown"
