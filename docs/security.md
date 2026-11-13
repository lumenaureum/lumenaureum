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
Settings has no undo for it: their galleries come down, their photos and archives are destroyed, and every record and
history line about them is erased. Sealed daily backups still contain the record until they age out (the 30 most recent daily backups are kept, and the
second location's copies on the same schedule). A small encrypted list of deletions is kept outside the database: if
an older backup is ever restored, those clients are deleted again at the next sign-in.

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
`python -m vault.cli restore <name> <new-folder>` asks for the master key and writes `crm.db`, `keyring.json`,
`settings.json` and `storage.json` (backups made before settings were included have only the first two) into a new
folder, checked; it never touches the running install. `--settings-only` writes just `settings.json`. **On a new machine** (the old one lost), nothing else is
needed: copy or mount the vault folder (or download the backup's `db/...age` file into a folder of the same layout) and
add `--from <that folder>`. The master key opens the backup, and the keyring inside it opens the database. To run
from the restored copy, put `crm.db`, `keyring.json` and `storage.json` in a fresh data folder and sign in with the password that was current when the
backup was made (or reset it with the master key).

## One process
The data key lives in the memory of one process. Run the app as a single process (threads are fine); several worker
processes would each need their own sign-in.
