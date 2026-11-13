"""Settings, findable from the search box: every setting by the words people use for it, and the page it lives on.
Keep this list in step with the Settings pages (setup.html steps, settings.html tabs, type_edit.html, vault, security)."""

# (label, where it lives, extra words to match, endpoint, endpoint arguments)
ENTRIES = [
    ("Business name, your name, brand", "General", "studio photographer company party agreements", "settings_view", {}),
    ("Business email and phone", "General", "contact email phone number picks documents", "settings_view", {}),
    ("Mailing address", "General", "address post po box checks mailed", "settings_view", {}),
    ("Tax ID and bank details", "General", "ein tax id bank transfer account routing invoice", "settings_view", {}),
    ("Mileage rate and home base", "General", "mileage miles irs rate travel home base drive", "settings_view", {}),
    ("Age of majority", "General", "minor adult 18 19 21 guardian parent release", "settings_view", {}),
    ("Logo on documents", "General", "logo brand image picture header", "settings_view", {}),
    ("Credit line", "General", "credit lumen aureum footer", "settings_view", {}),
    ("Delete demo clients", "General", "demo sample clients remove", "settings_view", {}),
    ("Time zone, home location for sunset times", "Business and time zone", "timezone zip latitude longitude sunset golden hour", "setup_step", {"step": "business"}),
    ("What you shoot", "What you shoot", "session types presets weddings portraits families seniors newborn pets headshots real estate products food events", "setup_step", {"step": "shoot"}),
    ("Deposit", "Payments and terms", "deposit retainer hold date booking", "setup_step", {"step": "money"}),
    ("Balance due", "Payments and terms", "balance before after delivery net invoice due", "setup_step", {"step": "money"}),
    ("Ways clients can pay", "Payments and terms", "payment methods cash check card venmo zelle bank transfer", "setup_step", {"step": "money"}),
    ("Reschedule fee", "Payments and terms", "reschedule fee cancel", "setup_step", {"step": "money"}),
    ("Photos ready within (delivery days)", "Payments and terms", "delivery days turnaround ready", "setup_step", {"step": "money"}),
    ("Quotes good for", "Payments and terms", "quote expires expiry term days hold", "setup_step", {"step": "money"}),
    ("Free travel distance", "Payments and terms", "travel free miles distance", "setup_step", {"step": "money"}),
    ("Prices (fee schedule)", "Prices", "prices fees price list rates packages add-ons extra hour", "settings_view", {"tab": "fees"}),
    ("Price list terms", "Prices", "price list terms review sample", "settings_view", {"tab": "fees"}),
    ("Sales tax", "Sales tax", "sales tax rate vat gst exempt local", "setup_step", {"step": "tax"}),
    ("Which documents still print SAMPLE", "Review status", "review reviewed sample draft documents", "settings_view", {"tab": "documents"}),
    ("Who owns the photos (copyright or license)", "Ownership and law", "copyright license ownership transfer rights", "setup_step", {"step": "documents"}),
    ("Which law applies", "Ownership and law", "governing law state jurisdiction", "setup_step", {"step": "documents"}),
    ("Agreement wording", "Agreement wording", "agreement contract terms wording", "wording_edit", {"doc": "agreement"}),
    ("Release wording", "Release wording", "release model release wording consent", "wording_edit", {"doc": "release"}),
    ("Copyright transfer wording", "Copyright transfer wording", "copyright transfer wording", "wording_edit", {"doc": "transfer"}),
    ("Photo license wording", "Photo license wording", "license wording usage", "wording_edit", {"doc": "license"}),
    ("Backups: back up now, list, restore check", "Backups", "backup back up now restore vault master key", "vault_view", {}),
    ("Where backups go, second copy", "Where backups go", "vault backup location second copy sftp s3 nas folder disk", "setup_step", {"step": "storage"}),
    ("Daily backup time", "Where backups go", "backup time schedule nightly", "setup_step", {"step": "storage"}),
    ("SSH key for SFTP", "Where backups go", "ssh key sftp authorized_keys fingerprint trust", "setup_step", {"step": "storage"}),
    ("Online galleries", "Online galleries", "gallery galleries web host address sftp s3 expire days", "setup_step", {"step": "galleries"}),
    ("Password, two-factor, recovery codes", "Security", "password two factor 2fa authenticator recovery codes sign in", "security", {}),
    ("Unattended unlock", "Security", "unlock key file restart unattended", "security", {}),
    ("Master key", "Security", "master key change recover", "security", {}),
    ("Who can reach this", "Who can reach this", "network allow ip addresses lan", "setup_step", {"step": "network"}),
    ("Export or import settings", "Settings file", "export import settings file json move new machine", "settings_view", {"tab": "file"}),
    ("Reset database, plugins", "System", "reset wipe database plugins system", "settings_view", {"tab": "system"}),
    ("About, license, version", "About", "about license version agpl", "about", {}),
]
TYPE_WORDS = "steps workflow intake questions terms reschedule no-show late outfits wear bring weather delivery deposit indoors who signs"


def find(term, types, url_for):
    """Settings matching every word typed. `types` is {key: label} for the session types."""
    words = [w for w in term.lower().split() if w]
    if not words:
        return []
    rows = []
    for label, where, extra, ep, args in ENTRIES:
        hay = f"{label} {where} {extra}".lower()
        if all(w in hay for w in words):
            rows.append(dict(text=label, sub=f"Settings, {where}", link=url_for(ep, **args)))
    for key, label in types.items():
        hay = f"{label} session type {TYPE_WORDS}".lower()
        if all(w in hay for w in words):
            rows.append(dict(text=f"{label}: steps, intake and terms", sub="Settings, Session types", link=url_for("type_edit", key=key)))
    return rows[:20]
