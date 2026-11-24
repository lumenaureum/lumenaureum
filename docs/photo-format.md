# The sealed photo format (LAP1)

Lumen Aureum seals things in three formats (`docs/sealed-format.md` lists them in plain words): files kept at rest,
small sealed messages, and link files. This is the first: files kept at rest at home and in the vault, any size,
sealed in pieces, each file under its own key, the format blind to what the file is.

Formal name (in these format docs only): LAP = Lumen Aureum Perpetuum, files kept at rest, in pieces.

What it holds today: the photo store's kinds `raw` (RAWs from the shoot), `export` (the edited exports a gallery is
built from) and `final` (retouched finals). Expected later, as new kinds with the format unchanged: signed agreements
(the final PDF with its signature page), other documents worth keeping sealed (model releases, signed waivers), print
files kept for remakes, video clips, and files a client sends the studio (which arrive through the web host, then are
kept at home in this format).

Every photo uploaded into Lumen Aureum (RAWs and edited exports) is stored in this format from the first byte that
reaches the server: the photo is encrypted in memory as each piece of the upload arrives, and only ciphertext is ever
written to disk. The same files are mirrored to the backup location and, when a session is sealed, moved into the
vault unchanged. This page specifies the format completely, so that it can be checked, and so that photos can be
recovered without this software.

## Construction

The format is an instance of **STREAM**, the online authenticated-encryption construction of Hoang, Reyhanitabar,
Rogaway and Vizár ("Online Authenticated-Encryption and its Nonce-Reuse Misuse-Resistance", CRYPTO 2015), the same
construction the `age` file format uses for its payload. The underlying AEAD is **AES-256-GCM** (NIST SP 800-38D).

- Each photo has its own random 256-bit key, used for that file only. A key is never reused across files.
- The photo is split into records of 1 MiB (1,048,576 bytes) of plaintext; the last record may be shorter.
- Record *i* is encrypted with nonce = 4 zero bytes followed by *i* as an 8-byte big-endian integer. Nonces never
  repeat under a key because the counter is unique per record and the key is unique per file.
- The associated data of record *i* is the ASCII string `<file id>:<i>:last` for the final record and
  `<file id>:<i>:more` for every other one. The file id is the photo's random 128-bit identifier in lowercase hex.

STREAM carries the final-record flag in the nonce; here it is in the associated data, which authenticates it in the
same way. Together, the counter, the flag and the file id mean that records cannot be reordered, dropped, repeated,
moved between files, or cut short at a record boundary without decryption failing. A reader must also check that the
total plaintext length equals the recorded file size.

## Layout

```
"LAP1"                                   4 bytes, magic
repeat for each record:
  length                                 4 bytes, big-endian: the length of the ciphertext that follows
  ciphertext                             the record's plaintext encrypted, followed by the 16-byte GCM tag
```

A photo is at least 1 byte and at most 4 GiB. A record's length field is at most 1 MiB + 16 (a full record and its
tag) and more than 16; a reader refuses any other value before reading the record.

An interrupted upload is resumed from the last complete record, but only when the file on disk is exactly as long as
its record in the database says. If it is longer or shorter (a database restored from a backup made part way through
the upload, or a record written but not yet recorded when the app stopped), the upload starts over from the first
byte under a fresh random key: a counter nonce must never encrypt a second, possibly different, record under the same
key. Writing then continues at the recorded end, so a partial file is always a valid prefix of records.

## Where the key lives

- In the app's database, which is itself encrypted (SQLCipher, under the data key).
- Beside the photo, in `<file id>.key`: a small JSON record (`v`, `id`, `session`, `label`, `kind`, `name`, `size`,
  `fkey`) encrypted with standard `age` to the vault master **public** key (X25519). Only the master secret, kept on
  paper, opens it. This is what lets photos be recovered even from a database restored to a point before they were
  uploaded, or with no database at all.

Equipment receipts (the Equipment and packing lists plugin) are stored the same way: kind `receipt`, `session`
null, `label` "Receipts". They are mirrored, given fresh keys on a rotation, sealed again to a new master key and
recovered by `recover-photos` (into a `Receipts` folder) exactly like photos; they are never sealed into the vault.

A data-key rotation gives every photo a fresh key (a finished photo whose file is missing here is fetched back from
its mirror copy first), rewrites its file and its `.key` record, so an old copy of the
database opens none of them.

## Recovering photos without the app

`python -m vault.cli recover-photos <dir>` asks for the master secret, opens each `.key` record with `age`, and
decrypts the photos into a new folder. Any tool that implements this page and `age` can do the same.

## Test vectors

`tests/vectors/photo-format.json` holds two vectors (one record, and two records crossing the 1 MiB boundary) with
the key, file id, plaintext and the exact file bytes or their SHA-256. `tests/test_photo_format_vectors.py` checks
that the app's writer produces exactly those files, that the app's reader opens them, and that an independent decoder
written only from this page opens them and rejects reordered, swapped, truncated, extended and altered files.

## Client galleries

Gallery files on the web host are a different, simpler format, because each one is small and written once: a random
96-bit nonce followed by AES-256-GCM ciphertext, under one random 256-bit key per gallery. The associated data is
`lumen-aureum-gallery:<file name>`, so a host cannot serve one file in place of another. The key travels only in the
`#` fragment of the gallery link, which browsers never send to any server; the browser decrypts with WebCrypto.

### Download galleries

A download gallery also carries each photo's full-size file, re-encoded from its pixels (JPEG quality 95), sealed
the same way. The file keeps no metadata from the original. It gets two fields, in EXIF and XMP: Creator, the studio's
name, always; and the copyright notice, only when the photographer keeps the copyright for that type. The client's
name is never written into a file. For **Download all**, the app writes stored (uncompressed) zips of the full-size files
at home, with the original file names in UTF-8, split between photos into zips of about 500 MB at most. Each zip is cut
into 8 MiB pieces, and each piece is sealed as its own file under a random name. The encrypted manifest lists each
zip's pieces in order, its size, and its file name. The browser fetches the pieces, decrypts each one, and saves the
pieces in order as one file. No readable zip leaves the app's machine, and the readable copy there is deleted as soon
as its pieces are sealed.

### Galleries with a codeword

A photographer can have a gallery ask for a word the client chose. The gallery is then encrypted, as above, under:

    key = PBKDF2-SHA256(word, salt = link secret (32 bytes) || gallery ID (ASCII; for a gallery moved to its link ID in 1.4, the ID it had), 600000 rounds, 32 bytes)

The word is normalized first: Unicode NFKC, trimmed, lowercase, runs of spaces made single. The link carries the
32-byte link secret as before, plus `c=1`, which tells the page to ask for the word; the word is never printed or
sent anywhere. The link alone, or the word alone, opens nothing; with the link, every guess at the word costs the
600,000 rounds. Someone who has the link (a found letter, say) can guess offline, so a single dictionary word would
not hold for long: the app accepts only three or more different words of three letters or more, or 14 or more letters, and suggests
five random words. The key proof and the picks use this final key. Whether a gallery has a codeword is fixed when it is
first published, so a link already sent always keeps working. Test vectors: `tests/vectors/gallery-codeword.json`.

