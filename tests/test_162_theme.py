import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

DARK, LIGHT = _block(":root"), _block(":root[data-theme=light]")

def _rgb(hex_):
    h = hex_.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _lum(rgb):
    lin = [c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

def contrast(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)

def over(fg, bg, alpha):

    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))

def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, selector
    return m.group(1)

@pytest.mark.parametrize("cls, tok, rgb_tok, page", [
    (".ui-chip-attention", "--warn", "--warn-rgb", "--bg"),
    (".ui-chip-attention", "--warn", "--warn-rgb", "--surface"),
    (".ui-chip-attention", "--warn", "--warn-rgb", "--surface-2"),
    (".ui-chip-late", "--crit", "--crit-rgb", "--bg"),
])
def test_light_status_chip_text_reads_on_its_tint(cls, tok, rgb_tok, page):

    alpha = float(re.search(r"rgba\(var\(" + re.escape(rgb_tok) + r"\),\s*([.\d]+)\)", _rule(cls)).group(1))
    fg = _rgb(LIGHT[tok])
    bg = over(fg, _rgb(LIGHT[page]), alpha)
    assert contrast(fg, bg) >= 4.5, f"{cls} on {page}: {contrast(fg, bg):.2f}:1"

@pytest.mark.parametrize("bar_tok", ["--nav-bg", "--nav-field", "--nav-hover"])
def test_light_focus_ring_shows_in_the_top_bar(bar_tok):

    assert "outline: 2px solid var(--gold)" in CSS
    assert "outline-color: var(--nav-ring)" in _rule("nav.top :focus-visible")
    assert DARK["--nav-ring"] == "var(--gold)"
    ring = _rgb(LIGHT["--nav-ring"])
    assert contrast(ring, _rgb(LIGHT[bar_tok])) >= 3.0, f"{bar_tok}: {contrast(ring, _rgb(LIGHT[bar_tok])):.2f}:1"

def test_light_zero_count_filter_chip_reads():

    assert "opacity: var(--zero-op)" in _rule(".ui-btn.ui-fchip.ui-fchip-zero")
    assert DARK["--zero-op"] == ".5"
    op = float(LIGHT["--zero-op"])
    bg = _rgb(LIGHT["--bg"])
    fg = over(_rgb(LIGHT["--ink"]), bg, op)
    assert contrast(fg, bg) >= 4.5, f"{contrast(fg, bg):.2f}:1"

def _stops(v):
    return [_rgb(h) for h in re.findall(r"#[0-9a-f]{6}", v)]

def test_light_primary_buttons_are_one_gold():

    assert "var(--bar)" in _rule("button.primary, .btn.primary, nav.top a.cta")
    assert "var(--on-bar)" in _rule("button.primary, .btn.primary, nav.top a.cta")
    r = _rule(".ui-btn-primary, a.ui-btn-primary")
    assert "background: var(--primary-bg)" in r and "color: var(--primary-ink)" in r
    assert (LIGHT["--primary-bg"], LIGHT["--primary-ink"]) == ("var(--bar)", "var(--on-bar)")
    assert (DARK["--primary-bg"], DARK["--primary-ink"], DARK["--primary-line"]) == (
        "var(--gold)", "var(--on-accent)", "var(--gold)")
    for stop in _stops(LIGHT["--bar"]):
        assert contrast(_rgb(LIGHT["--on-bar"]), stop) >= 4.5, LIGHT["--bar"]

def test_light_tooltip_is_not_pale_on_pale():

    r = _rule("#tipbox")
    assert "var(--tipbox-bg)" in r and "var(--tipbox-ink)" in r and "var(--tipbox-line)" in r
    assert (DARK["--tipbox-bg"], DARK["--tipbox-ink"], DARK["--tipbox-line"]) == ("#f4efe6", "#141210", "#cfc6b8")
    assert contrast(_rgb(LIGHT["--tipbox-ink"]), _rgb(LIGHT["--tipbox-bg"])) >= 4.5
    for page in ("--bg", "--surface", "--surface-2"):
        assert contrast(_rgb(LIGHT["--tipbox-line"]), _rgb(LIGHT[page])) >= 3.0, page

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    from helpers import fresh_modules, signed_in_client
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

def test_sign_in_page_follows_light_once_the_studio_is_unlocked(env):

    A, c = env
    assert c.post("/settings/appearance", data={"app_theme": "light"}, environ_base=E).status_code == 302
    c.post("/logout", environ_base=E)
    html = c.get("/login", environ_base=E).get_data(as_text=True)
    assert re.search(r'<html lang="en" data-theme="light"', html), re.search(r"<html[^>]*>", html).group(0)

def test_sign_in_page_stays_dark_while_the_studio_is_locked(env):

    A, c = env
    assert c.post("/settings/appearance", data={"app_theme": "light"}, environ_base=E).status_code == 302
    c.post("/logout", environ_base=E)
    import datakey
    datakey.lock()
    html = c.get("/login", environ_base=E).get_data(as_text=True)
    assert re.search(r'<html lang="en" data-theme="dark"', html)

@pytest.mark.parametrize("theme, scheme, bg", [("dark", "dark", "#12100e"), ("light", "light", "#efe8da"),
                                               ("auto", "dark light", "#12100e")])
def test_first_paint_follows_the_app_setting(env, theme, scheme, bg):

    A, c = env
    assert c.post("/settings/appearance", data={"app_theme": theme}, environ_base=E).status_code == 302
    html = c.get("/", environ_base=E).get_data(as_text=True)
    head = html.split('<link rel="stylesheet"', 1)[0]
    assert f'<meta name="color-scheme" content="{scheme}">' in head
    assert f"html {{ background: {bg}; }}" in head
    assert ("prefers-color-scheme: light" in head) == (theme == "auto")
    assert re.search(r"(?m)^html \{[^}]*background: none;", CSS)
