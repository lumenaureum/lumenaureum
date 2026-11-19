import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_payment_text_names_the_business():
    import config as C
    old = list(C.PAYMENT_METHODS)
    try:
        C.PAYMENT_METHODS[:] = ["cash", "check"]
        assert "Checks are made out to Patterson Photo LLC." in C.payment_text("Patterson Photo LLC")
        assert "Checks are made out to Patterson Photo LLC." in C.payment_text("  Patterson Photo LLC. ")
        assert "the business named on your invoice" in C.payment_text("")
        assert dict(C.build_terms(business="Patterson Photo LLC"))["Payment"].endswith("Checks are made out to Patterson Photo LLC.") \
            or "Checks are made out to Patterson Photo LLC." in dict(C.build_terms(business="Patterson Photo LLC"))["Payment"]
        C.PAYMENT_METHODS[:] = ["cash"]
        assert "Checks" not in C.payment_text("Patterson Photo LLC")
    finally:
        C.PAYMENT_METHODS[:] = old

def test_every_caller_passes_the_business_name():
    root = os.path.join(os.path.dirname(__file__), "..")
    app = open(os.path.join(root, "app.py")).read()
    assert "C.payment_text()" not in app and "C.build_terms(s[\"type\"])" not in app and "C.build_terms()" not in app
    for t in ("settings.html", "type_edit.html"):
        html = open(os.path.join(root, "templates", t)).read()
        assert "business_name" in html and "C.TERMS " not in html
