"""DECIMER Segmentation model service."""

import os

__version__ = "0.1.0"

# Largest page, in pixels, that OpenCV will decode (A3 at 300 DPI is about 17 Mpx). The library
# keeps one full-page boolean mask per detection, so a 150 Mpx page with ten structures needs
# 5-6 GB. OpenCV reads this variable once, when `cv2` is first imported, so it has to be set
# before anything imports cv2: importing this package is enough, which is why it lives here.
MAX_IMAGE_PIXELS = 40_000_000
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(MAX_IMAGE_PIXELS))
