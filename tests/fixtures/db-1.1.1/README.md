# A database written by 1.1.1

Used by `tests/test_upgrade_from_111.py`. Made by `tools/make_fixture.py --version 1.1.1`, which runs the real 1.1.1
code (commit 9e0e799, the commit published as v1.1.1, in a temporary git worktree) with 1.1.1's database library,
sqlcipher3-binary 0.6.0 (SQLCipher 4.12.0), and enters everything through that version's own routes and functions,
signed in as the owner. To make it again, with sqlcipher3-binary 0.6.0 installed:

    python tools/make_fixture.py --version 1.1.1

It is the 1.1.0 scenario (see `tests/fixtures/db-1.1.0/README.md`) entered with 1.1.1, plus what 1.1.1 added. The data
key and master key are random, so the files change with every run; the numbers below do not (except the dates
documents were issued, sessions were priced and booked, agreements were signed, galleries were built, uploaded,
opened, and picked from, and the invoice and receipt year, which come from the day the fixture was made, and the
gallery ids and keys, which are random).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.1.1 database (schema version 5), encrypted under the data key by SQLCipher 4.12.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: one entry, signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `expected.json` | What 1.1.1 showed: records, balances, Finances, numbers, documents, galleries, due dates, settings |

The galleries' files and the local gallery host folder are not kept; they were removed with the maker's temporary data
folder. The database rows that describe them are what the upgrade test checks.

## What is in it

Everything in the 1.1.0 fixture, entered the same way: Lane (1), Ortiz (2), Bell (3), Chen (4), Price (5); sessions 1
to 7 with the same quotes, payments, refund credit, issued and draft documents, deleted quote 2, the gallery look,
both logos, who owns the photos per type, Lane's gallery online with the client's picks and draft, and the Booked
hints. Doe (client 7, session 9) was made last and deleted.

What 1.1.1 added:

- **Issued documents kept whole**: INV-2026-0001, R-2026-0001 and INV-2026-0002 were issued by 1.1.1, which keeps
  each finished document (`bill_snapshots.html`). `expected.json` holds a SHA-256 of each (`snapshot_html`) and its
  words (`issued_text`).
- **The day each session first had a price** (`sessions.priced_on`): kept when each session page was opened. The
  inquiry with no price (session 3) has none.
- **Proof-marked galleries** (`galleries.proof`): the tiled SAMPLE mark is on, so both galleries are built as proofs.
- **An update waiting to clean up** (`galleries.prune_due`): Lane's gallery was rebuilt with a fourth photo while
  online. The update went up over it, and the earlier files wait for the gallery check after `prune_due`.
- **The deposit-due rule's inputs**: Kim (client 6), family session 8, quoted 300.00, its agreement signed while
  still Quoted, then Booked with a 100.00 deposit (Price's agreement was signed after booking, as in 1.1.0).
  `expected.json` holds each Booked-or-later session's deposit due date as 1.1.1 worked it out (`deposit_due`), with
  the agreement dates and booking days it used.
- **A proof gallery online before payment**: Kim's session is Shot with 200.00 still owed; its proof-marked gallery
  was uploaded without "early", which 1.1.1 allows for proofs.
- **The version last run**: settings `app_version` is 1.1.1, so the next version shows its one-time note on Today.

## What 1.1.1 showed

| Session | Quoted | Paid | Refund credit | Due | Invoice | Receipt | Licensed |
|---|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 40.00 | 0.00 | INV-2026-0001 (as issued: 100.00 paid) | R-2026-0001 | no |
| 2 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | draft | none | no |
| 3 Ortiz inquiry | 0.00 | 0.00 | 0.00 | 0.00 | none | none | no |
| 5 Bell | 60.00 | 60.00 | 0.00 | 0.00 | none | draft | no |
| 6 Chen | 60.00 | 0.00 | 0.00 | 60.00 | INV-2026-0002 | none | no |
| 7 Price | 175.00 | 0.00 | 0.00 | 175.00 | none | none | yes |
| 8 Kim | 300.00 | 100.00 | 0.00 | 200.00 | none | none | no |

Finances for 2026: received 545.00 (family 485.00, event 60.00); refunds -40.00; sales tax due 0.00; by month
February 100.00, March 250.00, April -40.00, May 135.00, July 100.00; overdue: session 8 200.00; upcoming balances:
session 2 175.00, session 6 60.00, session 7 175.00.

After the upgrade, every figure above is the same. The database opens with the newer SQLCipher, with the same
settings (4096-byte pages, 256,000 key iterations, HMAC-SHA512). A schema migration, and the backup before it, runs
only if the newer version changed the schema. Each issued document comes back byte for byte; the drafts are still
unnumbered (the next invoice is INV-<year>-0003, the next receipt R-<year>-0002); the next quote is 5, the next client
8 and session 10; both galleries, Lane's picks and pending cleanup, and Kim's proof gallery online with the balance
owed are as 1.1.1 kept them; priced days and deposit due dates are unchanged; and Today says once that the app was
updated.
