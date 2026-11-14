# Security and privacy

Lumen Aureum holds names, addresses, children's details, payments and photos. It is built so that losing the
machine, a disk, or a copy of its data folder exposes no client records.

## What is encrypted
| Data | Where | How |
|---|---|---|
| The database (every client, session, payment, note) | the app's data folder | SQLCipher: every page, plus its journal |
| Face photos of clients | the app's data folder | AES-256-GCM, each tied to its household |
| Storage settings (SFTP/S3 credentials) | the app's data folder | AES-256-GCM |
| Daily backups | the vault (and the second location) | `age`, to your master key |
| Uploaded photos (RAWs and edited exports) | the photos folder, and one mirror copy on the backup location | The STREAM construction with AES-256-GCM in 1 MiB records, a random key per file kept in the database and sealed beside the photo to your master key with `age`; encrypted as each piece arrives, before anything is written; the mirror holds the same ciphertext. Specified in [photo-format.md](photo-format.md), with test vectors |
| Delivered sessions (RAWs and exports) | the vault | The same files, moved into the vault 7 days after the files are marked delivered (or when you seal it); the app then forgets their keys, so only the master key opens them |
| Client galleries | your web host | AES-256-GCM, each file bound to its name; decrypted in the browser. The key is in the link on the letter you mail, after the `#`, which browsers never send to a server (the app keeps a copy to rebuild the link) |

One random **data key** encrypts everything in the data folder. It is never written down in the clear: the
`keyring.json` file holds it wrapped by your **password** and by your **master key** (both standard `age`, so the
stock `age` tool can recover it without this software). The keyring carries a check value, so a keyring edited
outside the app (for example, someone swapping in their own master key) is refused at the next sign-in. The check
cannot tell an older, genuine copy of the keyring from the current one: see "Rotating the data key".

## Signing in unlocks
After any restart the app holds no key: it is **locked**, and every page asks you to sign in. Your password unwraps
the data key; it stays in memory only after your two-factor code is accepted (a key waiting for the code is dropped
after five minutes). Recovery codes stand in for the two-factor code only; they never unlock the data without your
password. `/healthz` reports `"locked": true` so a NAS can show it.

**Choose a long password.** The keyring's password wrap uses `age`'s passphrase mode, which sets how slow each guess is
from the speed of the machine it runs on; on a slow NAS each guess is cheaper for an attacker who has a copy of the
keyring. A passphrase of several unrelated words (at least 12 characters; longer is better) is what protects it.

**Forgot your password?** "Forgot your password?" on the sign-in page takes your master key plus a one-time code it
writes to `RECOVERY-CODE.txt` in your data folder (it lasts 15 minutes), and lets you set a new one. The master key
alone is not enough: whoever resets the password must also be able to open the data folder on the machine.
This signs you in without the two-factor code: the master key outranks both, which is why it lives on paper.

## Unattended unlock (off by default)
Some installs restart unattended (NAS updates). Set `CRM_UNLOCK_KEY_FILE` to a path on a **separate** volume and turn
on Unattended unlock in Settings > Security: the app then unlocks itself at start from that file. The app refuses
a key file on the same disk as its data. Anyone with both
that volume and the data folder can read everything, so keep the file where a thief would not also take it (an
encrypted dataset that is locked when the machine is off, a USB key you keep elsewhere). This trades protection at
rest for availability; leave it off unless you need it.

## What is not encrypted by the app
- **The copy on your own computer.** Photos are uploaded into the app and sealed on arrival; a download (to edit again)
  is decrypted as it is sent. The files on the computer you edit on, and your memory cards, are yours to look after.
- **Plain photo folders, if you turn them on.** `CRM_PHOTO_FOLDERS=1` lets you pick RAW and export folders on a share
  instead of uploading. Those files are ordinary files, readable by anyone who can open the share; uploading is the
  safer way.
- **A reprint copy** opened from the vault is plain for the 7 days it exists, then removed.
- **PDFs** you open in the browser (quotes, agreements, letters) may be kept by the browser in its cache or
  Downloads. Delete downloads you don't need; on a laptop, use full-disk encryption.
- **Memory.** While unlocked, the data key is in the app's memory. A swap or hibernation file can hold memory on disk;
  use encrypted swap, or run on a machine that doesn't hibernate.
- **A running, unlocked machine** that is taken while on is protected only by the network allow-list and sign-in.

Galleries on a web host that runs PHP expire by themselves: the gallery page fetches every encrypted file through a
small gate script that refuses once the window is over (or if its window file is missing). On Apache, the folder's
.htaccess also refuses the encrypted files except through the gate. On other web servers, someone who already holds
the link and fetches the encrypted files by name could still get them until the app removes the gallery, which it
does on its next check while unlocked. The host keeps only a hash of the key proof, never the proof or the key.
Galleries on a plain S3 bucket are removed by the app, which needs to be unlocked to do it.

What a gallery link can and can't protect:
- **The link is the key.** Anyone holding the full link (with what follows the `#`) can open the gallery, the same as
  the client. A codeword adds a second thing they must know. On a PHP host the gate also refuses the encrypted files
  to anyone who can't prove they hold the key, so a codeword can't be guessed offline there; on a host without PHP,
  someone with the link could download the files and guess the codeword at their own pace, which is why the app
  refuses short or common codewords.
- **The host serves the page.** A web host that turns hostile could change the gallery page so that it sends the key
  somewhere when a client opens it. Use a host you trust.
- **Expiry needs the host's help.** The window is kept in two small files on the host (the first-visit stamp and the
  limits file). A host that alters them can keep a gallery up longer; the app still removes it on its next check.
- **What was downloaded stays downloaded.** A client who saved photos while the gallery was open keeps them.

## On the network
- HTTPS only, always: a certificate the app makes on first start (its SHA-256 fingerprint is printed in the container
  log to check against the browser), or your own. Lumen Aureum never serves plain HTTP. Session
  cookies are marked Secure.
- Requests must name the app by an IP address, `localhost` or a name in `CRM_HOSTNAMES` (DNS-rebinding defense).
- Forwarded headers are believed only from `CRM_TRUSTED_PROXIES` and refused from anyone else; the Docker gateway is
  refused unless listed, so a proxy can never silently pass the allow-list.
- A new install can only be claimed with the one-time setup code written to `SETUP-CODE.txt` in the data folder (and
  the log); the file is deleted once the owner account exists.
- Failed sign-ins lock out the source address for 15 minutes after five tries (the lockout is per source address, so it never locks the owner out of every device), at most two
  password checks run at once, and the owner is told of failed attempts at the next sign-in.
- A two-factor code works once. Signing out ends the session on the server; changing the password or recovery codes
  signs out every other device; Settings > Security can sign out every other device.
- Pages carry a Content Security Policy (no framing; scripts and connections only from the app itself; no inline
  script without the page's own nonce), nosniff and no-framing headers. Inline styles are still allowed.
- Galleries: client galleries are linked over https only; the viewer refuses to open on an insecure page.

Pages are sent with `Cache-Control: no-store`, so the browser does not keep client pages on disk, and the app keeps
no access log (request lines can carry search terms).

## Deleting a client
There is no undo: their galleries come down, their photos and archives are destroyed, and every record and
history line about them is erased. Sealed daily backups still contain the record until they age out (the 30 most recent daily backups are kept, and the
second location's copies on the same schedule). A small list of deletions, `deletions.json`, is kept outside the
database: if an older backup is ever restored, those clients are deleted again at the next sign-in. The list holds
only record numbers and creation times, no names.

Each entry in that list is signed with a key kept inside the encrypted database, so someone who can write to the data
folder but has no key cannot add an entry and have a client deleted at the next sign-in. An entry whose signature does
not check is never applied: it is moved to `deletions.unverified.json` and a line in the history says how many were
set aside. The key outlives a data-key rotation and comes back with any backup, so a restored backup still honors
deletions made after it (a backup from before 1.0.1, restored after a key rotation, cannot check them; those entries
are set aside the same way, not applied). Lists written by 1.0.0 carry no signatures: at the first sign-in on 1.0.1,
an entry whose client is already gone is signed (the deletion already happened), and an entry whose client is still
there is set aside and noted on that client's history instead of deleting them, because an unsigned file cannot prove
you asked for it.

Nothing set aside is ever applied on its own, but it isn't forgotten either. If a set-aside entry names a client who is
still here (for example after restoring a backup from before 1.0.1 following a key rotation, where genuine deletions
can't be checked), Today says so and links to a page listing those clients by name, with the date each was deleted
when it is known. **Apply these deletions** deletes them through the normal Delete, so each deletion is recorded again,
signed with the current key. **Keep them** leaves the clients as they are and marks those entries dismissed in
`deletions.unverified.json`, so the notice doesn't come back. Both are ordinary signed-in forms.

## Record numbers
Session, client, step (to-do), and event numbers are never handed out twice, even after the newest one is deleted. A
page left open on a deleted session, client, step, or event can't save into a new, unrelated one: anything sent from it
gets a page saying it was deleted, and nothing is changed. The highest number issued is kept in `doc-numbers.json` beside the
database (it survives restoring an older backup) and in a small table inside it (the database alone is enough if that
file is lost); at each sign-in both are raised to at least every number the database, its History and the deletions
list have seen.

## Rotating the data key
Changing your password or turning off unattended unlock stops the old password or key file from opening the
*current* keyring, but anyone who kept a copy of an older `keyring.json` (with the old password or old key file) can
still unwrap the same data key. Only a rotation revokes that. So if a password, a keyring copy or an unlock key file
may have leaked, stop the app and run `python -m vault.cli rotate`. Everything is
re-encrypted under a new key, uploaded photos included (each gets a fresh key and is rewritten; their mirror
copies are refreshed afterwards) - staged and checked first, swapped in last; an interrupted rotation is finished by
running the command again, and the app will not start until it is. It refuses to run while the app is running. Unattended unlock is turned off by a rotation.
Older backups keep their own keyring and still open with the master key.

## Restoring a backup
Follow **Restoring a backup** in [operations.md](operations.md#restoring-a-backup). It is the one place with the
steps, and it lists every file a restore involves. Moving only the database and its keyring is not enough: without
`deletions.json` and `doc-numbers.json`, clients you deleted come back and invoice and receipt numbers repeat.

What matters for security: `restore` asks for the master key, checks the backup, and writes into a new folder; it
never touches the running install. The master key opens the backup, and the keyring inside it opens the database, so
on a new machine the vault folder and the master key are all you need.

If the data key was rotated after the backup was made, `python -m vault.cli relink-photos` puts each photo's current
key back into the restored database. The key sealed beside each photo is sealed to the master *public* key, so anyone
able to write to the photos folder could write one, with a photo to match. Relink only takes a key whose record carries
a tag made with the key the restored database holds (each rotation adds one, with the key it retires); anything else is
refused and left for `recover-photos <folder> --from <photos folder>`, which decrypts photos into a separate folder without touching the database.
Photos rotated under 1.0.0 have no such tag, so relink refuses them too; that command still opens them.

## One process
The data key lives in the memory of one process. Run the app as a single process (threads are fine); several worker
processes would each need their own sign-in.
