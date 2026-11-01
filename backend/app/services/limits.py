"""Bound how many images are processed at the same moment.

Decoding and analysing a cheque scan costs memory in proportion to its pixels. Measured with the largest image the
service accepts (25 megapixels): about 110 MB for the decoded pixels and roughly 250 MB per request with the OCR helper
processes, and a threaded server starts as many such requests as there are connections - four at once peaked above a
gigabyte. On a 512 MB instance that ends in an out-of-memory kill and a restart for every user.

`image_slot` makes the heavy routes take one of a few slots. A request that cannot get a slot within a short time is
answered `503 busy` with a `Retry-After` header instead of being allowed to push the process over its memory limit.

    CV_MAX_CONCURRENT_IMAGES   slots (default 2)
    CV_IMAGE_QUEUE_SECONDS     how long a request waits for a slot (default 30)

Together with CV_MAX_IMAGE_PIXELS this puts a ceiling on memory: slots x memory-per-image + the idle process.
"""
from __future__ import annotations

import functools
import threading

from flask import current_app

from ..api.common import ApiError

_create = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    app = current_app._get_current_object()
    with _create:
        sem = app.extensions.get("image_slots")
        if sem is None:
            sem = app.extensions["image_slots"] = threading.BoundedSemaphore(max(1, int(app.config.get("MAX_CONCURRENT_IMAGES", 2))))
    return sem


def image_slot(fn):
    """Decorator for a route that decodes, loads or analyses images. Put it under login_required and rate_limited, so that
    requests which would be refused anyway never wait for a slot."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        sem = _semaphore()
        wait = float(current_app.config.get("IMAGE_QUEUE_SECONDS", 30))
        if not sem.acquire(timeout=max(0.0, wait)):
            raise ApiError(503, "busy", "The server is processing other images. Try again in a few seconds.", {"retry_after": 5})
        try:
            return fn(*args, **kwargs)
        finally:
            sem.release()
    wrapper.uses_image_slot = True
    return wrapper
