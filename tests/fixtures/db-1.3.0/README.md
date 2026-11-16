# A database written by 1.3.0

Used by `tests/test_upgrade_from_130.py`. Made by `tools/make_fixture.py --version 1.3.0`, which runs the real 1.3.0
code (commit 58a774b, the commit published as v1.3.0) with 1.3.0's database library, sqlcipher3 0.6.5 (SQLCipher
4.20.0, the version 1.3.0's `requirements.lock` pins), and enters everything through that version's own routes and
functions, signed in as the owner. To make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.3.0

(This copy was made with `--code` pointing at an export of commit 58a774b, `git archive 58a774b`, which is the same
code the temporary worktree holds.)

It is the 1.2.0 scenario (see `tests/fixtures/db-1.2.0/README.md`) entered with 1.3.0, plus what 1.3.0 added that the
database keeps. The data key, master key and the studio page's inquiry key are random, so the files change with every
run; the numbers below do not (except the dates documents were issued, sessions were priced and booked, agreements
were signed, galleries were built, uploaded, opened, picked from, delivered, gone dark and put back online, charges
were recorded, the balance was written off and the master key was checked, and the invoice and receipt year, which
come from the day the fixture was made, and the gallery ids, keys, file names and host fingerprints, which are random
or follow the maker's folder).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.3.0 database (schema version 7), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: three entries (a quoted session, Jensen, Doe), signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `galleries/` | The gallery build folders, one per gallery id (`.download` marks a download gallery, `.online` the build that is online) |
| `galleries-kept/` | The app's own copies of the clients' sent picks and drafts (1.3 keeps them so a gallery can move whole) |
| `host/` | The first gallery host (a local folder): the copy Move galleries left there of Lane's gallery |
| `web/` | The web folder of the gallery host in use: the galleries in `web/g/`, the studio page's files above them |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `expected.json` | What 1.3.0 showed: records, balances, Finances and its export, statements, charges, numbers, documents, galleries and their hosts, the studio page, settings |

The storage settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them
again with both gallery hosts at their new place. A local gallery host is known by its real path, and every gallery
record names the host it is on (`galleries.host_fp`, `moved_from`; `studio_files.host_fp`) by a fingerprint of that
path, so the test reads a folder under its own data folder as the same place under the maker's (`data_folder` in
`expected.json`). Nothing in the data is changed for it. Restored to another folder without that, every gallery would
read as still on an earlier host, waiting for Move galleries: it is another folder.

## What is in it

Everything in the 1.2.0 fixture, entered the same way: Lane (1), Ortiz (2), Bell (3), Chen (4), Price (5), Kim (6),
Reyes (7), Diaz (8); sessions 1 to 10 with the same quotes, payments, refund credit, issued and draft documents,
deleted quote 2, the gallery look, both logos, who owns the photos per type, the website, the delivery methods and
their History lines, Lane's gallery with the client's picks and draft, Kim's proof gallery online before the balance,
Reyes's download gallery from the retouched finals (opened: Files delivered), Diaz's delivered without picks, and the
Booked hints. Doe (client 16, session 18) was made last and deleted.

What 1.3.0 added:

- **Move galleries**: Lane's gallery went up on the first host (`host/`); then the gallery host was changed in Setup to
  `web/g/` (the same address, https://photos.example.com/g/) and Move galleries put it there with its picks and draft.
  Its record names the host it is on and the one it moved from (`moved_from`); its old copy is still on the first host,
  which storage keeps as an earlier host. Every later gallery went straight to `web/g/`.
- **Business details and sales tax**: business name, photographer and email set in Settings (the dark page and the
  galleries' limits carry the email); 6% flat sales tax set in Setup after the 1.2.0 sessions, so the sessions below
  are taxed.
- **Tips**: Ellis (client 9, session 11): 300.00 + 6% = 318.00; a deposit, the invoice issued (INV-<year>-0003), the
  balance, a 50.00 tip, 10.00 of it given back (a negative tip), the receipt issued (R-<year>-0002). The tips carry no
  sales tax (`payments.tax` 0, not empty).
- **A tip of a deleted client**: Jensen (client 12, session 14) paid 127.20 with a 20.00 tip, then was deleted: both stay
  in the books (`books`), the tip as a tip.
- **Waived**: Foster (client 10, session 12): 212.00, a 50.00 deposit, the balance (162.00) waived on its Paid in full
  step (`write_offs`).
- **A canceled booking keeps its deposit**: Grant (client 11, session 13): 265.00; 75.00 deposit and 100.00 toward the
  balance; then Lost, Canceled: 100.00 kept, 75.00 refund due, refunded. 1.3.0 recorded the cancel on the statement as
  three charges (Lost, deposit kept; its reversal; Canceled, deposit kept); 1.3.1 records one, and these three stay.
- **Statements**: every session's charge as 1.3.0 recorded it (`charges`): invoices as issued, work done, the kept
  deposit; with the write-off and the tax correction of the cancel (`tax_adjustments`).
- **Gone dark**: Hale (client 13, session 15): paid in full, proofs online, first opened 45 days before; at the check
  its days were over and its link now shows only the dark page with the studio's email.
- **Put back online**: Ivers (client 14, session 16): the same, gone dark, then put back online for 14 days.
- **The studio page**: switched on, its words saved, one photo of the studio's own; put on the host in the folder above
  the galleries (`web/`), with the inquiry form. Two inquiries left by the form were collected: Ada Lovelace was added
  (client 15, session 17, an Inquiry from the website), Grace Hopper waits for review.
- **The master key checked** (settings `master_checked`).
- **The version last run**: settings `app_version` is 1.3.0.

## What 1.3.0 showed

| Session | Quoted | Paid | Tips | Due | Kept | Written off | Invoice | Receipt |
|---|---|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 0.00 | 0.00 (40.00 refund credit) | | | INV-2026-0001 | R-2026-0001 |
| 2 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | | | draft | none |
| 5 Bell | 60.00 | 60.00 | 0.00 | 0.00 | | | none | draft |
| 6 Chen | 60.00 | 0.00 | 0.00 | 60.00 | | | INV-2026-0002 | none |
| 7 Price | 175.00 | 0.00 | 0.00 | 175.00 | | | none | none |
| 8 Kim | 300.00 | 100.00 | 0.00 | 200.00 | | | none | none |
| 9 Reyes | 400.00 | 400.00 | 0.00 | 0.00 | | | none | none |
| 10 Diaz | 200.00 | 200.00 | 0.00 | 0.00 | | | none | none |
| 11 Ellis | 318.00 | 318.00 | 40.00 | 0.00 | | | INV-2026-0003 | R-2026-0002 |
| 12 Foster | 212.00 | 50.00 | 0.00 | 0.00 | | 162.00 | none | none |
| 13 Grant (Canceled) | 265.00 | 100.00 | 0.00 | not owed (lost) | 100.00 | | none | none |
| 15 Hale | 159.00 | 159.00 | 0.00 | 0.00 | | | none | none |
| 16 Ivers | 159.00 | 159.00 | 0.00 | 0.00 | | | none | none |

| Gallery | Kind | Host | State |
|---|---|---|---|
| 1 Lane | proofs | `web/g/`, moved from `host/` (old copy still there) | online, picks and a draft |
| 8 Kim | proofs | `web/g/` | online, with the balance owed |
| 9 Reyes | download, from the finals; zip in 4 pieces | `web/g/` | online: Files delivered |
| 10 Diaz | download, delivered without picks; zip in 5 pieces | `web/g/` | online, not opened |
| 15 Hale | proofs | `web/g/` | gone dark (only the dark page) |
| 16 Ivers | proofs | `web/g/` | put back online for 14 days |

Finances for 2026: received 2118.20 (family 2058.20, event 60.00); refunds -115.00; tips 60.00; written off 162.00;
sales tax due 51.69 (corrections -4.25); by month February 100.00, March 250.00, April -40.00, May 135.00, July
247.20, August 225.00, September 818.00, October 383.00; overdue: session 8 200.00; upcoming balances: session 2
175.00, session 6 60.00, session 7 175.00. The export for the year (summary, payments, sales tax, write-offs, expenses,
mileage) is in `expected.json` row by row.

After the upgrade, every figure above is the same. The database opens with this version's SQLCipher, with the same
settings (4096-byte pages, 256,000 key iterations, HMAC-SHA512). The schema stays at 7, so no backup is taken and
Today says the data didn't need changes. Every payment keeps its kind and its sales tax: the tips are still tips
(1.3.1 relabels as Other only a tip row 1.2 left without its tax, written by hand there), and the deleted client's tip
is still a tip in the books. Every statement month, charge, write-off and tax correction reads as 1.3.0 kept it; each
issued document comes back byte for byte; the drafts are still unnumbered (the next invoice is INV-<year>-0004, the
next receipt R-<year>-0003). Every gallery is as 1.3.0 left it, on the host its record names, with the record of the
host Lane's moved from, the dark page and the put-back days; nothing waits to move and no client needs a new link. The
studio page's settings, photo, the files it put on the host (byte for byte) and its inquiries are kept, and the one
waiting can be added. The master key check is remembered. A gallery check changes nothing.
