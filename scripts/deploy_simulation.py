#!/usr/bin/env python3
"""Offline rehearsal of a container deployment (Docker image + Render blueprint) on this machine.

    sudo python3 scripts/deploy_simulation.py                      # profile "render": Dockerfile ENV + render.yaml values
    sudo python3 scripts/deploy_simulation.py --profile docker      # Dockerfile ENV only (the key lives in the data folder)
    sudo python3 scripts/deploy_simulation.py --report out.json --keep

It cannot build the image or talk to Render. What it does instead is run the REAL start-up path of the image
(`backend/docker_entrypoint.py`, then `run.py --prod`) the way a container runtime does, and then do to it what a
platform does to a service:

    first boot on a root-owned, empty disk  ->  smoke test (backend/tests/smoke_live.py) and a fingerprint of the data
    -> SIGTERM (a redeploy)  -> new "container" on the same disk  -> fingerprint must be identical
    -> SIGKILL in the middle of uploads (out-of-memory kill, grace period over)  -> restart  -> fingerprint, DB check
    -> a new release that ships different models / reports  -> shipped files refreshed, retrained ones kept
    -> backup while running, verify, restore into an empty folder, start from the restored copy
    -> control run on an EMPTY disk (the persistence check must FAIL there)
    -> hostile volume (a symbolic link planted where the models folder should be)

Container-like conditions that are reproduced: the server is PID 1 of its own PID namespace (`unshare --pid`), starts as
root on a root-owned disk, is handed the disk and drops to an unprivileged user (`nobody`), and runs from a code tree
that user cannot write to. With root and `unshare` missing, the checks that depend on them are skipped and said so.

What it does NOT show: Docker image build, Linux distribution differences (python:3.12-slim, Tesseract version), cgroup
memory limits (peak memory is measured, not enforced), the network, TLS, Render's disk driver and snapshots, Waitress
(if it is not installed here the Flask server is used - the report says which). See reports/final/DEPLOYMENT_VERIFICATION.md.

The simulation uses a throw-away folder under /tmp and synthetic data only. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[dict] = []
METRICS: dict = {}


# ------------------------------------------------------------------ reporting
def check(name: str, passed: bool, detail: str = "") -> bool:
    detail = detail if len(detail) <= 300 else "..." + detail[-300:]
    RESULTS.append({"name": name, "passed": bool(passed), "detail": detail})
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""), flush=True)
    return bool(passed)


def note(text: str) -> None:
    print(f"  note  {text}", flush=True)


def section(title: str) -> None:
    print(f"\n== {title}", flush=True)


# ------------------------------------------------------------------ configuration taken from the repository files
def dockerfile_env(path: Path) -> dict[str, str]:
    """The ENV lines of the final stage of the Dockerfile."""
    text = path.read_text()
    final = text[text.rindex("FROM python"):]
    env: dict[str, str] = {}
    for block in re.findall(r"^ENV\s+((?:.*\\\n)*.*)$", final, flags=re.M):
        for token in shlex.split(block.replace("\\\n", " ")):
            if "=" in token:
                k, v = token.split("=", 1)
                env[k] = v
    return env


def blueprint_env(path: Path) -> list[dict]:
    """The envVars of the first service of render.yaml: [{key, value | generate | prompt}]."""
    out, current, in_env = [], None, False
    for line in path.read_text().splitlines():
        if re.match(r"^\s+envVars:\s*$", line):
            in_env = True
            continue
        if in_env and re.match(r"^\s+disk:\s*$", line):
            break
        if not in_env:
            continue
        m = re.match(r"^\s+- key:\s*(\S+)", line)
        if m:
            current = {"key": m.group(1)}
            out.append(current)
            continue
        if current is not None:
            v = re.match(r"^\s+value:\s*(.*?)\s*(?:#.*)?$", line)
            if v:
                current["value"] = v.group(1).strip().strip('"')
            if re.match(r"^\s+generateValue:\s*true", line):
                current["generate"] = True
            if re.match(r"^\s+sync:\s*false", line):
                current["prompt"] = True
    return out


def disk_mount(path: Path) -> str:
    m = re.search(r"mountPath:\s*(\S+)", path.read_text())
    return m.group(1) if m else "/data"


# ------------------------------------------------------------------ the simulated machine
class Sim:
    def __init__(self, profile: str, keep: bool, overrides: dict[str, str] | None = None):
        self.profile, self.keep = profile, keep
        self.overrides = overrides or {}
        self.is_root = os.geteuid() == 0
        self.namespaces = self.is_root and self._can_unshare()
        self.user = "nobody"
        self.pw = pwd.getpwnam(self.user) if self.is_root else None
        self.root = Path(tempfile.mkdtemp(prefix="cv-sim-", dir="/tmp"))
        os.chmod(self.root, 0o755)                                   # the unprivileged user must be able to reach the code
        self.app, self.logs, self.work = self.root / "app", self.root / "logs", self.root / "work"
        for d in (self.logs, self.work):
            d.mkdir()
        self.password = secrets.token_urlsafe(18)
        self.secret_key = secrets.token_hex(32)                      # what Render's generateValue would provide
        self.docker_env = dockerfile_env(ROOT / "Dockerfile")
        self.blueprint = blueprint_env(ROOT / "render.yaml")
        self.mount = disk_mount(ROOT / "render.yaml")
        self.containers: list[Container] = []
        self.install_image()

    @staticmethod
    def _can_unshare() -> bool:
        if not shutil.which("unshare") or not shutil.which("pgrep"):
            return False
        try:
            return subprocess.run(["unshare", "--pid", "--fork", "--mount-proc", "true"], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def install_image(self) -> None:
        """Lay the files out as the Dockerfile does: root-owned, readable by everybody, writable by nobody but root."""
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "instance", ".env", "*.pyc")
        shutil.copytree(ROOT / "backend", self.app / "backend", ignore=ignore)
        shutil.copytree(ROOT / "reports", self.app / "reports", ignore=ignore)
        dist = ROOT / "Frontend" / "dist"
        if dist.is_dir():
            shutil.copytree(dist, self.app / "Frontend" / "dist")
        else:
            note("Frontend/dist is not built here: the web interface is not part of this rehearsal")
        for base, dirs, files in os.walk(self.app):
            for name in dirs:
                os.chmod(Path(base) / name, 0o755)
            for name in files:
                os.chmod(Path(base) / name, 0o644)
        if self.is_root:
            for base, dirs, files in os.walk(self.app):
                for name in [base] + [str(Path(base) / n) for n in dirs + files]:
                    os.lchown(name, 0, 0)

    def new_disk(self, name: str) -> Path:
        """An empty, root-owned folder: what a freshly attached hosted disk looks like."""
        d = self.root / name
        d.mkdir()
        os.chmod(d, 0o755)
        return d

    def env(self, data: Path, port: int) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HOME": "/root"}

        def mapped(value: str) -> str:
            return str(data) + value[len(self.mount):] if value.startswith(self.mount) else value
        env.update({k: mapped(v) for k, v in self.docker_env.items()})
        if self.profile == "render":
            for item in self.blueprint:
                if "value" in item:
                    env[item["key"]] = mapped(item["value"])
                elif item.get("generate"):
                    env[item["key"]] = self.secret_key
                elif item.get("prompt"):
                    env[item["key"]] = self.password
        env.setdefault("CV_ADMIN_PASSWORD", self.password)
        env.update(PORT=str(port), CV_DATA_ROOT=str(data), CV_RUN_AS=self.user)
        env.update(self.overrides)
        return env

    def run_as_app(self, argv: list[str], **kw) -> subprocess.CompletedProcess:
        """Run a command as the unprivileged app user (like a shell opened inside the container as `app`)."""
        extra = {}
        if self.is_root:
            extra = dict(user=self.pw.pw_uid, group=self.pw.pw_gid, extra_groups=[])
        return subprocess.run(argv, capture_output=True, text=True, **extra, **kw)

    def start(self, label: str, data: Path, port: int, app_dir: Path | None = None) -> "Container":
        c = Container(self, label, data, port, app_dir or self.app)
        self.containers.append(c)
        return c

    def cleanup(self) -> None:
        for c in self.containers:
            c.kill()
        if self.keep:
            print(f"\nkept {self.root}")
        else:
            subprocess.run(["rm", "-rf", "--one-file-system", str(self.root)], check=False)


def free_port(prefer: int | None = None) -> int:
    for candidate in ([prefer] if prefer else []) + [0]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("no free port")


class Container:
    """The image's start-up command, running as PID 1 of its own PID namespace when that is possible."""

    def __init__(self, sim: Sim, label: str, data: Path, port: int, app_dir: Path):
        self.sim, self.label, self.data, self.port = sim, label, data, port
        self.base = f"http://127.0.0.1:{port}"
        self.log_path = sim.logs / f"{label}.log"
        self.log = open(self.log_path, "wb")
        cmd = [sys.executable, "docker_entrypoint.py"]
        if sim.namespaces:
            cmd = ["unshare", "--pid", "--fork", "--kill-child", "--mount-proc", *cmd]
        self.started = time.time()
        self.proc = subprocess.Popen(cmd, cwd=app_dir / "backend", env=sim.env(data, port), stdout=self.log, stderr=subprocess.STDOUT)
        self.inner = self._find_inner()
        self.samples: list[tuple[int, int, int]] = []
        self._sampling = threading.Event()
        self._sampler: threading.Thread | None = None

    def _find_inner(self) -> int:
        if not self.sim.namespaces:
            return self.proc.pid
        end = time.time() + 15
        while time.time() < end:
            out = subprocess.run(["pgrep", "-P", str(self.proc.pid)], capture_output=True, text=True).stdout.split()
            if out:
                return int(out[0])
            time.sleep(0.05)
        raise RuntimeError("could not find the process inside the namespace")

    # --- state
    def alive(self) -> bool:
        return self.proc.poll() is None

    def log_text(self) -> str:
        self.log.flush()
        return self.log_path.read_text(errors="replace")

    def wait_healthy(self, timeout: float = 120.0) -> float | None:
        end = time.time() + timeout
        while time.time() < end:
            if not self.alive():
                return None
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        return time.time() - self.started
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        return None

    def status_field(self, field: str) -> str:
        for line in Path(f"/proc/{self.inner}/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return line.split(":", 1)[1].strip()
        return ""

    def _ns_pids(self) -> list[int]:
        try:
            ns = os.readlink(f"/proc/{self.inner}/ns/pid")
        except OSError:
            return []
        pids = []
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    if os.readlink(f"/proc/{entry}/ns/pid") == ns:
                        pids.append(int(entry))
                except OSError:
                    pass
        return pids

    def _rss_breakdown_kb(self) -> tuple[int, int, int]:
        """(total resident kB of every process of the container, the server's own, number of other processes)."""
        total, server, others = 0, 0, 0
        for pid in self._ns_pids() if self.sim.namespaces else [self.inner]:
            try:
                for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        total += kb
                        if pid == self.inner:
                            server = kb
                        else:
                            others += 1
            except OSError:
                pass
        return total, server, others

    def sample_memory(self) -> None:
        """Sum of the resident memory of every process of the container (server + OCR helpers), every 100 ms."""
        self._sampling.set()

        def loop():
            while self._sampling.is_set() and self.alive():
                self.samples.append(self._rss_breakdown_kb())
                time.sleep(0.1)
        self._sampler = threading.Thread(target=loop, daemon=True)
        self._sampler.start()

    def stop_sampling(self) -> dict:
        self._sampling.clear()
        if self._sampler:
            self._sampler.join(3)
        hwm = self.status_field("VmHWM")
        top = max(self.samples, default=(0, 0, 0))
        return {"peak_total_mb": round(top[0] / 1024, 1), "server_at_peak_mb": round(top[1] / 1024, 1),
                "other_processes_at_peak": top[2], "server_peak_mb": round(int(hwm.split()[0]) / 1024, 1) if hwm else None,
                "samples": len(self.samples)}

    # --- ending it
    def sigterm(self, timeout: float = 40.0) -> tuple[float, int | None]:
        t0 = time.time()
        os.kill(self.inner, signal.SIGTERM)
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return time.time() - t0, None
        return time.time() - t0, code

    def kill(self) -> None:
        if self.alive():
            try:
                os.kill(self.inner, signal.SIGKILL)
            except OSError:
                pass
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc.wait(30)
        if not self.log.closed:
            self.log.close()


# ------------------------------------------------------------------ small HTTP helpers (standard library)
def http(method: str, url: str, token: str | None = None, body=None, files=None, timeout: float = 60.0):
    headers, data = {}, None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if files:
        boundary = uuid.uuid4().hex
        parts = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{n}"\r\n'
                         f'Content-Type: image/png\r\n\r\n'.encode() + c + b"\r\n" for k, (n, c) in files.items())
        data = parts + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw


def login(c: Container) -> str:
    status, body = http("POST", c.base + "/api/auth/login", body={"username": "admin", "password": c.sim.password})
    if status != 200:
        raise RuntimeError(f"admin sign-in failed: HTTP {status}")
    return body["data"]["token"]


def tiny_png(seed: int) -> bytes:
    """A small valid PNG without any imaging library: 320x120 grey with a few dark bars."""
    w, h = 320, 120
    rows = []
    for y in range(h):
        row = bytearray([245] * w)
        if 40 + (seed % 5) <= y < 46 + (seed % 5):
            for x in range(30, 290):
                row[x] = 30
        rows.append(b"\x00" + bytes(row))
    raw = zlib.compress(b"".join(rows))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def big_png(side: int) -> bytes:
    """A side x side grey PNG with a few bars. Compresses to a few hundred KB, so it passes the upload-size limit while
    being as many pixels as the pixel limit allows - the case that costs the most memory."""
    row_plain = b"\x00" + bytes([245]) * side
    bar = bytearray(row_plain)
    for x in range(side // 10, side - side // 10):
        bar[1 + x] = 30
    rows = [bytes(bar) if (y * 12 // side) % 2 == 1 and (y % 40) < 6 else row_plain for y in range(side)]
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def run_tool(sim: Sim, script: str, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    """Run one of the image's own test tools from inside the image's tree, like `docker exec` would."""
    return subprocess.run([sys.executable, str(sim.app / "backend" / "tests" / script), *args], capture_output=True, text=True, timeout=timeout)


def persistence(sim: Sim, c: Container, command: str, state: Path, *extra: str):
    args = [command, c.base, sim.password]
    if command == "verify":
        args += ["--state", str(state), *extra]
    r = run_tool(sim, "persistence_check.py", *args)
    if command == "snapshot":
        state.write_text(r.stdout)
        state.chmod(0o600)
    return r


def db_integrity(sim: Sim, data: Path) -> str:
    """PRAGMA integrity_check, run as the app user so that SQLite never creates root-owned files in the data folder."""
    code = ("import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(';'.join(r[0] for r in c.execute('PRAGMA integrity_check')))")
    db = data / "instance" / "chequeverify.db"
    r = sim.run_as_app([sys.executable, "-c", code, str(db)])
    return (r.stdout or r.stderr).strip()


def tmp_files(data: Path) -> list[str]:
    return sorted(str(p.relative_to(data)) for p in data.rglob(".tmp-*"))


def owned_by_app(sim: Sim, data: Path) -> bool:
    if not sim.is_root:
        return True
    return all(os.lstat(p).st_uid == sim.pw.pw_uid for p in [data, *data.rglob("*")])


def shipped_count(sim: Sim) -> int:
    return sum(1 for d in (sim.app / "backend" / "models_store", sim.app / "reports" / "final") for p in d.iterdir() if p.is_file())


# ------------------------------------------------------------------ the scenarios
def scenario_first_boot(sim: Sim, data: Path, state: Path, quick: bool = False) -> Container | None:
    section("1. first boot on a root-owned empty disk")
    port = free_port(10000 if sim.profile == "render" else 8000)
    METRICS["port"] = port
    check("the disk starts out root-owned (like a freshly attached hosted disk)", os.stat(data).st_uid == 0 or not sim.is_root)
    c = sim.start("boot1", data, port)
    healthy = c.wait_healthy()
    if not check("the server comes up and answers /api/health", healthy is not None, f"{healthy:.1f} s" if healthy else c.log_text()[-600:]):
        return None
    METRICS["first_boot_seconds"] = round(healthy, 1)
    if sim.namespaces:
        nspid = c.status_field("NSpid").split()
        check("the server is PID 1 of its namespace (as in a container)", nspid[-1] == "1", f"NSpid {' '.join(nspid)}")
    if sim.is_root:
        uid = int(c.status_field("Uid").split()[0])
        check("the server does not run as root", uid == sim.pw.pw_uid, f"uid {uid}")
        check("the disk now belongs to the app user", owned_by_app(sim, data))
        probe = sim.run_as_app([sys.executable, "-c", "import sys;open(sys.argv[1],'w')", str(sim.app / "backend" / "x.py")])
        check("the app user cannot modify the application code", probe.returncode != 0)
    db = data / "instance" / "chequeverify.db"
    check("the database is private (mode 600)", oct(db.stat().st_mode & 0o777) == "0o600", oct(db.stat().st_mode & 0o777))
    manifest = json.loads((data / ".seed_manifest.json").read_text())["files"] if (data / ".seed_manifest.json").is_file() else {}
    check("the shipped models and reports were copied and recorded", len(manifest) == shipped_count(sim) > 0, f"{len(manifest)} files")
    log = c.log_text()
    METRICS["web_server"] = "Flask development server (waitress not installed here)" if "waitress is not installed" in log else "waitress"
    check("start-up produced no traceback", "Traceback" not in log, METRICS["web_server"])
    if sim.profile == "render":
        status, body = http("GET", c.base + "/api/auth/config")
        offered = body.get("data", {}) if isinstance(body, dict) else {}
        check("the blueprint's settings were applied: email sign-in stays off until a mail server is configured, the password form stays on",
              status == 200 and offered.get("email_signin") is False and offered.get("password_signin") is True, str(offered))
    if quick:
        return c
    c.sample_memory()
    r = run_tool(sim, "smoke_live.py", c.base, sim.password)
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    try:
        summary = json.loads(last)
    except ValueError:
        summary = {"passed": 0, "failed": ["unreadable output"]}
    check("the live smoke test passes against this container", r.returncode == 0 and not summary.get("failed"),
          f"{summary.get('passed')} checks" + (f"; failed: {summary.get('failed')}" if summary.get("failed") else ""))
    if summary.get("failed"):
        print(r.stdout[-1500:])
    # a short burst of uploads as a memory measurement
    token = login(c)
    for i in range(12):
        http("POST", c.base + "/api/cheques", token, files={"file": (f"c{i}.png", tiny_png(i))})
    METRICS["memory"] = c.stop_sampling()
    note(f"peak memory of the whole container: {METRICS['memory']['peak_total_mb']} MB (server alone {METRICS['memory']['server_peak_mb']} MB); "
         "a Render Starter instance has 512 MB - this is measured, not enforced")
    snap = persistence(sim, c, "snapshot", state)
    check("a fingerprint of the stored data was taken", snap.returncode == 0 and state.stat().st_size > 100, snap.stderr[-300:])
    return c


def scenario_memory(sim: Sim, c: Container) -> None:
    section("1b. memory with the largest image the service accepts (a Render Starter instance has 512 MB)")
    token = login(c)
    limit = int(sim.env(c.data, c.port).get("CV_MAX_IMAGE_PIXELS", "25000000"))
    side = int(limit ** 0.5) - 2
    png = big_png(side)
    METRICS["largest_image"] = {"pixels": side * side, "file_kb": round(len(png) / 1024)}
    note(f"test image: {side}x{side} = {side * side / 1e6:.1f} megapixels, {len(png) // 1024} KB (pixel limit {limit / 1e6:.0f} MP, upload limit "
         f"{sim.env(c.data, c.port).get('CV_MAX_UPLOAD_MB', '10')} MB)")
    for label, count in (("one at a time", 1), ("four at once", 4), ("eight at once", 8)):
        c.samples.clear()
        c.sample_memory()
        statuses = []

        def post():
            statuses.append(http("POST", c.base + "/api/cheques", token, files={"file": ("big.png", png)}, timeout=180)[0])
        threads = [threading.Thread(target=post) for _ in range(count)]
        started = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(200)
        elapsed = time.time() - started
        peak = c.stop_sampling()
        METRICS.setdefault("memory_largest_image", {})[label] = {**peak, "statuses": sorted(statuses), "seconds": round(elapsed, 1)}
        note(f"{count} image(s), {label}: {elapsed:.1f} s in total")
        check(f"the largest accepted image is processed, {label} (requests beyond the free slots wait their turn)",
              all(s in (200, 201) for s in statuses) and len(statuses) == count,
              f"HTTP {sorted(statuses)}; peak {peak['peak_total_mb']} MB (server {peak['server_at_peak_mb']} MB + {peak['other_processes_at_peak']} helper processes)")
        check(f"the container stays under 512 MB, {label}", peak["peak_total_mb"] < 512 and c.alive(), f"{peak['peak_total_mb']} MB")


def scenario_redeploy(sim: Sim, c: Container, data: Path, state: Path) -> Container | None:
    section("2. redeploy: SIGTERM, then a new container on the same disk")
    seconds, code = c.sigterm()
    METRICS["stop_seconds"] = round(seconds, 2)
    check("the server stops promptly on SIGTERM with exit code 0", code == 0 and seconds < 10, f"{seconds:.2f} s, exit {code}")
    check("no temporary or half-written files were left", tmp_files(data) == [], ", ".join(tmp_files(data)))
    check("the database passes SQLite's integrity check", db_integrity(sim, data) == "ok", db_integrity(sim, data))
    c2 = sim.start("boot2", data, c.port)
    healthy = c2.wait_healthy()
    if not check("the new container comes up on the same disk", healthy is not None, f"{healthy:.1f} s" if healthy else c2.log_text()[-600:]):
        return None
    METRICS["restart_seconds"] = round(healthy, 1)
    seed_lines = [ln for ln in c2.log_text().splitlines() if ln.startswith("seed:")]
    check("the shipped files were recognised as already present (nothing re-copied, nothing replaced)",
          len(seed_lines) == 1 and re.fullmatch(r"seed: \d+ current", seed_lines[0]) is not None, "; ".join(seed_lines) or "no seed line")
    r = persistence(sim, c2, "verify", state)
    check("everything stored before the redeploy is still there and identical", r.returncode == 0, (r.stdout + r.stderr).strip()[-400:])
    return c2


def scenario_hard_kill(sim: Sim, c: Container, data: Path, state: Path) -> Container | None:
    section("3. SIGKILL in the middle of uploads (out-of-memory kill / grace period over)")
    token = login(c)
    done, stop = [], threading.Event()

    def uploader(n):
        i = 0
        while not stop.is_set():
            i += 1
            try:
                status, _ = http("POST", c.base + "/api/cheques", token, files={"file": (f"k{n}-{i}.png", tiny_png(n * 100 + i))}, timeout=20)
                if status in (200, 201):
                    done.append(1)
            except OSError:
                return
    threads = [threading.Thread(target=uploader, args=(n,), daemon=True) for n in range(4)]
    for t in threads:
        t.start()
    end = time.time() + 20
    while len(done) < 6 and time.time() < end:
        time.sleep(0.05)
    before = len(done)
    c.kill()
    stop.set()
    for t in threads:
        t.join(15)
    check("the server was killed while uploads were in flight", before >= 1, f"{before} uploads had completed, {len(done)} in total")
    leftovers = tmp_files(data)
    note(f"temporary files left by the kill: {len(leftovers)}")
    c2 = sim.start("boot3", data, c.port)
    healthy = c2.wait_healthy()
    if not check("the server starts again after the kill", healthy is not None, f"{healthy:.1f} s" if healthy else c2.log_text()[-600:]):
        return None
    check("the database passes SQLite's integrity check", db_integrity(sim, data) == "ok", db_integrity(sim, data))
    r = persistence(sim, c2, "verify", state)
    check("everything stored before the kill is intact", r.returncode == 0, (r.stdout + r.stderr).strip()[-400:])
    status, body = http("GET", c2.base + "/api/cheques?per_page=1", login(c2))
    total = (body.get("meta") or {}).get("total", 0) if isinstance(body, dict) else 0
    check("uploads that finished before the kill are listed", status == 200 and total >= before, f"{total} cheques")
    note(f"temporary files still present after the restart: {len(tmp_files(data))} (a start-up sweep removes those older than a minute)")
    return c2


def scenario_new_release(sim: Sim, c: Container, data: Path, state: Path) -> Container | None:
    section("4. a new release ships different models and reports")
    c.sigterm()
    models, reports = sim.app / "backend" / "models_store", sim.app / "reports" / "final"
    fusion = models / "signature_fusion.json"
    if not fusion.is_file():
        note("no shipped signature model in this checkout: skipping the model refresh scenario")
        c2 = sim.start("boot4", data, c.port)
        return c2 if c2.wait_healthy() is not None else None
    original = fusion.read_text()
    release2 = json.loads(original)
    release2.setdefault("meta", {})["version"] = "release-2-shipped"
    fusion.write_text(json.dumps(release2))
    report = reports / "MODEL_EVALUATION.md"
    report.write_text(report.read_text() + "\nrelease 2 addendum\n")
    c2 = sim.start("boot4", data, c.port)
    healthy = c2.wait_healthy()
    check("the new release starts", healthy is not None, "" if healthy is not None else c2.log_text()[-500:])
    log = c2.log_text()
    check("untouched copies of the shipped files were refreshed", "models/signature_fusion.json updated" in log and "reports/MODEL_EVALUATION.md updated" in log)
    on_disk = json.loads((data / "models" / "signature_fusion.json").read_text())
    check("the disk now holds the new release's model", on_disk.get("meta", {}).get("version") == "release-2-shipped")
    r = persistence(sim, c2, "verify", state, "--allow", "models,reports")
    check("records, images and settings are unaffected by the release", r.returncode == 0, (r.stdout + r.stderr).strip()[-400:])
    # an administrator retrains on the server: the file now differs from every shipped version
    c2.sigterm()
    retrained = dict(release2)
    retrained["meta"] = {**release2["meta"], "version": "retrained-on-this-disk"}
    target = data / "models" / "signature_fusion.json"
    target.write_text(json.dumps(retrained))
    if sim.is_root:
        os.chown(target, sim.pw.pw_uid, sim.pw.pw_gid)
    release3 = dict(release2)
    release3["meta"] = {**release2["meta"], "version": "release-3-shipped"}
    fusion.write_text(json.dumps(release3))
    c3 = sim.start("boot5", data, c.port)
    healthy = c3.wait_healthy()
    check("the release after that starts", healthy is not None, "" if healthy is not None else c3.log_text()[-500:])
    check("a model retrained on the disk is kept, not overwritten", json.loads(target.read_text())["meta"]["version"] == "retrained-on-this-disk"
          and "models/signature_fusion.json kept: changed on this disk" in c3.log_text())
    fusion.write_text(original)
    return c3


def scenario_backup(sim: Sim, c: Container, data: Path, state: Path) -> None:
    section("5. backup while running, verify, restore, start from the restored copy")
    backups = sim.root / "backups"
    backups.mkdir()
    if sim.is_root:
        os.chown(backups, sim.pw.pw_uid, sim.pw.pw_gid)
    env = {**sim.env(data, c.port)}
    manage = [sys.executable, "manage.py"]

    def manage_run(*args):
        extra = dict(user=sim.pw.pw_uid, group=sim.pw.pw_gid, extra_groups=[]) if sim.is_root else {}
        return subprocess.run([*manage, *args], cwd=sim.app / "backend", env=env, capture_output=True, text=True, timeout=300, **extra)
    made = manage_run("backup", "--out", str(backups))
    check("a backup can be taken while the server is running", made.returncode == 0, (made.stdout + made.stderr).strip()[-300:])
    archives = sorted(backups.glob("chequeverify-backup-*.tar.gz"))
    if not archives:
        return
    archive = archives[-1]
    check("the backup file is private (mode 600)", oct(archive.stat().st_mode & 0o777) == "0o600")
    verified = manage_run("verify-backup", str(archive))
    check("the backup verifies (every file matches its recorded checksum)", verified.returncode == 0, verified.stdout.strip()[-200:])
    restored = sim.root / "restored"
    if sim.is_root:
        parent = sim.root / "restore_parent"
        parent.mkdir()
        os.chown(parent, sim.pw.pw_uid, sim.pw.pw_gid)
        restored = parent / "data"
    done = manage_run("restore-backup", str(archive), "--into", str(restored))
    check("the backup restores into an empty folder", done.returncode == 0, (done.stdout + done.stderr).strip()[-300:])
    c.sigterm()
    c2 = sim.start("restored", restored, c.port)
    healthy = c2.wait_healthy()
    check("the service starts from the restored copy", healthy is not None, f"{healthy:.1f} s" if healthy is not None else c2.log_text()[-500:])
    if healthy is not None:
        r = persistence(sim, c2, "verify", state, "--allow", "models,reports")
        text = (r.stdout + r.stderr).strip()
        if sim.profile == "render":
            check("all records and images are present in the restored copy (the key comes from the environment, so sessions survive)", r.returncode == 0, text[-300:])
        else:
            only_token = r.returncode == 1 and all("secret key did not persist" in line for line in text.splitlines() if line.startswith("FAIL"))
            check("all records and images are present in the restored copy; the only difference is that sessions ended (the key is not in backups, by design)", only_token, text[-300:])
        c2.sigterm()


def scenario_empty_disk(sim: Sim, port: int, state: Path) -> None:
    section("6. control: the same service on an EMPTY disk must be reported as data loss")
    data = sim.new_disk("data_empty")
    c = sim.start("empty", data, port)
    if c.wait_healthy() is None:
        check("the control container starts", False, c.log_text()[-400:])
        return
    r = persistence(sim, c, "verify", state)
    text = r.stdout + r.stderr
    check("the persistence check FAILS on an empty disk (it cannot be fooled by a fresh start)", r.returncode == 1 and "FAIL" in text, text.strip().splitlines()[0] if text.strip() else "")
    c.sigterm()


def scenario_hostile_volume(sim: Sim, port: int) -> None:
    section("7. hostile volume: a symbolic link planted where the models folder should be")
    victim = sim.root / "victim"
    victim.mkdir()
    (victim / "sentinel.txt").write_text("must stay as it is")
    os.chmod(victim, 0o755)
    data = sim.new_disk("data_hostile")
    (data / "models").symlink_to(victim)
    (data / "reports").mkdir()
    (data / "reports" / "final").symlink_to(victim)
    c = sim.start("hostile", data, port)
    healthy = c.wait_healthy(timeout=40)
    before = sorted(p.name for p in victim.iterdir())
    c.kill()
    check("nothing was written through the planted links", before == ["sentinel.txt"] and (victim / "sentinel.txt").read_text() == "must stay as it is", f"{before}")
    note("the container " + ("still started" if healthy is not None else "refused to start (fail closed)") + " with the planted links")


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=("render", "docker"), default="render")
    ap.add_argument("--report", help="write the results as JSON")
    ap.add_argument("--keep", action="store_true", help="keep the throw-away folder (logs are in <folder>/logs)")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="override one environment variable of the container (repeatable)")
    ap.add_argument("--only-memory", action="store_true", help="quick mode: first boot and the memory scenario only")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        print("this rehearsal needs Linux (/proc, PID namespaces)")
        return 2
    overrides = dict(item.split("=", 1) for item in args.env if "=" in item)
    sim = Sim(args.profile, args.keep, overrides)
    if overrides:
        print("overrides: " + ", ".join(f"{k}={v}" for k, v in overrides.items()))
    print(f"profile {sim.profile}; root {sim.is_root}; PID namespaces {sim.namespaces}; folder {sim.root}")
    if not sim.is_root:
        note("not running as root: the root-owned disk, the privilege drop and the read-only code checks are skipped")
    if not sim.namespaces:
        note("PID namespaces are not available: the PID 1 / SIGTERM-as-init conditions are not reproduced")
    state = sim.work / "state.json"
    data = sim.new_disk("data")
    try:
        c = scenario_first_boot(sim, data, state, quick=args.only_memory)
        if c is not None:
            scenario_memory(sim, c)
        if args.only_memory:
            c = None
        if c is not None:
            c = scenario_redeploy(sim, c, data, state)
        if c is not None:
            c = scenario_hard_kill(sim, c, data, state)
        if c is not None:
            c = scenario_new_release(sim, c, data, state)
        if c is not None:
            scenario_backup(sim, c, data, state)
        if not args.only_memory:
            scenario_empty_disk(sim, METRICS.get("port") or free_port(), state)
            scenario_hostile_volume(sim, METRICS.get("port") or free_port())
    finally:
        sim.cleanup()
    failed = [r for r in RESULTS if not r["passed"]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    for r in failed:
        print(f"  FAILED: {r['name']}  {r['detail']}")
    if args.report:
        Path(args.report).write_text(json.dumps({"profile": sim.profile, "root": sim.is_root, "namespaces": sim.namespaces,
                                                 "metrics": METRICS, "results": RESULTS}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
