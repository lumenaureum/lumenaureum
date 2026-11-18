from datetime import date

from flask import abort, current_app, url_for

STAGES = ("shot", "delivered", "closed")
STEP = "QR letter mailed"

BLOCK = """<div id="letters" class="letters">
  <div class="row" style="gap:18px;flex-wrap:wrap">
    {% if gal %}<a class="go" href="{{ url_for('letter_qr', sid=s.id) }}" target="_blank"{% if not link %} data-tip="No gallery link yet"{% endif %}>QR letter &rsaquo;</a>
    {% if not link %}<span class="mute" style="font-size:13px" data-tip="No gallery link yet: prints with a blank box where the QR goes">draft</span>{% endif %}{% endif %}
    <a class="go" href="{{ url_for('letter_release', sid=s.id) }}" target="_blank">{{ 'Photo license' if lic else 'Copyright transfer' }} &rsaquo;</a>
    {% if C.delivery_method(s.type, s.delivery_method) == 'sd' %}<a class="go" href="{{ url_for('letter_labels', sid=s.id) }}" target="_blank">SD labels &rsaquo;</a>{% endif %}
  </div>
  {% if s.address %}<div class="mute" style="font-size:13px;margin-top:6px;white-space:pre-line"{% if printed %} data-tip="Letter printed {{ nice(printed) }}"{% endif %}>{{ s.address }}</div>{% endif %}
</div>"""

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    from flask import render_template_string
    ctx.migrate("""CREATE TABLE IF NOT EXISTS letters_printed (
        session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE, at TEXT NOT NULL);""")

    def setting(key, default=""):
        r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
        return (r["value"] or "").strip() if r and r["value"] else default

    def gallery_link(sid):
        fn = current_app.config.get("gallery_link")
        if not callable(fn):
            return None
        try:
            return fn(sid) or None
        except Exception:
            return None

    def load(sid):
        s = q("""SELECT s.*, h.name AS household, h.address AS address, h.company AS company FROM sessions s
                 JOIN households h ON h.id=s.household_id WHERE s.id=?""", (sid,), one=True)
        if not s:
            abort(404)
        subjects = q("""SELECT p.* FROM people p JOIN session_people sp ON sp.person_id=p.id
                        WHERE sp.session_id=? ORDER BY p.is_minor, p.id""", (sid,))
        adults = q("""SELECT * FROM people WHERE household_id=? AND is_minor=0 AND role IN ('parent','adult')
                      ORDER BY role='parent' DESC, id""", (s["household_id"],))
        return s, subjects, adults

    def oxford(names):
        names = [n for n in names if n]
        if len(names) <= 2:
            return " and ".join(names)
        return ", ".join(names[:-1]) + ", and " + names[-1]

    def first(name):
        return (name or "").split()[0] if (name or "").split() else ""

    def long_date(iso):
        if not iso:
            return ""
        dt = date.fromisoformat(iso[:10])
        return dt.strftime("%A, %B %-d") + ("" if dt.year == date.today().year else dt.strftime(", %Y"))

    def full_date(iso):
        return date.fromisoformat(iso[:10]).strftime("%B %-d, %Y") if iso else ""

    def greeting(s, adults):
        if adults:
            return "Dear " + oxford([first(a["name"]) for a in adults[:2]]) + ","
        name = s["household"]
        return "Dear " + (name[4:] if name.lower().startswith("the ") else name) + ","

    def who_shot(s, subjects, adults=()):
        if ctx.C.TYPE_KIND.get(s["type"]) == "property":
            if ctx.C.is_place(s["type"]) and (s["title"] or "").strip():
                return s["title"].strip()
            return ("your products" if s["type"] in ctx.C.PRODUCT_TYPES else
                    "your food" if s["type"] in ctx.C.INDOOR_TYPES else "the property")
        subjects = [p for p in subjects if (p["role"] if "role" in p.keys() else "") != "pet"] or subjects
        to = {a["id"] for a in adults[:2]}
        if family_wording(s) and (s["type"] == "family" or len(subjects) > 3):
            return "your family"
        if len(subjects) == 1 and subjects[0]["id"] in to:
            return "you"
        if family_wording(s):
            if not subjects and len(adults) == 1 and not q("SELECT 1 FROM people WHERE household_id=? AND is_minor=1",
                                                          (s["household_id"],), one=True):
                return "you"
            if not subjects:
                return "your family"
        elif len(subjects) > 3 or not subjects:
            return "you"
        return oxford([first(p["name"]) for p in subjects])

    def family_wording(s):

        if ctx.C.TYPE_KIND.get(s["type"]) != "portrait" or s["event_id"]:
            return False
        if ("company" in s.keys() and (s["company"] or "").strip()):
            return False
        t = next((t for t in ctx.C.TYPES if t.get("key") == s["type"]), None) or ctx.C.shipped_type(s["type"]) or {}
        return not t.get("license")

    def studio():
        return setting("studio_name", "") or ""

    def qr_svg(data):
        import segno
        return segno.make(data, error="m", micro=False).svg_inline(scale=4, border=4, dark="#000", light="#fff")

    def slug(s):
        return "".join(ch for ch in s["household"].replace("The ", "") if ch.isalnum()) or "client"

    def pdf(tpl, name, **kw):
        return ctx.html_pdf(render_template_string(tpl, **kw), name)

    @app.get("/letters/<int:sid>/qr-letter.pdf", endpoint="letter_qr")
    def letter_qr(sid):
        s, subjects, adults = load(sid)
        link = gallery_link(sid)
        draft = not link or not (s["address"] or "").strip()
        lines = [s["household"]] + [ln.strip() for ln in (s["address"] or "").splitlines() if ln.strip()]
        when = f" on {long_date(s['shoot_date'])}" if s["shoot_date"] else ""
        out = pdf(LETTER, f"letter-{slug(s)}.pdf", studio=studio(), photographer=setting("photographer_name"), draft=draft,
                  lines=lines, today=date.today().strftime("%B %-d, %Y"), greeting=greeting(s, adults),
                  who=who_shot(s, subjects, adults), when=when, qr=qr_svg(link) if link else None, days=ctx.C.gallery_days(s["type"]) * (2 if s["extension_used"] else 1),
                  sd=any(st == ("auto", "sd_shipped") for steps in ctx.C.WORKFLOW[s["type"]].values() for st in steps),
                  license=ctx.licensed(sid), delivery=ctx.C.delivery_text(s["type"], s["delivery_method"]),
                  credit="" if setting("credit_off") == "1" else ctx.C.CREDIT, logo=__import__("app").logo_src(),
                  codeword=bool(link and "&c=1" in link), event_family=bool(s["event_id"]),
                  proofs=bool(app.config.get("gallery_proofs") and app.config["gallery_proofs"](sid)),
                  download=bool(app.config.get("gallery_download") and app.config["gallery_download"](sid)))
        if not draft:
            first = not q("SELECT 1 FROM letters_printed WHERE session_id=?", (sid,), one=True)
            x("""INSERT INTO letters_printed (session_id, at) VALUES (?, date('now','localtime'))
                 ON CONFLICT(session_id) DO UPDATE SET at=excluded.at""", (sid,))
            if first:
                ctx.audit("Printed the QR letter", sid=sid)
        return out

    @app.get("/letters/<int:sid>/print-release.pdf", endpoint="letter_release")
    def letter_release(sid):
        s, subjects, adults = load(sid)
        name_studio, photographer = studio(), setting("photographer_name")
        owner = photographer or name_studio
        realestate = ctx.licensed(sid) or bool(s["event_id"])

        places = ctx.C.TYPE_KIND.get(s["type"]) == "property"
        kind = ctx.C.doc_label(s["type"])
        picks = ctx.C.MARKETING_PICKS
        if s["portfolio_no"]:
            favorites = "I will not show any photo from this session in my portfolio, on my website, on social media, or anywhere else."
            portfolio = favorites
        else:
            favorites = (ctx.C.portfolio_choice(picks) + " This permission continues after this transfer. I show no other photo"
                         " from your session anywhere.")
            portfolio = ctx.C.portfolio_choice(picks) + " I show no other photo from your session anywhere."
        portfolio = portfolio if not places else \
            "I may show these photos in my portfolio and on my website, never with private details."
        facts = [("Client" if realestate else "Family", s["household"])] + ([] if places else [
                 ("Photographed", oxford([p["name"] for p in subjects]) or "-")]) + [
                 ("Session", kind + (f", {full_date(s['shoot_date'])}" if s["shoot_date"] else "")),
                 ("Photographer", f"{photographer}, {name_studio}" if photographer and name_studio else owner)]
        import wording
        core = __import__("app")
        doc = "license" if realestate else "transfer"
        ag = core.agreement_ctx(sid) if realestate else {}
        values = {"PHOTOGRAPHER": owner,
                  "FAVORITES": favorites,
                  "WHAT THE LICENSE COVERS": ctx.C.fill(s["type"], "license_use"),
                  "LICENSE SCOPE": ag.get("lic_scope", ""), "PORTFOLIO": portfolio,
                  "OTHER USES": ("Anyone else who wants to use the photos needs their own license from me." if places else
                                 "The people photographed share this license. Anything else, such as selling the photos or"
                                 + (" using them to advertise a business," if ctx.C.fill(s["type"], "license_use")
                                    == ctx.C.DEFAULT_POLICY["license_use"] else " licensing them to others,")
                                 + " needs my written permission."),
                  "PRINT RELEASE": ("" if places else f"This is a print release: any print lab may print these photos for"
                                                      f" {s['household']} and the people photographed.")}
        sections = wording.sections(core.own_wording(doc, s["type"]), values)
        if s["event_id"]:
            rights_ = core.event_rights_text(s["event_id"]) or ("I keep the copyright in the event photos. Each family"
                      " receives a permanent personal license to its own photos and to group photos that include its child.")
            sections = [("Your photos", rights_), ("Printing", values["PRINT RELEASE"] or
                         f"This is a print release: any print lab may print these photos for {s['household']}.")]
        return pdf(TRANSFER, f"{'photo-license' if realestate else 'copyright-transfer'}-{slug(s)}.pdf",
                   head=(name_studio or photographer).upper(), title="Photo License" if realestate else "Copyright Transfer",
                   facts=facts, sections=sections, photographer=photographer,
                   contacts=[n_ + (f", for {core.company_of(s['household_id'])}" if core.company_of(s["household_id"]) else "")
                             for n_ in (core.agreement_signers(core.plan_head(sid))[1] or [adults[0]["name"] if adults else ""])],
                   draft=not core.doc_reviewed(doc, s["type"]))

    @app.get("/letters/<int:sid>/sd-labels.pdf", endpoint="letter_labels")
    def letter_labels(sid):
        s, _, _ = load(sid)
        fam = s["household"]
        return pdf(LABELS, f"sd-labels-{slug(s)}.pdf", head=studio().upper()[:22], fam=fam[:24], small=len(fam) > 16,
                   when=full_date(s["shoot_date"]) if s["shoot_date"] else "", n=70)

    def printed_on(sid):
        r = q("SELECT at FROM letters_printed WHERE session_id=?", (sid,), one=True)
        return r["at"] if r else None

    def block(s):
        if s["stage"] not in STAGES:
            return []
        steps_ = {st[1] for steps in ctx.C.WORKFLOW[s["type"]].values() for st in steps}
        html = render_template_string(BLOCK, s=s, link=gallery_link(s["id"]), printed=printed_on(s["id"]),
                                      nice=ctx.nice, lic=ctx.licensed(s["id"]) or bool(s["event_id"]), gal=bool(steps_ & {"gallery_built", "letter_mailed", "first_visit"})
                                      or ctx.C.delivery_method(s["type"], s["delivery_method"]) == "link")
        return [("Letters", html)]

    def step_action(label, s):
        if label != STEP or s["letter_mailed"] or printed_on(s["id"]):
            return None
        proofs_ = app.config.get("gallery_proofs")
        owed = 0 if proofs_ and proofs_(s["id"]) else ctx.release_blocked(s["id"])
        if owed:
            return dict(verb=f"Collect {ctx.money(owed)} first", href=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}")
        return dict(verb="Print letter",
                    href=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#letters",
                    alt=dict(verb="Mark mailed", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="mark", field="letter_mailed")))

    ctx.hooks.stage_blocks.append(block)
    ctx.hooks.step_actions.append(step_action)

LETTER = """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: Letter; margin: 0; }
body { margin: 0; font: 12.5pt/17.5pt "Times New Roman", Times, serif; color: #000; }
.abs { position: absolute; } .sans { font-family: Helvetica, Arial, sans-serif; }
.ret { left: 0.875in; top: 0.5in; } .ret b { font: bold 10pt Helvetica, Arial, sans-serif; display: block; }
.ret span { font: 9.5pt Helvetica, Arial, sans-serif; color: #4d4d4d; }
.draft { right: 1in; top: 0.5in; font: bold 10pt Helvetica, Arial, sans-serif; color: #737373; }
.to { left: 0.875in; top: 2.0in; font: 11.5pt/15pt Helvetica, Arial, sans-serif; }
.to i { color: #737373; font-size: 10pt; }
.date { right: 1in; top: 2.0in; font-size: 12pt; }
.logo { left: 0; right: 0; top: 0.45in; text-align: center; } .logo img { max-height: 0.6in; max-width: 2.4in; }
.body { position: absolute; left: 0.875in; right: 1in; top: 3.75in; }
.body p { margin: 0 0 10pt; }
.qr { width: 2.1in; height: 2.1in; margin: 8pt auto 4pt; display: block; }
.qr svg { width: 100%; height: 100%; }
.noqr { border: 1px dashed #666; display: flex; align-items: center; justify-content: center; text-align: center; color: #666; font: 10pt Helvetica, Arial, sans-serif; }
.cap { text-align: center; font: 10pt Helvetica, Arial, sans-serif; margin-bottom: 14pt; }
.sig { margin-top: 26pt; border-top: 0.6pt solid #000; width: 2.6in; padding-top: 3pt; font-size: 12pt; }
.sig i { display: block; font-size: 11.5pt; }
</style></head><body>
{% if logo %}<div class="abs logo"><img src="{{ logo }}" alt=""></div>{% endif %}
<div class="abs ret"><b>{{ (studio or photographer)|upper }}</b>{% if photographer and studio %}<span>{{ photographer }}</span>{% endif %}</div>
{% if draft %}<div class="abs draft">DRAFT</div>{% endif %}
<div class="abs to">{% for ln in lines %}{{ ln }}<br>{% endfor %}{% if lines|length == 1 %}<i>No mailing address on file</i>{% endif %}</div>
<div class="abs date">{{ today }}</div>
<div class="body">
  <p>{{ greeting }}</p>
  <p>Thank you for letting me photograph {{ who }}{{ when }}. {% if download %}Your photos are finished and ready to download.{% elif proofs %}Your proofs are ready to pick from.{% else %}Your photos are finished, and I think you are going to love them.{% endif %}</p>
  <p>Scan the code below with your phone's camera to open your private gallery{% if download %}, where you can download each photo or all of them at once{% endif %}. The code on this letter is the key to it, so keep the letter somewhere safe.{% if codeword %} When the gallery asks, enter the word you chose with {{ studio or photographer or 'your photographer' }}.{% endif %}</p>
  {% if qr %}<div class="qr">{{ qr|safe }}</div>{% else %}<div class="qr noqr">QR code goes here<br>(gallery not linked yet)</div>{% endif %}
  <div class="cap">{{ 'Scan to download your photos' if download else 'Scan to open your proofs' if proofs else 'Scan to open your photos' }}</div>
  <p>The gallery is available for {{ days }} days after you first open it, so open it when you have some quiet time to enjoy them.</p>
  {% if proofs and not download %}<p>Once you have picked, I'll finish your photos and send them to you.</p>{% else %}
  {% if download %}<p>Please download them before the gallery closes.</p>{% elif delivery and delivery != 'as full-resolution digital files, with an online gallery' %}<p>You will also receive every edited photo {{ delivery }}.</p>{% elif sd %}<p>Every edited photo will also come to you on an SD card.</p>{% endif %}
  <p>{% if event_family %}Your photos are for your family's personal use: to print, share and post, not to sell. The license letter with your photos has the details.{% elif license %}Your license to use the photos is set out in your agreement, and your license letter is a print release for any print lab.{% else %}The photos are yours to print and use however you like.{% endif %}</p>{% endif %}
  <p>With thanks,</p>
  <div class="sig">{% if photographer %}{{ photographer }}{% endif %}{% if studio %}<i>{{ studio }}</i>{% endif %}</div>
{% if credit %}<p class="sans" style="margin-top:18pt;text-align:center;font-size:8pt;color:#8a8478">{{ credit }}</p>{% endif %}
</div></body></html>"""

TRANSFER = """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: Letter; margin: 0.85in 1in 0.8in; }
body { margin: 0; font: 11.5pt/15.5pt "Times New Roman", Times, serif; color: #000; }
.head { font: bold 10pt Helvetica, Arial, sans-serif; }
h1 { font: bold 22pt "Times New Roman", Times, serif; margin: 10pt 0 6pt; padding-bottom: 6pt; border-bottom: 0.6pt solid #000; }
table { border-collapse: collapse; margin: 6pt 0 10pt; } td { padding: 2pt 0; vertical-align: top; }
td.k { width: 1.4in; font: 9.5pt Helvetica, Arial, sans-serif; color: #595959; text-transform: uppercase; padding-top: 3pt; }
h2 { font: bold 10.5pt/14pt Helvetica, Arial, sans-serif; margin: 10pt 0 3pt; } p { margin: 0; }
.sig { display: flex; gap: 0.6in; margin-top: 40pt; } .sig div { flex: 1; display: flex; gap: 0.15in; }
.sig span { white-space: nowrap; border-top: 0.6pt solid #000; padding-top: 3pt; font: 8.5pt Helvetica, Arial, sans-serif; color: #595959; text-transform: uppercase; }
.sig span.l { flex: 1; } .sig span.d { width: 0.85in; }
.draftline { font: bold 9pt Helvetica, Arial, sans-serif; color: #8a5a6b; border: 1pt dashed #b86f86; padding: 4pt 8pt; margin-bottom: 10pt; }
</style></head><body>
{% if draft %}<div class="draftline"><b>SAMPLE wording, not yet reviewed by you.</b> Once signed, this may be binding on you and your client. Read every line and make it fit your business and where you work before you use it.</div>{% endif %}
<div class="head">{{ head }}</div><h1>{{ title }}</h1>
<table>{% for k, v in facts %}<tr><td class="k">{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>
{% for h, t in sections %}<h2>{{ h }}</h2><p>{{ t }}</p>{% endfor %}
<div class="sig"><div><span class="l">Photographer{% if photographer %}&nbsp; {{ photographer }}{% endif %}</span><span class="d">Date</span></div>
{% for contact in contacts or [''] %}<div><span class="l">Client{% if contact %}&nbsp; {{ contact }}{% endif %}</span><span class="d">Date</span></div>{% endfor %}</div>
</body></html>"""

LABELS = """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: Letter; margin: 0.5in; }
body { margin: 0; } .grid { display: grid; grid-template-columns: repeat(7, 0.85in); grid-auto-rows: 0.8in; gap: 0.2in; justify-content: center; }
.l { border: 0.3pt dotted #999; display: flex; flex-direction: column; align-items: center; justify-content: space-between; padding: 5pt 6pt 6pt; text-align: center; }
.h { font: bold 5.5pt Helvetica, Arial, sans-serif; border-bottom: 0.4pt solid #000; width: 100%; padding-bottom: 2pt; }
.f { font: bold 8pt "Times New Roman", Times, serif; } .f.s { font-size: 6.5pt; } .w { font: 5.5pt Helvetica, Arial, sans-serif; }
</style></head><body><div class="grid">{% for i in range(n) %}<div class="l"><div class="h">{{ head }}</div><div class="f {{ 's' if small }}">{{ fam }}</div><div class="w">{{ when }}</div></div>{% endfor %}</div></body></html>"""
