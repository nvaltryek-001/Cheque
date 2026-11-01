"""Turn `npm audit --json` (read from standard input) into GitHub Actions annotations.

    npm audit --json | python3 scripts/npm_audit_annotations.py [LEVEL]

Prints one `::error title=npm advisory::...` line per package at or above LEVEL (default: high), naming the package,
the affected versions, the advisory titles and the fix npm offers. Annotations can be read without opening the job
log. Nothing private is involved: advisories are public information about public packages.
"""
import json
import sys

ORDER = {"info": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}


def lines(report: dict, level: str = "high") -> list[str]:
    threshold = ORDER.get(level, 3)
    out = []
    for name, entry in sorted((report.get("vulnerabilities") or {}).items()):
        severity = entry.get("severity", "")
        if ORDER.get(severity, 0) < threshold:
            continue
        via = []
        for item in entry.get("via") or []:
            via.append(item.get("title", "advisory") if isinstance(item, dict) else "through " + str(item))
        fix = entry.get("fixAvailable")
        if isinstance(fix, dict):
            fix_text = "upgrade %s to %s%s" % (fix.get("name"), fix.get("version"), " (major version, may break)" if fix.get("isSemVerMajor") else "")
        else:
            fix_text = "no fix offered" if not fix else "update available"
        direct = "direct dependency" if entry.get("isDirect") else "indirect"
        out.append("%s %s [%s, %s]: %s | %s" % (name, entry.get("range", ""), severity, direct, "; ".join(via) or "-", fix_text))
    return out


def main() -> int:
    level = sys.argv[1] if len(sys.argv) > 1 else "high"
    try:
        report = json.load(sys.stdin)
    except ValueError as exc:
        print("::error title=npm advisory::npm audit did not return JSON (%s)" % exc)
        return 0
    found = lines(report, level)
    for text in found:
        print("::error title=npm advisory::" + text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    if not found:
        print("::notice title=npm advisory::no package at level %s or above in the JSON report (keys: %s)" % (level, ", ".join(sorted(report))[:300]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
