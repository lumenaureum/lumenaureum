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

**Face photos** are the optional picture you can add on a client's page, so you recognize them when you meet. They
appear only inside the app (a client without one shows initials instead), never in a gallery or on the studio page,
and nothing in the app analyzes or recognizes faces.

One random **data key** encrypts everything in the data folder. It is never written down in the clear: the
`keyring.json` file holds it wrapped by your **password** and by your **master key** (both standard `age`, so the
stock `age` tool can recover it without this software). The keyring carries a check value, so a keyring edited
outside the app (for example, someone swapping in their own master key) is refused at the next sign-in. The check
cannot tell an older, genuine copy of the keyring from the current one: see "Rotating the data key".

## Signing in unlocks
After any restart the app holds no key: it is **locked**, and every page asks you to sign in. Your password unwraps
the data key; it stays in memory only after your two-factor code is accepted (a key waiting for the code is dropped
after five minutes, and at once whenever the password, the master key or the recovery codes change, or you sign out
every other device, so a sign-in started before the change can't finish after it). Recovery codes stand in for the two-factor code only; they never unlock the data without your
password. `/healthz` reports `"locked": true` so a NAS can show it.

The app never unlocks itself. Earlier versions had an optional unattended unlock from a key
file on another volume; 1.5 removed it, so a restart always leaves the studio locked until you sign in.

**Idle sign-out.** A sign-in ends after a time with no tap and no page opened (8 hours by default; Settings, Security,
**Sign out when idle**, 1 to 72 hours). About 2 minutes before, a warning offers **Stay signed in**. Only a real tap
counts: a page's own background requests (status checks, refreshes) never keep a sign-in alive. What you typed and did
not save stays on the page, and the sign-in page says why you were signed out. Updating to a new version signs every
device out.

**Choose a long password.** The keyring's password wrap uses `age`'s passphrase mode (scrypt), which sets how slow
each guess is from the speed of the machine it runs on; on a slow NAS each guess is cheaper for an attacker who has a
copy of the keyring. The library the app uses has no setting for a minimum, and the app does not build its own
encryption to get one. A passphrase of several unrelated words (at least 12 characters; longer is better) is what
protects it. Settings > Security shows the work factor your wrap has, and says when it is below 18, the setting the
standard `age` tool uses on every machine.

To strengthen a wrap a slow machine made: stop the app, copy `keyring.json` to a faster computer with Lumen Aureum's
code, run `python -m vault.cli rewrap-password <path to the copy>` there and enter your password, then put the copy
back in the data folder and start the app. The password stays the same. The command changes nothing when the faster
computer is no faster, or when it is so much faster (more than 3 above the current work factor) that the slower
machine might be unable to check the password: `age` refuses a work factor more than 4 above the one it would choose
itself. If that ever happens, the sign-in page says so, and Forgot your password sets the password again.

**Forgot your password?** "Forgot your password?" on the sign-in page takes your master key plus a one-time code it
writes to `RECOVERY-CODE.txt` in your data folder (it lasts 15 minutes), and lets you set a new one. The master key
alone is not enough: whoever resets the password must also be able to open the data folder on the machine.
This signs you in without the two-factor code, on purpose. The master key plus the data folder already open
everything without signing in at all: the stock `age` tool unwraps the data key from `keyring.json` with the master
key, offline, and the data key opens the database and every file. A two-factor check on this page would stop no one
who holds both, and would only lock you out if you lost your phone along with your password. The master key outranks
the password and the two-factor code alike, which is why it lives on paper: keep it as carefully as the data itself.

## Notices while locked
While the app is locked, backups and gallery checks wait, so a restart while you are away (a power cut, a NAS update)
can go unnoticed for days. The locked-studio notice sends a message to your phone when that happens. It is optional and off until you set it up in Settings,
Notifications: the address of an ntfy server, a topic, and an access token if the server needs one. **Send a test
notice** checks it. Once ntfy is set up, **Studio locked after a restart** shows as a ticked box that stays on. The
first time the studio restarts locked without it, Today suggests it once.

When the studio starts locked, the app sends a notice, then one a day until you sign in: "Your studio restarted and
has been locked since <day and time>. Backups and gallery closings are waiting until you sign in." The notice never
carries a link, a client's name, a count, an amount or anything else from your data. Sign in from the app's own
address as usual. It goes over HTTPS only. A notice that fails is written to the app's log and tried again an hour
later at the soonest. A restart while the studio is still locked keeps the time it first locked and sends nothing
more than the one a day. When you sign in after 15 minutes or more locked, Today says once since when the studio was
locked; the backups and gallery closings that waited catch up then.

The app has no key while it is locked, so it can't read the server address, topic and token from the encrypted
database. It keeps a copy of them, with the studio's time zone, in a small plain file in the data folder,
`ntfy.json`, and anyone who can read the data folder can read them. Use a topic nobody could guess, and an access
token where the server takes one. ntfy can run on your own machine (it is also in the TrueNAS app catalog), with a
private topic and a token. Your phone must then be able to reach that server when you are away from home, over a VPN
for example, or notices arrive only at home.

## What is not encrypted by the app
- **The copy on your own computer.** Photos are uploaded into the app and sealed on arrival; a download (to edit again)
  is decrypted as it is sent. The files on the computer you edit on, and your memory cards, are yours to look after.
- **Plain photo folders, if you turn them on.** `CRM_PHOTO_FOLDERS=1` lets you pick RAW and export folders on a share
  instead of uploading. Those files are ordinary files, readable by anyone who can open the share; uploading is the
  safer way.
- **A reprint copy** opened from the vault is plain for the 7 days it exists, then removed.
- **The studio page's builds.** Preview, and Upload, build the site as plain files in `data/site/` (pages, and photos
  at web size with their copyright mark, as they appear online). The preview stays until the next upload, a take-down,
  or the app locking or restarting; an upload's build stays only until it has gone up (or, while it waits for an
  approval, until the app locks). Thumbnails on the Studio page are kept in memory only, and cleared on lock.
- **PDFs** you open in the browser (quotes, agreements, letters) may be kept by the browser in its cache or
  Downloads. Delete downloads you don't need; on a laptop, use full-disk encryption.
- **Memory.** While unlocked, the data key is in the app's memory. A swap or hibernation file can hold memory on disk;
  use encrypted swap, or run on a machine that doesn't hibernate. The app never writes its memory to disk itself: core
  dumps are off (the process is marked not dumpable and its core file size is 0, and `compose.yaml` sets the same), so
  a crash leaves no copy of a key or of client data on the machine's disk.
- **The ntfy settings** (server, topic, access token, time zone), in `ntfy.json` in the data folder, so the locked
  studio can send its notice, and `lock-state.json` beside it: when the studio locked and when the last notice went,
  times only. See [Notices while locked](#notices-while-locked).
- **Kept copies of web host files.** When the studio page replaces files of yours on the web host (Replace these
  files), a copy of each old file is kept in `data/site-kept/` as it was on the host, so it can be put back. They are
  the public files of your website.
- **A running, unlocked machine** that is taken while on is protected only by the network allow-list and sign-in.

Galleries on a web host that runs PHP expire by themselves: the gallery page fetches every encrypted file through a
small gate script that refuses once the window is over (or if its window file is missing). The host keeps only a hash
of the key proof, never the proof or the key. Galleries on a plain S3 bucket are removed by the app, which needs to be
unlocked to do it. The app also removes every gallery on its first check after the window ends, on any host.
Outside the encrypted files, a gallery's host holds only its dates and your studio email: the gate gives the email to
a visitor once the gallery has closed, and the page left at an expired gallery's link shows your studio name and email.
The client's name and the photos are never on it.

**Adopting a gallery already online** reads it with the key in the link you paste, and writes nothing on the host. It
is refused unless the gallery's folder is on the web host in your settings, the app can write there with its own
credentials (it writes and removes a small test file in that folder), and the key opens the gallery's manifest.
Bringing it up to date rebuilds it at the same link only after the old gallery has been read in full.

Deleting a session or client takes its gallery down from the host in use (the delete stops if that fails) and from any
earlier web host that still holds a copy. A copy that can't be removed then (the host can't be reached) is kept in
a list of gallery folders still to come down, tried again by the regular check (an unreachable host once a day) and by
Take the old copies down; Today mentions it until it is down. While a delete runs, the session's gallery can't be put
back online, uploaded or rebuilt, and the delete checks once more, just before the records go, that it is down.

What a gallery link can and can't protect:
- **The link is the key.** Anyone holding the full link (with what follows the `#`) can open the gallery, the same as
  the client. A codeword adds a second thing they must know. The files a codeword could be tested against (the
  manifest, the print offer, the client's picks and draft) are kept on the web host under names made from the key,
  so someone holding only the link can't even find them, on any web server; on a host that runs PHP the gate also
  refuses them to anyone who can't prove they hold the key. Each guess at a codeword is one request through the gate,
  and after 10 wrong guesses in an hour at one gallery, every request must carry a small proof of work (about a second
  in a browser, once an hour), so guessing gets slow and nobody is ever locked out, whoever else shares their address.
  A web host that hands gallery files out without asking for the key (Setup's host check tests a real gallery for
  this), or one that runs no PHP, would let someone with the link try codewords as fast as their computer allows, so
  the app refuses galleries with a codeword there and says why. It also refuses short or common codewords.
- **The host serves the page.** A web host that turns hostile (or is broken into) could change the gallery page so
  that it sends the key somewhere when a client opens it. No web page can fully prevent that, whoever serves it: it
  is a limit of encryption delivered by a web page, not something a setting or a choice of host protects against.
  What the app does: about once an hour, at the regular gallery check, it fetches each online gallery's page from its
  public address and reads the page and the gallery's scripts back from the host, and compares every byte with what it
  put there. A difference seen at two checks in a row shows on Today and in the Gallery block on the client page, with Take down now
  and Put the published page back. This finds a change after it happens; it can't stop a client opening a changed
  page before the next check.
- **Expiry needs the host's help.** The window is kept in two small files on the host (the first-visit stamp and the
  limits file). A host that alters them can keep a gallery up longer, and one that hands out gallery files without the
  gate doesn't close a gallery by itself; the app still removes it on its next check.
  Nothing a visitor writes to the gallery folder (picks, a draft) can stop that check.
- **What was downloaded stays downloaded.** A client who saved photos while the gallery was open keeps them.

### What is sealed, and how
Everything a gallery sends back to the app, and everything a visitor writes in the studio page's form, is sealed in
the browser in one shared format, LAS1 ([Sealed formats](sealed-format.md)), before it leaves the device. The host
keeps it as a `.las` file it can't open, and checks only its shape. Each gallery is a link: its folder on the web host
is named by its link ID (`g-` and 23 random characters), and its link key (the part of the client's link after "#")
is its own, made at random for it alone.

| Item | Sealed by | File on the host | Who can open it |
|---|---|---|---|
| The client's picks | the gallery page, on Send my picks | `<link ID>/picks.las` | the app, and the client's browser (to show what was really sent) |
| The picks still being made | the gallery page, a moment after each change | `<link ID>/draft.las` | the app, and the client's other devices |
| A studio page inquiry | the visitor's browser | `inquiries/<sender>-<content>.las` | only the app |

Picks and drafts are sealed under a key derived (HKDF-SHA256) from the link key for that one purpose and that one
link (its kind included), so a draft can't be passed off as sent picks, and one gallery's picks can't be passed off as
another's. Every item carries its own item ID: the app takes each in once, and an older one a host hands back never
replaces a newer one.
Inquiries are sealed to the studio's public key with a one-time key (ECDH P-256, HKDF-SHA256, AES-256-GCM), and carry
the studio key's ID, so the key can be changed later without losing an inquiry on its way. Every envelope's header is
authenticated in full; the host's address is in none of them, so a gallery moved to another host keeps working.

The link files (`manifest.bin`, and the photo and Download all files with random names) are not envelopes: each is a
12-byte nonce, then AES-256-GCM under the link key, with the file's own name as associated data, so the host can't
serve one file in place of another. Anyone with the link (and the word, if set) can open them; nobody else.

On the web host, `manifest.bin`, `offer.bin`, `picks.las` and `draft.las` are kept under names made from the key
proof: the first 24 hex characters of SHA-256("lumen-aureum file name", 0, the plain name, 0, the proof), with the
same extension. The gate and `picks.php` work the name out from the proof a visitor sends; the page does too, where no
gate runs. The photo and Download all files already have random names that only the sealed manifest lists. So none of
a gallery's sealed files sits at a name anyone can guess. (Below, each is called by its plain name.)

Galleries online before 1.4 had shorter folder names and sealed picks in an earlier form. The 1.4 update moves every
gallery to its link ID and carries every pick, draft and order sent before over once (see
[Operations](operations.md#the-14-update)); the old folder keeps only a page that sends the browser on to the new one.
1.4 reads only the new form.

### The PHP files on a web host
Each gallery folder holds a few small PHP files beside the encrypted photos. Every file that keeps the gallery's state
begins with a PHP exit line (`<?php exit; ?>`), so on a host that runs PHP, fetching it shows nothing, whatever web
server the host runs; nothing in the folder depends on `.htaccess` to stay private. Every script answers with an
`X-Host-Scripts` header naming its set (1.5.0), on every answer, so the app can tell the scripts it put there are the
ones running. What each does:

| File | What it does |
|---|---|
| `limits.php` | The gallery's window: how many days after the first visit, and the date it closes if nobody opens it (with the exact instant that day ends in your time zone, written again when the time zone changes). The app writes it at upload and on every change. After Move galleries, the copy left on the earlier host is marked moved (with your email, which earlier copies didn't carry), and a page built before 1.3 there is replaced with this version's, which reads the same files and tells the client the gallery moved. It also says whether the gallery has a codeword, and whether the host sits behind a proxy or CDN. Galleries from before 1.4 had it as `limits.txt`; the 1.4 update moves it. |
| `seen-key.php` | The SHA-256 of the key proof (the proof is the first 32 hex characters of the SHA-256 of the link key). It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `seen.php` | Records the first visit in `seen-at.php` (before 1.4, `seen.txt`), once, and only for a visitor who sends the key proof. A stamp left empty is replaced. |
| `gate.php` | Serves the encrypted photo files, `manifest.bin`, `offer.bin`, `draft.las` and `picks.las`, and only while the window is open. A missing or unreadable `limits.php` counts as closed. It asks for the key proof for every file, and finds the sealed files by the names the proof gives. A one-time download's list and end go in `once.php` and `done.php`. It never serves anything else. |
| `picks.php` | Saves the client's picks (`picks.las`) or the picks still being made (`draft.las`). It takes one form field holding a sealed envelope (LAS1, purpose picks or draft, bound to this gallery's folder name, its link ID), up to 256 KB, that doesn't look like readable text, from a visitor who sends the key proof, and only while the window is open, under the name the proof gives. Wrong proofs count toward the same limit as the gate's. On a copy marked moved it saves nothing and answers with your email. |
| `orders.php` | Keeps a print order sent from a download gallery, as `orders/<hash>.las`, for the app to collect. It takes one sealed envelope (LAS1, purpose print-order: sealed to your studio key with the link key mixed in, bound to this gallery's folder name and the studio key's ID, at most 64 KB and padded to one of five sizes), with the key proof, a proof of work, and only while the window is open, more than 24 hours before the gallery closes, and prints are on (`offer-kid.php` is there). 5 orders an hour and 10 waiting from one sender (behind a proxy or CDN, where every client seems to share an address, only the gallery's own limits), 100 files and 6.5 MB waiting in all. Past the hourly limit it answers with Retry-After (when the oldest of the five is an hour old), so the gallery page tells the client when to try again. It never reads, lists or serves an order, and `orders/` holds an empty `index.html`, so a web server that lists folders shows nothing there. |
| `offer.bin` | The print offer: your price list (prices only, never your cost), the shipping lines, your email and the studio's public key, sealed under the link key like `manifest.bin`, so the host can't swap the key the page seals to. Only on a gallery that offers prints. |
| `offer-kid.php` | The studio key's ID `orders.php` checks. It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `sealed.php` | The shape check `picks.php` and `inquiry.php` use (the sealed format's checks, steps 1 to 15). It holds no key and opens nothing; fetched on its own it prints nothing. |
| `tries.php` | Written by the gate, `picks.php` and `orders.php`: the times of recent wrong key proofs at this gallery (no address, not even hashed). It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `.htaccess` | For web servers that read it (Apache): the same files refused once more, the `orders/` folder refused, and every photo file sent through `gate.php`, so a gallery also closes on time when a file is fetched by its name. Nothing a client needs kept private depends on it. |

How the gate answers:
- A request with no key proof is refused at once.
- A right key proof is never refused because of what anyone else sent: no address is ever locked out.
- A wrong key proof is refused at once. In a gallery with a codeword it is counted, for the gallery as a whole: after
  10 wrong proofs in an hour, every request must carry a proof of work (`SHA-256(SHA-256("lumen-aureum gate work", 0,
  proof, 0, hour) . w)` starting with 16 zero bits, one more for each doubling of the wrong proofs, at most 20). The
  gate answers 429 with the number of bits, the page works it out (about a second, once an hour) and asks again. So
  each guess at a codeword costs work on top of the codeword's own 600,000 rounds, and a client sharing an address
  with someone guessing (an office network, a phone carrier, a proxy or CDN in front of the host) still gets in.
  Nothing waits on the host, so a script sending wrong proofs can't tie up its PHP workers. A gallery without a
  codeword counts nothing: its proof can't be guessed.
- The gallery page reads `picks.las` through the gate, with the proof, to check what was really sent. The app reads
  it over the upload connection, not through the gate. With `manifest.bin`, and only for a visitor who sent the right
  proof, the gate says whether `draft.las` and `picks.las` are there, so the page asks only for the ones that are.

**On any web server.** None of this depends on how the web host is set up. The sealed files sit under names only the
key gives, the state files print nothing, and the folders a client's things wait in hold an empty `index.html`. What a
web server that ignores `.htaccess` (nginx with PHP-FPM, LiteSpeed without rewrite support, `AllowOverride None`)
changes is only this: someone who has a gallery's link could fetch its files by their names without going through the
gate, so the gate can't close the gallery on its last day by itself (the app takes it down at its next check), and
could try codewords without the gate's proof of work. So Setup's host check fetches a real gallery's manifest by its
name without the proof: on a host that hands it out, Setup says so in plain words, and galleries with a codeword are
refused there (Today says so too if it starts later). A gallery with a codeword already there stays online, but
someone with its link could try codewords much faster: Today says to take it down or move to a host that passes the
check. A gallery without a codeword is as safe there as anywhere (its key can't be guessed). The 1.4 update and every
later gallery update move each gallery's files to these names, and the refit's first check of each host tries it the
same way.

### Print orders
Print orders are sealed in the client's browser so that only your Lumen Aureum can open them; your web host holds
them only as unreadable files until the app collects them. When you prepare an order for the lab, the app cuts the
print files at home and hands them straight to your browser as a download, with an order sheet; nothing readable is
saved on your NAS or put on your web host. What you save on your own computer, and what you send to your lab, is
yours to look after, under your lab's own terms.

- **Who can open an order.** Only the app: it is sealed to your studio key (the same key as the studio page's
  inquiries; the private half is only in the app's encrypted database). The link key is mixed in, so only someone
  holding the gallery link could have made it: the host, which knows the public key, can't invent an order. Nobody
  can read one back from the host, the client's own browser included; the client gets the details from you.
- **How long a ship-to is kept in full.** Only two kinds of address are ever reduced: an address a client sent prints
  to for someone else ("ship to someone else"), and the address on a guest order whose orderer was never accepted as a
  client. Each is kept in full until one year after its order closed (closed, canceled or expired), then reduced to
  city, state and ZIP (the daily rules do it, and History says so). A client's own address is never reduced: that
  includes an orderer matched to a client by email, added to the client's family, or made a client of their own from
  the review list. Issued invoices are never changed: they keep the address they were issued with.
- **What an order does not prove** is who typed it. Links get forwarded, and anyone holding the link can order in any
  name, to any address. An order is a request to you, not an instruction: it is reviewed and invoiced to whoever
  ordered, and nothing goes to a lab before it is paid in full (unless you choose Order before payment for that
  order). Someone not already your client lands on a review list; the app never makes a client by itself.
- **What the host can do** is withhold an order, or send it again (the app records every order id it ever took in and
  ignores a repeat). The padding hides an order's size.
- **Collection before deletion.** No path removes a gallery folder from a host (going dark, a take-down, an update, a
  move, taking old copies down) before every order waiting there, and the picks and draft, have been collected on the
  same connection. If that fails, nothing is deleted and it is tried again. Forgetting a host as gone for good warns
  that orders not yet collected there may be lost.
- **The client's browser keeps** only the order being put together (photos, sizes, quantities, crops: no names, no
  address, no prices) and, after sending, a small marker per order (date, number of prints, total), sealed like the
  picks (below).

**What the client's browser keeps is sealed.** The gallery page keeps the client's picks, notes included, the print
cart and order markers, and the gallery's colors in the browser's storage, so they survive a reload. Each is sealed
(AES-256-GCM) under a key derived (HKDF-SHA256, "lumen-aureum local storage") from the gallery key, which comes from
the link (and the codeword) on every visit and is never stored; each entry's name is itself a keyed hash, so nothing
there says which galleries the browser opened. So a gallery in a folder of your main website is safe from that
site's other scripts (a plugin, an analytics script), and nothing readable about a client stays on their device.
Older entries are sealed (and the readable copy removed) the first time the gallery is opened with this version.

## The studio page
The studio page is public and unencrypted, like any website; see [Studio page](studio-page.md). What it guards:
- **Client photos.** Only from sessions whose client allows portfolio use, never one on the client's Don't show list
  (a retouched version of it included), and none from a session whose list has any entry that isn't exactly one of
  the session's file names (two names in one entry, a range, a name with words after it). Permissions are checked
  again when a held upload is approved, and the site built again without a photo its client stopped allowing. Each
  photo is saved again from its pixels, so no camera data or location goes up.
- **Its files on the host.** Only files it put there, and only while each still matches the SHA-256 recorded when it
  went up. Anything edited there since is left. A client's photo its client no longer allows stays on the list while
  it is on the host, edited or not: Today reminds (with the page switched off too), and Remove it anyway deletes it.
- **Inquiries.** Sealed in the visitor's browser to the studio's key (the sealed format LAS1, purpose inquiry: ECDH
  P-256, HKDF-SHA256, AES-256-GCM, with the studio key's ID in the header); the host keeps `.las` files it can't open,
  and `inquiry.php` (with `sealed.php` beside it) refuses anything not sealed to this studio's key ID.
  Because the public key reaches the visitor inside a page the host serves, a hostile host could hand out a key of its
  own and read what visitors type: that is true of any form on a host you don't run. `inquiry.php` needs a small proof
  of work from the browser (`SHA-256(SHA-256(envelope) . w)` starting with 17 zero bits, about a second on a phone),
  keeps the same envelope once, and holds at most 200 in all. Past 5 an hour or 10 waiting from one sender (an IPv4
  address, or one IPv6 /56), a sender is never simply refused: each further inquiry needs 20 bits of work (a few
  seconds, asked for by the host and done by the form), so visitors who share an address (an office, a phone
  network, a proxy) still get through and a script sending many pays for each. When full it drops the newest of the
  sender with the most waiting rather than refusing everyone. Behind a proxy or CDN that the host check saw, where
  everyone seems to share an address, no sender is counted: every inquiry needs the 20 bits, and a full folder refuses
  more rather than drop one already kept. Senders are kept only as an HMAC keyed
  with a random value per install, written into `inquiry.php` (PHP source is never served). The app collects up to
  200 per check with no daily cap, holds them in the encrypted database for you to Add or mark as Spam (none becomes a
  client record by itself), and Today flags more than 10 in a day.
- **The inquiries folder** holds only sealed files, the rate list `.rate.php` (hashed addresses and times, behind a
  PHP exit line, so fetching it shows nothing) and an empty `index.html` (so a web server that lists folders shows
  nothing), on any web server; `inquiries/.htaccess` closes it once more where the server reads it. Taking the site
  down collects what waits, then removes the folder's own files and the folder.

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
- Web host certificates: the app checks the web host over its https address with the system's normal certificate
  checks (the authority and the name). A certificate that isn't publicly trusted (self-signed, or from your own
  authority) is shown in Setup with its SHA-256 fingerprint and accepted only once you confirm it; from then on only
  its public key (SHA-256 of the SubjectPublicKeyInfo) is accepted at that address, in a certificate that still names
  the address, checked before anything is sent. A certificate for another name is never offered or accepted. A new
  key stops every check of the host until you confirm or refuse it in Setup; it is never accepted by itself. The
  checks never follow a redirect, and every connection and read has a time limit. The confirmed
  certificate is kept with your storage settings, encrypted like the SFTP host key. Certificate checks are never
  simply turned off: there is no setting for it. See [Where it runs](where-it-runs.md#the-web-hosts-certificate).
- S3 storage: every request is signed (AWS Signature Version 4, the content's hash included), and the secret key never
  crosses the network. A self-signed certificate is trusted the same way as the web host's: shown in Setup, accepted
  only once you confirm it, and only its key from then on; a new key stops backups there until you decide. Plain
  `http://` is accepted only to an address on your own network (checked against the address actually reached, on
  every connection), never to one on the internet. See [Where it runs](where-it-runs.md#s3-storage).

Pages are sent with `Cache-Control: no-store`, so the browser does not keep client pages on disk, and the app keeps
no access log (request lines can carry search terms).

## Deleting a client
There is no undo: their galleries come down, their photos and archives are destroyed, and every record and
history line about them is erased. Sealed daily backups still contain the record until they age out (the 30 most recent daily backups are kept, and the
second location's copies on the same schedule). A small list of deletions, `deletions.json`, is kept outside the
database: if an older backup is ever restored, those clients are deleted again at the next sign-in. Deleted sessions
are on the same list, and are deleted again the same way (their History lines stay, as after any session delete). The
list holds only record numbers and creation times, no names.

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
Changing your password stops the old password from opening the *current* keyring, but anyone who kept a copy of an
older `keyring.json` (with the old password) can still unwrap the same data key. Only a rotation revokes that. So if
a password, a keyring copy, or an unlock key file from before 1.5 may have leaked, stop the app and run `python -m vault.cli rotate`. Everything is
re-encrypted under a new key, uploaded photos included (each gets a fresh key and is rewritten; their mirror
copies are refreshed afterwards; a photo whose file here is missing is fetched back from its mirror copy first, and
if one can't be fetched the rotation stops before anything is changed) - staged and checked first, swapped in last; an interrupted rotation is finished by
running the command again, and the app will not start until it is. It refuses to run while the app is running.
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
