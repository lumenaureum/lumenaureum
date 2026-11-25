import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
CSS = open(os.path.join(ROOT, "static", "style.css")).read()

def _block(selector):

    i = CSS.index(selector + " {")
    body = CSS[CSS.index("{", i) + 1:]
    depth, end = 1, 0
    for end, ch in enumerate(body):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if not depth:
            break
    body = re.sub(r"/\*.*?\*/", "", body[:end], flags=re.S)
    return {m.group(1): " ".join(m.group(2).split()) for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)}

DARK, LIGHT, AUTO = _block(":root"), _block(":root[data-theme=light]"), _block("  :root[data-theme=auto]")

def _lum(hex_):
    h = hex_.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

def contrast(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)

def test_match_this_device_repeats_the_light_palette_exactly():
    assert AUTO == LIGHT
    assert "@media (prefers-color-scheme: light)" in CSS.split("  :root[data-theme=auto]")[0][-200:]

def test_every_light_token_has_a_dark_one_and_the_reverse():

    assert set(LIGHT) - {"--card"} <= set(DARK)
    assert set(DARK) - set(LIGHT) <= {"--serif", "--r"}

def test_no_rule_tests_the_theme_itself():

    named = re.findall(r"\[data-theme=[a-z]+\]", CSS)
    assert sorted(named) == ["[data-theme=auto]", "[data-theme=light]"]
    assert CSS.count(":root {") == 1

@pytest.mark.parametrize("fg, bg, need", [

    *[(fg, bg, 4.5) for fg in ("--ink", "--mute", "--gold", "--green", "--amber", "--red", "--blue-ink", "--warn",
                               "--crit", "--good")
      for bg in ("--bg", "--surface", "--surface-2", "--hover")],
    *[(fg, "--col-bg", 4.5) for fg in ("--ink", "--mute")],

    ("--gold", "--gold-soft", 4.5), ("--green", "--green-soft", 4.5), ("--amber", "--amber-soft", 4.5),
    ("--red", "--red-soft", 4.5), ("--blue-ink", "--blue-soft", 4.5),

    ("--on-accent", "--gold", 4.5),

    ("--nav-text", "--nav-bg", 4.5), ("--nav-ink", "--nav-bg", 4.5), ("--nav-brand", "--nav-bg", 4.5),
    ("--nav-text", "--nav-hover", 4.5), ("--nav-text", "--nav-field", 4.5), ("--nav-field-ph", "--nav-field", 4.5),
    ("--tip-ink", "--tip-bg", 4.5), ("--pop-ink", "--surface", 4.5), ("--ink", "--band-bg", 4.5),

    *[(fg, "--surface", 3.0) for fg in ("--k-client", "--k-money", "--k-craft", "--blue", "--ok")],
])
def test_light_theme_contrast(fg, bg, need):
    a, b = LIGHT[fg], LIGHT[bg]
    assert contrast(a, b) >= need, f"{fg} {a} on {bg} {b}: {contrast(a, b):.2f}:1, needs {need}:1"

def test_light_bar_carries_its_words():

    ends = re.findall(r"#[0-9a-f]{6}", LIGHT["--bar"])
    assert ends and all(contrast(LIGHT["--on-bar"], e) >= 4.5 for e in ends)

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

def _theme(c, url="/"):
    html = c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True)
    return re.search(r'<html lang="en" data-theme="([a-z]+)"', html).group(1)

def test_appearance_is_saved_and_painted_on_every_page(env):
    A, c = env
    assert _theme(c) == "dark"
    page = c.get("/settings?tab=appearance", environ_base=E).get_data(as_text=True)
    assert 'id="f-app_theme"' in page and page.count('name="app_theme"') == 3
    assert re.search(r'value="dark" checked', page)
    for v in ("light", "auto", "dark"):
        r = c.post("/settings/appearance", data={"app_theme": v}, environ_base=E)
        assert r.status_code == 302 and "saved=appearance-form" in r.headers["Location"]
        assert _theme(c) == v and _theme(c, "/clients") == v and _theme(c, "/settings?tab=appearance") == v
        assert re.search(f'value="{v}" checked', c.get("/settings?tab=appearance", environ_base=E).get_data(as_text=True))

def test_a_made_up_theme_changes_nothing(env):
    A, c = env
    c.post("/settings/appearance", data={"app_theme": "light"}, environ_base=E)
    r = c.post("/settings/appearance", data={"app_theme": "neon"}, environ_base=E, follow_redirects=True)
    assert _theme(c) == "light" and "Not saved" in r.get_data(as_text=True)

def test_no_script_decides_the_theme():

    base = open(os.path.join(ROOT, "templates", "base.html")).read()
    assert 'data-theme="{{ app_theme() }}"' in base
    assert "<script>" not in base and "data-theme" not in open(os.path.join(ROOT, "static", "ui.js")).read()
