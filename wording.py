import re

from markupsafe import Markup, escape

MAX_LEN = 20000

DISCLAIMER = ("The agreement, release, and other documents that come with Lumen Aureum are examples only. They are not legal advice, and the makers of Lumen Aureum make no promise that they suit your business or hold up where you work. Read them, change them for your business and the laws where you work, and have a lawyer review them before you use them. You are responsible for the documents you give your clients.")
NUMBERED = re.compile(r"\d{1,3}\.\s+")
FIELD = re.compile(r"\[([A-Z][A-Z ]{0,30}[A-Z])\]")

FIELDS = {
    "agreement": [
        ("CLIENT", "who signs: the company, or the person"),
        ("PHOTOGRAPHER", "you, and your business if you have one"),
        ("SESSION", "the kind of session"),
        ("DATE", "the shoot date"),
        ("PRICE", "the total"),
        ("DEPOSIT", "the deposit"),
        ("BALANCE DUE", "when the balance is due"),
        ("PAYMENT", "how clients may pay"),
        ("CANCELLATION", "your cancel and reschedule terms, with the weather plan"),
        ("IF I CANNOT", "what you offer if you can't do the session: a new date, or for a date that can't move, a replacement photographer"),
        ("WHAT I PROVIDE", "the opening of section 5, by kind of session"),
        ("HOW YOU DELIVER", "how the photos reach the client"),
        ("DELIVERY DAYS", "days until the photos are ready"),
        ("THE LINK", "for delivery by download link: how long the link stays open (empty otherwise)"),
        ("HOW LONG", "how long you keep unedited files"),
        ("THE PHOTOS", "who owns the photos, or what the license covers"),
        ("HOW I MAY USE THEM", "your portfolio permission"),
        ("MINORS", "the parent-or-guardian sentence, when a minor is photographed"),
        ("PRODUCTS", "care of products lent for the shoot, for product sessions"),
        ("LAW", "whose law applies"),
        ("RECORDS YEARS", "how many years you keep business records after the last session"),
    ],
    "release": [
        ("PERSON", "who is photographed"),
        ("PHOTOGRAPHER", "you"),
        ("OCCASION", "the session or event"),
        ("EVENT RIGHTS", "the event's ownership terms, for group events"),
        ("HOW THE PHOTOS ARE USED", "delivery, ownership, and portfolio use"),
        ("GUARDIAN", "the parent-or-guardian sentence, when a minor is photographed"),
    ],
}

TRANSFER_FIELDS = [
    ("PHOTOGRAPHER", "you, or your studio"),
    ("FAVORITES", "the portfolio paragraph: up to how many photos you may show (you choose; the client suggests and may say no to any), or that you show none when the client declined"),
]
LICENSE_FIELDS = [
    ("PHOTOGRAPHER", "you, or your studio"),
    ("WHAT THE LICENSE COVERS", "the license use set for the session type"),
    ("LICENSE SCOPE", "term, territory, and exclusivity, when the session sets them"),
    ("PORTFOLIO", "your portfolio permission, by kind of session"),
    ("OTHER USES", "who else may use the photos and what needs your permission"),
    ("PRINT RELEASE", "a print release for personal sessions (empty for business licenses)"),
]

STANDARD = {
    "agreement": """# 1. Who this is between
This agreement is between [CLIENT] ("you") and [PHOTOGRAPHER] ("I" or "me").

# 2. Booking and payment
A deposit, if required, holds your date and is applied to the price. The balance is [BALANCE DUE]. [PAYMENT] Location permits, parking and venue fees are charged at what they cost me and are never discounted.

# 3. Canceling and rescheduling
[CANCELLATION]

If something outside both our control (such as a venue closure, a government order, a natural disaster, a public health emergency, or weather severe enough to make the session unsafe) prevents the session, we move it to a new date within 12 months at no charge. If we cannot agree on a date in that time, I refund what you have paid, less costs I have already paid out for your session, such as permits or a second photographer, which I will list for you. Rain or ordinary bad weather is handled under the weather plan above.

# 4. If I cannot do the session
If I cannot photograph your session because of illness, injury, emergency or equipment failure, [IF I CANNOT]

# 5. Limits of my responsibility
If photos are lost, damaged or not taken, for any reason, or I do not do something this agreement says, I will, at your choice, refund the part of the price that covers what was lost or not done, or reshoot it at no charge if that is possible. In no case will I owe you more than you have paid me under this agreement. This is your only remedy against me. I am not responsible for indirect or consequential losses, such as the cost of re-staging an event, or for emotional distress. This section does not limit my responsibility for harm I cause through gross negligence or on purpose.

# 6. What I provide, and what you receive
[WHAT I PROVIDE] Before delivery I may show you proofs in a private gallery so you can choose favorites. Proofs are for choosing only and are not the delivered photos. You receive the edited photos [HOW YOU DELIVER], within [DELIVERY DAYS] days of the session. [THE LINK] I keep the unedited files and my copies of the edited photos for [HOW LONG] after delivery, then delete them. Copies in my backups are removed as those backups are replaced. If you ask me in writing, I will delete your photos sooner. Please keep your own copies of the photos you receive.

# 7. The photos
[THE PHOTOS]

# 8. How I may use the photos
[HOW I MAY USE THEM]

# 9. Minors
[MINORS]

# 10. Products in my care
[PRODUCTS]

# 11. Safety and locations
You are responsible for your own safety and belongings at each location. Some locations may refuse photography on the day, and we will move to another spot.

# 12. Your information
I keep your contact details, session notes and photos to run your session and my business records. I take care to keep them secure, and the copies in my own systems are encrypted where my software allows. I share them only with the services I use to run my business (such as my web host, email, payment and print lab), and only as much as each needs. At a group event, group photos go to the families in them. I never sell your information. I keep business records for up to [RECORDS YEARS] years after our last session and then delete them, except what I must keep longer for taxes or legal reasons. If you ask, I will tell you what I hold about you, or delete it, except records I need to keep for taxes, legal reasons, or to show what we agreed. Your gallery link works for anyone who has it: keep the letter safe and do not post the link.

# 13. The whole agreement
This agreement and the quote are our whole agreement. Changes are made in writing. The law of [LAW] applies.
""",
    "transfer": """# The photos are yours
[PHOTOGRAPHER] transfers to you all copyright in the final edited photos delivered from this session, effective when the session is paid in full. Proofs, unedited files and photos that are not delivered are not included. You may print, share, publish, sell or change them however you like, with no permission needed.

# Portfolio
[FAVORITES]

# Printing
If a print lab asks whether you may print these photos, show them this page: they belong to you.
""",
    "license": """# Your license
[PHOTOGRAPHER] keeps the copyright. Your license to the final edited photos delivered from this session covers [WHAT THE LICENSE COVERS].[LICENSE SCOPE] Proofs, unedited files and photos that are not delivered are not included.

# Other uses
[OTHER USES]

# Printing
[PRINT RELEASE]

# Portfolio
[PORTFOLIO]
""",
    "release": """# Permission to photograph
I give [PHOTOGRAPHER] permission to photograph [PERSON] at [OCCASION].

# How the photos are used
[EVENT RIGHTS]

[HOW THE PHOTOS ARE USED]

# Parent or guardian
[GUARDIAN]

# My information
The photographer keeps my details only to run the session and its records, and never sells them. I may ask for them to be deleted; the photographer keeps this release and the records the law requires.
""",
}

FREE_SWAPS = [
    ("A deposit, if required, holds your date and is applied to the price. The balance is [BALANCE DUE]. [PAYMENT]",
     "There is nothing to pay for this session: no deposit and no balance."),
    (" If we cannot agree on a date in that time, I refund what you have paid, less costs I have already paid out for your"
     " session, such as permits or a second photographer, which I will list for you.", ""),
    ("If photos are lost, damaged or not taken, for any reason, or I do not do something this agreement says, I will, at"
     " your choice, refund the part of the price that covers what was lost or not done, or reshoot it at no charge if that"
     " is possible. In no case will I owe you more than you have paid me under this agreement. This is your only remedy"
     " against me.",
     "If photos are lost, damaged or not taken, I will reshoot at no charge if that is possible. This is your only remedy"
     " against me."),
]

def free_text(text):

    for old, new in FREE_SWAPS:
        text = text.replace(old, new)
    return text

def render(text, values):

    sections, cur = [], None
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        if lines[0].startswith("# "):
            cur = [lines[0][2:].strip(), []]
            sections.append(cur)
            lines = lines[1:]
        body = "\n".join(lines).strip()
        if body:
            if cur is None:
                cur = [None, []]
                sections.append(cur)
            cur[1].append(body)
    out, n = [], 0
    for head, paras in sections:
        filled = [_fill(p, values) for p in paras]
        filled = [p for p in filled if p.strip()]
        if not filled:
            continue
        if head:
            m = NUMBERED.match(head)
            if m:
                n += 1
                head = f"{n}. {head[m.end():]}"
            out.append(Markup("<h2>%s</h2>") % Markup(_fill(head, values)))
        out += [Markup("<p>%s</p>") % Markup(p.replace("\n", "<br>")) for p in filled]
    return Markup("\n").join(out)

def sections(text, values):

    out, head = [], None
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        if lines[0].startswith("# "):
            head = lines[0][2:].strip()
            lines = lines[1:]
        body = FIELD.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), "\n".join(lines)).strip()
        if body:
            out.append((head or "", body))
            head = None
    return out

def _fill(s, values):
    safe = str(escape(s))
    return FIELD.sub(lambda m: str(escape(values[m.group(1)])) if m.group(1) in values else m.group(0), safe)

def clean(text):

    text = (text or "").replace("\r\n", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    return text[:MAX_LEN].strip()

FIELDS["transfer"] = TRANSFER_FIELDS
FIELDS["license"] = LICENSE_FIELDS
LABELS = {"agreement": "Agreement", "release": "Release", "transfer": "Copyright transfer", "license": "Photo license"}

AGREEMENT_1_3 = """# 1. Who this is between
This agreement is between [CLIENT] ("you") and [PHOTOGRAPHER] ("I" or "me").

# 2. Booking and payment
A deposit, if required, holds your date and is applied to the price. The balance is [BALANCE DUE]. [PAYMENT] Location permits, parking and venue fees are charged at what they cost me and are never discounted.

# 3. Canceling and rescheduling
[CANCELLATION]

If something outside both our control (such as a venue closure, a government order, a natural disaster, a public health emergency, or weather severe enough to make the session unsafe) prevents the session, we move it to a new date within 12 months at no charge. If we cannot agree on a date in that time, I refund what you have paid, less costs I have already paid out for your session, such as permits or a second photographer, which I will list for you. Rain or ordinary bad weather is handled under the weather plan above.

# 4. If I cannot do the session
If I cannot photograph your session because of illness, injury, emergency or equipment failure, [IF I CANNOT]

# 5. Limits of my responsibility
If photos are lost, damaged or not taken, for any reason, or I do not do something this agreement says, I will, at your choice, refund the part of the price that covers what was lost or not done, or reshoot it at no charge if that is possible. In no case will I owe you more than you have paid me under this agreement. This is your only remedy against me. I am not responsible for indirect or consequential losses, such as the cost of re-staging an event, or for emotional distress. This section does not limit my responsibility for harm I cause through gross negligence or on purpose.

# 6. What I provide, and what you receive
[WHAT I PROVIDE] You receive the edited photos [HOW YOU DELIVER], within [DELIVERY DAYS] days of the session. [THE LINK] I keep the unedited files and my copies of the edited photos for [HOW LONG] after delivery, then delete them. Copies in my backups are removed as those backups are replaced. If you ask me in writing, I will delete your photos sooner. Please keep your own copies of the photos you receive.

# 7. The photos
[THE PHOTOS]

# 8. How I may use the photos
[HOW I MAY USE THEM]

# 9. Minors
[MINORS]

# 10. Products in my care
[PRODUCTS]

# 11. Safety and locations
You are responsible for your own safety and belongings at each location. Some locations may refuse photography on the day, and we will move to another spot.

# 12. Your information
I keep your contact details, session notes and photos to run your session and my business records. I keep them secure, and the copies in my own systems are encrypted where my software allows. I share them only with the services I use to run my business (such as my gallery host, email, payment and print lab), and only as much as each needs. At a group event, group photos go to the families in them. I never sell your information. I keep business records for up to [RECORDS YEARS] years after our last session, then delete them. If you ask, I will tell you what I hold about you, or delete it, except records I must keep for taxes. Your gallery link works for anyone who has it: keep the letter safe and do not post the link.

# 13. The whole agreement
This agreement and the quote are our whole agreement. Changes are made in writing. The law of [LAW] applies.
"""
assert AGREEMENT_1_3 != STANDARD["agreement"]
