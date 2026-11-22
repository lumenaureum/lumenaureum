GROUPS = [
    dict(id="studio", title="Studio", pages=[
        dict(id="profile", label="Profile", sub="names, contact, time zone",
             what="Your names and how clients reach you. They print on quotes, invoices and agreements.",
             ep="settings_view", args={"tab": "profile"},
             match=[("settings_view", {"tab": "general"}), ("setup_step", {"step": "business"})],
             fields=[
                 dict(key="photographer_name", label="Your name", anchor="f-photographer_name",
                      words="photographer owner name signature sign check-in"),
                 dict(key="studio_name", label="Brand name", anchor="f-studio_name",
                      words="studio brand company trading name"),
                 dict(key="business_name", label="Business name on agreements", anchor="f-business_name",
                      words="business legal company party agreements llc"),
                 dict(key="business_email", label="Business email", anchor="f-business_email",
                      words="contact email address picks documents printed top quotes invoices"),
                 dict(key="business_phone", label="Business phone", anchor="f-business_phone",
                      words="contact phone number telephone cell"),
                 dict(key="business_website", label="Website", anchor="f-business_website",
                      words="website site web address url link homepage"),
                 dict(key="business_address", label="Mailing address", anchor="f-business_address",
                      words="address post po box checks mailed mail street"),
                 dict(key="business_tax_id", label="Tax ID", anchor="f-business_tax_id",
                      words="ein tax id taxpayer number invoice printed"),
                 dict(key="timezone", label="Time zone", anchor="f-timezone",
                      words="timezone time zone clock dates deadlines location"),
                 dict(key="home_zip", label="Home ZIP code", anchor="f-home_zip",
                      words="home base zip location sunset golden hour light where i work from travel distance start"),
                 dict(key="home_lat", label="Home latitude and longitude", anchor="f-home_zip", input="home_lat",
                      words="latitude longitude coordinates gps home location"),
                 dict(key="home_lon", label="Home longitude", anchor="f-home_zip", input="home_lon", search=False),

                 dict(key="home_base", label="Home base (old)", anchor="", input=None, search=False),
             ]),
        dict(id="branding", label="Branding", sub="logo, gallery font, proof mark",
             what="How your documents and client galleries look.",
             ep="settings_view", args={"tab": "branding"},
             match=[("settings_view", {"tab": "gallery"})],
             fields=[
                 dict(key="logo_png", label="Logo on documents", anchor="logo", input="logo",
                      words="logo brand image picture header documents quotes invoices"),
                 dict(key="logo_dark_png", label="Logo for dark backgrounds", anchor="gl-dark", input="logo",
                      words="logo dark white black charcoal gallery background"),
                 dict(key="gallery_font", label="Gallery font", anchor="gl-font-l",
                      words="gallery font typeface script elegant classic look"),
                 dict(key="brand_font_400", label="Your own font", anchor="own-font", input="regular",
                      words="font upload own woff ttf otf typeface brand"),
                 dict(key="brand_font_300", label="Your own font, light weight", anchor="own-font", input="light",
                      search=False),
                 dict(key="brand_font_name", label="Your own font's name", anchor="own-font", input="regular", search=False),
                 dict(key="gallery_bg", label="Gallery background", anchor="gl-bg-l",
                      words="gallery background color ivory white charcoal black look"),
                 dict(key="proof_mark", label="Proof mark", anchor="gl-mark-l",
                      words="proof mark watermark proofs corner tiled logo"),
                 dict(key="proof_mark_text", label="Proof mark word", anchor="gl-word", search=False),
                 dict(key="proof_mark_strength", label="Proof mark strength", anchor="gl-str", search=False),
                 dict(key="credit_off", label="Credit line", anchor="f-credit", input="credit",
                      words="credit lumen aureum footer made with"),
             ]),
        dict(id="clients", label="Clients", sub="check-ins, labels, age of majority",
             what="How clients are kept and grouped: check-in timing, labels, and who counts as a minor.",
             ep="settings_view", args={"tab": "clients"},
             fields=[
                 dict(key="check_in_snooze_months", label="Months until a checked-in client is due again",
                      anchor="f-check_in_snooze_months",
                      words="check in checked snooze months clients repeat remind reach out time to check in"),
                 dict(key="age_majority", label="Age of majority", anchor="f-age_majority",
                      words="minor adult 18 19 21 guardian parent release signs"),
                 dict(key=None, label="Client labels: rename or merge", anchor="labels", input=None,
                      words="label labels tag tags rename merge combine clients group"),
                 dict(key=None, label="Delete demo clients", anchor="demo", input=None, optional=True,
                      words="demo sample clients remove delete"),
             ]),
    ]),
    dict(id="sessions", title="Sessions", pages=[
        dict(id="shoot", label="What you shoot", sub="your session types",
             what="The kinds of sessions you offer. Each type has its own steps, intake and terms.",
             ep="setup_step", args={"step": "shoot"},
             fields=[
                 dict(key="TYPES", label="What you shoot: add or remove session types", anchor="types", input="preset_",
                      words="session types presets add type weddings portraits families seniors newborn pets headshots "
                            "real estate products food events"),
             ]),
        dict(id="prices", label="Prices", sub="price list, price list terms",
             what="Your price list, and the terms printed on it.",
             ep="settings_view", args={"tab": "prices"},
             match=[("settings_view", {"tab": "fees"})],
             fields=[
                 dict(key=None, label="Prices", anchor="fees", input="new_label",
                      words="prices fees fee schedule price list rates packages add-ons extra hour"),
                 dict(key=None, label="Price list terms", anchor="terms", input=None,
                      words="price list terms review sample"),
             ]),
        dict(id="prints", label="Print orders", sub="print prices, shipping, lab", plugin="prints",
             what="Prints clients order from their download galleries.",
             ep="prints_settings", args={},
             fields=[
                 dict(key=None, label="Print orders: turn on, where orders show up", anchor="", input=None,
                      words="print prints print orders order photos lab plugin turn on off where"),
                 dict(key=None, label="Print prices, shipping and lab", anchor="", input=None,
                      words="print prints price prices price list lab cost shipping markup order orders sizes"),
             ]),
    ]),
    dict(id="money", title="Money", pages=[
        dict(id="payments", label="Payments and terms", sub="deposits, due dates, ways to pay, travel",
             what="Deposits, when the balance is due, how clients pay, and what travel costs.",
             ep="setup_step", args={"step": "money"},
             fields=[
                 dict(key="DEPOSIT_AMOUNT", label="Deposit to hold a date", anchor="f-deposit", input="deposit",
                      words="deposit retainer hold date booking book"),
                 dict(key="EVENT_DEPOSIT_PCT", label="Deposit for events", anchor="f-event_pct", input="event_pct",
                      words="deposit percent events group percentage"),
                 dict(key="NET_TERMS_TYPES", label="Balance due", anchor="bal-l", input="before_days",
                      words="balance before after delivery net invoice due released on payment"),
                 dict(key="quote_term_days", label="Quotes good for", anchor="f-quote_term_days",
                      words="quote expires expiry term days hold prices"),
                 dict(key="DELIVERY_DAYS", label="Photos ready within", anchor="f-delivery_days", input="delivery_days",
                      words="delivery days turnaround ready how long"),
                 dict(key="RESCHEDULE_FEE", label="Reschedule fee", anchor="f-reschedule_fee", input="reschedule_fee",
                      words="reschedule fee cancel move date"),
                 dict(key="PAYMENT_METHODS", label="Ways clients can pay", anchor="pay-methods", input="pay_other",
                      words="payment methods cash check checks card venmo zelle bank transfer"),
                 dict(key="pay_details", label="Where clients pay you", anchor="f-pay_details",
                      words="venmo zelle card payment link pay checks payable"),
                 dict(key="bank_details", label="Bank details for transfers", anchor="f-bank_details",
                      words="bank transfer account routing wire ach invoice"),
                 dict(key="TRAVEL_FREE_MILES", label="Travel is free within", anchor="f-travel_free", input="travel_free",
                      words="travel free miles distance mileage"),
                 dict(key="mileage_rate", label="Mileage rate", anchor="f-mileage_rate",
                      words="mileage miles irs rate travel drive driving trips cents per mile"),
             ]),
        dict(id="tax", label="Sales tax", sub="your rate, what is taxed",
             what="Whether you charge sales tax, and at what rate.",
             ep="setup_step", args={"step": "tax"},
             fields=[
                 dict(key="tax_mode", label="Sales tax", anchor="tax-mode", words="sales tax none flat regional vat gst exempt"),
                 dict(key="sales_tax_pct", label="Sales tax rate", anchor="f-sales_tax_pct",
                      words="sales tax rate vat gst exempt percent"),
                 dict(key="local_tax_pct", label="Local sales tax rate", anchor="f-local_tax_pct", optional=True,
                      words="local sales tax rate city county percent"),
             ]),
    ]),
    dict(id="documents", title="Documents", pages=[
        dict(id="reviews", label="Review status", sub="what still prints SAMPLE",
             what="Which documents you have reviewed, and which still print SAMPLE.",
             ep="settings_view", args={"tab": "documents"},
             fields=[dict(key="doc_reviews", label="Which documents still print SAMPLE", anchor="reviews", input=None,
                          words="review reviewed sample draft documents approve")]),
        dict(id="law", label="Ownership and law", sub="who owns the photos, which law applies",
             what="Who owns the photos once paid, and which law your agreements name.",
             ep="setup_step", args={"step": "documents"},
             fields=[
                 dict(key="LICENSE_TYPES", label="Who owns the photos", anchor="rights-l", input="rights_",
                      words="copyright license ownership transfer rights"),
                 dict(key="governing_law", label="Which law applies", anchor="f-governing_law",
                      words="governing law state jurisdiction"),
             ]),
        dict(id="agreement", label="Agreement wording", sub="", what="", ep="wording_edit", args={"doc": "agreement"},
             fields=[dict(key="agreement_text", label="Agreement wording", anchor="w-text", input="text",
                          words="agreement contract terms wording")]),
        dict(id="release", label="Release wording", sub="", what="", ep="wording_edit", args={"doc": "release"},
             fields=[dict(key="release_text", label="Release wording", anchor="w-text", input="text",
                          words="release model release wording consent")]),
        dict(id="transfer", label="Copyright transfer wording", sub="", what="", ep="wording_edit",
             args={"doc": "transfer"}, show="transfer",
             fields=[dict(key="transfer_text", label="Copyright transfer wording", anchor="w-text", input="text",
                          words="copyright transfer wording")]),
        dict(id="license", label="Photo license wording", sub="", what="", ep="wording_edit", args={"doc": "license"},
             fields=[dict(key="license_text", label="Photo license wording", anchor="w-text", input="text",
                          words="license wording usage")]),
    ]),
    dict(id="web", title="Galleries and website", pages=[
        dict(id="galleries", label="Online galleries", sub="web host, how long galleries stay up",
             what="Where client galleries are published, and how long they stay up.",
             ep="setup_step", args={"step": "galleries"},
             fields=[
                 dict(key=None, label="Web host for galleries", anchor="f-gallery_type", input=None,
                      words="gallery galleries web host sftp s3 publish server"),

                 dict(key="gallery_base_url", label="Gallery address", anchor="f-gallery_type", input=None,
                      words="gallery address web address url https link"),
                 dict(key="GALLERY_DAYS", label="Gallery days open", anchor="f-gallery_days", input="gallery_days",
                      words="gallery galleries expire days open close how long"),
                 dict(key="GALLERY_UNOPENED_DAYS", label="A gallery nobody opens comes down after", anchor="f-unopened_days",
                      input="unopened_days", words="gallery unopened never opened days"),
                 dict(key=None, label="Move galleries to a new web host", anchor="move", input=None, optional=True,
                      words="move galleries new web host earlier host migrate"),
                 dict(key=None, label="The web host's certificate", anchor="webcert", input=None, optional=True,
                      words="certificate tls ssl https self-signed fingerprint trust"),
             ]),
        dict(id="studio_page", label="Studio page", sub="your public website", plugin="studio_page",
             what="A small public website for your studio.",
             ep="studio_page", args={},
             fields=[dict(key=None, label="Studio page: summary for modern search tools (llms.txt)", anchor="sp-llms",
                          input=None, optional=True, words="llms llms.txt search tools assistants chatbots summary website studio page "
                                            "public site")]),
    ]),
    dict(id="data", title="Data and backups", pages=[
        dict(id="backups", label="Backups", sub="where they go, back up now, list",
             what="Where backups go, when they run, and the backups made so far.",
             ep="vault_view", args={}, match=[("setup_step", {"step": "storage"})],
             fields=[
                 dict(key=None, label="Backups: back up now, list, restore check", anchor="", input=None,
                      words="backup backups back up now restore vault master key"),
                 dict(key=None, label="Where backups go", anchor="f-primary_type", input=None,
                      words="vault backup backups location sftp s3 nas folder disk destinations"),
                 dict(key=None, label="Second copy of backups", anchor="f-secondary_type", input=None,
                      words="backup backups second copy another place offsite"),
                 dict(key="backup_time", label="Daily backup time", anchor="f-backup_time",
                      words="backup backups time schedule nightly daily"),
             ]),
        dict(id="file", label="Settings file", sub="export, import",
             what="All your settings in one file, to set up another install or keep a copy.",
             ep="settings_view", args={"tab": "file"},
             fields=[dict(key=None, label="Export or import settings", anchor="settings-file", input="file",
                          words="export import settings file json move new machine")]),
    ]),
    dict(id="access", title="Security and access", pages=[
        dict(id="security", label="Security", sub="password, two-factor, master key",
             what="Your password, recovery codes, master key, and who has signed in.",
             ep="security", args={}, match=[("master_key_check", {})],
             fields=[
                 dict(key=None, label="Password, two-factor, recovery codes", anchor="password", input="password",
                      words="password two factor 2fa authenticator recovery codes sign in"),
                 dict(key=None, label="Master key", anchor="master-key", input=None,
                      words="master key change recover"),
                 dict(key=None, label="Check your master key", anchor="master-key", input=None,
                      words="master key check test verify paper lost"),
                 dict(key="idle_signout_hours", label="Sign out when idle", anchor="f-idle_signout_hours",
                      words="idle sign out signed log out logout timeout inactive security hours session"),
                 dict(key=None, label="Sign out every other device", anchor="devices", input=None,
                      words="sign out devices other phones sessions"),
                 dict(key=None, label="Account activity", anchor="activity", input=None, optional=True,
                      words="activity sign-ins log history audit"),
             ]),
        dict(id="notifications", label="Notifications", sub="the locked-studio notice",
             what="A message to your phone when the studio restarts and stays locked.",
             ep="settings_view", args={"tab": "notifications"},
             fields=[dict(key="ntfy_server", label="Locked-studio notice", anchor="notice", input=None,
                          words="ntfy notice notification notifications phone push locked restart unlock topic inbox new inquiry picks print order")]),
        dict(id="network", label="Who can reach this", sub="the addresses that can connect",
             what="Which devices can open Lumen Aureum.",
             ep="setup_step", args={"step": "network"},
             fields=[dict(key=None, label="Who can reach this", anchor="net-detail", input=None,
                          words="network allow ip addresses lan reach connect")]),
    ]),
    dict(id="app", title="App", pages=[
        dict(id="plugins", label="Plugins", sub="turn parts on or off",
             what="Parts of Lumen Aureum you can turn on or off.",
             ep="plugins_settings", args={},
             fields=[dict(key=None, label="Plugins: turn parts on or off", anchor="", input="name",
                          words="plugin plugins parts features optional turn on off switch")]),
        dict(id="system", label="System", sub="database, reset",
             what="Where the database is, and starting over.",
             ep="settings_view", args={"tab": "system"},
             fields=[dict(key=None, label="Reset database", anchor="reset", input="confirm",
                          words="reset wipe database plugins system start over")]),
        dict(id="about", label="About", sub="version, license", what="", ep="about", args={},
             fields=[dict(key=None, label="About, license, version", anchor="", input=None,
                          words="about license version agpl source")]),
    ]),
]

ELSEWHERE = [
    dict(label="Adopt a gallery already online", where="A session's page, under More",
         words="adopt adoption existing gallery import online already link", ep="sessions", args={}, anchor=""),
    dict(label="Close a gallery now, or change its closing day", where="A session's page, Gallery",
         words="close closing day sunset early end take down expire gallery shorten", ep="sessions", args={}, anchor=""),
]

OLD_TABS = {"general": "profile", "gallery": "branding", "fees": "prices"}

def pages():

    for g_ in GROUPS:
        for p_ in g_["pages"]:
            yield dict(p_, group=g_["title"])

def page(pid):
    return next((p_ for p_ in pages() if p_["id"] == pid), None)

def fields():

    for p_ in pages():
        for f_ in p_.get("fields", []):
            yield dict(f_, page=p_)

def field_input(f_):

    return f_["key"] if f_.get("input", "") == "" else f_["input"]

def matches(p_, ep, view_args, args):

    def ok(want_ep, want):
        if ep != want_ep:
            return False
        return all(str((view_args or {}).get(k_, (args or {}).get(k_, ""))) == str(v_) for k_, v_ in want.items())
    if p_.get("plugin") and ep == "plugin_page" and (view_args or {}).get("name") == p_["plugin"]:
        return True
    if ok(p_["ep"], p_["args"]):
        return True
    return any(ok(e_, a_) for e_, a_ in p_.get("match", []))

def current(ep, view_args, args):

    return next((p_ for p_ in pages() if matches(p_, ep, view_args, args)), None)

def by_anchor():

    out = {}
    for f_ in fields():
        if f_.get("anchor"):
            out.setdefault(f_["anchor"], f_["page"]["id"])
    return out

def entries():

    out = []
    for p_ in pages():
        ep_, args_ = (("plugin_page", {"name": p_["plugin"]}) if p_.get("plugin") else (p_["ep"], dict(p_["args"])))
        where = p_["label"]
        labels = {f_["label"] for f_ in p_.get("fields", [])}
        if p_["label"] not in labels:
            out.append((p_["label"], where, f"{p_['group']} {p_.get('sub', '')} {p_.get('what', '')}", ep_, args_, ""))
        for f_ in p_.get("fields", []):
            if f_.get("search") is False:
                continue
            out.append((f_["label"], where, f_.get("words", ""), ep_, args_, f_.get("anchor", "")))
    for e_ in ELSEWHERE:
        out.append((e_["label"], e_["where"], e_["words"], e_["ep"], e_["args"], e_["anchor"]))
    return out
