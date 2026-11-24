import json
import sys

ORDER = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]
STATES = {"not-fixed": "no fix yet", "wont-fix": "the distribution won't fix it"}
LISTED = {"Critical", "High", "Medium"}

def native_rows(report):

    seen = {}
    for m in report.get("matches") or []:
        art, vuln = m.get("artifact") or {}, m.get("vulnerability") or {}
        if art.get("type") == "python":
            continue
        sev = vuln.get("severity") or "Unknown"
        sev = sev if sev in ORDER else "Unknown"
        fix = vuln.get("fix") or {}
        state = fix.get("state")
        if state == "fixed":
            fixed_in = ", ".join(fix.get("versions") or []) or "fixed (version not given)"
        else:
            fixed_in = STATES.get(state, "unknown")
        key = (art.get("name", "?"), art.get("version", "?"), vuln.get("id", "?"))
        seen[key] = (sev, key[0], key[1], key[2], fixed_in)
    return sorted(seen.values(), key=lambda r: (ORDER.index(r[0]), r[1], r[3]))

def table(rows):
    out = ["| Severity | Library | Version | Vulnerability | Fixed in |", "|---|---|---|---|---|"]
    out += [f"| {s} | {n} | {v} | {i} | {f} |" for s, n, v, i, f in rows]
    return out

def summary(report, label):
    rows = native_rows(report)
    head = f"### System libraries in the {label} image: known vulnerabilities (report only)"
    if not rows:
        return "\n".join([head, "", "None found in the SBOM's system libraries."]) + "\n"
    counts = [f"{sum(r[0] == s for r in rows)} {s.lower()}" for s in ORDER if any(r[0] == s for r in rows)]
    notyet = sum(r[4] == STATES["not-fixed"] for r in rows)
    wont = sum(r[4] == STATES["wont-fix"] for r in rows)
    unknown = sum(r[4] == "unknown" for r in rows)
    fixable = len(rows) - notyet - wont - unknown
    state = f"A fix exists for {fixable}; {notyet} have no fix yet; the distribution won't fix {wont}"
    state += f"; {unknown} have no fix state." if unknown else "."
    out = [head, "", f"{len(rows)} found: {', '.join(counts)}. {state}", ""]
    top = [r for r in rows if r[0] in LISTED]
    rest = [r for r in rows if r[0] not in LISTED]
    if top:
        out += table(top) + [""]
    if rest:
        out += [f"<details><summary>{len(rest)} low, negligible or unrated</summary>", ""] + table(rest) + ["", "</details>"]
    return "\n".join(out) + "\n"

def main(argv):
    path = argv[1] if len(argv) > 1 else "native-vulns.json"
    label = argv[2] if len(argv) > 2 else "release"
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        print(f"### System libraries in the {label} image\n\nThe check made no report ({type(e).__name__}). "
              "The release is not affected; see the step's log.\n")
        return 0
    print(summary(report, label), end="")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
