"""Time the real upload -> OCR path on a folder of cheque images, in process (Flask test client).

    python3 -I scripts/time_uploads.py backend DATASET_SPLIT_DIR     # DATASET_SPLIT_DIR holds one folder per bank

Sends every image once, one at a time, then eight uploads at the same moment (two image slots, the rest wait).
Prints counts and times only - never file names or extracted values. A throwaway instance folder and a random
throwaway administrator password are used, so nothing of the real application is touched. Times depend on the
machine: the dataset report quotes a 2-CPU sandbox.
"""
import glob
import io
import os
import secrets
import statistics
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict

backend, split = sys.argv[1], sys.argv[2]
sys.path.insert(0, backend)
tmp = tempfile.mkdtemp(prefix="cv-timing-")
pw = "Tt-" + secrets.token_urlsafe(12) + "9"
os.environ.update(CV_INSTANCE_DIR=tmp, CV_ADMIN_PASSWORD=pw, CV_OCR_ENGINE="tesseract", CV_MIN_PASSWORD_LENGTH="12",
                  CV_MAX_IMAGE_PIXELS="12000000", CV_MAX_CONCURRENT_IMAGES="2", CV_SECRET_KEY=secrets.token_hex(32))
from app import create_app  # noqa: E402

app = create_app()
files = sorted(f for f in glob.glob(os.path.join(split, "*", "*")) if f.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")))
bank_of = {f: os.path.basename(os.path.dirname(f)) for f in files}
blobs = {f: open(f, "rb").read() for f in files}


def login(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": pw})
    assert r.status_code == 200, r.status_code
    return {"Authorization": "Bearer " + r.get_json()["data"]["token"]}


def send(client, headers, path):
    t0 = time.perf_counter()
    r = client.post("/api/cheques", headers=headers, data={"file": (io.BytesIO(blobs[path]), "cheque" + os.path.splitext(path)[1])},
                    content_type="multipart/form-data")
    dt = time.perf_counter() - t0
    body = r.get_json(silent=True) or {}
    final = ((body.get("data") or {}).get("final")) or {}
    filled = sum(1 for k in ("account_number", "ifsc", "amount_figures", "amount_words", "payee_name", "date", "micr_line") if final.get(k) not in (None, "", []))
    return r.status_code, dt, filled


def stats(xs):
    xs = sorted(xs)
    return f"median {statistics.median(xs):.2f} s, p90 {xs[int(0.9 * (len(xs) - 1))]:.2f} s, max {xs[-1]:.2f} s"


c = app.test_client()
h = login(c)
print(f"images: {len(files)} in {len(set(bank_of.values()))} bank folders {sorted(set(bank_of.values()))}")

# 1. one at a time (the first request also loads the models)
rows = [(p,) + send(c, h, p) for p in files]
first = rows[0][2]
warm = [r[2] for r in rows[1:]]
print(f"\n1) sequential, one request at a time: HTTP {dict(Counter(r[1] for r in rows))}")
print(f"   first request (loads models): {first:.2f} s")
print(f"   the other {len(warm)}: {stats(warm)}; total for all {len(rows)} = {sum(r[2] for r in rows):.1f} s")
per_bank = defaultdict(list)
for p, st, dt, filled in rows[1:]:
    per_bank[bank_of[p]].append(dt)
for b, xs in sorted(per_bank.items()):
    print(f"   {b:<10} {len(xs)} images, median {statistics.median(xs):.2f} s")

# 2. eight browsers at once (two image slots, the rest wait)
n = 8
picked = (files * 2)[:n]
results, lock = [], threading.Lock()


def worker(path):
    cl = app.test_client()
    st, dt, _ = send(cl, h, path)
    with lock:
        results.append((st, dt))


threads = [threading.Thread(target=worker, args=(p,)) for p in picked]
t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join(300)
wall = time.perf_counter() - t0
print(f"\n2) {n} uploads at the same moment: HTTP {dict(Counter(r[0] for r in results))}, all done after {wall:.1f} s")
print(f"   per-request waiting time including the queue: {stats([r[1] for r in results])}")
print(f"\nthroughput: about {len(warm) / sum(warm):.2f} cheques per second when sent one by one on this machine ({os.cpu_count()} CPUs)")
