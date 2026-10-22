"""The cls service as it runs in production: the shared skeleton, our model, our limits."""

from decimer_service import Limits, Model, create_app
from fastapi import FastAPI

from .model import ClsModel

# One Keras model, called from one request at a time (about 1 GB resident), 16 requests allowed to
# wait, uploads up to 20 MiB.
LIMITS = Limits(max_concurrency=1, max_waiting=16, max_image_bytes=20 * 1024 * 1024)


def build_app(model: Model | None = None) -> FastAPI:
    return create_app(ClsModel() if model is None else model, LIMITS)
