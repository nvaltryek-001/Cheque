#!/usr/bin/env python3
"""Pre-push audit: shows exactly what a push would publish and refuses to pass when it finds private data.

    python scripts/prepush_audit.py                       # commits that are not on the upstream branch yet
    python scripts/prepush_audit.py --base origin/main    # everything the branch adds to main (use this in CI)
    python scripts/prepush_audit.py --staged              # what the next commit would contain
    python scripts/prepush_audit.py --terms FILE          # also look for your private values (see below)
    python scripts/prepush_audit.py --json                # machine-readable result

What is checked, for every commit in the range (a file added in one commit and removed in a later one is still
published with the history, so intermediate commits count, not only the final state):

  * paths     datasets, instance/ data, databases, .env files, keys, images, PDFs/office/archives, model binaries,
              files that look like labels or provenance tables, build output, files git ignores but tracks
  * content   private keys, cloud/GitHub/Slack/Google tokens, JWTs, passwords in URLs, secret-looking assignments
  * personal  (warnings) IFSC / PAN / Aadhaar / 12-18 digit numbers / e-mail addresses
  * terms     YOUR private values, one per line in a file kept OUTSIDE the repository: real account numbers,
              payee or signer names, cheque numbers … Any occurrence in a path, commit message or added line fails.
              Matching is case-insensitive; digit strings also match with spaces or hyphens between the digits.
              The report never prints a term, only its label and number.

              Terms file format (UTF-8, '#' starts a comment):   [W:]label|value        or just   value
              "W:" marks a low-sensitivity term (cheque number, file name …) that only warns.

Exit status: 0 = nothing blocking, 1 = at least one ERROR (do not push), 2 = the audit itself could not run.
Standard library only; works on Linux, macOS and Windows with git on the PATH.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# paths that may be committed although a rule below would block them (exact repo-relative paths or globs)
ALLOWED_PATHS = [
    "backend/models_store/micr_e13b.npz",       # bundled E-13B glyph templates (no cheque images)
]
# Font files that ship with the interface (SIL Open Font License, Frontend/public/fonts/OFL.txt) are binary and would be
# refused. They are accepted only in that one folder, only when the file really starts with a web-font header, and only
# when small, so the exception cannot be used to carry an image, a database or an archive.
FONT_PATH = re.compile(r"^Frontend/public/fonts/[A-Za-z0-9._-]+\.woff2?$")
FONT_MAGIC = (b"wOFF", b"wOF2")
FONT_MAX_BYTES = 256 * 1024
# content scanning is skipped for generated files
SKIP_CONTENT = ["*package-lock.json", "*.min.js", "*.svg", "*.lock"]
# generic "password = '…'" matches in these places are example/test credentials: reported as INFO
EXAMPLE_PATHS = [r"^\.github/", r"^backend/tests/", r"^docs/", r"^README", r"^render\.yaml$", r"^Dockerfile$",
                 r"^start\.(sh|ps1)$", r"^legacy/", r"\.example$", r"^reports/",
                 r"^scripts/time_uploads\.py$"]       # builds a throw-away key and password at run time (secrets.token_*)

IMAGE_EXT = r"png|jpe?g|gif|bmp|tiff?|webp|heic|heif|ico|jfif"
PATH_RULES = [
    ("dataset-content", r"^(dataset|signature)/(?!PUT_[A-Z_]+\.txt$)", "private dataset folder"),
    ("dataset-folder", r"(^|/)(new_datset|vaildate|private_data|datasets|uploads|specimens)/", "dataset / upload folder"),
    ("instance-data", r"(^|/)instance/", "runtime data (database, uploads, secret key)"),
    ("database", r"\.(db|sqlite3?|db-wal|db-shm|db-journal)$", "database file"),
    ("env-file", r"(^|/)\.env(\.(?!example$|sample$|template$)[^/]*)?$", "environment file (secrets)"),
    ("key-material", r"\.(pem|key|p12|pfx|jks|keystore|asc|gpg)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|"
                     r"(^|/)secret_key$|(^|/)(credentials|secrets)\.json$", "key or credential file"),
    ("build-output", r"(^|/)(node_modules|__pycache__|\.venv[^/]*|venv|dist|build)/|\.pyc$", "build or environment output"),
    ("labels-or-provenance", r"(?i)(^|/)[^/]*(_private|private_|_labels|labels_|ground_?truth|provenance)[^/]*"
                             r"\.(csv|tsv|json|jsonl|xlsx?)$", "looks like private labels / provenance data"),
    ("image", rf"(?i)\.({IMAGE_EXT})$", "image file – cheque and signature images must never be committed"),
    ("document-or-archive", r"(?i)\.(pdf|docx?|xlsx?|pptx?|zip|7z|tar|gz|tgz|rar)$", "document or archive"),
    ("model-binary", r"(?i)\.(onnx|pth|pt|h5|pkl|pickle|joblib|bin|npz|npy|safetensors|ckpt)$", "model / array binary"),
]
WARN_PATH_RULES = [("tabular-data", r"(?i)\.(csv|tsv|jsonl)$", "tabular data – confirm it holds no real records")]

SECRET_RULES = [
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("aws-access-key", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("password-in-url", r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:[^/\s:@'\"]{3,}@[^/\s'\"]+"),
]
_SECRET_NAME = r"(?:password|passwd|secret|secret[_-]?key|token|api[_-]?key|private[_-]?key|access[_-]?key|auth[_-]?key)"
# name = "literal"   /   "name": "literal"   (the literal is quoted, has no spaces and is at least 6 characters)
QUOTED_SECRET = re.compile(rf"""(?i)\b([A-Za-z0-9_]*{_SECRET_NAME})['"]?\s*[:=]\s*(['"])([^'"\s]{{6,}})\2""")
# NAME=value in env files, shell, YAML and docker commands (upper-case names, unquoted value)
ENV_SECRET = re.compile(rf"\b([A-Z][A-Z0-9_]*{_SECRET_NAME.upper()})=([^\s'\"$#]{{6,}})")
PLACEHOLDER = re.compile(r"(?i)(change|your|example|placeholder|dummy|xxx|<|todo|none|null|false|true|\$\{|\{\{|\*\*\*|"
                         r"environ|getenv|secret-here|replace)")
PII_RULES = [
    ("ifsc-like", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("pan-like", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("aadhaar-like", re.compile(r"\b[2-9][0-9]{3}[ -][0-9]{4}[ -][0-9]{4}\b")),
    ("long-number", re.compile(r"(?<![\d.\-])\d{12,18}(?![\d.\-])")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.|users\.noreply\.github\.com|anthropic\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]


@dataclass
class Finding:
    severity: str
    rule: str
    where: str
    detail: str


@dataclass
class Term:
    label: str
    value: str
    severity: str
    regex: re.Pattern
    index: int


class AuditError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ git helpers
def git(root: str, *args: str, binary: bool = False, check: bool = True):
    p = subprocess.run(["git", "-C", root, *args], capture_output=True)
    if check and p.returncode != 0:
        raise AuditError(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout if binary else p.stdout.decode("utf-8", errors="replace")


def resolve_base(root: str, requested: str | None) -> tuple[str, str]:
    """Returns (ref, description). With no ref the audit covers the commits that are not on the upstream yet."""
    def ok(ref: str) -> bool:
        return subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                              capture_output=True).returncode == 0
    if requested:
        if not ok(requested):
            raise AuditError(f"base '{requested}' is not a known commit (run `git fetch` first?)")
        return requested, requested
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    for cand in ("@{upstream}", f"origin/{branch}", "origin/main", "origin/master"):
        if ok(cand):
            return cand, cand
    return EMPTY_TREE, "(empty: the whole history is new)"


def parse_raw(out: bytes) -> list[dict]:
    """Parse `git ... --raw -z` output into dicts (status, path, new blob)."""
    parts = out.split(b"\0")
    items, i = [], 0
    while i < len(parts) and parts[i]:
        meta = parts[i].decode()
        fields = meta.lstrip(":").split()
        status, path = fields[4][0], parts[i + 1].decode("utf-8", errors="replace")
        items.append({"status": status, "path": path, "blob": fields[3]})
        i += 2
    return items


def unit_changes(root: str, unit: str) -> list[dict]:
    if unit == "STAGED":
        out = git(root, "diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev", binary=True)
    else:
        out = git(root, "diff-tree", "--root", "--no-commit-id", "-r", "-z", "--raw", "--no-renames", "--no-abbrev",
                  unit, binary=True)
    return parse_raw(out)


def unit_added_lines(root: str, unit: str):
    """Yield (path, line number, text) for every added line of a commit (or of the index)."""
    if unit == "STAGED":
        text = git(root, "diff", "--cached", "-U0", "--no-renames", "--no-color")
    else:
        text = git(root, "show", "-U0", "--no-renames", "--no-color", "--format=", unit)
    path, lineno, header = "", 0, False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            header, path = True, ""
        elif header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@"):
            header = False
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif not header and line.startswith("+") and path:
            yield path, lineno, line[1:]
            lineno += 1


def blob_info(root: str, blob: str) -> tuple[int, bool]:
    if set(blob) == {"0"}:
        return 0, False
    size = int(git(root, "cat-file", "-s", blob).strip())
    head = git(root, "cat-file", "blob", blob, binary=True)[:8192]
    return size, b"\0" in head


def is_shipped_font(root: str, path: str, blob: str, size: int) -> bool:
    if not FONT_PATH.match(path) or size > FONT_MAX_BYTES or set(blob) == {"0"}:
        return False
    return git(root, "cat-file", "blob", blob, binary=True)[:4] in FONT_MAGIC


# ------------------------------------------------------------------------------------------------ rules
def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or path == p for p in patterns)


def compile_term(value: str) -> re.Pattern:
    v = value.strip()
    if v.isdigit() and len(v) >= 8:                       # also matches "1234 5678 9012" and "1234-5678-9012"
        return re.compile(r"(?<!\d)" + r"[ -]?".join(v) + r"(?!\d)")
    body = re.escape(v).replace(r"\ ", r"\s+")
    return re.compile((r"(?<!\d)" + body + r"(?!\d)") if v.isdigit() else body, re.I)


def load_terms(path: str | None) -> list[Term]:
    terms: list[Term] = []
    if not path:
        return terms
    try:
        lines = open(path, encoding="utf-8-sig").read().splitlines()
    except OSError as exc:
        raise AuditError(f"cannot read the terms file: {exc}") from exc
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        severity = ERROR
        if line[:2].upper() == "W:":
            severity, line = WARN, line[2:]
        label, sep, value = line.partition("|")
        if not sep:
            label, value = "term", line
        value = value.strip()
        if len(value) < 4:
            continue
        terms.append(Term(label.strip() or "term", value, severity, compile_term(value), len(terms) + 1))
    return terms


def term_hits(text: str, terms: list[Term]) -> list[Term]:
    return [t for t in terms if t.regex.search(text)]


def mask(text: str, terms: list[Term]) -> str:
    for t in terms:
        text = t.regex.sub(f"<{t.label}#{t.index}>", text)
    return text


def check_path(path: str, extra_allowed: list[str]) -> list[Finding]:
    found: list[Finding] = []
    allowed = matches_any(path, ALLOWED_PATHS + extra_allowed)
    for rule, rx, why in PATH_RULES:
        if re.search(rx, path) and not allowed:
            found.append(Finding(ERROR, rule, path, why))
    for rule, rx, why in WARN_PATH_RULES:
        if re.search(rx, path) and not allowed:
            found.append(Finding(WARN, rule, path, why))
    return found


def check_line(path: str, lineno: int, text: str, where: str) -> list[Finding]:
    found: list[Finding] = []
    if "audit:allow" in text:
        return found
    for rule, rx in SECRET_RULES:
        if re.search(rx, text):
            found.append(Finding(ERROR, rule, f"{where}{path}:{lineno}", "matches a known secret format"))
    for rx, group in ((QUOTED_SECRET, 3), (ENV_SECRET, 2)):
        m = rx.search(text)
        if m and not PLACEHOLDER.search(m.group(group)):
            example = any(re.search(p, path) for p in EXAMPLE_PATHS)
            found.append(Finding(INFO if example else ERROR, "secret-assignment", f"{where}{path}:{lineno}",
                                 ("example/test credential (confirm it is not real): " if example else "hard-coded secret: ")
                                 + m.group(1)))
            break
    for rule, rx in PII_RULES:
        if rx.search(text):
            found.append(Finding(WARN, rule, f"{where}{path}:{lineno}", "possible personal or financial identifier"))
    return found


def audit(root: str, base: str | None, staged: bool, terms: list[Term], extra_allowed: list[str],
          extra_skip: list[str], verbose: bool) -> dict:
    ref, desc = ("(index)", "(index)") if staged else resolve_base(root, base)
    head = git(root, "rev-parse", "--short", "HEAD").strip()
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    findings: list[Finding] = []
    commits: list[dict] = []
    if staged:
        units = ["STAGED"]
    else:
        revs = git(root, "rev-list", "--reverse", f"{ref}..HEAD" if ref != EMPTY_TREE else "HEAD").split()
        units = revs
        for sha in revs:
            fmt = git(root, "show", "-s", "--format=%h%x00%an%x00%ae%x00%s%x00%B", sha).split("\0")
            commits.append({"sha": fmt[0], "author": fmt[1], "email": fmt[2], "subject": fmt[3]})
            body = fmt[4] if len(fmt) > 4 else ""
            for t in term_hits(fmt[3] + "\n" + body, terms):
                findings.append(Finding(t.severity, "private-term", f"commit {fmt[0]} message",
                                        f"{t.label}#{t.index} (length {len(t.value)})"))
            for rule, rx in SECRET_RULES:
                if re.search(rx, body):
                    findings.append(Finding(ERROR, rule, f"commit {fmt[0]} message", "matches a known secret format"))
    touched: dict[str, dict] = {}
    for unit in units:
        where = "" if unit == "STAGED" else f"{unit[:7]} "
        for ch in unit_changes(root, unit):
            path, status = ch["path"], ch["status"]
            rec = touched.setdefault(path, {"status": status, "size": 0, "binary": False, "first": status})
            rec["status"] = status
            for t in term_hits(path, terms):
                findings.append(Finding(t.severity, "private-term", f"{where}{path}", f"in the file name: {t.label}#{t.index}"))
            if status == "D":
                continue
            size, binary = blob_info(root, ch["blob"])
            rec.update(size=size, binary=binary)
            findings += [Finding(f.severity, f.rule, where + f.where, f.detail) for f in check_path(path, extra_allowed)]
            if size > 5 * 1024 * 1024:
                findings.append(Finding(ERROR, "large-file", where + path, f"{size / 1048576:.1f} MB"))
            elif size > 1024 * 1024:
                findings.append(Finding(WARN, "large-file", where + path, f"{size / 1048576:.1f} MB"))
            if binary and not matches_any(path, ALLOWED_PATHS + extra_allowed) \
                    and not is_shipped_font(root, path, ch["blob"], size) \
                    and not any(f.rule in ("image", "document-or-archive", "model-binary") and f.where.endswith(path) for f in findings):
                findings.append(Finding(ERROR, "binary-file", where + path, "binary content"))
        for path, lineno, text in unit_added_lines(root, unit):
            if matches_any(path, SKIP_CONTENT + extra_skip):
                continue
            findings += check_line(path, lineno, text, where)
            for t in term_hits(text, terms):
                detail = f"{t.label}#{t.index} (length {len(t.value)})"
                if verbose:
                    detail += "  » " + mask(text.strip(), terms)[:140]
                findings.append(Finding(t.severity, "private-term", f"{where}{path}:{lineno}", detail))
    # files that are tracked although .gitignore excludes them (someone used `git add -f`)
    for path in git(root, "ls-files", "-ci", "--exclude-standard").split("\n"):
        if path and path in touched:
            findings.append(Finding(WARN, "tracked-but-ignored", path, ".gitignore excludes this path but it is tracked"))
    diff_args = ("diff", "--cached") if staged else ("diff", ref, "HEAD")
    raw = git(root, *diff_args, "--name-status", "--no-renames", "-z", binary=True).split(b"\0")
    final = {raw[i + 1].decode("utf-8", errors="replace"): raw[i].decode() for i in range(0, len(raw) - 1, 2)}
    uncommitted = [l for l in git(root, "status", "--porcelain").split("\n") if l] if not staged else []
    # de-duplicate (the same line can be reported by several rules or commits)
    seen, unique = set(), []
    for f in findings:
        key = (f.severity, f.rule, f.where, f.detail)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return {"branch": branch, "base": desc, "head": head, "commits": commits, "final_files": final, "touched": touched,
            "findings": unique, "uncommitted": uncommitted, "terms": len(terms)}


# ------------------------------------------------------------------------------------------------ output
def human(size: int) -> str:
    return f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB"


def render(res: dict, staged: bool) -> str:
    out = [f"PRE-PUSH AUDIT   branch {res['branch']}   base {res['base']}   head {res['head']}"
           if not staged else "PRE-COMMIT AUDIT of the staged changes", ""]
    if not staged:
        out.append(f"Commits that would be published ({len(res['commits'])}):")
        for c in res["commits"]:
            out.append(f"  {c['sha']}  {c['subject']}   [{c['author']} <{c['email']}>]")
        out.append("")
    bad = {}
    for f in res["findings"]:
        path = re.split(r"[:]\d+$", f.where.split(" ", 1)[-1])[0]
        if f.severity in (ERROR, WARN):
            bad.setdefault(path, set()).add(f.severity)
    out.append(f"Files that would be {'committed' if staged else 'published'} (final state: {len(res['final_files'])}):")
    for path, st in sorted(res["final_files"].items()):
        rec = res["touched"].get(path, {})
        flag = "ERROR" if ERROR in bad.get(path, ()) else "warn" if WARN in bad.get(path, ()) else "ok"
        size = human(rec.get("size", 0)) if st != "D" else ""
        out.append(f"  {st}  {path:<70} {size:>9}  {flag}")
    gone = sorted(p for p in res["touched"] if p not in res["final_files"])
    if gone and not staged:
        out.append("")
        out.append("Added and removed again inside the range (still part of the published history):")
        out += [f"  {p}" for p in gone]
    out.append("")
    for sev in (ERROR, WARN, INFO):
        rows = [f for f in res["findings"] if f.severity == sev]
        if rows:
            out.append(f"{sev} ({len(rows)})")
            out += [f"  [{f.rule}] {f.where}: {f.detail}" for f in rows[:200]]
            if len(rows) > 200:
                out.append(f"  … {len(rows) - 200} more")
            out.append("")
    if res["uncommitted"] and not staged:
        out.append(f"Not part of this push (uncommitted changes in the working tree: {len(res['uncommitted'])}):")
        out += [f"  {l}" for l in res["uncommitted"][:40]]
        out.append("")
    if not res["terms"]:
        out.append("NOTE: no --terms file given – private account numbers, names and labels were NOT searched for.")
    else:
        out.append(f"Private terms searched: {res['terms']} (values are never printed).")
    n = {s: sum(f.severity == s for f in res["findings"]) for s in (ERROR, WARN, INFO)}
    verdict = "FAILED – do not push" if n[ERROR] else "no blocking findings (review the warnings)"
    out.append(f"RESULT: {n[ERROR]} error(s), {n[WARN]} warning(s), {n[INFO]} note(s) → {verdict}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="compare against this ref (default: the upstream branch)")
    ap.add_argument("--staged", action="store_true", help="audit the staged changes instead of commits")
    ap.add_argument("--terms", help="file with your private values (kept outside the repository)")
    ap.add_argument("--allow", action="append", default=[], metavar="GLOB", help="path that may be committed anyway")
    ap.add_argument("--skip-content", action="append", default=[], metavar="GLOB", help="do not scan the text of these paths")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("-v", "--verbose", action="store_true", help="show the masked source line of private-term hits")
    ap.add_argument("--repo", default=".", help="repository root (default: current directory)")
    a = ap.parse_args(argv)
    try:
        root = git(a.repo, "rev-parse", "--show-toplevel").strip()
        res = audit(root, a.base, a.staged, load_terms(a.terms), a.allow, a.skip_content, a.verbose)
    except AuditError as exc:
        print(f"audit could not run: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({**res, "findings": [asdict(f) for f in res["findings"]]}, indent=2))
    else:
        print(render(res, a.staged))
    blocking = any(f.severity == ERROR or (a.strict and f.severity == WARN) for f in res["findings"])
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
