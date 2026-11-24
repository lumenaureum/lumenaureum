import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import native_vuln_report as nvr

CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

def _step(name_part):

    m = re.search(r"\n      - name: [^\n]*" + re.escape(name_part) + r"[^\n]*\n(.*?)(?=\n      - |\n  [a-z-]+:\n|\Z)", CI, re.S)
    assert m, name_part
    return m.group(0)

def _publish_job():
    return CI[CI.index("\n  publish:\n"):CI.index("\n  publish-index:\n")]

def test_the_check_runs_in_the_release_job_after_the_sbom_is_made():
    job = _publish_job()
    assert job.index("output-file: sbom.spdx.json") < job.index("system libraries for known vulnerabilities")

def test_the_check_reads_the_sbom_and_never_fails_the_release():
    s = _step("system libraries for known vulnerabilities")
    assert "sbom: sbom.spdx.json" in s
    assert "continue-on-error: true" in s and "fail-build: false" in s
    assert re.search(r"timeout-minutes: \d+", s)

def test_the_action_is_pinned_by_commit_and_the_tool_by_version():
    s = _step("system libraries for known vulnerabilities")
    assert re.search(r"uses: anchore/scan-action@[0-9a-f]{40}\s+# v\d+\.\d+\.\d+", s)
    assert re.search(r"grype-version: v\d+\.\d+\.\d+\b", s)

def test_the_summary_step_never_fails_the_release_either():
    s = _step("Add that report to this run's summary")
    assert "continue-on-error: true" in s and "tools/native_vuln_report.py native-vulns.json" in s

def _match(name, version, typ, vid, sev, state="fixed", versions=("1.1",)):
    return {"artifact": {"name": name, "version": version, "type": typ},
            "vulnerability": {"id": vid, "severity": sev, "fix": {"state": state, "versions": list(versions)}}}

REPORT = {"matches": [
    _match("libssl3t64", "3.5.7-1", "deb", "CVE-2026-0001", "High"),
    _match("libssl3t64", "3.5.7-1", "deb", "CVE-2026-0001", "High"),
    _match("libcairo2", "1.18.4-1", "deb", "CVE-2026-0002", "Low", state="not-fixed", versions=()),
    _match("libharfbuzz0b", "10.2.0-1", "deb", "CVE-2026-0003", "Critical"),
    _match("curl", "8.14.1-2", "deb", "CVE-2026-0004", "Medium", state="wont-fix", versions=()),
    _match("cryptography", "46.0.1", "python", "GHSA-xxxx-yyyy-zzzz", "Critical"),
]}

def test_python_packages_are_left_out_and_duplicates_listed_once():
    rows = nvr.native_rows(REPORT)
    assert [r[1] for r in rows] == ["libharfbuzz0b", "libssl3t64", "curl", "libcairo2"]

def test_the_summary_counts_lists_and_folds_the_minor_ones():
    out = nvr.summary(REPORT, "amd64")
    assert "amd64 image" in out and "4 found: 1 critical, 1 high, 1 medium, 1 low. A fix exists for 2; 1 have no fix yet; the distribution won't fix 1." in out
    top, folded = out.split("<details>")
    assert "CVE-2026-0003" in top and "CVE-2026-0001" in top and "CVE-2026-0002" not in top
    assert "CVE-2026-0002" in folded and "no fix yet" in folded
    assert "| Medium | curl | 8.14.1-2 | CVE-2026-0004 | the distribution won't fix it |" in top
    assert "cryptography" not in out

def test_nothing_found_says_so():
    assert "None found" in nvr.summary({"matches": []}, "arm64")

def test_a_missing_or_broken_report_still_exits_zero(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    for path in (tmp_path / "missing.json", bad):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "native_vuln_report.py"), str(path), "amd64"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and "made no report" in r.stdout, r.stdout + r.stderr

def test_a_real_report_runs_end_to_end(tmp_path):
    p = tmp_path / "native-vulns.json"
    p.write_text(json.dumps(REPORT))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "native_vuln_report.py"), str(p), "arm64"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "| Critical | libharfbuzz0b |" in r.stdout
