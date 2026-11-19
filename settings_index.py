ENTRIES = [
    ("Business name, your name, brand", "General", "studio photographer company party agreements llc", "settings_view", {}, "f-business_name"),
    ("Business email and phone", "General", "contact email phone number picks documents printed top quotes invoices", "settings_view", {}, "f-business_email"),
    ("Website", "General", "website site web address url link gallery homepage", "settings_view", {}, "f-business_website"),
    ("Mailing address", "General", "address post po box checks mailed mail", "settings_view", {}, "f-business_address"),
    ("Tax ID", "General", "ein tax id taxpayer number invoice printed", "settings_view", {}, "f-business_tax_id"),
    ("Where clients pay you", "General", "venmo zelle card payment link pay checks payable", "settings_view", {}, "f-pay_details"),
    ("Bank details for transfers", "General", "bank transfer account routing wire ach invoice", "settings_view", {}, "f-bank_details"),
    ("Mileage rate", "General", "mileage miles irs rate travel drive driving trips cents per mile", "settings_view", {}, "f-mileage_rate"),
    ("Home base", "General", "home base drive start travel distance address", "settings_view", {}, "f-home_base"),
    ("Age of majority", "General", "minor adult 18 19 21 guardian parent release signs", "settings_view", {}, "f-age_majority"),
    ("Check-in wait after a check-in", "General", "check in checked snooze months clients repeat remind reach out", "settings_view", {}, "f-check_in_snooze_months"),
    ("Sign out when idle", "General", "idle sign out signed log out logout timeout inactive security hours session", "settings_view", {}, "f-idle_signout_hours"),
    ("Logo on documents", "General", "logo brand image picture header", "settings_view", {}, "logo"),
    ("Client gallery look and proof mark", "General", "gallery font background color script watermark proof mark logo", "settings_view", {}, "gallery-look"),
    ("Credit line", "General", "credit lumen aureum footer made with", "settings_view", {}, "f-credit"),
    ("Delete demo clients", "General", "demo sample clients remove", "settings_view", {}, "demo"),
    ("Time zone", "Business and time zone", "timezone time zone clock dates deadlines", "setup_step", {"step": "business"}, "f-timezone"),
    ("Home location for sunset times", "Business and time zone", "zip latitude longitude sunset golden hour light", "setup_step", {"step": "business"}, "f-home_zip"),
    ("What you shoot", "What you shoot", "session types presets weddings portraits families seniors newborn pets headshots real estate products food events", "setup_step", {"step": "shoot"}, ""),
    ("Deposit to hold a date", "Payments and terms", "deposit retainer hold date booking book", "setup_step", {"step": "money"}, "f-deposit"),
    ("Deposit for events", "Payments and terms", "deposit percent events group percentage", "setup_step", {"step": "money"}, "f-event_pct"),
    ("Balance due", "Payments and terms", "balance before after delivery net invoice due released on payment", "setup_step", {"step": "money"}, "bal-l"),
    ("Ways clients can pay", "Payments and terms", "payment methods cash check checks card venmo zelle bank transfer", "setup_step", {"step": "money"}, "pay-methods"),
    ("Reschedule fee", "Payments and terms", "reschedule fee cancel move date", "setup_step", {"step": "money"}, "f-reschedule_fee"),
    ("Photos ready within", "Payments and terms", "delivery days turnaround ready how long", "setup_step", {"step": "money"}, "f-delivery_days"),
    ("Quotes good for", "Payments and terms", "quote expires expiry term days hold prices", "setup_step", {"step": "money"}, "f-quote_term_days"),
    ("Travel is free within", "Payments and terms", "travel free miles distance mileage", "setup_step", {"step": "money"}, "f-travel_free"),
    ("Prices", "Prices", "prices fees fee schedule price list rates packages add-ons extra hour", "settings_view", {"tab": "fees"}, "fees"),
    ("Price list terms", "Prices", "price list terms review sample", "settings_view", {"tab": "fees"}, "terms"),
    ("Sales tax rate", "Sales tax", "sales tax rate vat gst exempt local percent", "setup_step", {"step": "tax"}, "f-sales_tax_pct"),
    ("Which documents still print SAMPLE", "Review status", "review reviewed sample draft documents approve", "settings_view", {"tab": "documents"}, "reviews"),
    ("Who owns the photos", "Ownership and law", "copyright license ownership transfer rights", "setup_step", {"step": "documents"}, "rights-l"),
    ("Which law applies", "Ownership and law", "governing law state jurisdiction", "setup_step", {"step": "documents"}, "f-governing_law"),
    ("Agreement wording", "Agreement wording", "agreement contract terms wording", "wording_edit", {"doc": "agreement"}, "w-text"),
    ("Release wording", "Release wording", "release model release wording consent", "wording_edit", {"doc": "release"}, "w-text"),
    ("Copyright transfer wording", "Copyright transfer wording", "copyright transfer wording", "wording_edit", {"doc": "transfer"}, "w-text"),
    ("Photo license wording", "Photo license wording", "license wording usage", "wording_edit", {"doc": "license"}, "w-text"),
    ("Backups: back up now, list, restore check", "Backups", "backup backups back up now restore vault master key", "vault_view", {}, ""),
    ("Where backups go", "Where backups go", "vault backup backups location sftp s3 nas folder disk", "setup_step", {"step": "storage"}, "f-primary_type"),
    ("Second copy of backups", "Where backups go", "backup backups second copy another place offsite", "setup_step", {"step": "storage"}, "f-secondary_type"),
    ("Daily backup time", "Where backups go", "backup backups time schedule nightly daily", "setup_step", {"step": "storage"}, "f-backup_time"),
    ("Online galleries", "Online galleries", "gallery galleries web host address sftp s3 publish", "setup_step", {"step": "galleries"}, "f-gallery_type"),
    ("Gallery days open", "Online galleries", "gallery galleries expire days open close how long", "setup_step", {"step": "galleries"}, "f-gallery_days"),
    ("Studio page: summary for modern search tools (llms.txt)", "Studio page", "llms llms.txt search tools assistants chatbots summary website studio page public site",
     "studio_page", {}, "sp-llms"),
    ("Password, two-factor, recovery codes", "Security", "password two factor 2fa authenticator recovery codes sign in", "security", {}, ""),
    ("Locked-studio notice", "Security", "ntfy notice notification phone push locked restart unlock", "security", {}, "notice"),
    ("Master key", "Security", "master key change recover", "security", {}, ""),
    ("Check your master key", "Security", "master key check test verify paper lost", "master_key_check", {}, ""),
    ("Who can reach this", "Who can reach this", "network allow ip addresses lan", "setup_step", {"step": "network"}, ""),
    ("Export or import settings", "Settings file", "export import settings file json move new machine", "settings_view", {"tab": "file"}, "settings-file"),
    ("Reset database, plugins", "System", "reset wipe database plugins system", "settings_view", {"tab": "system"}, "reset"),
    ("About, license, version", "About", "about license version agpl", "about", {}, ""),
]

TYPE_FIELDS = [
    ("Ask for a gallery codeword", "codeword codewords gallery words password", "f-gallery_codeword"),
    ("Deposit for this type", "deposit retainer", "dep-l"),
    ("Portfolio permission discount", "portfolio discount social media", "folio-l"),
]
TYPE_WORDS = "steps workflow intake questions terms reschedule no-show late outfits wear bring weather delivery deposit indoors who signs"

def _link(url_for, ep, args, anchor):
    if ep == "settings_view" and "tab" not in args:
        args = dict(args, tab="general")
    return url_for(ep, **args) + (f"#{anchor}" if anchor else "")

def entries(types, url_for, help_words=None):

    help_words = help_words or {}
    out = []
    for label, where, extra, ep, args, anchor in ENTRIES:
        try:
            link = _link(url_for, ep, args, anchor)
        except Exception:
            continue
        out.append(dict(text=label, sub=f"Settings, {where}", link=link,
                        words=f"{label} {where} {extra} {help_words.get(anchor, '')}".lower()))
    for key, label in types.items():
        out.append(dict(text=f"{label}: steps, intake and terms", sub="Settings, Session types",
                        link=url_for("type_edit", key=key), words=f"{label} session type {TYPE_WORDS}".lower()))
        for flabel, words, anchor in TYPE_FIELDS:
            out.append(dict(text=f"{flabel}: {label}", sub="Settings, Session types",
                            link=_link(url_for, "type_edit", {"key": key}, anchor),
                            words=f"{flabel} {label} {words} {help_words.get(anchor, '')}".lower()))
    return out

def find(term, types, url_for, help_words=None):

    words = [w for w in term.lower().split() if w]
    if not words:
        return []
    hits = [e_ for e_ in entries(types, url_for, help_words) if all(w in e_["words"] for w in words)]
    hits.sort(key=lambda e_: not whole_words(e_["words"], words))
    return [dict(text=e_["text"], sub=e_["sub"], link=e_["link"]) for e_ in hits[:20]]

def whole_words(text_, words):

    import re
    have = set(re.findall(r"[a-z0-9.]+", text_)) | set(re.findall(r"[a-z0-9]+", text_))
    return all(w in have for w in words)

_HELP = None

def help_words():

    global _HELP
    if _HELP is None:
        import os
        import re
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        pat = re.compile(r'(?:for|id)="([^"{}]+)"[^<]*>[^<{]*(?:<[^>]*>[^<{]*)*?\{\{\s*help\("((?:[^"\\]|\\.)*)"')
        out = {}
        for name in ("settings.html", "setup.html", "type_edit.html"):
            try:
                src = open(os.path.join(root, name), encoding="utf-8").read()
            except OSError:
                continue
            for line in src.splitlines():
                for m in pat.finditer(line):
                    out.setdefault(m.group(1), m.group(2))
        _HELP = out
    return _HELP
