# A database written by 1.2.0

Used by `tests/test_upgrade_from_120.py`. Made by `tools/make_fixture.py --version 1.2.0`, which runs the real 1.2.0
code (commit e37c6e6, the commit published as v1.2.0, in a temporary git worktree) with 1.2.0's database library,
sqlcipher3 0.6.5 (SQLCipher 4.20.0), and enters everything through that version's own routes and functions, signed
in as the owner. To make it again, with sqlcipher3 0.6.5 installed:

    python tools/make_fixture.py --version 1.2.0

It is the 1.1.1 scenario (see `tests/fixtures/db-1.1.1/README.md`) entered with 1.2.0, plus what 1.2.0 added. The data
key and master key are random, so the files change with every run; the numbers below do not (except the dates
documents were issued, sessions were priced and booked, agreements were signed, galleries were built, uploaded,
opened, picked from, and delivered, and the invoice and receipt year, which come from the day the fixture was made,
and the gallery ids, keys, and file names, which are random).

**Test password:** `test password 123` (the keyring is wrapped to it; `tests/helpers.py` TEST_PASSWORD).

## Files

| File | What it is |
|---|---|
| `crm.db` | The 1.2.0 database (schema version 6), encrypted under the data key by SQLCipher 4.20.0 |
| `keyring.json` | The data key, wrapped to the test password and to a test master key |
| `deletions.json` | The deletion list: one entry, signed (its MAC key is the `deletions` row in `side_keys`) |
| `doc-numbers.json` | The number marks: invoices, receipts, quotes, and client, session, step, and event ids |
| `galleries/` | The gallery build folders, one per gallery id (`.download` marks a download gallery, `.online` the build that is online, `.online-was` what a download gallery replaced) |
| `host/` | The local gallery host, as the galleries were put online: sealed files, the page, the gate, `limits.txt`, and the client's `seen.txt` and `picks.bin` where the client opened or picked |
| `photos/` | The photos folder: the exported JPEGs the galleries were built from, and the sealed retouched finals |
| `expected.json` | What 1.2.0 showed: records, balances, Finances, numbers, documents, galleries, download galleries, delivery, settings |

Unlike the earlier fixtures, the gallery folders are kept: in 1.2 a download gallery is a download gallery by the
files in its build folder, not by a database column, so an upgrade must find them as an install would. The storage
settings (`storage.json`) are not kept; they name the maker's temporary folder. The upgrade test writes them again
with the gallery host at its new place (a local host, as `expected.json` `gallery_host` records).

## What is in it

Everything in the 1.1.1 fixture, entered the same way: Lane (1), Ortiz (2), Bell (3), Chen (4), Price (5), Kim (6);
sessions 1 to 8 with the same quotes, payments, refund credit, issued and draft documents, deleted quote 2, the
gallery look, both logos, who owns the photos per type, Lane's gallery online with the client's picks and draft,
Kim's proof gallery online before the balance, and the Booked hints. Lane's rebuild while online is there too, but
1.2 removes the earlier build's files as soon as the update is up, so nothing waits for a check (`prune_due` is
empty). Doe (client 9, session 11) was made last and deleted.

What 1.2.0 added:

- **The website** (settings `business_website`, https://studio.example.com): every gallery built after it was set
  carries it inside its encrypted manifest.
- **The delivery method chosen early, with a History line**: Reyes (client 7, family session 9, titled "Reyes autumn
  portraits") chose Download link in the Shoot form while still an inquiry; Diaz (client 8, family session 10, no
  title) chose it in the delivery form. Each has its "Delivery changed from USB drive to Download link" line.
- **The agreement says how long the link stays open**: Reyes's agreement was signed after the change ("The link stays
  open for 30 days after you first open it; ask once and I'll extend it.").
- **Download galleries**: full-size files, and the whole set prepared as one zip sealed in pieces. The piece size was
  set to 16 KiB for this fixture (1.2.0 uses 8 MiB), so these small photos still come in several pieces, as a large
  gallery's would.
- **Retouched finals after proofs**: Reyes paid in full; the proofs went online, the client opened them and sent
  picks; three retouched finals were uploaded in Photos and the download gallery built from them; Upload put it
  online in the proofs' place, at the same link.
- **Files delivered by the first open**: the client then opened Reyes's download gallery, so Files delivered is
  done and the gallery's days count from that day, not from the first open of the proofs.
- **Deliver without picks**: Diaz paid in full; the proofs went online, Deliver without picks was pressed, and the
  download gallery (from the same four photos) went online in their place. The client has not opened it yet.
- **Gallery titles**: Reyes's gallery and zip are titled with the session title; Diaz's with the client's name.
- **The version last run**: settings `app_version` is 1.2.0.

## What 1.2.0 showed

| Session | Quoted | Paid | Refund credit | Due | Invoice | Receipt | Licensed | Delivery |
|---|---|---|---|---|---|---|---|---|
| 1 Lane | 350.00 | 310.00 | 40.00 | 0.00 | INV-2026-0001 (as issued: 100.00 paid) | R-2026-0001 | no | default |
| 2 Ortiz | 250.00 | 75.00 | 0.00 | 175.00 | draft | none | no | default |
| 3 Ortiz inquiry | 0.00 | 0.00 | 0.00 | 0.00 | none | none | no | default |
| 5 Bell | 60.00 | 60.00 | 0.00 | 0.00 | none | draft | no | default |
| 6 Chen | 60.00 | 0.00 | 0.00 | 60.00 | INV-2026-0002 | none | no | default |
| 7 Price | 175.00 | 0.00 | 0.00 | 175.00 | none | none | yes | default |
| 8 Kim | 300.00 | 100.00 | 0.00 | 200.00 | none | none | no | default |
| 9 Reyes | 400.00 | 400.00 | 0.00 | 0.00 | none | none | no | Download link |
| 10 Diaz | 200.00 | 200.00 | 0.00 | 0.00 | none | none | no | Download link |

| Gallery | Kind | Photos | Online | Opened |
|---|---|---|---|---|
| 1 Lane | proofs (proof mark) | 4 | yes | yes, picks and a draft |
| 8 Kim | proofs (proof mark) | 2 | yes, with the balance owed | no |
| 9 Reyes | download, from the retouched finals; zip in 4 pieces | 3 | yes, in place of the proofs | yes: Files delivered |
| 10 Diaz | download, delivered without picks; zip in 5 pieces | 4 | yes, in place of the proofs | no |

Finances for 2026: received 1145.00 (family 1085.00, event 60.00); refunds -40.00; sales tax due 0.00; by month
February 100.00, March 250.00, April -40.00, May 135.00, July 100.00, August 150.00, September 250.00, October 200.00;
overdue: session 8 200.00; upcoming balances: session 2 175.00, session 6 60.00, session 7 175.00.

After the upgrade, every figure above is the same. The database opens with this version's SQLCipher, with the same
settings (4096-byte pages, 256,000 key iterations, HMAC-SHA512). A sealed backup is taken, then the schema moves from 6
to 7. Each issued document comes back byte for byte; the drafts are still unnumbered (the next invoice is
INV-<year>-0003, the next receipt R-<year>-0002); the next quote is 6, the next client 10 and session 12. Every
gallery is as 1.2.0 kept it; the download galleries are still download galleries, online, with every full-size file
and zip piece in place, and the pieces put back together are the zip 1.2.0 prepared. The delivery methods, their
History lines, Deliver without picks, the finals, the picks, Files delivered and the download gallery's days, the
website, and the gallery titles are unchanged. 1.3's gallery columns start empty (`limits_contact`, `dark_at`,
`restored_at`, `restore_days`, `restore_once`), and `host_fp` and `link_base` name the host and address the galleries
are on. A gallery check keeps all four galleries online, and a first open of Diaz's download gallery is still Files
delivered. Today says once that the app was updated.
