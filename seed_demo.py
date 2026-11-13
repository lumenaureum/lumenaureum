#!/usr/bin/env python3
"""Load made-up demo clients (all flagged demo=1; delete them from Settings)."""
from datetime import date, timedelta
import app as A
import datakey

if not datakey.is_unlocked():          # run from the command line: unlock like the web app does
    import getpass
    _k = datakey.unwrap_password(getpass.getpass("Password: "))
    if not _k:
        raise SystemExit("That password doesn't open this install.")
    datakey.set_unlocked(_k)
    A.on_unlock()

T = date.today()
D = lambda n: (T + timedelta(days=n)).isoformat()

with A.app.app_context():
    q, x = A.q, A.x
    if q("SELECT COUNT(*) n FROM households WHERE demo=1", one=True)["n"]:
        raise SystemExit("demo data already loaded")

    def household(name, source="", **kw):
        return x("INSERT INTO households (name, source, address, demo) VALUES (?,?,?,1)",
                 (name, source, kw.get("address", "")))

    REL = {"Dana Harlow": ("mother", "F", 46), "Ava Harlow": ("daughter", "F", None), "Chidi Okafor": ("father", "M", 44),
           "Ngozi Okafor": ("daughter", "F", None), "Tobe Okafor": ("son", "M", 12), "Mara Brennan": ("self", "F", 38),
           "Rosa Castellano": ("mother", "F", 49), "Leo Castellano": ("son", "M", None), "Gil Navarro": ("self", "M", 61),
           "J. Pryor": ("self", "", None), "Kate Duffy": ("mother", "F", 40), "Owen": ("son", "M", 15), "Ana Ruiz": ("mother", "F", 42),
           "Sofia": ("daughter", "F", 15), "Mateo": ("son", "M", 15), "Ewa Kowalski": ("mother", "F", 45), "Jan": ("son", "M", 16),
           "Tom Bell": ("father", "M", 47), "Grace": ("daughter", "F", 15)}

    def person(hid, name, role="adult", minor=0, grad=None, email="", phone=""):
        rel, g_, age = REL.get(name, ("", "", None))
        return x("""INSERT INTO people (household_id, name, role, is_minor, grad_year, email, phone, relation, gender, birth_year)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (hid, name, role, minor, grad, email, phone, rel, g_, (T.year - age) if age else None))

    C_ = A.C

    def pick(*want, kind="portrait"):
        """A session type this install has: the first wanted key, else the first of the wanted kind."""
        for w in want:
            if w in C_.SESSION_TYPES:
                return w
        return next((k for k, kd in C_.TYPE_KIND.items() if kd == kind), next(iter(C_.SESSION_TYPES)))

    def finish_tasks_before(sid, day):
        x("UPDATE tasks SET done=due WHERE session_id=? AND due < ?", (sid, day))

    # booked portrait, shoot in 5 days
    h = household("The Harlows", "friend", address="12 Example Lane\nSampletown 00000")
    dana = person(h, "Dana Harlow", "parent", email="dana@example.com", phone="555-0101")
    ava = person(h, "Ava Harlow", "child", 1, 2027)
    s = A.create_session(h, pick("senior", "portrait"), "booked", "Golden hour, three looks", D(5), subject_ids=[ava])
    x("UPDATE sessions SET start_time='16:10', stops=? WHERE id=?",
      ("Riverside Park - north trail\nOld Mill Plaza\nHilltop overlook (finale)", s))
    x("UPDATE sessions SET intake=? WHERE id=?", ('{"school": "Sample High, 2027", "interests": "soccer, books, flowers", "looks": "nature, city", "outfits": "3", "must": "with her bike", "avoid": "", "props": "dog (Biscuit)", "who": "Ava, mom"}', s))
    x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (s, "Portrait session", 450))
    x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)", (s, "deposit", 150, D(-9), "check"))
    x("UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=? AND kind='agreement'", (D(-10), D(-9), s))
    finish_tasks_before(s, D(-1))
    x("UPDATE tasks SET done=? WHERE session_id=? AND stage IN ('inquiry','quoted')", (D(-10), s))
    x("UPDATE tasks SET done=NULL WHERE session_id=? AND title LIKE 'Send prep guide'", (s,))
    x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)", (h, s, D(-12), "email", "Asked about portraits after seeing a friend's photos"))
    x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)", (h, s, D(-9), "call", "Booked. Wants the bike and the dog in a few frames"))

    # quoted family
    h2 = household("The Okafors", "referral")
    x("UPDATE households SET referred_by=? WHERE id=?", (h, h2))
    person(h2, "Chidi Okafor", "parent", email="chidi@example.com")
    kids = [person(h2, "Ngozi Okafor", "child", 1, 2028), person(h2, "Tobe Okafor", "child", 1, 2031)]
    s2 = A.create_session(h2, pick("family", "portrait"), "quoted", "Fall family", D(13), subject_ids=kids)
    x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (s2, "Family session", 375))
    x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (s2, "Park permit", 50))
    x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)", (h2, s2, D(-3), "email", "Sent quote, Harlows referred them"))
    x("UPDATE tasks SET done=? WHERE session_id=? AND stage IN ('inquiry','quoted') AND title != 'Plan stops, check permits'", (D(-3), s2))

    # quiet inquiry
    h3 = household("Mara Brennan", "saw a print")
    m = person(h3, "Mara Brennan", email="mara@example.com")
    s3 = A.create_session(h3, pick("portrait", "family"), "inquiry", "Asked about a portrait session", subject_ids=[m])
    x("UPDATE sessions SET created=? WHERE id=?", (D(-8) + " 10:00:00", s3))
    x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)", (h3, s3, D(-8), "text", "Wants new photos for her studio's website, in spring"))

    # delivered portrait, gallery live, balance owed
    h4 = household("The Castellanos", "referral", address="40 Sample Road\nExampleville 00000")
    person(h4, "Rosa Castellano", "parent", email="rosa@example.com")
    leo = person(h4, "Leo Castellano", "child", 1, 2027)
    s4 = A.create_session(h4, pick("senior", "portrait"), "delivered", "", D(-14), subject_ids=[leo])
    x("UPDATE sessions SET start_time='16:30', stops=?, gallery_built=?, letter_mailed=?, first_visit=? WHERE id=?",
      ("Lakeside gardens\nStone bridge\nWest lawn (finale)", D(-6), D(-6), D(-4), s4))
    x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (s4, "Portrait session", 450))
    x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)", (s4, "deposit", 150, D(-30), "check"))
    x("INSERT INTO expenses (session_id, date, label, amount, miles) VALUES (?,?,?,?,?)", (s4, D(-14), "Drive, three stops", 0, 31))
    x("UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=?", (D(-30), D(-29), s4))
    finish_tasks_before(s4, D(0))
    x("UPDATE tasks SET done=? WHERE session_id=? AND stage IN ('inquiry','quoted')", (D(-30), s4))

    # closed personal-brand shoot
    h5 = household("Gil Navarro", "website")
    g = person(h5, "Gil Navarro", email="gil@example.com")
    s5 = A.create_session(h5, pick("portrait", "family"), "closed", "Headshots at his workshop", D(-40), subject_ids=[g])
    x("UPDATE sessions SET stops=?, sd_shipped=?, favorites=? WHERE id=?", ("His workshop\nThe street outside", D(-28), "#0412, #0587", s5))
    x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (s5, "Portrait session", 300))
    x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)", (s5, "balance", 300, D(-30), "cash"))
    x("INSERT INTO expenses (session_id, date, label, amount, miles) VALUES (?,?,?,?,?)", (s5, D(-40), "Parking", 15, 44))
    x("UPDATE documents SET status='signed', signed=? WHERE session_id=?", (D(-41), s5))
    x("UPDATE tasks SET done=due WHERE session_id=?", (s5,))

    # lost
    h6 = household("Pryor Realty", "cold email")
    pr = person(h6, "J. Pryor")
    s6 = A.create_session(h6, pick("realestate", "product", kind="property") if "property" in C_.TYPE_KIND.values() else pick("portrait"), "lost", "Listing photos", subject_ids=[pr])
    x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)", (h6, s6, D(-20), "email", "Went with their usual photographer"))

    # group event
    e = x("INSERT INTO events (name, date, venue, demo) VALUES (?,?,?,1)", ("Spring team photo day", D(12), "Maple Grove Club"))
    for fam, kids_, parent, signed in [("The Duffys", ["Owen"], "Kate Duffy", True), ("The Ruiz family", ["Sofia", "Mateo"], "Ana Ruiz", True),
                                       ("The Kowalskis", ["Jan"], "Ewa Kowalski", False), ("The Bells", ["Grace"], "Tom Bell", False)]:
        hh = household(fam, "Spring team photo day")
        person(hh, parent, "parent", phone="555-01" + str(20 + len(kids_) * 7 + len(fam) % 9))
        ids = [person(hh, k, "child", 1) for k in kids_]
        ss = A.create_session(hh, C_.EVENT_TYPE, "booked", "Spring team photo day", D(12), event_id=e, subject_ids=ids)
        if signed:
            x("UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=?", (D(-5), D(-3), ss))
        else:
            x("UPDATE documents SET status='sent', sent=? WHERE session_id=?", (D(-5), ss))
        finish_tasks_before(ss, D(0))

    x("UPDATE tasks SET done=? WHERE event_id=? AND title=?", (D(-3), e, "Confirm the venue's photo rules"))
    x("UPDATE events SET notes=? WHERE id=?", ("Games start at 10:00. Organizer: the club office. Set up by the clubhouse.", e))
    x("INSERT INTO settings (key, value) VALUES ('mileage_rate', '') ON CONFLICT(key) DO NOTHING")
    x("INSERT INTO settings (key, value) VALUES ('theme', 'dark') ON CONFLICT(key) DO NOTHING")
    print("demo loaded")

