from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_jpeg


@pytest.fixture(scope="session")
def base_jpeg() -> bytes:
    return make_jpeg()


@pytest.fixture(scope="session")
def icc_profile() -> bytes:
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return bytes(profile.tobytes())


@pytest.fixture(scope="session")
def icc_jpeg(icc_profile: bytes) -> bytes:
    return make_jpeg(icc=icc_profile)
