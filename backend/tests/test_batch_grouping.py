"""Several requests can belong to one batch: the Batch page sends one cheque per request, so a slow server (the Render
Free plan has a tenth of a CPU) never holds one request for minutes, and the results can appear one by one."""
import io
import re

import numpy as np

from conftest import png_bytes

IMAGE = png_bytes(np.full((300, 600, 3), 250, np.uint8))


def _batch(client, headers, count=1, **form):
    files = [(io.BytesIO(IMAGE), f"c{i}.png") for i in range(count)]
    return client.post("/api/cheques/batch", headers=headers, data={"files": files, **form}, content_type="multipart/form-data")


def test_requests_that_name_the_same_batch_form_one_batch(client, operator):
    first = _batch(client, operator)
    assert first.status_code == 201, first.json
    batch_id = first.json["data"]["batch_id"]
    assert re.fullmatch(r"[0-9a-f]{12}", batch_id)
    second = _batch(client, operator, batch_id=batch_id)
    assert second.status_code == 201 and second.json["data"]["batch_id"] == batch_id
    listed = client.get(f"/api/cheques?batch_id={batch_id}", headers=operator).json
    assert listed["meta"]["total"] == 2


def test_without_a_batch_id_every_request_gets_its_own(client, operator):
    a = _batch(client, operator).json["data"]["batch_id"]
    b = _batch(client, operator, batch_id="").json["data"]["batch_id"]          # blank means "not given"
    assert a != b


def test_a_chosen_batch_id_must_have_the_shape_the_server_makes(client, operator):
    for bad in ("x", "../../etc", "0123456789ab\n", "AAAAAAAAAAAA", "0123456789abcdef", "'; DROP TABLE cheques;--"):
        r = _batch(client, operator, batch_id=bad)
        assert r.status_code == 400 and r.json["error"]["code"] == "validation_error", bad
    assert client.get("/api/cheques", headers=operator).json["meta"]["total"] == 0          # nothing was stored for a refused id
