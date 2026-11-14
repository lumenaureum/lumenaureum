# The sealed photo format (LAP1)

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

A photo is at least 1 byte and at most 4 GiB. An interrupted upload is resumed from the last complete record: any
bytes after it are cut off before writing continues, so a partial file is always a valid prefix of records.

## Where the key lives

- In the app's database, which is itself encrypted (SQLCipher, under the data key).
- Beside the photo, in `<file id>.key`: a small JSON record (`v`, `id`, `session`, `label`, `kind`, `name`, `size`,
  `fkey`) encrypted with standard `age` to the vault master **public** key (X25519). Only the master secret, kept on
  paper, opens it. This is what lets photos be recovered even from a database restored to a point before they were
  uploaded, or with no database at all.

A data-key rotation gives every photo a fresh key, rewrites its file and its `.key` record, so an old copy of the
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

### Galleries with a codeword

A photographer can have a gallery ask for a word the client chose. The gallery is then encrypted, as above, under:

    key = PBKDF2-SHA256(word, salt = link secret (32 bytes) || gallery id (ASCII), 600000 rounds, 32 bytes)

The word is normalized first: Unicode NFKC, trimmed, lowercase, runs of spaces made single. The link carries the
32-byte link secret as before, plus `c=1`, which tells the page to ask for the word; the word is never printed or
sent anywhere. The link alone, or the word alone, opens nothing; with the link, every guess at the word costs the
600,000 rounds. Someone who has the link (a found letter, say) can guess offline, so a single dictionary word would
not hold for long: the app accepts only three or more different words of three letters or more, or 14 or more letters, and suggests
five random words. The key proof and the picks use this final key. Whether a gallery has a codeword is fixed when it is
first published, so a link already sent always keeps working. Test vectors: `tests/vectors/gallery-codeword.json`.

