# A database written by 1.5.0

Used by `tests/test_upgrade_from_150.py`. Made by `tools/make_fixture.py --version 1.5.0`, which runs the real 1.5.0
code (commit 48507a6, the commit published as v1.5.0) with 1.5.0's database library, sqlcipher3 0.6.5 (SQLCipher
4.20.0, the version 1.5.0's `requirements.lock` pins), and enters everything through that version's own routes and
functions, signed in as the owner. Making it needs PHP (`php -S` stands in for the web host at its https address). To
make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.5.0

It is the 1.4.1 scenario (see `tests/fixtures/db-1.4.1/README.md`) entered with 1.5.0, plus what 1.5.0 added that the
database keeps. 1.5's own rules apply while it is entered: an old session address opens the client page, a client's
picks coming in move a session at Shot to Delivered (Reyes and Yates are Delivered here, not Shot), the Finances
"to invoice" list counts with the owed one, and money shows in cents. The data key, master key, gallery keys and link
IDs, the studio page's inquiry key, the print studio key, the web host's and the S3 storage's certificates and the S3
server's port are random, so the files change with every run, and so do the print orders' ids; the figures below do
not (except the dates documents were issued, sessions were priced and booked, galleries were built, uploaded, opened,
picked from, delivered, gone dark and put back online, links replaced, orders were collected, ordered and shipped,
certificates were confirmed, the host and the served pages checked, prices approved, the release wording reviewed,
contact ended, and the invoice and receipt year, which come from the day the fixture was made).

Nothing in it depends on how many days pass after it was made: the upgrade test tells a gallery check that the clock
just moved, so it never expires a gallery, and no assertion counts days from the day it was made.

Before publishing a fixture made again, run the publish guard over it: a random size or time can read like a word it
refuses.

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.5.0 database (schema version 9), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `ntfy.json` | The locked-studio notice's plain mirror (server, topic, token, time zone), which the locked app reads |
| `deletions.json` | The deletion list: three entries (a quoted session, Jensen, Doe), signed |
| `doc-numbers.json` | The number marks: invoices (the print invoices in the same run), receipts, quotes, and ids |
| `galleries/` | The gallery build folders, one per g- link ID (`.download` marks a download gallery, `.online` the build that is online) |
| `galleries-kept/` | The app's own copies of the clients' sent picks and drafts (`picks.las`, `draft.las`): Lane's, and Yates's at its new link |
| `host/` | The first gallery host (a local folder): the copy Move galleries left there of Lane's gallery |
| `web/` | The web folder of the web host in use: the galleries in `web/g/`, the studio page's files above them |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `faces/` | Lane's face photo, sealed under the data key |
| `expected.json` | What 1.5.0 showed: everything the 1.4.1 fixture records, and the 1.5.0 settings, the notice's plain file, Account activity, labels, End contact, the check-in, the business client, the 1.5 stage flow's History, the reasons, the replaced quote, the payment note and the trip |

The storage settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them
again exactly as for the 1.4.1 fixture (both gallery hosts at their new place, the web host's certificate pin, the S3
location and its pin). As in the 1.4.1 fixture, a local host is known by its real path, so the test reads a folder
under its own data folder as the same place under the maker's (`data_folder`). Nothing in the data is changed for it.

## What is in it

Everything in the 1.4.1 fixture, entered the same way, with the same clients and ids, and three clients added: Zeller
(client 23, session 25), Quinn (client 24, session 26) and Abbott (client 25, session 27).

What 1.5.0 added that the database keeps:

- **Settings, General**: sign out after 4 idle hours, a check-in wait of 9 months, a mileage rate of 0.70 and a home
  base, saved with the General form sent as shown.
- **The locked-studio notice** (Settings, Security): an ntfy server, topic and token, kept in the encrypted settings
  and mirrored to `ntfy.json`. Account activity lists it, with the master key check and the accounts export.
- **The release wording** edited on the document: saved as the general release wording, and counted as its review.
- **The prices approved**: the current price list approved (it stops printing SAMPLE).
- **The 1.5 stage flow**: Zeller (family) had its quote prepared and marked sent (Quoted), Book it, a 100.00 deposit
  paid in trade (Other, "Two framed prints in trade"), Replace the quote (318.00 canceled and kept) with a line added
  and Book it on the new quote (397.50), Shoot done, and an 18-mile trip at the rate in force (0.70).
- **Do not book and Cancel the booking**: Quinn (an inquiry) was not booked, "They went elsewhere"; Abbott (headshots,
  quoted, booked, nothing paid) had the booking canceled, Other, "Moved out of state".
- **Clients**: the photographer's own labels (Ellis: "Slow to pay", "Prefers text"; Zeller: "Referral"), End contact
  on Foster, a check-in logged for Kim, and Vance ticked as a business client.
- **The version last run**: settings `app_version` is 1.5.0.

## What 1.5.0 showed

Sessions 1 to 24 read as in the 1.4.1 fixture, except Reyes (10) and Yates (23), Delivered under 1.5's rules when
their client's picks came in. Added:

| Session | Stage | Quoted | Paid | Due | Note |
|---|---|---|---|---|---|
| 25 Zeller | Shot | 397.50 | 100.00 | 297.50 | the quote replaced once; the deposit in trade |
| 26 Quinn | Lost | 0.00 | 0.00 | 0.00 | They went elsewhere |
| 27 Abbott | Lost | 159.00 | 0.00 | 159.00 | Other (Moved out of state); not counted as owed |

Finances for 2026: received 3626.20 (family 3232.20, headshot 334.00, event 60.00); refunds -145.00; tips 60.00;
written off 162.00; sales tax due 118.65; by month as in the 1.4.1 fixture except October 1225.00; owed or to invoice:
Kim 200.00 and Zeller 297.50. Print orders as in the 1.4.1 fixture. Every row is in `expected.json`.

After the upgrade to 1.5.1, every record and figure above is the same. The schema stays 9 (no upgrade backup), no
session moves and the stage rules and price approval do not run again; Today says the app was updated until the note
is hidden. 1.5.1's documented changes: the gallery look is on its own Settings page, the mileage rate is set on Payments
and terms only, and each session type shows its icon.
