# The sealed formats

Lumen Aureum is security-focused: everything it keeps or sends is sealed with standard primitives, in one of three
formats, described in these docs so anyone can check them.

## The three formats, in plain words

- **Files kept at rest.** Photos (and later other files) the app keeps at home and in the vault: any size, sealed in
  pieces, each file under its own key. The format knows nothing of what the file is. See `docs/photo-format.md`.
- **Small sealed messages.** What a client's or visitor's browser sends back through your web host (picks, a draft,
  an inquiry, a print order): sealed in the browser before it leaves the device, one purpose each, bound to the link
  it was sent from, size-capped per purpose. The host keeps only files it can't open. This document.
- **Link files.** What a client's browser downloads from the web host to see a gallery (`manifest.bin`, `offer.bin`,
  the photo files and Download all pieces): no header, random names, sealed under the link key. Described below.

The rule of thumb: kept at home, any size: the first. Small, travels, one purpose: the second. Downloaded by a client
from the web host: a link file. Too big for a purpose's cap: not a small sealed message.

Their formal names (in these format docs only):

- LAP = Lumen Aureum Perpetuum: files kept at rest, in pieces.
- LAS = Lumen Aureum Sigillum: small sealed messages, one purpose each.
- Link files have no formal name.

Not in any of the three: the database (SQLCipher, under the data key), the keyring and each photo's `.key` record
(standard `age`), backups and the deletion list (see `docs/security.md`), and the studio page, which is public by design.

## Links and link IDs

Everything a client or visitor reaches on the web host is a **link**: today a gallery, later a session's or an
event's page. Each link has its own folder on the web host, named by its **link ID**, and its own **link key**: 32 random
bytes that reach the client after the "#" of the link (which browsers never send to any server). A link's ID and key
are made at random for it alone, never derived from a client's, a session's, or anything else's ID or key, and a link ID
is not a session ID.

A link ID is 25 characters: its **kind** letter, "-", then 23 characters from `A-Z a-z 0-9 - _` (a fresh 17-byte random
value in base64url). The kinds:

| Kind | Meaning | Purposes it can carry |
|---|---|---|
| `g-` | a gallery | picks, draft, print-order |
| `s-` | a session (reserved) | none yet |
| `e-` | an event (reserved) | none yet |
| `c-` | a client (reserved) | none yet |
| `t-` | a team (reserved) | none yet |

- The kind is part of the ID's bytes wherever the ID is bound (below): it is never stripped, so a message sealed for one
  kind of link can never be taken for another.
- The alphabet has no ".": a link ID never ends like a plugin's own folder on the host (those end ".d").
- No ID made before 1.4 can have this shape: those were Python's `token_urlsafe` output, whose length is never one more
  than a multiple of four, and 25 is. The 1.4 update moved every gallery to a new `g-` link ID; its old folder keeps
  only a page that sends the browser on to the new one (a script that keeps the "#" part, so the link key carries
  across), for the rest of that gallery's life. Since then every ID follows the one rule above.
- An empty ID (a studio inquiry, which comes from the public studio page, not from a link) is bound by its purpose
  alone.

The word "gallery" in a few byte strings of the formats (the associated data of link files, `lumen-aureum-gallery:<file
name>`) is historical: those bytes are part of the cryptography and do not change with the names. So is the settings
key `gallery_host` (in `storage.json` and exported settings files), which names the web host: a stored key, kept as it is.

## Small sealed messages (LAS1)

The test vectors are in `tests/vectors/sealed-v1.json`; the same file is passed by the three implementations:
`sealed.py` (the app), `plugins/sealed_assets/sealed.js` (the browser; the gallery page and the inquiry form carry
exact copies) and `plugins/sealed_assets/sealed.php` (the web host, which only checks shape and never holds a key).

### Primitives

Only standard primitives that the app, every current browser (WebCrypto), and the `cryptography` library already
offer: AES-256-GCM (96-bit nonce, 128-bit tag), HKDF-SHA256 (RFC 5869, 32-byte output), ECDH on P-256 with a one-time
key (the shared secret is the 32-byte x-coordinate), SHA-256, and the platform's random number generator. No new
dependency. HPKE (RFC 9180) describes the same idea; it is not used because neither browsers nor PHP offer its P-256
suite today, so it would have to be assembled by hand.

### Layout

All integers are single bytes; there is no padding and nothing after the ciphertext.

```
offset   size   field
0        4      magic "LAS1": the format and its version
4        1      mode: 01 link, 02 studio, 03 studio+link
5        1      purpose code
6        1      N, the length of the link ID: 0 in mode 02, 25 otherwise
7        N      link ID: its kind letter, "-", 23 of A-Z a-z 0-9 - _
--- modes 02 and 03 only ---
7+N      8      kid: the first 8 bytes of SHA-256 of the recipient's public key (65 bytes, uncompressed)
15+N     65     epk: the sender's one-time P-256 public key, uncompressed
--- all modes ---
H        12     nonce (H = 7+N in mode 01, 80+N in modes 02 and 03)
H+12     L+16   AES-256-GCM ciphertext of the plaintext (at least 1 byte), then the tag
```

The **binding** is bytes 0 to 7+N (magic, mode, purpose, N, link ID with its kind); the **header** is everything
before the nonce. Every purpose's plaintext is one UTF-8 JSON object with its own `"v"` field and its own **item ID**.

### Purposes

| Code | Name | Mode | Link kinds | Largest envelope | Item ID | Sealed by | Opened by |
|---|---|---|---|---|---|---|---|
| 01 | picks | link | g | 262144 bytes | `id` | the gallery page | the app, the gallery page |
| 02 | draft | link | g | 262144 bytes | `id` | the gallery page | the app, the gallery page |
| 03 | inquiry | studio | none (no ID) | 16384 bytes | `id` | the studio page's form | the app |
| 04 | print-order | studio+link | g | 65536 bytes | `oid` | the gallery page | the app |

A purpose has exactly one mode and a fixed set of link kinds. Codes are never reused; 00 and 80 to FF are never valid.
A new purpose, or a purpose for a reserved kind, is an addition to this table and the vectors, not a new version.

### Item IDs

Every payload carries an item ID: 32 lowercase hex characters, 128 random bits from the browser that sealed it (`id`;
print orders have always called theirs `oid`). The web host can hand the app the same item twice, drop one, change
their order, or put an older one back in place of a newer one; it can't make a new one. So the app takes each item in
once, by its ID, and an item older than the newest it has taken in for the same link and purpose never replaces it. A
payload without a valid item ID is not read. (Picks and drafts carried over from 1.3 by the 1.4 update had none: the app
gave each one made from its own bytes when it sealed them again.)

### Keys

```
info  = "lumen-aureum sealed" 00 || binding
AAD   = header
nonce = 12 fresh random bytes for every envelope

mode 01 link:          key = HKDF-SHA256(ikm = K,       salt = empty,      info)
mode 02 studio:        key = HKDF-SHA256(ikm = Z,       salt = epk || rpk, info)
mode 03 studio+link:   key = HKDF-SHA256(ikm = Z || K,  salt = epk || rpk, info)
```

K is the link key (the 32 bytes in the client's link, or the key derived from the link and the codeword). rpk is the
recipient's public key (the studio's), epk the sender's one-time public key, made for that one envelope and then
discarded; Z is their ECDH shared secret. An empty salt is the RFC 5869 default (32 zero bytes). Mode 03 feeds the
key-agreement secret followed by the link key into HKDF, the "hybrid" input of NIST SP 800-56C rev. 2.

What this gives:
- **Bound to its purpose and its link, twice.** The binding (with the link's kind) is in the key derivation and in the
  associated data, so a draft can't be opened as sent picks, one link's picks can't be passed off as another's, and a
  purpose can't be carried by a kind of link it doesn't belong to.
- **No host or address inside.** A link moved to another host keeps working, and the app may copy an envelope between
  hosts byte for byte.
- **Each purpose and link has its own key**, derived from the link key; link files use the link key directly and are
  unchanged.
- **Mode 03**: only the app can open it (it needs the studio's private key), and only someone holding the link could
  have made it; the host, which knows the public key, can't invent one, and someone who later holds the link can't read
  it back.
- In mode 01 the key is fixed per purpose and link and nonces are random; the usual limit of 2^32 messages per key is
  about 200 years of drafts saved every 1.5 seconds.

### Checking

Every implementation checks in the same order and stops at the first failure, so all three give the same answer. The
inputs are the envelope, the purpose expected, the link ID expected ("" for a studio purpose), and optionally the key ID
expected.

| Step | Test | Error |
|---|---|---|
| 1 | shorter than 4 bytes | E_SHORT |
| 2 | bytes 0 to 2 are not "LAS" | E_MAGIC |
| 3 | byte 3 is not "1" | E_VERSION |
| 4 | longer than the purpose's largest envelope | E_LONG |
| 5 | shorter than 7 bytes | E_SHORT |
| 6 | mode is not 01, 02, or 03 | E_MODE |
| 7 | purpose byte is not the expected purpose's code | E_PURPOSE |
| 8 | mode is not the purpose's mode | E_MODE |
| 9 | N is not 0 (mode 02), or not 25 (modes 01, 03) | E_SCOPE |
| 10 | shorter than 7 + N | E_SHORT |
| 11 | a link ID byte outside the allowed set, no "-" after its kind letter, or another link ID than expected | E_SCOPE |
| 12 | the link's kind is not one the purpose belongs to | E_KIND |
| 13 | shorter than the header, the nonce, and 17 bytes | E_SHORT |
| 14 | modes 02, 03: epk does not start with 04 | E_POINT |
| 15 | modes 02, 03, when a key ID is expected: another key ID | E_KID |

The web host adds one more test: if the ciphertext is 64 bytes or longer and more than 85% of its bytes are
printable text, it is refused (E_TEXT), so the scripts can't be used to park readable text. The host checks a message
against its own folder's name, so a message sealed for a link of the wrong kind is refused there too.

Opening (the app, all modes; the browser, mode 01 only) then needs a private key for the key ID (E_KID), a valid point
for epk (E_POINT), and a tag that verifies (E_AUTH). E_AUTH covers a wrong key, a wrong link, a changed header, and
changed ciphertext alike. Sealing refuses an unknown purpose, an ID that isn't a link ID (E_SCOPE), a link of a kind the
purpose doesn't belong to (E_KIND), an empty or oversized plaintext, and a key of the wrong size or shape.

### Transport and storage

- **Sent** as one form field, `d`, in an `application/x-www-form-urlencoded` POST: base64url without padding. Decoding
  refuses an empty string, any other character (no `=`, `+`, `/` or white space), a length leaving a remainder of 1
  when divided by 4, and a string that isn't the exact re-encoding of its bytes. The host checks the string's length
  before decoding.
- **Kept** (on the host, and the copies the app keeps of the client's picks) as the raw bytes, in a file whose name ends
  `.las`: `<link ID>/picks.las`, `<link ID>/draft.las` (and from 1.6 each device's own `picks-<device>.las` and
  `draft-<device>.las`, `<device>` being 16 random hex also sealed in the payload as `d`), `inquiries/<sender>-<content>.las`,
  `<link ID>/orders/<content>.las`.

### Test vectors

`tests/vectors/sealed-v1.json` holds the link kinds and the purpose table, the shared keys, valid envelopes (with every
intermediate value: info, salt, key input, key, associated data), invalid ones with the answer each step must give
(check, host check, open), and transport strings. It is frozen with the release that ships it: a test pins its
SHA-256, and a later version adds a new file.

### Signatures, later

LAS1 carries no signature. A later purpose that records a signature (a client signing an agreement) must carry, inside
its payload, the SHA-256 of the exact link file that was signed (the agreement as the client's browser downloaded it),
so what was signed can never be argued about afterwards. A real digital signature would be a layer on top, not part of
this format.

### A version 2

A new version is a new magic, `LAS2`, and a new vectors file; there is no algorithm choice inside a version and no
negotiation. A reader picks its code path from the magic and refuses what it does not know (E_VERSION). The app, the
host's check, and the pages learn a new version in one release, with the host scripts going up before the page that
seals with it. Stored envelopes are never rewritten in place: they are carried over as new files beside the old ones,
checked, and the old ones removed last, as the 1.4 update did for the forms before this one. What would not fit LAS1
without a new mode or version: anything sealed for more than one recipient at once (a new mode), large uploads (those
go to the web host as files and are kept at home in the at-rest format), post-quantum keys (a new version).

## Link files

What a client's browser downloads from a link's folder: `manifest.bin` (what the gallery holds), `offer.bin` (print
prices and the studio key for print orders), the photo files and Download all pieces (`<24 hex>.bin`). Each is a
12-byte nonce, then AES-256-GCM under the link key directly, with `lumen-aureum-gallery:<the file's own name>` as
associated data (the word "gallery" there is historical, see above), so a host can't serve one file in place of
another. The page itself and its scripts are public code, not sealed. A codeword gallery's link key is
PBKDF2-SHA256(the codeword, salt = the link's 32-byte secret followed by the gallery's ID, 600000 rounds); a gallery
that moved to its link ID in 1.4 keeps salting with the ID it had (its page names it, `data-cws`), so its link and
codeword open the same key as before. `tests/vectors/gallery-codeword.json` has the vectors.

Expected later: the agreement a client reads before signing, video files, and a gallery delivered without a web host
(the same sealed files on a USB key or as one download).
