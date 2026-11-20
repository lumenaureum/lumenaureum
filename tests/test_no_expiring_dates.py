import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRITES = re.compile(r"(seen\.txt|limits\.txt)")
WRITE_CALL = re.compile(r"write_text|write_bytes|host_write|put_stream")
DATE = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
MARK = "# fixed date:"

def offenders():
    out = []
    for f in sorted(HERE.glob("test_*.py")):
        if f.name == Path(__file__).name:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if not (WRITES.search(line) and WRITE_CALL.search(line)) or MARK in line:
                continue
            for y, m, d in DATE.findall(line):
                if 2026 <= int(y) < 2090:
                    out.append(f"{f.name}:{n}: {y}-{m}-{d}")
    return out

def test_gallery_dates_in_tests_cannot_expire_unnoticed():
    bad = offenders()
    assert not bad, ("fixed dates written to seen.txt/limits.txt (make them relative to the run, or mark the line "
                     f"'{MARK} <why it cannot expire>'):\n  " + "\n  ".join(bad))
