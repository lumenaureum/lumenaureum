# A database written by 1.3.1

Used by `tests/test_upgrade_from_131.py`. Made by `tools/make_fixture.py --version 1.3.1`, which runs the real 1.3.1
code (commit 40adae6, the commit published as v1.3.1) with 1.3.1's database library, sqlcipher3 0.6.5 (SQLCipher
4.20.0, the version 1.3.1's `requirements.lock` pins), and enters everything through that version's own routes and
functions, signed in as the owner. To make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.3.1

(This copy was made with `--code` pointing at an export of commit 40adae6, `git archive 40adae6`, which is the same
code the temporary worktree holds.)

It is the 1.3.0 scenario (see `tests/fixtures/db-1.3.0/README.md`) entered with 1.3.1, plus what 1.3.1 changed that
leaves data. The data key, master key and the studio page's inquiry key are random, so the files change with every
run; the numbers below do not (except the dates documents were issued, sessions were priced and booked, agreements
were signed, galleries were built, uploaded, opened, picked from, delivered, gone dark and put back online, charges
were recorded, the balance was written off, the master key was checked and the gallery host was found unreachable,
and the invoice and receipt year, which come from the day the fixture was made; the gallery ids, keys, file names and
host fingerprints, which are random or follow the maker's folder; and the password wrap's work factor, which age picks
from the speed of the machine that made it).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.3.1 database (schema version 7), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password (scrypt work factor 19 on the maker's machine) and to a test master key |
| `deletions.json` | The deletion list: three entries (a quoted session, Jensen, Doe), signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `galleries/` | The gallery build folders, one per gallery id (`.download` marks a download gallery, `.online` the build that is online) |
| `galleries-kept/` | The app's own copies of the clients' sent picks and drafts |
| `host/` | The first gallery host (a local folder): the copy Move galleries left there of Lane's gallery |
| `web/` | The web folder of the gallery host in use: the galleries in `web/g/`, the studio page's files above them |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `expected.json` | What 1.3.1 showed: records, balances, Finances and its export, statements, charges, numbers, documents, galleries and their hosts, the unreachable-host memory, the studio page, the password work factor, settings |

The storage settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them
again with both gallery hosts at their new place. A local gallery host is known by its real path, and every gallery
record names the host it is on (`galleries.host_fp`, `moved_from`; `studio_files.host_fp`; the unreachable-host row in
`settings`) by a fingerprint of that path, so the test reads a folder under its own data folder as the same place under
the maker's (`data_folder` in `expected.json`). Nothing in the data is changed for it.

## What is in it

Everything in the 1.3.0 fixture, entered the same way, with two clients added (so the ids after Lane move up by one,
and again after Ada): Lane (1), Ortiz (2), Moss (3), Bell (4), Chen (5), Price (6), Kim (7), Reyes (8), Diaz (9),
Ellis (10), Foster (11), Grant (12), Hale (14), Ivers (15), Ada Lovelace (16), Nash (17). Jensen (client 13, session
15) and Doe (client 18, session 20) were deleted; the quoted session 5 too.

What 1.3.1 changed that the database keeps:

- **An expired gallery whose old copy from a move came down**: Moss (client 3, session 2; 150.00, paid in full in June,
  no sales tax yet) went up on the first host beside Lane's, and Move galleries put both on `web/g/`, leaving the old
  copies on `host/`. Moss's gallery was then opened 45 days before the check, so its days ended: `web/g/` holds only its
  dark page, and the old copy on `host/` came down with it (1.3.0 left it there). Its record names no host it moved from
  (`moved_from` empty) and nothing is left to come down (`gallery_leftovers` is empty).
- **A gallery whose upload failed partway**: Nash (client 17, session 19; 150.00 + 6% = 159.00, paid in full in
  October) has proofs built; its first upload put its files on `web/g/`, then the host dropped the connection. The
  record is `error` ("the host dropped the connection"), never uploaded, and names the host the upload went to
  (`host_fp`, which 1.3.0 left empty), so Move galleries would find it if the host changed.
- **The unreachable-host memory, shared**: at a check the first host could not be reached (its `.vault-root` marker
  was away for the moment); 1.3.1 keeps that as a settings row, `gallery_unreachable:<fingerprint>` with the time it
  failed, which every worker reads (1.3.0 kept it in one process). The host was back afterward; the row stays until
  the next try, a day later.
- **A canceled booking with money paid is one charge**: Grant (session 14), called off as Canceled with its 75.00
  deposit and 100.00 toward the balance: one charge, "Canceled, deposit kept" 100.00 (1.3.0 wrote three).
- **The password wrap's work factor**: Settings > Security shows it (19 here, read from `keyring.json`).
- **The version last run**: settings `app_version` is 1.3.1.

## What 1.3.1 showed

| Session | Quoted | Paid | Tips | Due | Kept | Written off | Invoice | Receipt |
|---|---|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 0.00 | 0.00 (40.00 refund credit) | | | INV-2026-0001 | R-2026-0001 |
| 2 Moss | 150.00 | 150.00 | 0.00 | 0.00 | | | none | none |
| 3 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | | | draft | none |
| 6 Bell | 60.00 | 60.00 | 0.00 | 0.00 | | | none | draft |
| 7 Chen | 60.00 | 0.00 | 0.00 | 60.00 | | | INV-2026-0002 | none |
| 8 Price | 175.00 | 0.00 | 0.00 | 175.00 | | | none | none |
| 9 Kim | 300.00 | 100.00 | 0.00 | 200.00 | | | none | none |
| 10 Reyes | 400.00 | 400.00 | 0.00 | 0.00 | | | none | none |
| 11 Diaz | 200.00 | 200.00 | 0.00 | 0.00 | | | none | none |
| 12 Ellis | 318.00 | 318.00 | 40.00 | 0.00 | | | INV-2026-0003 | R-2026-0002 |
| 13 Foster | 212.00 | 50.00 | 0.00 | 0.00 | | 162.00 | none | none |
| 14 Grant (Canceled) | 265.00 | 100.00 | 0.00 | not owed (lost) | 100.00 | | none | none |
| 16 Hale | 159.00 | 159.00 | 0.00 | 0.00 | | | none | none |
| 17 Ivers | 159.00 | 159.00 | 0.00 | 0.00 | | | none | none |
| 19 Nash | 159.00 | 159.00 | 0.00 | 0.00 | | | none | none |

| Gallery | Kind | Host | State |
|---|---|---|---|
| 1 Lane | proofs | `web/g/`, moved from `host/` (old copy still there) | online, picks and a draft |
| 2 Moss | proofs | `web/g/` (moved from `host/`; that copy came down) | gone dark (only the dark page) |
| 9 Kim | proofs | `web/g/` | online, with the balance owed |
| 10 Reyes | download, from the finals; zip in 4 pieces | `web/g/` | online: Files delivered |
| 11 Diaz | download, delivered without picks; zip in 5 pieces | `web/g/` | online, not opened |
| 16 Hale | proofs | `web/g/` | gone dark (only the dark page) |
| 17 Ivers | proofs | `web/g/` | put back online for 14 days |
| 19 Nash | proofs | `web/g/` (recorded) | first upload failed partway: its files there, never uploaded |

Finances for 2026: received 2427.20 (family 2367.20, event 60.00); refunds -115.00; tips 60.00; written off 162.00;
sales tax due 60.69 (corrections -4.25); by month February 100.00, March 250.00, April -40.00, May 135.00, June
150.00, July 247.20, August 225.00, September 818.00, October 542.00; overdue: session 9 200.00; upcoming balances:
session 3 175.00, session 7 60.00, session 8 175.00. The export for the year (summary, payments, sales tax,
write-offs, expenses, mileage) is in `expected.json` row by row.

After the upgrade, every figure above is the same. The database opens with this version's SQLCipher, with the same
settings (4096-byte pages, 256,000 key iterations, HMAC-SHA512). The schema stays at 7, so no backup is taken and
Today says the data didn't need changes. Every payment keeps its kind and its sales tax, and the deleted client's tip
is still a tip in the books. Every statement month, charge (the cancel's one), write-off and tax correction reads as
1.3.1 kept it; each issued document comes back byte for byte; the drafts are still unnumbered (the next invoice is
INV-<year>-0004, the next receipt R-<year>-0003). Every gallery is as 1.3.1 left it, on the host its record names: Lane's
with the record of the host it moved from, Moss's and Hale's dark, Ivers's put-back days, Nash's failed upload and the
host it went to; nothing waits to move, nothing is left to come down, and no client needs a new link. The unreachable
host's row is kept as written: the host is tried again once a day has passed since it failed, and a check after that
reaches it and forgets it. The studio page's settings, photo, the files it put on the host (byte for byte) and its
inquiries are kept, and the one waiting can be added. The master key check is remembered, and Settings > Security shows
the same work factor. A gallery check changes nothing else.
