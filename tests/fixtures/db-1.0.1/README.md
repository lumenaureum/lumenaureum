# A database written by 1.0.1

Used by `tests/test_upgrade_from_101.py`. Made by `tools/make_fixture.py --version 1.0.1`, which runs the real 1.0.1
code (tag v1.0.1, commit af70748, in a temporary git worktree) and enters everything through that version's own routes
and functions, signed in as the owner. To make it again:

    python tools/make_fixture.py --version 1.0.1

The data key and master key are random, so the files change with every run; the numbers below do not (except the
dates documents were issued and the gallery was built, and the invoice and receipt year, which come from the day the
fixture was made, and the gallery id and keys, which are random).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.0.1 database (schema version 3), encrypted under the data key |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The 1.0.1 deletion list: one entry, signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `expected.json` | What 1.0.1 showed: every client, session, and payment, balances, Finances, numbers, the gallery |

The gallery's encrypted files are not kept: the `galleries` row points at the folder it was built in, which was
removed with the maker's temporary data folder (a rebuild makes them again). The photos it was built from were three
plain test JPEGs, using 1.0.1's opt-in folder flow (CRM_PHOTO_FOLDERS=1).

## What is in it

- Time zone set to America/New_York through the Settings prompt (`/settings/timezone`).
- Clients: Lane (1), Ortiz (2), and the event families Bell (3) and Chen (4). A fifth client, Doe (5), was made last
  with one session (7) and then deleted (the 1.0.1 background job), so its ids are the newest ever issued.
- Lane, family session 1, Delivered: one price-list fee (300.00) and a custom line, Extra prints (50.00). Deposit
  100.00 (2026-02-20), then invoice INV-2026-0001 issued and marked sent (frozen showing 100.00 paid, 250.00 due).
  Balance 250.00 (2026-03-20) after the invoice, then a 40.00 refund (2026-04-02), kept as a 40.00 refund credit (the
  price stays 350.00). Receipt R-2026-0001 issued after the refund (frozen showing 310.00 paid).
- Lane's gallery: codeword "amber harbor willow" set on the Delivery form, then built from three photos. Not uploaded,
  so `cw_fixed` is empty; its key is the codeword key and the link asks for the word (`&c=1`).
- Ortiz, family session 2, Booked: one line, 250.00, quote 1 prepared, deposit 75.00 (2026-05-01). Its invoice was
  opened but not issued: a draft, no number. Session 3, an inquiry, no quote. Session 4, a mini session (150.00),
  was quoted as quote 2 and then deleted: the highest quote number belongs to a session that is gone (the database's
  largest quote is 1; doc-numbers.json remembers 2).
- Event 1, Spring Recital (2026-05-16), families pay 60.00 each: Bell (session 5) paid 60.00, and its receipt was
  opened but not issued (a draft, no number); Chen (session 6) owes 60.00, and its invoice INV-2026-0002 is issued.
- id_marks: sessions 7, households 5, events 1, tasks 63.

## What 1.0.1 showed

| Session | Quoted | Paid | Refund credit | Due | Invoice | Receipt |
|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 40.00 | 0.00 | INV-2026-0001 (as issued: 100.00 paid; "since paid in full") | R-2026-0001 |
| 2 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | draft | none |
| 3 Ortiz inquiry | 0.00 | 0.00 | 0.00 | 0.00 | none | none |
| 5 Bell | 60.00 | 60.00 | 0.00 | 0.00 | none | draft |
| 6 Chen | 60.00 | 0.00 | 0.00 | 60.00 | INV-2026-0002 | none |

Finances for 2026: received 445.00 (family 385.00, event 60.00); refunds -40.00; sales tax due 0.00; by month
February 100.00, March 250.00, April -40.00, May 135.00; nothing overdue; upcoming balances: session 2 175.00,
session 6 60.00.

After the upgrade, every figure above is the same, the issued numbers and their frozen copies are unchanged, the drafts
are still unnumbered (the next invoice is INV-<year>-0003, the next receipt R-<year>-0002), the next quote is 3, the
next client 6 and session 8, and the deletion still verifies.
