"""The seg service as it runs in production: the shared skeleton, our model, our limits."""

from decimer_service import Limits, Model, create_app
from fastapi import FastAPI

from .model import SegModel

# One Mask R-CNN inference at a time (about 2 GB resident, all cores), 8 requests allowed to
# wait, uploads up to 40 MiB.
LIMITS = Limits(max_concurrency=1, max_waiting=8, max_image_bytes=40 * 1024 * 1024)


def build_app(model: Model | None = None) -> FastAPI:
    return create_app(SegModel() if model is None else model, LIMITS)
