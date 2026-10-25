"""The ocsr service as it runs in production: the shared skeleton, our model, our limits."""

from decimer_service import Limits, Model, create_app
from fastapi import FastAPI

from .model import OcsrModel

# One transformer decode at a time (2.6 to 3.1 GB resident, the printed-structure model only:
# decimer 2.8.1 does not load the hand-drawn one; TensorFlow decides the threads), 4 requests
# allowed to wait, uploads up to 20 MiB. A decode takes 2 to 5 s for a structure but 38 to 55 s for
# anything that is not one (README "The token limit"), so a long queue is a long wait for the last
# in line: 4 keeps a queue of structures well inside the worker's 120 s timeout, and the bodies
# read before the queue decision are at most 5 x 20 MiB.
#
# ready_cache_seconds=0: the skeleton refreshes its readiness cache on every 200 /infer, so with
# the default 30 s /readyz would not reach `OcsrModel.selftest()` while requests succeed, and a
# model that answers wrongly but completely (wrong weights still decode) would stay ready under
# traffic. With 0 every /readyz asks the model, which keeps its own policy (30 s window for a
# passed selftest or a completed request, a real pass at least every 5 minutes; see model.py).
LIMITS = Limits(
    max_concurrency=1, max_waiting=4, max_image_bytes=20 * 1024 * 1024, ready_cache_seconds=0
)


def build_app(model: Model | None = None) -> FastAPI:
    return create_app(OcsrModel() if model is None else model, LIMITS)
