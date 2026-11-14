# A database written by 1.1.0

Used by `tests/test_upgrade_from_110.py`. Made by `tools/make_fixture.py --version 1.1.0`, which runs the real 1.1.0
code (tag v1.1.0, commit a821c8f, in a temporary git worktree) and enters everything through that version's own routes
and functions, signed in as the owner. To make it again:

    python tools/make_fixture.py --version 1.1.0

It is the 1.0.1 scenario (see `tests/fixtures/db-1.0.1/README.md`) entered with 1.1.0, plus what 1.1.0 added. The data
key and master key are random, so the files change with every run; the numbers below do not (except the dates
documents were issued, the gallery was built, uploaded, opened, and picked from, and the invoice and receipt year,
which come from the day the fixture was made, and the gallery id and keys, which are random).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.1.0 database (schema version 4), encrypted under the data key |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: one entry, signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `expected.json` | What 1.1.0 showed: records, balances, Finances, numbers, document words, the gallery, settings |

The gallery's files and the local gallery host folder are not kept; they were removed with the maker's temporary data
folder. The database rows that describe them are what the upgrade test checks.

## What is in it

The 1.0.1 basics, entered the same way: time zone America/New_York; Lane (1), Ortiz (2), and the event families Bell
(3) and Chen (4); Lane's family session 1 with invoice INV-2026-0001 (issued with only the deposit paid, marked sent),
the balance, a 40.00 refund credit, and receipt R-2026-0001; Ortiz's Booked session 2 (draft invoice), inquiry 3, and
mini session 4 quoted as quote 2 and deleted; the Spring Recital with Bell (session 5, paid, draft receipt) and Chen
(session 6, invoice INV-2026-0002 issued). Doe (client 6, session 8) was made last and deleted.

What 1.1.0 added:

- **Who owns the photos** (setup, Documents): Family is a transfer type, Headshots a licensed type.
- **Gallery look** (Settings, Client galleries): font Elegant, the dark background #262422, a tiled proof mark reading
  SAMPLE at strength 35.
- **Logos**: a logo for documents and a logo for dark gallery backgrounds, both uploaded through Settings.
- **A gallery online**: Lane's gallery (codeword "amber harbor willow") built from three photos and uploaded to a local
  gallery host. The client opened it (the gallery's first-visit stamp), sent picks for one photo (retouch,
  "soften the light"), then kept picking: a draft of three photos. The gallery check stored the picks and the
  still-picking count and time (`draft_n` 3, `draft_at`), so the session shows "Client is still picking (3 photos
  marked)".
- **Booked hints**: Price (client 5), headshot session 7, quoted 175.00 (quote 3) and Booked with deposit and
  agreement open, then the agreement marked signed: "deposit still open". Ortiz's session 2 shows "deposit and
  agreement still open"; Bell and Chen show "agreement still open".
- **Issued documents** kept as 1.1.0 kept them: values only (no `html` column). `expected.json` holds the words of each
  issued invoice and receipt as 1.1.0 showed them (`issued_text`).

## What 1.1.0 showed

| Session | Quoted | Paid | Refund credit | Due | Invoice | Receipt | Licensed |
|---|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 40.00 | 0.00 | INV-2026-0001 (as issued: 100.00 paid) | R-2026-0001 | no |
| 2 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | draft | none | no |
| 3 Ortiz inquiry | 0.00 | 0.00 | 0.00 | 0.00 | none | none | no |
| 5 Bell | 60.00 | 60.00 | 0.00 | 0.00 | none | draft | no |
| 6 Chen | 60.00 | 0.00 | 0.00 | 60.00 | INV-2026-0002 | none | no |
| 7 Price | 175.00 | 0.00 | 0.00 | 175.00 | none | none | yes |

Finances for 2026: received 445.00 (family 385.00, event 60.00); refunds -40.00; sales tax due 0.00; by month
February 100.00, March 250.00, April -40.00, May 135.00; nothing overdue; upcoming balances: session 2 175.00,
session 6 60.00, session 7 175.00.

After the upgrade (schema 4 to 5, with a backup first), every figure above is the same; each issued document is kept
whole at the first sign-in, with the words 1.1.0 showed; the drafts are still unnumbered (the next invoice is
INV-<year>-0003, the next receipt R-<year>-0002); the next quote is 4, the next client 7 and session 9; the gallery,
its picks, and its still-picking line are as 1.1.0 kept them; the look settings, logos, ownership per type, and
Booked hints are unchanged; and the deletion still verifies.
