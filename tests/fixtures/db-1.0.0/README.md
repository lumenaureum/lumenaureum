# A database written by 1.0.0

Used by `tests/test_upgrade_from_100.py`. Made by `tools/make_fixture_100.py`, which runs the real 1.0.0 code
(tag v1.0.0, commit a0966c9, in a temporary git worktree) and enters everything through that version's own routes
and functions, signed in as the owner. To make it again:

    python tools/make_fixture_100.py

The data key and master key are random, so the files change with every run; the numbers below do not (except the
invoice year, which is the year the fixture was made).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.0.0 database (schema version 2), encrypted under the data key |
| `crm-before-delete.db` | The same database just before the client was deleted (an older backup) |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The 1.0.0 deletion list: one entry, unsigned (1.0.0 wrote no MAC) |
| `doc-numbers.json` | The 1.0.0 invoice number marks |
| `expected.json` | What 1.0.0 showed: every client, session, and payment, balances, Finances, numbers |

## What is in it

- Clients: Lane (1), Ortiz (2), and the event families Bell (3) and Chen (4). A fifth client, Doe (5), was made last
  with one session (6) and then deleted, so its ids are the newest ever issued.
- Lane, family session 1, Delivered: one price-list fee (300.00) and a custom line, Extra prints (50.00). Deposit 100.00
  (2026-02-20) and balance 250.00 (2026-03-20), then a 40.00 refund (2026-04-02) recorded the 1.0.0 way: a -40.00
  "Refund" quote line (fee_key `refund:3`). Invoice INV-2026-0001 issued and marked sent after the refund.
- Ortiz, family session 2, Booked: one line, 250.00, deposit 75.00 (2026-05-01). Session 3, an inquiry, no quote.
- Event 1, Spring Recital (2026-05-16), families pay 60.00 each: Bell (session 4) paid 60.00; Chen (session 5) has not.

## What 1.0.0 showed

| Session | Quoted | Paid | Due | Price without the refund line |
|---|---|---|---|---|
| 1 Lane | 310.00 | 310.00 | 0.00 | 350.00 |
| 2 Ortiz | 250.00 | 75.00 | 175.00 | 250.00 |
| 3 Ortiz inquiry | 0.00 | 0.00 | 0.00 | 0.00 |
| 4 Bell | 60.00 | 60.00 | 0.00 | 60.00 |
| 5 Chen | 60.00 | 0.00 | 60.00 | 60.00 |

Finances for 2026: received 445.00 (family 385.00, event 60.00); refunds -40.00; sales tax due 0.00; by month
February 100.00, March 250.00, April -40.00, May 135.00; nothing overdue; upcoming balances: session 2 175.00,
session 5 60.00.

After the upgrade, session 1 quotes 350.00 (the price agreed) with a 40.00 refund credit, and every balance and
Finances figure is the same as above.
