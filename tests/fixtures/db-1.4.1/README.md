# A database written by 1.4.1

Used by `tests/test_upgrade_from_141.py`. Made by `tools/make_fixture.py --version 1.4.1`, which runs the real 1.4.1
code (commit 879c2b0, the commit published as v1.4.1) with 1.4.1's database library, sqlcipher3 0.6.5 (SQLCipher
4.20.0, the version 1.4.1's `requirements.lock` pins), and enters everything through that version's own routes and
functions, signed in as the owner. Making it needs PHP (`php -S` stands in for the web host at its https address). To
make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.4.1

It is the 1.4.0 scenario (see `tests/fixtures/db-1.4.0/README.md`) entered with 1.4.1, plus what 1.4.1 added that the
database keeps. The data key, master key, gallery keys and link IDs, the studio page's inquiry key, the print studio key,
the web host's and the S3 storage's certificates and the S3 server's port are random, so the files change with every
run, and so do the print orders' ids; the figures below do not (except the dates documents were issued, sessions were
priced and booked, galleries were built, uploaded, opened, picked from, delivered, gone dark and put back online, links
replaced, orders were collected, ordered and shipped, certificates were confirmed, the host and the served pages checked,
and the invoice and receipt year, which come from the day the fixture was made).

Before publishing a fixture made again, run the publish guard over it: a random size or time can read like a word it
refuses.

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.4.1 database (schema version 8), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: three entries (a quoted session, Jensen, Doe), signed |
| `doc-numbers.json` | The number marks: invoices (the print invoices in the same run), receipts, quotes, and ids |
| `galleries/` | The gallery build folders, one per g- link ID (`.download` marks a download gallery, `.online` the build that is online) |
| `galleries-kept/` | The app's own copies of the clients' sent picks and drafts (`picks.las`, `draft.las`): Lane's, and Yates's at its new link |
| `host/` | The first gallery host (a local folder): the copy Move galleries left there of Lane's gallery |
| `web/` | The web folder of the web host in use: the galleries in `web/g/`, the studio page's files above them |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `faces/` | Lane's face photo, sealed under the data key |
| `expected.json` | What 1.4.1 showed: everything the 1.4.0 fixture records, and the S3 location and its certificate pin, the codeword setting per type and the codewords kept, the replaced link, the served-page check, the receipts' license line |

The storage settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them
again with both gallery hosts at their new place, the certificate 1.4.1 confirmed for the web host's address
(`web_cert` in `expected.json`), and the second backup location on S3 storage with its pinned certificate (`s3`; the
access key and secret are the test server's own, `tests/fake_s3.py`). As in the 1.4.0 fixture, a local host is known
by its real path, so the test reads a folder under its own data folder as the same place under the maker's
(`data_folder`). Nothing in the data is changed for it.

## What is in it

Everything in the 1.4.0 fixture, entered the same way, with the same clients and ids, and three clients added: Upton
(client 20, session 22), Yates (client 21, session 23) and Vance (client 22, session 24).

What 1.4.1 added that the database keeps:

- **S3 backups with a pinned certificate**: in Setup, Backups, the primary location stays the local folder and a second
  location is S3 storage on the owner's network (a small S3 server on this machine, `tests/fake_s3.py`, over https with
  a self-signed certificate, path-style addressing). Setup showed the certificate and stopped; it was trusted there, its
  key pinned for `127.0.0.1:<port>` in the storage settings (`s3_certs`), and the step's test wrote and removed its
  test file there. Nothing waits to be confirmed.
- **Codewords on by default**: every type asks for a gallery codeword. Headshots were turned off in Settings (the
  type's form sent as shown, the box unticked), so a headshot inquiry doesn't ask; Upton's family inquiry asked and
  keeps the three words typed (it stays an inquiry: PHP's own server hands out files, so a codeword gallery can't go
  online on this web host).
- **A replaced gallery link**: Yates (family, 212.00 with tax, paid in full) had proofs online; the client opened them,
  sent picks for one photo and kept picking (a draft of two), both collected. The link was replaced: the old folder
  came down from the web host (and is listed to come down from the first host, which was unreachable at the last try),
  the gallery was built under a new link ID and key and put online, the client's picks and draft sealed again for it.
  History says so, and the to-do "Send the client the new gallery link" is open.
- **The served-page check**: every gallery online read back as published; then Ivers's page was changed on the web host
  and seen at two checks in a row (`served_check` in settings), so Today warns about it and History says so.
- **The receipt's license line**: Price (headshots, a licensed type, the agreement signed) paid in full and its receipt
  issued (R-<year>-0003) names the agreement; Vance (headshots, no agreement, 159.00 with tax, paid in full) has a
  receipt (R-<year>-0004) that says nothing of a license.
- **The version last run**: settings `app_version` is 1.4.1.

## What 1.4.1 showed

Sessions 1 to 21 read as in the 1.4.0 fixture, except Price (8), now paid in full. Added:

| Session | Stage | Quoted | Paid | Due | Receipt |
|---|---|---|---|---|---|
| 8 Price | Booked | 175.00 | 175.00 | 0.00 | R-<year>-0003, with the license line |
| 22 Upton | Inquiry | 0.00 | 0.00 | 0.00 | none |
| 23 Yates | Shot | 212.00 | 212.00 | 0.00 | none |
| 24 Vance | Quoted | 159.00 | 159.00 | 0.00 | R-<year>-0004, no license line |

Finances for 2026: received 3526.20 (family 3132.20, headshot 334.00, event 60.00); refunds -145.00; tips 60.00;
written off 162.00; sales tax due 112.99; by month as in the 1.4.0 fixture through October, then November 516.00. Print
orders as in the 1.4.0 fixture. Every row is in `expected.json`.

After the upgrade to 1.5, every record and figure above is the same, except 1.5's documented changes: money shows in
cents; Reyes and Yates (at Shot with the client's picks in) move to Delivered once, each with a History note, and get
no deletion date from that move; the prices used on sent quotes count as approved; the schema moves to 9 after the
backup; Today says the app was updated until the note is hidden. The S3 pin is the one accepted at its address
(another key there is stopped), headshots still don't ask for a codeword, Yates's gallery is online at its new link
with the picks and draft, the served-page warning about Ivers stands, and the receipts read as issued.
