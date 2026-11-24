import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
INSTRUCTIONS = re.sub(r"\\\n", " ", "\n".join(l for l in DOCKERFILE.splitlines() if not l.lstrip().startswith("#")))

def _apt_install():
    m = re.search(r"apt-get install -y --no-install-recommends ([^&]+)", INSTRUCTIONS)
    assert m
    return set(m.group(1).split())

def test_only_what_pdfs_and_time_zones_need_is_installed():
    assert _apt_install() == {"libpango-1.0-0", "libpangoft2-1.0-0", "libharfbuzz-subset0",
                              "fonts-liberation", "fonts-dejavu-core", "fonts-droid-fallback", "tzdata"}
    assert "TODO" not in DOCKERFILE

def test_no_other_network_tool_reaches_the_image():
    for tool in ("curl", "wget", "netcat", "openssh-client"):
        assert not re.search(rf"\b{tool}\b", INSTRUCTIONS), tool

def test_every_published_debian_fix_is_taken_at_build_and_the_lists_are_dropped():
    run = INSTRUCTIONS[INSTRUCTIONS.index("apt-get update"):]
    run = run[:run.index("\n")]
    assert "apt-get upgrade -y" in run and "rm -rf /var/lib/apt/lists/*" in run

def test_the_base_image_stays_pinned_by_digest():
    assert re.search(r"^FROM python:3\.13-slim-trixie@sha256:[0-9a-f]{64}$", INSTRUCTIONS, re.M)

def test_the_health_check_is_the_apps_own_probe():
    m = re.search(r"^HEALTHCHECK (.*)$", INSTRUCTIONS, re.M)
    assert m
    assert m.group(1).split("CMD", 1)[1].strip() == '["python", "-I", "-S", "/app/healthcheck.py"]'
    assert "--timeout=5s" in m.group(1)
    assert "timeout=4" in (ROOT / "healthcheck.py").read_text()

def test_the_code_is_compiled_at_build_so_a_check_or_a_start_never_compiles():
    run = INSTRUCTIONS[INSTRUCTIONS.index("COPY . ."):]
    assert "python -m compileall -q /usr/local/lib/python3.13 /app" in run

def test_nothing_run_inside_the_container_calls_curl():
    for path in [ROOT / "Dockerfile", ROOT / "compose.yaml", *ROOT.glob("*.sh"), *ROOT.glob("tools/**/*.sh")]:
        if path.exists():
            text = path.read_text() if path.name != "Dockerfile" else INSTRUCTIONS
            assert not re.search(r"\bcurl\b", text), path

def test_the_docs_tell_catalog_users_to_switch_their_health_check():
    ops = (ROOT / "docs" / "operations.md").read_text()
    sec = (ROOT / "docs" / "security.md").read_text()
    assert '["CMD", "python", "/app/healthcheck.py"]' in ops and "1.5.2 and later" in ops
    image = sec[sec.index("## What is in the image"):]
    image = image.split("\n## ", 1)[0]
    assert "python /app/healthcheck.py" in image and "no curl" in image
