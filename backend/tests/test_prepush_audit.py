"""scripts/prepush_audit.py – the audit that must stop private data from being pushed.
Every test builds a throw-away git repository; nothing here touches the real one."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepush_audit.py"
pytestmark = pytest.mark.skipif(not SCRIPT.is_file() or shutil.which("git") is None,
                                reason="audit script or git not available")


def _load():
    spec = importlib.util.spec_from_file_location("prepush_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prepush_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


audit = _load()


# Fake secrets are assembled at run time so that no source line of this file looks like a real secret
# (this audit, GitHub's push protection and other scanners would otherwise flag the test file itself).
FAKE_PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"
FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_URL = "postgres://admin:" + "s3cretpass" + "@db.internal/app"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("# demo\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def commit(repo, files: dict, message="change", remove=()):
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    for name in remove:
        git(repo, "rm", "-q", "-f", name)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def run(repo, capsys, *extra, base="HEAD~1"):
    code = audit.main(["--repo", str(repo), "--base", base, "--json", *extra])
    out = capsys.readouterr().out
    return code, json.loads(out)


def rules(res, severity=None):
    return {f["rule"] for f in res["findings"] if severity is None or f["severity"] == severity}


# ---------------------------------------------------------------- clean and blocked changes
def test_ordinary_code_passes(repo, capsys):
    commit(repo, {"src/app.py": "def add(a, b):\n    return a + b\n", "docs/notes.md": "hello\n"})
    code, res = run(repo, capsys)
    assert code == 0 and not [f for f in res["findings"] if f["severity"] == "ERROR"]
    assert set(res["final_files"]) == {"src/app.py", "docs/notes.md"}


@pytest.mark.parametrize("path,content,rule", [
    (".env", "A=1\n", "env-file"),
    ("backend/.env.production", "A=1\n", "env-file"),
    ("data/chequeverify.db", b"SQLite format 3\0", "database"),
    ("backend/instance/storage/x.txt", "x", "instance-data"),
    ("dataset/train/axis/1.jpeg", b"\xff\xd8\xff", "dataset-content"),
    ("signature/signature_dataset/p1.png", b"\x89PNG", "dataset-content"),
    ("docs/cheque.jpg", b"\xff\xd8\xff\xe0", "image"),
    ("docs/report.pdf", b"%PDF-1.4", "document-or-archive"),
    ("ocr/labels_private.csv", "a,b\n", "labels-or-provenance"),
    ("keys/server.pem", "x", "key-material"),
    ("model/weights.pkl", b"\x80\x04", "model-binary"),
    ("Frontend/node_modules/x/index.js", "x", "build-output"),
])
def test_forbidden_paths_block(repo, capsys, path, content, rule):
    commit(repo, {path: content})
    code, res = run(repo, capsys)
    assert code == 1 and rule in rules(res, "ERROR"), res["findings"]


def test_placeholders_and_the_bundled_model_are_allowed(repo, capsys):
    commit(repo, {"backend/.env.example": "CV_SECRET_KEY=change-me\n", "dataset/PUT_OCR_DATASET_HERE.txt": "put data here\n",
                  "backend/models_store/micr_e13b.npz": b"PK\x03\x04\0\0"})
    code, res = run(repo, capsys)
    assert code == 0, res["findings"]


@pytest.mark.parametrize("line,rule", [
    (FAKE_PRIVATE_KEY, "private-key"),
    ("aws = '" + FAKE_AWS_KEY + "'", "aws-access-key"),
    ("t = 'ghp_" + "a" * 36 + "'", "github-token"),
    ("url = '" + FAKE_URL + "'", "password-in-url"),
    ("db_password = 'Tr0ub4dor-and-more'", "secret-assignment"),
    ("export API_TOKEN=abcdef123456", "secret-assignment"),
])
def test_secrets_in_added_lines_block(repo, capsys, line, rule):
    commit(repo, {"src/settings.py": line + "\n"})
    code, res = run(repo, capsys)
    assert code == 1 and rule in rules(res, "ERROR"), res["findings"]


def test_placeholders_variables_and_marker_are_not_secrets(repo, capsys):
    commit(repo, {"src/a.py": "password = hash_password(request.json['password'])\nsecret = os.environ['CV_SECRET_KEY']\n"
                              "token = 'change-me-please'\nTOKEN_KEY = 'cv_token'\n"
                              "pw = 'Real-looking-pw-1'  # audit:allow\n"})
    code, res = run(repo, capsys)
    assert code == 0, res["findings"]


def test_example_credentials_in_tests_and_docs_are_only_notes(repo, capsys):
    commit(repo, {"backend/tests/test_x.py": "login(password='Operator-123')\n", "docs/howto.md": "ADMIN_PASSWORD=Zx9-Qw7-rt4k\n"})
    code, res = run(repo, capsys)
    assert code == 0 and "secret-assignment" in rules(res, "INFO")
    commit(repo, {"src/prod.py": "ADMIN_PASSWORD = 'Zx9-Qw7-rt4k'\n"})
    code, res = run(repo, capsys)
    assert code == 1 and "secret-assignment" in rules(res, "ERROR")


def test_a_secret_added_and_removed_again_is_still_reported(repo, capsys):
    commit(repo, {"src/leak.py": "KEY = '" + FAKE_AWS_KEY + "'\n"}, "oops")
    commit(repo, {"src/ok.py": "x = 1\n"}, "fix", remove=["src/leak.py"])
    code, res = run(repo, capsys, base="HEAD~2")
    assert code == 1 and "aws-access-key" in rules(res, "ERROR")           # history is published, not only the tip
    assert "src/leak.py" not in res["final_files"] and "src/leak.py" in res["touched"]


def test_personal_identifier_heuristics_warn_and_strict_fails(repo, capsys):
    commit(repo, {"src/x.py": "ifsc = 'ABCD0123456'\nn = 123456789012345\nmail = 'someone@bank.in'\n"})
    code, res = run(repo, capsys)
    assert code == 0 and {"ifsc-like", "long-number", "email"} <= rules(res, "WARN")
    code, _ = run(repo, capsys, "--strict")
    assert code == 1


def test_large_files(repo, capsys):
    commit(repo, {"data/big.txt": "x" * (2 * 1024 * 1024)})
    code, res = run(repo, capsys)
    assert code == 0 and "large-file" in rules(res, "WARN")
    commit(repo, {"data/huge.txt": "x" * (6 * 1024 * 1024)})
    code, res = run(repo, capsys)
    assert code == 1 and "large-file" in rules(res, "ERROR")


# ---------------------------------------------------------------- private terms
@pytest.fixture()
def terms(tmp_path_factory):
    f = tmp_path_factory.mktemp("private") / "terms.txt"
    f.write_text("# private\naccount_number|123456789012\npayee_name|Alice Example\nW:cheque_number|654321\n"
                 "payee_name|Bob\n", encoding="utf-8")            # 'Bob' is too short and is ignored
    return str(f)


def test_private_terms_block_and_are_never_printed(repo, capsys, terms):
    commit(repo, {"src/t.py": "a = 'A/C 1234 5678 9012'\nname = 'ALICE   example'\nok = 'Bob'\n"})
    code = audit.main(["--repo", str(repo), "--base", "HEAD~1", "--terms", terms, "-v"])
    out = capsys.readouterr().out
    assert code == 1 and "account_number#1" in out and "payee_name#2" in out
    assert "123456789012" not in out and "1234 5678 9012" not in out and "Alice" not in out and "ALICE" not in out


def test_low_sensitivity_terms_only_warn_and_digits_need_boundaries(repo, capsys, terms):
    commit(repo, {"src/t.py": "chq = '654321'\nnot_it = '16543210'\nacct = '9123456789012345'\n"})
    code, res = run(repo, capsys, "--terms", terms)
    msgs = [(f["severity"], f["detail"]) for f in res["findings"] if f["rule"] == "private-term"]
    assert code == 0 and len(msgs) == 1 and msgs[0][0] == "WARN" and msgs[0][1].startswith("cheque_number")


def test_terms_are_searched_in_file_names_and_commit_messages(repo, capsys, terms):
    commit(repo, {"docs/Alice Example notes.md": "x\n"}, "note about 123456789012")
    code, res = run(repo, capsys, "--terms", terms)
    where = [f["where"] for f in res["findings"] if f["rule"] == "private-term"]
    assert code == 1 and any("message" in w for w in where) and any("Alice Example notes.md" in w for w in where)


def test_missing_terms_file_is_reported_as_an_error_of_the_audit(repo, capsys):
    code = audit.main(["--repo", str(repo), "--base", "HEAD", "--terms", str(repo / "nope.txt")])
    assert code == 2 and "terms file" in capsys.readouterr().err


# ---------------------------------------------------------------- modes and base handling
def test_staged_mode_audits_the_index(repo, capsys):
    (repo / ".env").write_text("A=1\n")
    git(repo, "add", ".env")
    code = audit.main(["--repo", str(repo), "--staged", "--json"])
    res = json.loads(capsys.readouterr().out)
    assert code == 1 and "env-file" in rules(res, "ERROR")
    git(repo, "reset", "-q")
    assert audit.main(["--repo", str(repo), "--staged", "--json"]) == 0


def test_unknown_base_is_an_audit_failure(repo, capsys):
    assert audit.main(["--repo", str(repo), "--base", "origin/does-not-exist"]) == 2
    assert "not a known commit" in capsys.readouterr().err


def test_text_report_lists_files_commits_and_uncommitted_changes(repo, capsys):
    commit(repo, {"src/app.py": "x = 1\n"}, "add app")
    (repo / "scratch.txt").write_text("unstaged\n")
    audit.main(["--repo", str(repo), "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert "add app" in out and "src/app.py" in out and "scratch.txt" in out and "Not part of this push" in out
    assert "NOTE: no --terms file given" in out and "RESULT: 0 error(s)" in out
