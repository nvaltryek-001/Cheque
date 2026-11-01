"""The cap on images processed at the same moment (app/services/limits.py) - what keeps a 512 MB instance alive."""
import ast
import threading
import time
from pathlib import Path

import pytest

from app.services import cheques, limits
from conftest import png_bytes, synthetic_signature, upload

API = Path(__file__).resolve().parent.parent / "app" / "api"
# functions that decode, load or analyse an image: a route that calls one of them holds a lot of memory while it runs
HEAVY = {"read_upload", "load_image", "decode_image", "reprocess", "run_pipeline", "add_specimen", "process_or_fail"}


def _routes():
    for path in sorted(API.glob("*_routes.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and any(ast.unparse(d).startswith("bp.") for d in node.decorator_list):
                yield path.name, node


def test_every_route_that_processes_images_takes_a_slot():
    """A new route that decodes images but forgets the decorator would silently reopen the memory problem."""
    heavy_routes = []
    for fname, node in _routes():
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(node) if isinstance(n, ast.Call)}
        if calls & HEAVY:
            heavy_routes.append(f"{fname}:{node.name}")
            decorators = [ast.unparse(d) for d in node.decorator_list]
            assert "image_slot" in decorators, f"{fname}:{node.name} processes images but has no @image_slot"
            order = [d.split("(")[0] for d in decorators]
            if "login_required" in order:
                assert order.index("login_required") < order.index("image_slot")      # unauthenticated callers never take a slot
            if "rate_limited" in order:
                assert order.index("rate_limited") < order.index("image_slot")        # nor do requests that are refused anyway
    assert len(heavy_routes) >= 10, heavy_routes                                       # the inventory itself is not empty


def test_defaults_and_the_render_blueprint_keep_the_worst_case_inside_512_mb():
    from app.config import Config
    assert Config.MAX_CONCURRENT_IMAGES == 2 and Config.IMAGE_QUEUE_SECONDS == 30
    for name in ("render.yaml", "render.paid.yaml"):                                     # Free and paid plan both have 512 MB
        blueprint = (Path(__file__).resolve().parents[2] / name).read_text()
        pixels = int(__import__("re").search(r"CV_MAX_IMAGE_PIXELS\s*\n\s*value:\s*\"?(\d+)", blueprint).group(1))
        slots = int(__import__("re").search(r"CV_MAX_CONCURRENT_IMAGES\s*\n\s*value:\s*\"?(\d+)", blueprint).group(1))
        assert pixels <= 12_500_000 and slots <= 2, name                                # 600 dpi cheque = 10.7 MP; measured in the rehearsal


def test_at_most_the_configured_number_of_images_run_at_once(app):
    app.config["MAX_CONCURRENT_IMAGES"] = 2
    state, results, lock = {"now": 0, "peak": 0}, [], threading.Lock()

    @limits.image_slot
    def work():
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        return "done"

    def run():
        with app.app_context():
            results.append(work())
    threads = [threading.Thread(target=run) for _ in range(8)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == ["done"] * 8                                                      # everybody is served, in turns
    assert state["peak"] == 2                                                           # never more than the cap, and the cap is used
    assert time.time() - start >= 0.55                                                  # 8 jobs / 2 slots x 0.15 s: they really did queue


def test_a_failing_request_gives_its_slot_back(app):
    app.config["MAX_CONCURRENT_IMAGES"] = 1

    @limits.image_slot
    def boom():
        raise RuntimeError("the pipeline failed")

    @limits.image_slot
    def fine():
        return "ok"
    with app.app_context():
        for _ in range(3):
            with pytest.raises(RuntimeError):
                boom()
        assert fine() == "ok"


def test_a_request_that_waits_too_long_gets_503_busy_with_retry_after(app, client, operator, monkeypatch):
    app.config["MAX_CONCURRENT_IMAGES"] = 1
    app.config["IMAGE_QUEUE_SECONDS"] = 0
    entered, release = threading.Event(), threading.Event()
    real = cheques.create_from_upload

    def slow(*args, **kwargs):
        entered.set()
        release.wait(20)
        return real(*args, **kwargs)
    monkeypatch.setattr(cheques, "create_from_upload", slow)
    first = {}

    def first_request():
        first["r"] = app.test_client().post("/api/cheques", headers=operator, data=upload(png_bytes(synthetic_signature(1)), "a.png"),
                                            content_type="multipart/form-data")
    t = threading.Thread(target=first_request)
    t.start()
    assert entered.wait(20)
    r = client.post("/api/cheques", headers=operator, data=upload(png_bytes(synthetic_signature(2)), "b.png"),
                    content_type="multipart/form-data")
    release.set()
    t.join(30)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "busy"
    assert r.headers["Retry-After"] == "5"
    assert first["r"].status_code in (200, 201)                                         # the request that held the slot finished normally
    again = client.post("/api/cheques", headers=operator, data=upload(png_bytes(synthetic_signature(3)), "c.png"),
                        content_type="multipart/form-data")
    assert again.status_code in (200, 201)                                              # and the slot is free again


def test_unauthenticated_callers_never_wait_for_a_slot(app, client):
    app.config["MAX_CONCURRENT_IMAGES"] = 1
    with app.app_context():
        sem = limits._semaphore()
        assert sem.acquire(timeout=1)                                                   # every slot is taken
        try:
            t0 = time.time()
            r = client.post("/api/cheques", data=upload(png_bytes(synthetic_signature(1)), "a.png"), content_type="multipart/form-data")
            assert r.status_code == 401 and time.time() - t0 < 5
        finally:
            sem.release()
