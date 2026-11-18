# A database written by 1.4.0

Used by `tests/test_upgrade_from_140.py`. Made by `tools/make_fixture.py --version 1.4.0`, which runs the real 1.4.0
code (commit c45ce75, the commit published as v1.4.0) with 1.4.0's database library, sqlcipher3 0.6.5 (SQLCipher
4.20.0, the version 1.4.0's `requirements.lock` pins), and enters everything through that version's own routes and
functions, signed in as the owner. Making it needs PHP (`php -S` stands in for the web host at its https address). To
make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.4.0

(This copy was made with `--code` pointing at an export of commit c45ce75, `git archive c45ce75`, which is the same
code the temporary worktree holds.)

It is the 1.3.1 scenario (see `tests/fixtures/db-1.3.1/README.md`) entered with 1.4.0, plus what 1.4.0 added that the
database keeps. The data key, master key, gallery keys and link IDs, the studio page's inquiry key, the print studio key
and the web host's certificate are random, so the files change with every run, and so do the print orders' ids (they
are numbered in the order they are collected); the figures below do not (except the dates documents were issued,
sessions were priced and booked, galleries were built, uploaded, opened, picked from, delivered, gone dark and put
back online, orders were collected, ordered and shipped, the certificate was confirmed and the host checked, and the
invoice and receipt year, which come from the day the fixture was made).

Before publishing a fixture made again, run the publish guard over it: a random size or time can read like a word it
refuses.

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.4.0 database (schema version 8), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: three entries (a quoted session, Jensen, Doe), signed |
| `doc-numbers.json` | The number marks: invoices (the print invoices in the same run), receipts, quotes, and ids |
| `galleries/` | The gallery build folders, one per g- link ID (`.download` marks a download gallery, `.online` the build that is online) |
| `galleries-kept/` | The app's own copies of the clients' sent picks and drafts (`picks.las`, `draft.las`) |
| `host/` | The first gallery host (a local folder): the copy Move galleries left there of Lane's gallery |
| `web/` | The web folder of the web host in use: the galleries in `web/g/`, the studio page's files above them |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `faces/` | Lane's face photo, sealed under the data key |
| `expected.json` | What 1.4.0 showed: everything the 1.3.1 fixture records, and the print orders' tables, invoices and Finances figures, the price list, the plugin switch, the certificate pin and host check, the refund choices and what each session waits for, the session headers, the face photo, the link IDs |

The storage settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them
again with both gallery hosts at their new place and the certificate 1.4.0 confirmed for the web host's address
(`web_cert` in `expected.json`). As in the 1.3.1 fixture, a local host is known by its real path, so the test reads a
folder under its own data folder as the same place under the maker's (`data_folder`). Nothing in the data is changed
for it.

## What is in it

Everything in the 1.3.1 fixture, entered the same way, with the same clients and ids, and two clients added: Wren
(client 18, session 20) and Olsen (client 19, session 21). Jensen (client 13, session 15) and Doe (client 20, session
22) were deleted; the quoted session 5 too.

What 1.4.0 added that the database keeps:

- **Galleries under link IDs**: every gallery is made under a `g-` link ID, its files on the web host under names only
  its key gives. Lane's has a codeword; the client's picks and a newer draft were sealed for its link ID (LAS1) and
  collected (kept in `galleries-kept/`). The studio page's two inquiries are LAS1 too: Ada's added as a client, Grace's
  waiting for review.
- **The web host's certificate**: the web host at https://photos.example.com/ (PHP's own server behind TLS) showed a
  self-signed certificate; Setup showed it and it was trusted, so its key (SPKI SHA-256) is pinned for
  `photos.example.com:443` in the storage settings. The host check found PHP working (galleries closing on time,
  picks, the inquiry form, print ordering; not codeword galleries, as PHP's own server hands out files), and every
  gallery online got 1.4's page. The 1.4 upgrade is recorded as finished.
- **Print orders**: turned on in Settings, Plugins (`plugin_on_prints`); the price list saved from the SAMPLE with lab
  costs on 4x6 (0.95) and 8x10 (3.40), their prices filled from the costs at 3x (3.00 and 11.00), and 16x20 at 165.00;
  prints offered, so each download gallery (Reyes, Diaz, Wren) carries the offer. Wren (371.00 with tax, paid in full)
  has a download gallery of two printable photos, from which six orders came: from Harper Wren, matched to the client,
  one **new**; one **invoiced** with 10.00 paid of 34.50; one **paid** with 39.50 (5.00 over the balance: a credit to
  give back) and its crops confirmed, ready for the lab; one ordered at "Sample Lab" (no. L-1001; lab cost 12.40
  prints, 6.75 shipping, 0.80 tax) and **shipped**; one paid, **canceled** and refunded; and one from Dale Rivers, not
  on file, **unmatched** on the review list. The four print invoices are INV-<year>-0004 to 0007, in the session
  invoices' run.
- **The refund's question**: Olsen (212.00 with tax, paid in full) had 30.00 refunded with "stays held until paid
  again" chosen, so the gallery waits for 30.00; Lane's and Grant's refunds release once nothing is owed.
- **A canceled booking's header**: Grant's session header says "Deposit kept $100", as its Billing does.
- **A face photo** on Lane's client page.
- **The version last run**: settings `app_version` is 1.4.0.

## What 1.4.0 showed

Sessions 1 to 19 read as in the 1.3.1 fixture. Added:

| Session | Quoted | Paid | Due | Waits for | Invoice | Receipt |
|---|---|---|---|---|---|---|
| 20 Wren | 371.00 | 371.00 | 0.00 | nothing | none | none |
| 21 Olsen | 212.00 | 182.00 | 0.00 (30.00 refund credit) | 30.00 (held) | none | none |

Finances for 2026: received 2980.20 (family 2920.20, event 60.00); refunds -145.00; tips 60.00; written off 162.00;
sales tax due 91.99; by month as before through September, then October 1125.00, November -30.00. Print orders, kept
apart: received 84.00 (sales 60.87, shipping 19.48, tax 3.65), refunds 34.50, lab cost 19.15 and lab tax 0.80, profit
60.40. The export for the year adds prints.csv and print-lab-costs.csv; every row is in `expected.json`.

After the upgrade, every figure above is the same, and so is every row of the print tables. The database opens with
this version's SQLCipher, with the same settings. The schema stays at 8, so no backup is taken. The certificate is the
one accepted at the web host's address (another key there is stopped), Setup shows it as confirmed, and the host check
is kept. A gallery check changes nothing.
