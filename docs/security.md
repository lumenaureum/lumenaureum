# Security and privacy

Lumen Aureum holds names, addresses, children's details, payments, and photos. It is built so that losing the
machine, a disk, or a copy of its data folder exposes no client records.

## What is encrypted
| Data | Where | How |
|---|---|---|
| The database (every client, session, payment, note, and your crew list) | the app's data folder | SQLCipher: every page, plus its journal |
| Face photos of clients | the app's data folder | AES-256-GCM, each tied to its household |
| Storage settings (SFTP/S3 credentials) | the app's data folder | AES-256-GCM |
| Daily backups | the vault (and the second location) | `age`, to your master key |
| Uploaded photos (RAWs and edited exports), and equipment receipts | the photos folder, and one mirror copy on the backup location | The STREAM construction with AES-256-GCM in 1 MiB records, a random key per file kept in the database and sealed beside the photo to your master key with `age`; encrypted as each piece arrives, before anything is written; the mirror holds the same ciphertext. Specified in [photo-format.md](photo-format.md), with test vectors |
| Delivered sessions (RAWs and exports) | the vault | The same files, moved into the vault 7 days after the files are marked delivered (or when you seal it); the app then forgets their keys, so only the master key opens them |
| Client galleries | your web host | AES-256-GCM, each file bound to its name; decrypted in the browser. The key is in the link on the letter you mail, after the `#`, which browsers never send to a server (the app keeps a copy to rebuild the link) |

**Links in a sealed folder.** A sealed archive holds the files themselves, never links: a link keeps only where a file
was, and the folder is removed once it is sealed. A link to a file is sealed as that file. A link to a folder, or one
that leads nowhere, stops the seal before anything is removed, and the message names it (a backup's face photos folder
leaves such a link out instead, so the backup still happens). Opening an archive or a backup never creates a link that
leads outside the folder it opens into.

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
after five minutes, and at once whenever the password, the master key, or the recovery codes change, or you sign out
every other device, so a sign-in started before the change can't finish after it). Recovery codes stand in for the two-factor code only; they never unlock the data without your
password. `/healthz` reports `"locked": true` so a NAS can show it.

**A wrong email answers like a wrong password, in the same time.** If the sign-in page checked the email by opening
the database, a right password with a wrong email would take measurably longer than a wrong password, and that
difference would tell someone guessing that the password was right. So the keyring also holds a keyed hash of the
owner's email (keyed by the data key, so it reveals nothing to anyone without it, and covered by the keyring's check
value, so editing or removing it is refused like any other change). The password unwraps the key, the email is
compared with that hash, and the database is never opened for a wrong email. A keyring from before 1.5.5 has no hash
yet: until the next sign-in that passes two-factor (or the master key) adds it, the app checks the email in the
database as it did before. Rotating the data key makes the hash again under the new key.

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
who holds both, and would only lock you out if you lost your phone along with your password. The master key opens
everything without the password or the two-factor code, which is why it lives on paper: keep it as carefully as the
data itself. It is not the top of a ladder, though: while signed in, the password plus one two-factor code or recovery
code is enough to ask for a new master key (see "Lost your master key?"), so guard those as closely. Such a key takes
effect only 48 hours later, and any signed-in device can cancel it until then.

**Lost your master key?** While you can still sign in, Settings > Security > **Replace a lost master key** makes a new
one. A lost key matters two ways. Without it, the next forgotten password or broken machine would be final, because
the master key is the one way back. And whoever finds it can open any backup made before the replacement, and with the
keyring inside one, your data as it is today, until you also rotate the data key (see below). The page asks for your password and a two-factor
code (or a recovery code), since the new key opens everything, then shows the new key the way first setup does: copy
it, print it, and type its last six characters back. Nothing changes until you do, and the key is never stored, logged,
or shown again. Then the new key waits 48 hours (see "The 48-hour wait" below). When it takes effect, the data key in
`keyring.json` is wrapped to the new key (your password's wrap stays as it was),
each sealed session's key is replaced by a fresh one wrapped to the new key, the key kept beside each photo in the
photos folder is sealed again to the new key and copied over the one beside that photo's copy on the backup location
(so those copies open with the new key alone; one that can't be copied is copied again, photo and key, by the next
photo copy), a backup is made, every device is signed out (the one that asked for it too, so it signs in again under
the new key), every known browser is forgotten (see "Failed sign-ins") and Account activity records it. The key
files and the backup run as a background job, and its result is one plain line.

**The 48-hour wait.** Someone who learns your password and gets one code (a look over your shoulder, a family member
with your phone, a phished pair) could otherwise make a master key of their own in a minute. Every backup and sealed
session after that would be sealed to their key, a password change would not undo it, and nothing would tell you. So a
new master key asked for without the old one does not take effect for 48 hours:

- Only its public half waits, in the encrypted settings, with the time it was asked for and the time it takes effect.
  Both are kept in UTC, so changing the studio's time zone doesn't shorten or lengthen the 48 hours.
  The secret half was shown once and has left memory; nothing on disk can open anything with it.
- Until then the current key keeps working: backups, sealed sessions, and photo keys are still sealed to it, and it
  still opens the keyring.
- Settings > Security, the alert bell, and Today show "A new master key takes effect on ..." with
  **Cancel the new master key**. Any signed-in device can cancel it, without the password, since canceling only keeps the key in use.
  Every sign-in says so too. When phone notices are set up, the phone gets "A new master key was asked for. It takes
  effect in 48 hours. If it wasn't you, sign in and cancel it in Settings, Security." (title "Studio master key"),
  once per request, in those words only. This one is always on, with no box to untick (Settings, Notifications lists
  it, ticked and grayed out): it goes whenever a notice server is set up, so someone with your password and a code
  can't turn it off before asking. Removing or replacing the server doesn't stop it either: for 30 days the first
  server removed or replaced keeps getting this one notice, and nothing else, as well as any server set up now.
- A second request while one waits is refused, before a recovery code is used up; cancel the waiting one first. The
  first one keeps its time, so a request can't be swapped for another or pushed back quietly.
- The app checks at each sign-in, on page loads, and every few minutes. A restart during the wait changes nothing:
  the wait is in the database, and if its time passed while the app was locked, it takes effect at the next sign-in.
  A wait found more than a week past its time (for example in a database restored from a backup made during a wait
  that was later canceled) is dropped, not applied, and Account activity says so.
- The rotation ask (below) and the sign-in change notice come when the key takes effect, not when it is asked for.
- When it takes effect, every device is signed out, the one that asked for it too, and every known browser is
  forgotten. Whoever asked signs in again with the password and two-factor; someone who slipped in during the wait
  loses that sign-in.
- After it takes effect, every sign-in says "Master key replaced on <date>" for 30 days.

Giving the old key (below), or `python -m vault.cli new-master`, proves you hold the key in use, so it takes effect at
once and cancels any new key still waiting.

What was sealed with the lost key no longer opens for you: sessions and photos already moved into the vault, and every
backup made before the replacement. The app can't open them for you, because it never held the key. They still open
for whoever holds the lost key. Such a session says "Sealed with a master key that was replaced; it can't be opened" on its page instead of
offering a reprint, and a reprint of a session with some of each opens the rest and says how many it couldn't.

**Still have the old key?** Type it in **Old master key, if you have it** on the same form, and nothing stays closed
to you. It is checked first, against the keyring: a wrong one changes nothing (and
counts as a wrong guess, like a wrong password), and no recovery code is used up. Then every session key is opened with
the old key and wrapped to the new one (a key the old one doesn't open stops the whole change before anything is
written), so sealed sessions open with the new key; and the background job opens each key file in the vault sealed to
the old key (a sealed photo's key, the session key kept beside each archive) and seals it again to the new key, in
place; the second location takes the new copies at its next daily run. The old key is held in memory only, from the
form until that job ends, and never stored or logged. Backups made before the change are sealed whole to the old key
and are not re-made: they still open with the old key, so keep it until they age out (the 30 most recent daily
backups are kept), then destroy it. `python -m vault.cli new-master` does the same from the command line, without the
vault's key files.

**No photo is sealed to the replaced key.** The key file beside a photo moves to the new key in that background job,
which takes a while on a large photo store. A photo sealed into the vault before the job got to it would carry a key
file only the replaced key opens, and its key in the database is forgotten at that moment, so it would be lost. So
sealing always writes the photo's key file again, from the database, to the master key in use right then, just before
copying it; a seal waits (and is tried again later) while that job runs; one photo the job can't write doesn't stop
the others, and the job's result names it; and a second replacement while the job runs gets a job of its own, run
after the first.

A new master key shown for confirming, and the old key if it was typed, leave memory when the app locks and once
their ten minutes run out, whether or not the page is opened again.

**After replacing the master key, rotate the data key.** Replacing the master key wraps the same data key to the new
key; it does not change the data key. Every `keyring.json` from before the change, including the one inside each sealed
backup, still opens that data key with the old master key, and the data key opens the live database and photos,
clients added later included. So a replaced key stops opening anything new only once the data key is rotated. If the
old key may have been found or seen, stop the app and rotate (see "Rotating the data key" below, and "If you suspect a
compromise" in [operations.md](operations.md#if-you-suspect-a-compromise)). Replace the master key first, then rotate:
the rotation wraps the new data key to whichever master key is in use. Until a rotation runs, the alert bell and
Settings > Security say so, with the steps; the rotation clears it. Backups made before the rotation still open with
the old key, holding the data as it was then, until they age out.

## Notices while locked
While the app is locked, backups and gallery checks wait, so a restart while you are away (a power cut, a NAS update)
can go unnoticed for days. The locked-studio notice sends a message to your phone when that happens. It is optional and off until you set it up in Settings,
Notifications: the address of an ntfy server, a topic, and an access token if the server needs one. **Send a test
notice** checks it. Once ntfy is set up, **Studio locked after a restart** shows as a ticked box that stays on. The
first time the studio restarts locked without it, Today suggests it once.

When the studio starts locked, the app sends a notice, then one a day until you sign in: "Your studio restarted and
has been locked since <day and time>. Backups and gallery closings are waiting until you sign in." The notice never
carries a link, a client's name, a count, an amount, or anything else from your data. Sign in from the app's own
address as usual. It goes over HTTPS only. A notice that fails is written to the app's log and tried again an hour
later at the soonest. A restart while the studio is still locked keeps the time it first locked and sends nothing
more than the one a day. When you sign in after 15 minutes or more locked, Today says once since when the studio was
locked; the backups and gallery closings that waited catch up then.

## The studio inbox notice
Beside **Studio locked after a restart**, **Something new in the studio inbox** is a second, optional notice through
the same ntfy server and topic. It is off until you tick it. When it is on, the app sends a notice when a new website
inquiry, a client's picks, or a print order comes in. The notice says only "Something new came in. Sign in to your
studio to see it." It never says what came in or how many, and never carries a name, an email address, a session
title, an ID, an amount, a date, or a link, because anyone who knows the topic can read it, and anyone near your phone
can read its lock screen.

What comes in together is one notice, and there is at most one every 30 minutes; the next one carries everything that
came in meanwhile. Spam and form posts the app throws away never set it off. What is waiting to be told is kept in
the encrypted settings, so a restart neither loses nor repeats them, and turning the notice off or on starts afresh. It
runs only while the studio is unlocked: while it is locked, the app can't read its settings, and the locked-studio
notice covers that time. A notice that fails is written to the app's log and tried again ten minutes later at the
soonest; counting never holds up or breaks taking in the inquiry, picks, or order. The audit log records only that the
notice was turned on or off.

**A backup failed or is late** is a third optional box, off until you tick it. It sends "The last backup failed" or
"No backup has been made in over a day", with nothing else: never the error's own words, a folder, or a server. It is
sent when backups go wrong, then at most once a day while they stay that way; once a backup works again, the next
problem is told at once. Like the inbox notice, it runs only while the studio is unlocked.

Five more boxes work the same way, each with its own title so you can tell them apart on your phone. The first four
are off until you tick them; the last one, sign-in changes, is on until you untick it, from the moment a notice server
is set up (an install that turned it off keeps it off). What each one sends is the whole message; nothing from your data is ever added to it:

- **Storage running low** ("Studio storage"): the disk that holds the studio's data or photos has less than 10%
  free. Never a folder, a disk name, or how much is used. At most once a day while it lasts.
- **A gallery closes tomorrow** ("Gallery closing"): "One or more galleries close tomorrow." Never how many, which
  gallery or whose. Once a day, between 9 in the morning, and 9 at night, never at midnight.
- **An update was installed** ("Studio updated"): the studio was updated to a new version. Not the version number:
  the topic is readable by anyone who knows it, and a version tells a stranger which known weaknesses to try. Today and
  About say which version and what changed. Ticking the box after an update doesn't send a notice for that update.
- **A gallery page on your web host changed** ("Gallery page changed"): the served-page check found a gallery's page or
  scripts on the web host differ from what the app put there, at two checks in a row. Never how many or which.
  Today and the gallery's panel say which, with Take down now. At most once a day while it lasts, and at once when
  another gallery is found.
- **Your password, master key, or recovery codes changed** ("Studio sign-in changed"): "A way into your studio was
  changed. If it wasn't you, sign in and look at Account activity." Sent when the password is changed (in Settings, or
  with the master key after a forgotten password), the master key is replaced, or new recovery codes are made. Never
  which one, when or from where: someone who has just taken over the account learns nothing from your lock screen, and
  you learn to look. The change is noted in the same step that makes it and told by the next minute's check, once per
  change. The app has no other way to change two-factor than new recovery codes; signing in with a recovery code is not
  a change. Someone with your password and a code could turn the box off first: the audit log records that, and the
  change itself. A change made before a notice server is set up is never told later, and stopping the notices forgets
  the last change. A new master key asked for without the old one is told when it is asked for by its own notice
  ("Studio master key", see "The 48-hour wait"), and by this one when it takes effect.

**A new master key was asked for** ("Studio master key") is not one of the boxes: like the locked-studio notice it is
always on, with no way to turn it off, whenever a notice server is set up, and for 30 days it also goes to the first
server removed or replaced. See "The 48-hour wait".

Each one's last notice is kept in the encrypted settings, so a restart doesn't repeat it, and turning a box off or on
starts afresh. One notice that fails, or a check behind it that fails, is written to the app's log and never stops
the others, the backups, the gallery checks, or anything else the studio is doing. The audit log records only that a
notice was turned on or off.

The app has no key while it is locked, so it can't read the server address, topic, and token from the encrypted
database. It keeps a copy of them, with the studio's time zone, in a small plain file in the data folder,
`ntfy.json`, and anyone who can read the data folder can read them. Use a topic nobody could guess, and an access
token where the server takes one.

Use a private ntfy server: one you run yourself (it is also in the TrueNAS app catalog) or one you trust, with a
private topic and a token. On the public server, ntfy.sh, a topic is open to anyone who learns or guesses its name, and
the notices pass through a service you don't control. Settings, Notifications says so beside a saved ntfy.sh address.
The notices are written so that even a stranger reading them learns little: no counts, names, amounts, dates, or links,
only that something of a kind happened. With your own server, your phone must be able to reach it when you are away
from home, over a VPN for example, or notices arrive only at home.

## What is not encrypted by the app
- **The copy on your own computer.** Photos are uploaded into the app and sealed on arrival; a download (to edit again)
  is decrypted as it is sent. The files on the computer you edit on, and your memory cards, are yours to look after.
- **Plain photo folders, if you turn them on.** `CRM_PHOTO_FOLDERS=1` lets you pick RAW and export folders on a share
  instead of uploading. Those files are ordinary files, readable by anyone who can open the share; uploading is the
  safer way.
- **A reprint copy** opened from the vault is plain for the 7 days it exists, then removed.
- **The studio page's builds.** Preview, and Upload, build the site as plain files in `data/site/` (pages, and photos
  at web size with their copyright mark, as they appear online). The preview stays until the next upload, a take-down,
  or the app locking or restarting; an upload's build stays only until it has gone up (or, while it is on hold, until
  the app locks). Thumbnails on the Studio page are kept in memory only, and cleared on lock.
- **PDFs** you open in the browser (quotes, agreements, letters) may be kept by the browser in its cache or
  Downloads. Delete downloads you don't need; on a laptop, use full-disk encryption.
- **A secret typed on a setup step that was refused** (a storage password or key) waits in the app's memory, never
  on disk or in the page, so the retry needn't ask for it again. It is gone after 15 minutes, once the retry uses it,
  or when the app locks or restarts, whichever comes first.
- **Memory.** While unlocked, the data key is in the app's memory. A swap or hibernation file can hold memory on disk;
  use encrypted swap, or run on a machine that doesn't hibernate. The app never writes its memory to disk itself: core
  dumps are off (the process is marked not dumpable and its core file size is 0, and `compose.yaml` sets the same), so
  a crash leaves no copy of a key or of client data on the machine's disk.
- **The ntfy settings** (server, topic, access token, time zone), in `ntfy.json` in the data folder, so the locked
  studio can send its notice, and `lock-state.json` beside it: when the studio locked and when the last notice went,
  times only. See [Notices while locked](#notices-while-locked).
- **Added plugins' files**, in `plugins/` in the data folder: each plugin's code and files as Lumen Aureum published
  them, so they can load before you sign in. They hold nothing of yours. See [Plugins](#plugins).
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

**Gallery folders this studio doesn't know.** Once after an update, the app looks through the web host in use for
gallery folders no record here names, and offers them on Move galleries. Such a folder may be a live gallery whose
clients still use its link: one made by another install, or after the backup this database was restored from. So a
folder counts only when it holds a sealed manifest and a gallery page made for that very folder (a website's folder is
never listed), Adopt a gallery already online is shown before Remove, and the app never removes one on its own. If this
database had never put a gallery on that web host when it looked (a fresh install, or an older backup restored), the
app does not offer removal at all, and refuses it if asked. A folder named in the link ID form every gallery has had since
1.4 is never offered for removal on any database, and the app refuses to remove it even when asked: a real leftover of
a move carries an ID from before 1.4, and a restored backup that did use the host knows nothing of the galleries made
after it. Such a folder can be adopted with the link its client received; removing it by hand on the host is the
owner's choice.

Deleting a session or client takes its gallery down from the host in use (the delete stops if that fails) and from any
earlier web host that still holds a copy. A copy that can't be removed then (the host can't be reached) is kept in
a list of gallery folders still to come down, tried again by the regular check (an unreachable host once a day) and by
Take the old copies down; Today mentions it until it is down. While a delete runs, the session's gallery can't be put
back online, uploaded, or rebuilt, and the delete checks once more, just before the records go, that it is down.

What a gallery link can and can't protect:
- **The link is the key.** Anyone holding the full link (with what follows the `#`) can open the gallery, the same as
  the client. A passphrase adds a second thing they must know. The files a passphrase could be tested against (the
  manifest, the print offer, the client's picks, and draft) are kept on the web host under names made from the key,
  so someone holding only the link can't even find them, on any web server; on a host that runs PHP the gate also
  refuses them to anyone who can't prove they hold the key. Each guess at a passphrase is one request through the gate,
  and after 10 wrong guesses in an hour at one gallery, every request must carry a small proof of work (about a second
  in a browser, once an hour), so guessing gets slow and nobody is ever locked out, whoever else shares their address.
  A web host that hands gallery files out without asking for the key (Setup's host check tests a real gallery for
  this), or one that runs no PHP, would let someone with the link try passphrases as fast as their computer allows, so
  the app refuses galleries with a passphrase there and says why. It also refuses short or common passphrases.
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
| The client's picks | the gallery page, on Send my picks | `<link ID>/picks-<device>.las` (`picks.las` from a page before 1.6) | the app, and the device that sent them (to show what was really sent) |
| The picks still being made | the gallery page, a moment after each change | `<link ID>/draft-<device>.las` (`draft.las` from a page before 1.6) | the app, and the device that made them |
| A studio page inquiry | the visitor's browser | `inquiries/<sender>-<content>.las` | only the app |

Picks and drafts are sealed under a key derived (HKDF-SHA256) from the link key for that one purpose and that one
link (its kind included), so a draft can't be passed off as sent picks, and one gallery's picks can't be passed off as
another's. Every item carries its own item ID: the app takes each in once, and an older one a host hands back never
replaces a newer one.

Each device keeps its own picks and draft, so two people picking on two devices (a couple) never overwrite each other.
`<device>` is 16 random hex characters the browser chose the first time it saved a pick there; it says nothing about
who is picking, and it is also sealed inside the item. The app opens every picks file in the folder with the gallery's
key, takes one only when it sits under exactly the name its sealed device ID gives, and puts them together: every
photo anyone picked, and a Please don't show from anyone wins over a portfolio suggestion.
A device new to the gallery (another phone, or the same one after a move to a new address) shows what everyone picked
so far: it reads every device's file through the gate with the key proof, the same files the app reads, and keeps only
the photos this gallery shows. It writes nothing of its own until the client changes or sends a pick; from then on its
file names the item IDs it started from (`rp`), and the app sets those older files aside, so what the client took off
stays off. Nothing marks which pick came from which device.
Inquiries are sealed to the studio's public key with a one-time key (ECDH P-256, HKDF-SHA256, AES-256-GCM), and carry
the studio key's ID, so the key can be changed later without losing an inquiry on its way. Every envelope's header is
authenticated in full; the host's address is in none of them, so a gallery moved to another host keeps working.

The link files (`manifest.bin`, and the photo and Download all files with random names) are not envelopes: each is a
12-byte nonce, then AES-256-GCM under the link key, with the file's own name as associated data, so the host can't
serve one file in place of another. Anyone with the link (and the word, if set) can open them; nobody else.

On the web host, `manifest.bin`, `offer.bin` and the picks and drafts are kept under names made from the key
proof: the first 24 hex characters of SHA-256("lumen-aureum file name", 0, the plain name, 0, the proof), with the
same extension. The gate and `picks.php` work the name out from the proof a visitor sends; the page does too, where no
gate runs. The photo and Download all files already have random names that only the sealed manifest lists. So none of
a gallery's sealed files sits at a name anyone can guess. (Below, each is called by its plain name.)

Galleries online before 1.4 had shorter folder names and sealed picks in an earlier form. The 1.4 update moves every
gallery to its link ID and carries every pick, draft, and order sent before over once (see
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
| `limits.php` | The gallery's window: how many days after the first visit, and the date it closes if nobody opens it (with the exact instant that day ends in your time zone, written again when the time zone changes). The app writes it at upload and on every change. After Move galleries, the copy left on the earlier host is marked moved (with your email, which earlier copies didn't carry), and a page built before 1.3 there is replaced with this version's, which reads the same files and tells the client the gallery moved. It also says whether the gallery has a passphrase, and whether the host sits behind a proxy or CDN. Galleries from before 1.4 had it as `limits.txt`; the 1.4 update moves it. |
| `seen-key.php` | The SHA-256 of the key proof (the proof is the first 32 hex characters of the SHA-256 of the link key). It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `seen.php` | Records the first visit in `seen-at.php` (before 1.4, `seen.txt`), once, and only for a visitor who sends the key proof. A stamp left empty is replaced. |
| `gate.php` | Serves the encrypted photo files, `manifest.bin`, `offer.bin` and the client's picks and drafts (each device's own, and `picks.las` and `draft.las` from before 1.6), and only while the window is open. A missing or unreadable `limits.php` counts as closed. It asks for the key proof for every file, and finds the sealed files by the names the proof gives. A one-time download's list and end go in `once.php` and `done.php`. It never serves anything else. |
| `picks.php` | Saves the client's picks or the picks still being made, each device's under its own name (`picks-<device>.las`, `draft-<device>.las`; a page before 1.6 writes `picks.las` and `draft.las`). It takes one form field holding a sealed envelope (LAS1, purpose picks or draft, bound to this gallery's folder name, its link ID), up to 256 KB, that doesn't look like readable text, from a visitor who sends the key proof, and only while the window is open, under the name the proof gives. Wrong proofs count toward the same limit as the gate's. A gallery holds at most 64 picks and draft files: past that a new device is refused (a device already there still saves), so whoever holds the link can't fill your web host with a new 256 KB file for every made-up device; the app reads and keeps no more than 64 either, whatever the host holds. On a copy marked moved it saves nothing and answers with your email. |
| `orders.php` | Keeps a print order sent from a gallery that offers prints, as `orders/<hash>.las`, for the app to collect. It takes one sealed envelope (LAS1, purpose print-order: sealed to your studio key with the link key mixed in, bound to this gallery's folder name and the studio key's ID, at most 64 KB and padded to one of five sizes), with the key proof, a proof of work, and only while the window is open, more than 24 hours before the gallery closes, and prints are on (`offer-kid.php` is there). 5 orders an hour and 10 waiting from one sender (behind a proxy or CDN, where every client seems to share an address, only the gallery's own limits), 100 files and 6.5 MB waiting in all. Past the hourly limit it answers with Retry-After (when the oldest of the five is an hour old), so the gallery page tells the client when to try again. It never reads, lists, or serves an order, and `orders/` holds an empty `index.html`, so a web server that lists folders shows nothing there. |
| `offer.bin` | The print offer: your price list (prices only, never your cost), the shipping lines, your email, and the studio's public key, sealed under the link key like `manifest.bin`, so the host can't swap the key the page seals to. Only on a gallery that offers prints. |
| `offer-kid.php` | The studio key's ID `orders.php` checks. It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `sealed.php` | The shape check `picks.php` and `inquiry.php` use (the sealed format's checks, steps 1 to 15). It holds no key and opens nothing; fetched on its own it prints nothing. |
| `tries.php` | Written by the gate, `picks.php` and `orders.php`: the times of recent wrong key proofs at this gallery (no address, not even hashed). It starts with `<?php exit; ?>`, so fetching it shows nothing. |
| `.htaccess` | For web servers that read it (Apache): the same files refused once more, the `orders/` folder refused, and every photo file sent through `gate.php`, so a gallery also closes on time when a file is fetched by its name. Nothing a client needs kept private depends on it. |

How the gate answers:
- A request with no key proof is refused at once.
- A right key proof is never refused because of what anyone else sent: no address is ever locked out.
- A wrong key proof is refused at once. In a gallery with a passphrase it is counted, for the gallery as a whole: after
  10 wrong proofs in an hour, every request must carry a proof of work (`SHA-256(SHA-256("lumen-aureum gate work", 0,
  proof, 0, hour) . w)` starting with 16 zero bits, one more for each doubling of the wrong proofs, at most 20). The
  gate answers 429 with the number of bits, the page works it out (about a second, once an hour) and asks again. So
  each guess at a passphrase costs work on top of the passphrase's own 600,000 rounds, and a client sharing an address
  with someone guessing (an office network, a phone carrier, a proxy or CDN in front of the host) still gets in.
  Nothing waits on the host, so a script sending wrong proofs can't tie up its PHP workers. A gallery without a
  passphrase counts nothing: its proof can't be guessed.
- The gallery page reads its own device's picks (or `picks.las` from before 1.6) through the gate, with the proof, to
  check what was really sent. The app reads them over the upload connection, not through the gate. With `manifest.bin`, and only for a visitor who sent the right
  proof, the gate says whether `draft.las` and `picks.las` are there, so the page asks only for the ones that are,
  and lists the web host names of every device's picks and drafts (names made from the key proof, nothing about who
  or which device; the files stay sealed under the gallery key), so a device new to the gallery starts from what
  everyone picked so far. Only a visitor with the right proof gets the list or any of the files.

**On any web server.** None of this depends on how the web host is set up. The sealed files sit under names only the
key gives, the state files print nothing, and the folders a client's things wait in hold an empty `index.html`. What a
web server that ignores `.htaccess` (nginx with PHP-FPM, LiteSpeed without rewrite support, `AllowOverride None`)
changes is only this: someone who has a gallery's link could fetch its files by their names without going through the
gate, so the gate can't close the gallery on its last day by itself (the app takes it down at its next check), and
could try passphrases without the gate's proof of work. So Setup's host check fetches a real gallery's manifest by its
name without the proof: on a host that hands it out, Setup says so in plain words, and galleries with a passphrase are
refused there (Today says so too if it starts later). A gallery with a passphrase already there stays online, but
someone with its link could try passphrases much faster: Today says to take it down or move to a host that passes the
check. A gallery without a passphrase is as safe there as anywhere (its key can't be guessed). The 1.4 update and every
later gallery update move each gallery's files to these names, and the refit's first check of each host tries it the
same way.

### The sealed card
A session delivered on a "Sealed card (SD or USB)" gets its download gallery written onto the card (or into a zip to
copy onto it), with no web host. The threat is a card that goes astray: in the mail, left in a car, handed to the
wrong person. So the card holds only the page, a plain "Read me" and the sealed files under random names, sealed the
same way as an online gallery. The key is never written to the card: it comes from a 27-character code printed only in
the card letter (128 random bits, with a check character so a typo is told apart from a wrong code). Without the
letter, a lost card is only sealed bytes; that is why the letter says to keep it apart from the card. The page opens
from the card in a desktop browser; it reads nothing from any network and runs under the same strict policy as an
online gallery. The app keeps the code in its own encrypted database, so it can print the letter again. A zip made
for copying is removed from the app's folder once it has been saved.

### Print orders
Print orders are sealed in the client's browser so that only your Lumen Aureum can open them; your web host holds
them only as unreadable files until the app collects them. When you prepare an order for the lab, the app cuts the
print files at home and hands them straight to your browser as a download, with an order sheet; nothing readable is
saved on your NAS or put on your web host. What you save on your own computer, and what you send to your lab, is
yours to look after, under your lab's own terms.

- **Prints from a proofing gallery.** A gallery that offers prints from its proofs puts only the proofs on your web
  host, as any proofing gallery does. The threat it answers: a print store whose visitors could save every full-size
  file would give away what it sells. Each full-size file is sealed under the gallery key in the gallery's folder on
  the NAS and is never uploaded; the app opens it, in memory, only to prepare an order for the lab.
- **Who can open an order.** Only the app: it is sealed to your studio key (the same key as the studio page's
  inquiries; the private half is only in the app's encrypted database). The link key is mixed in, so only someone
  holding the gallery link could have made it: the host, which knows the public key, can't invent an order. Nobody
  can read one back from the host, the client's own browser included; the client gets the details from you.
- **How long a ship-to is kept in full.** Only two kinds of address are ever reduced: an address a client sent prints
  to for someone else ("ship to someone else"), and the address on a guest order whose orderer was never accepted as a
  client. Each is kept in full until one year after its order closed (closed, canceled, or expired), then reduced to
  city, state, and ZIP (the daily rules do it, and History says so). A client's own address is never reduced: that
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
the link (and the passphrase) on every visit and is never stored; each entry's name is itself a keyed hash, so nothing
there says which galleries the browser opened. So a gallery in a folder of your main website is safe from that
site's other scripts (a plugin, an analytics script), and nothing readable about a client stays on their device.
Older entries are sealed (and the readable copy removed) the first time the gallery is opened with this version.

## The calendar link
An optional, private address a phone's calendar app subscribes to (Settings, Calendar link). One-way and read-only:
the calendar app asks, the studio answers with a standard calendar file, and nothing comes back.

- **What it carries.** Each session as its type, day, start time, and length; a penciled date marked tentative; a
  group event as one entry a day. Never a client's name, contact details, a place, a note, a session title, an event's
  name, an amount, or a link, and nothing to choose here that would add them: the file is built from those four facts
  only. Entry ids are derived from the link's secret, so they carry no record number. Demo clients, lost sessions, and
  leads not penciled are left out, and so is anything older than 90 days.
- **Off by default.** Turning it on makes a random secret (256 bits) that is part of the address. It is kept only in
  the encrypted settings: never in a settings file, never in History (which records only that the link was turned on,
  made new or turned off), and the app keeps no access log. An error while answering is logged without the address.
  A reverse proxy in front of the app keeps its own access log, which would hold the link: turn that log off for
  `/calendar-feed/`, or make a new link if one ever landed there.
- **Replace or stop in one step.** **Make a new link** replaces the secret, and the old address stops at once (every
  phone must subscribe again); nothing else does, so turning it on again from a page opened earlier keeps the link
  phones use. **Turn off the calendar link** deletes it.
- **Who can reach it.** The same as the app: the network allow-list and HTTPS still apply; only the sign-in is
  replaced by the secret. A wrong address counts toward the same limits as a wrong password (5 in 15 minutes locks
  that address, 20 an hour per network), on its own count, so a phone still asking with an old link never locks you
  out of signing in. Keep the studio home-only: subscribe from your home network or a private network such as
  Tailscale. Anyone who learns the link, and can reach the studio, sees when your sessions are and what kind.
- **While locked.** After a restart the secret is in the encrypted settings the app can't read, so it can't tell a
  right link from a wrong one: every request gets "locked, try again later" (HTTP 503) and nothing about the link.
  Calendar apps keep what they last had. A copy kept for the locked time was considered and left out: even sealed, it
  would be data on disk the sign-in doesn't protect, for a calendar that is at most a few hours behind.

## Sending sessions to your calendar
Optional, and off by default (Settings, Sessions, Send to your calendar; Settings, Plugins). With it on, the studio
also connects to the calendar service you chose (Google Calendar, Microsoft Outlook, or a CalDAV service such as iCloud
or Fastmail), sending only each session's type, day, start time, and length. Off, it connects to nothing.

- **What it carries.** Exactly what the calendar link carries, built by the same code from the same sessions: the
  session type, the day, the start time, and the length, tentative for a penciled date, a group event as one entry a
  day. Never a client's name, contact details, a place, a note, an amount, or a link. An inquiry appears only once its
  date is penciled. Each event is marked private and carries an id derived from a secret salt kept in the studio, so
  no record number leaves. The id starts with a mark of this studio's own, and the studio lists, changes, and removes
  only events with its own mark: two studios sending to one calendar never touch each other's events.
- **One calendar, one direction.** The studio writes events to the calendar you picked, and reads that calendar only
  to find the events it wrote there (to change or remove them, and so that nothing is added twice). It never reads
  what your other events say, and never changes an event it didn't write. (Outlook's list also gives each other
  event's id and start time in that calendar; the studio drops them at once.)
- **What the sign-in can reach.** A Google service account reaches only the calendars shared with it, and asks only
  for permission to change events. Microsoft has no permission for a single calendar: the sign-in can reach every
  calendar of that account, though the studio only writes to the one you picked. An iCloud app password reaches all
  of that Apple Account's calendars and other iCloud data; Fastmail can limit an app password to calendars. Use a
  calendar kept for sessions where you can.
- **The sign-in.** The service account key, Microsoft's refresh token, or the app password is sealed under the data
  key (AES-256-GCM, its own subkey) inside the encrypted database: never in a settings file, never in History, never
  logged, never shown on a page. Microsoft's sign-in uses a code you enter on Microsoft's own site, so your Microsoft
  password never reaches the studio. Stop sending deletes the sign-in, after asking whether the events it sent should
  come out of your calendar too; switching the plugin off deletes it as well.
- **How it connects.** HTTPS only (plain http is accepted for a CalDAV server on your own network), with a time limit
  on every connection and every answer, an overall limit on each request, and answers of a sane size only. A redirect
  is never followed to another host, so the sign-in is never sent anywhere but the service; a CalDAV server's own
  addresses are followed only on its own host (for iCloud, also iCloud's own pNN-caldav.icloud.com hosts), over HTTPS.
  A CalDAV server's XML answer is refused if it declares entities, whatever encoding it is in.
- **A setup left unfinished.** A Microsoft sign-in or a CalDAV app password waiting for its calendar to be picked is
  deleted 30 minutes after the setup started, or at once with Cancel.
- **When it sends.** In the background, never inside a page: after a change is saved, every 15 minutes, and after the
  studio is unlocked following a restart. Nothing is sent while the studio is locked. A failure shows on its settings
  page in plain words, on Today after an hour, and as a phone notice if you tick that one. After a failure the studio
  waits before trying again by itself: longer after each failure in a row, and at least as long as the service asks.
  Send now tries at once. Setting up and Send a test event talk to the service while you wait, without holding up
  saves anywhere else in the studio.

## The studio page
The studio page is public and unencrypted, like any website; see [Studio page](studio-page.md). What it guards:
- **Client photos.** Only from sessions whose client allows portfolio use, never one on the client's Don't show list
  (a retouched version of it included), and none from a session whose list has any entry that isn't exactly one of
  the session's file names (two names in one entry, a range, a name with words after it). Permissions are checked
  again when a held upload goes ahead, and the site built again without a photo its client stopped allowing. Each
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
- **The inquiry form on another website** (`inquiry-form.php`). The threat: a page built elsewhere (a site builder,
  its plugins, its analytics) runs scripts the photographer doesn't control; a form placed inside that page with a
  script tag would let every one of them read what visitors type, or swap the key the form seals to. The fix: the form
  is never put inside their page. The snippet is an `<iframe>` (or, for the button, a link to a tab of its own) of a
  page served from the studio's own web host, so the form, its script, and the visitor's typing belong to that host's
  origin, which the other page's scripts can't reach. It is the Contact page's form: sealed in the browser, posted to
  the same `inquiry.php`. The page is sent with the site's content policy plus `frame-ancestors` naming only the
  websites listed in Settings (https only, each checked again before it goes into the header), and
  `frame-ancestors 'none'` for the button, so another site can't frame it to trick visitors. Nothing from the other
  website comes back to the app, and nothing of the app's goes to it but that address.
- **The Contact page in a frame.** The threat: another site shows the studio's Contact page inside a frame and
  overlays its own buttons, so a visitor's clicks or typing go somewhere they didn't intend. A page's own content
  policy can't forbid framing, so on a host that reads `.htaccess` (Apache), `contact/.htaccess` sends
  `frame-ancestors 'none'` and `X-Frame-Options: DENY` for that folder (only when the server has mod_headers, so a host
  without it serves the page unchanged rather than an error). It stays out of the site's top folder on purpose: there
  it would also reach `inquiry-form.php`, whose own header lists the sites allowed to frame it. Other hosts: the
  Contact page can be framed there; the form still seals to the studio's key in the browser.
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
  refused unless listed, so a proxy can never silently pass the allow-list. The proxy must replace those headers, never
  add to what the visitor sent ([why, and how](operations.md#behind-a-reverse-proxy)).
- Another website can't make the app save something, or download something that leaves a History line (a client
  export, the accounts download), through your signed-in browser. The browser says where each request came from
  (`Sec-Fetch-Site`, or on an older browser `Origin` or `Referer`); a save or one of those downloads is accepted only
  from the app's own pages, or from an address you typed or bookmarked. One that says nothing about where it came
  from is refused too, so an older browser following a link that hides where it came from can't slip through. Every
  browser that runs the app says where a request came from, and clients never reach the app itself (galleries and
  inquiries go through your web host), so nothing you use is affected.
- A new install can only be claimed with the one-time setup code written to `SETUP-CODE.txt` in the data folder (and
  the log); the file is deleted once the owner account exists.
- Failed sign-ins: five wrong tries from one address within 15 minutes lock that address out for 15 minutes, and no
  sender gets more than 20 wrong tries an hour. At most two password checks run at once, and the owner is told of
  failed attempts, with the exact address of each, at the next sign-in. The 15-minute lock lands on the exact address
  only, so a device on the same network never puts the owner's own devices behind it. The hourly count is kept per
  sender: an IPv4 address, or an IPv6 /64. One IPv6 device is handed a whole /64 and can take a new address from it
  for every try; counted by exact address, it would get a fresh allowance each time and could keep both password
  checks busy so the owner's own sign-in waits. Counting the /64 holds it to 20 an hour. The threat that leaves: on
  IPv6, every device on a home network usually shares one /64, so a device on the owner's own network (a compromised
  smart TV, a guest's laptop) that keeps sending 20 or more wrong passwords an hour would keep every other device on
  that network waiting. The fix: a browser that has signed in fully (password and two-factor code) in the last 30 days
  is a known browser, and the /64's hourly count never holds it back. Nothing else changes for it: it still needs the
  password and the two-factor code, the five-tries lock on its exact address still applies, and its wrong tries still
  count toward the /64. The browser keeps a random 32-byte token in a cookie that pages can't read, that is sent only
  over HTTPS and only by the app's own pages (HttpOnly, Secure, SameSite=Strict). This machine keeps only the token's
  SHA-256 hash, its expiry date, and a fingerprint of the keyring's password and master-key wraps, in
  `known-browsers.json` in the data folder (no client data, and nothing in it is a token). So a token can't be forged,
  guessed or taken from that file; it stops working after 30 days, when the password or the master key changes
  (however it was changed, `vault.cli` included), and when every other device is signed out or new recovery codes are
  made. The browser that changes the password or signs out every other device is given a fresh token at that moment
  (it signed in with two-factor and has just given the password again); every other token is revoked. When a new
  master key asked for without the old one takes effect, every token goes, that browser's too. And one sent with five wrong passwords or codes is thrown away at once, so a stolen token is worth at most five
  guesses past the /64's ceiling, once. A browser that has not signed in for 30 days, or a new one, is still held to
  the /64's count until it signs in. IPv6 reaches the app only through a reverse proxy you set up. An IPv4 address
  written the IPv6 way (`::ffff:a.b.c.d`) is counted as the IPv4 address.
- A two-factor code works once. Signing out ends the session on the server; changing the password or recovery codes
  signs out every other device; Settings > Security can sign out every other device.
- A sign-in turned away because both password checks are already running ("Busy checking other sign-ins") does
  not count as a failed try. No password was checked, so nothing was guessed; counting it meant an owner who kept
  trying while someone flooded the sign-in page could lock their own address out. The flood itself is still counted:
  every password that is actually checked and wrong counts against the address that sent it.
- Pages carry a Content Security Policy (no framing; scripts and connections only from the app itself; no inline
  script without the page's own nonce), nosniff and no-framing headers. Inline styles are still allowed.
- Galleries: client galleries are linked over https only; the viewer refuses to open on an insecure page. The gallery
  page carries its own Content Security Policy, which allows only its own script, pinned by its SHA-256 hash: a script
  added to the page (by a web host, or anything that can write there) does not run. Galleries already online get this
  page at the app's next gallery update, like any change to the gallery page; nothing needs rebuilding.
- Web host certificates: the app checks the web host over its https address with the system's normal certificate
  checks (the authority and the name). A certificate that isn't publicly trusted (self-signed, or from your own
  authority) is shown in Setup with its SHA-256 fingerprint and accepted only once you confirm it; from then on only
  its public key (SHA-256 of the SubjectPublicKeyInfo) is accepted at that address, in a certificate that still names
  the address, checked before anything is sent. A certificate for another name is never offered or accepted. A new
  key stops every check of the host until you confirm or refuse it in Setup; it is never accepted by itself. The
  checks never follow a redirect, and every connection and read has a time limit. The confirmed
  certificate is kept with your storage settings, encrypted like the SFTP host key. Certificate checks are never
  simply turned off: there is no setting for it. See [Where it runs](where-it-runs.md#the-web-hosts-certificate).
- Addresses only this machine reaches: a gallery address that leads to loopback (`localhost`, 127.0.0.0/8, ::1), to
  "this host" (0.0.0.0/8 and `::`, which a connection sends to this machine; a name that resolves to 0.0.0.0 too) or a
  link-local address (169.254.0.0/16, fe80::/10, where cloud servers keep their own settings service) is saved with a
  warning, on the save and on the web host's card in Setup. Clients can never open such an address, and the app's
  own checks of the host (its certificate, what the host can do) would be sent to this machine instead. Only the
  owner can set it, so it is a warning, not a refusal. Private network ranges (192.168.x.x and the like) get no
  warning. Storage and S3 endpoints get none either: only the app talks to them, a test checks each one before it
  is saved, and storage on the same machine is a real choice.
- S3 storage: every request is signed (AWS Signature Version 4, the content's hash included), and the secret key never
  crosses the network. A self-signed certificate is trusted the same way as the web host's: shown in Setup, accepted
  only once you confirm it, and only its key from then on; a new key stops backups there until you decide. Plain
  `http://` is accepted only to an address on your own network (checked against the address actually reached, on
  every connection), never to one on the internet. See [Where it runs](where-it-runs.md#s3-storage).
- Connection errors: when a storage, backup, or web host connection fails in a way the app has no plain words for, it
  shows that error's own text and keeps it with the connection's last test. A library could put a login or a signed
  address in such text, so before it is shown or kept, an address is cut to its host (its login, path, and query
  string dropped), a `user:password@` login and any value named like a credential (key, token, secret, signature,
  password, an Authorization header) read `[hidden]`, and the text is cut short.

Pages are sent with `Cache-Control: no-store`, so the browser does not keep client pages on disk, and the app keeps
no access log (request lines can carry search terms).

### Reaching the web host
Whoever can write to the web host can change the gallery page that unlocks the photos (see The host serves the page,
above). The photos themselves are sealed before they leave, so the sign-in to the host guards that page, not the
photos. Each way of reaching the host is judged by what it does with that sign-in, best first:

- **SFTP with a key** (the default and the recommendation). Nothing secret crosses the network: the server sees a
  signature, never the key, and a copied signature is useless. The app's own key is kept encrypted with your data.
- **SFTP with a password**, for a web host whose control panel has no way to add a key. The password crosses the
  network inside the SSH connection, encrypted, and only after the server has proved it holds the key you trusted
  in Setup (first contact shows its fingerprint; a changed key is refused until you trust it again), so it can't be
  handed to a server pretending to be yours. It is kept with your storage settings, encrypted with your data key,
  never shown again (the field reads "saved"), never written to a log, a settings file, or an error, and dropped from
  memory when the studio locks. A saved password is only ever sent to the server, port, and user it was typed for:
  change any of them and it must be typed again. A settings file never carries the password.
  Nor does it carry, or bring, which server key to trust, for a password or a key sign-in alike (a file from someone
  else could otherwise say which server to believe, and point a saved password at theirs): the key you trusted on
  this machine stays for the same server and user, and any other server has none until Setup has shown its
  fingerprint here and you have trusted it. The weaker point is the host's side: a password can be guessed,
  reused elsewhere or leaked by the host, so use a long one that is used nowhere else. Backup locations stay key-only.
- **S3-compatible storage as a static website.** Requests are signed (the secret key never crosses the network) and
  the bucket holds only sealed files and a page. A static website runs no scripts, so there is no gate: galleries
  close when the app takes them down, picks come back by email, and print orders and the inquiry form need a host
  that runs PHP. Without a gate, someone holding only a gallery's link could try passphrases as fast as their computer
  allows, so a gallery with a passphrase is refused on this host by its kind, before any check has run, with the
  reason on its client page. The same refusal sits where every upload passes, so no path (an update, Move galleries,
  putting a gallery back online) can publish a passphrase gallery here without its check. That refusal reads the
  gallery's own record (whether it was built or put online with a passphrase), not only the session's passphrase box,
  and a gallery on its way up keeps its passphrase when passphrases are turned off: otherwise one caught mid-upload would
  lose the passphrase in the app's records, look like a gallery without one, and later be moved here unchecked. Choosing S3 while passphrases
  are on is never a silent switch: Setup says the host can't check passphrases and the owner either turns passphrases off
  for galleries here or chooses another host; a studio already on S3 with passphrases on is told the same in Online
  galleries and the alert bell.
- **Left out: plain FTP.** It sends the sign-in in the clear, and whoever holds the sign-in can change the page that
  unlocks the photos. **Left out: WebDAV.**
- **Not built yet: FTPS.** It can be made safe (TLS asked for first with AUTH TLS, the certificate checked or trusted
  once like the web host's, the file channel encrypted too, and no fallback to plain FTP), but building and testing
  it to that standard needs a test FTPS server the app doesn't have yet. Until then, a host that offers FTPS almost
  always offers SFTP with a password as well.

## Deleting a client
There is no undo: their galleries come down, their photos and archives are destroyed, and every record and
history line about them is erased. Sealed daily backups still contain the record until they age out (the 30 most recent daily backups are kept, and the
second location's copies on the same schedule). A small list of deletions, `deletions.json`, is kept outside the
database: if an older backup is ever restored, those clients are deleted again at the next sign-in. Deleted sessions
are on the same list, and are deleted again the same way (their History lines stay, as after any session delete). The
list holds only record numbers and creation times, no names.

Their sealed photo archives are deleted from every backup location and the key that opens them is destroyed. That key
is also inside each database backup made before the delete (sealed to your master key), so until those backups age out, a
copy of an archive kept somewhere else (a versioned bucket, a snapshot of the vault disk) could still be opened with
the master key and one of those backups. After that, no copy anywhere can be opened.

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

## Clients in a file
**Import clients from a file** treats the file as untrusted. It refuses a file over 2 MB, more than 5,000 rows or more
than 60 columns, and anything that is not text. It reads UTF-8 (with or without the mark spreadsheets put first),
UTF-16 with its mark, or else the Windows/Latin-1 encoding older spreadsheets save in. Every value has control and
text-direction characters removed and is cut to a set length; nothing in the file is ever run, opened, or followed.
The uploaded file is kept only in the app's memory while you check the columns and the preview (an hour at most,
and gone at a restart): it is never written to disk outside the encrypted database. Nothing is written until you
press Import, and a client already here is never merged on its own: you pick Skip or Update for each. On Update, a
person in the file is the person here with the same email, else the same name, else the same phone; a phone a family
shares never writes one partner's email or birth date over the other's (the new person is added instead), and notes
are added to, never replaced. **Remove this import** takes back the clients an import added that you have not worked
with since (a session, note, label, or event among them); each one is recorded on the deletions list like any other
deleted client.

A file's **Minor** column never quietly turns a child into an adult. Minor status protects children (a parent or
guardian signs for them, and agreements keep the clause about a child turning 18), so the app reads a Minor value the
way it reads the Minor box on the person form: when it agrees with the birth date, nothing is stored over the birth
date and the mark keeps moving with it; only a value that disagrees is kept as an explicit choice. A blank Minor cell
says nothing, so the birth date decides. Update on a client already here goes through the same rule, so the minor mark
the preview promises is the one the app uses afterward.

## Record numbers
Session, client, step (to-do), and event numbers are never handed out twice, even after the newest one is deleted. A
page left open on a deleted session, client, step, or event can't save into a new, unrelated one: anything sent from it
gets a page saying it was deleted, and nothing is changed. The highest number issued is kept in `doc-numbers.json` beside the
database (it survives restoring an older backup) and in a small table inside it (the database alone is enough if that
file is lost); at each sign-in both are raised to at least every number the database, its History, and the deletions
list have seen.

## Rotating the data key
Changing your password stops the old password from opening the *current* keyring, but anyone who kept a copy of an
older `keyring.json` (with the old password) can still unwrap the same data key. Only a rotation revokes that. So if
a password, a keyring copy, an unlock key file from before 1.5, or a master key you have since replaced may have
leaked, stop the app and run `python -m vault.cli rotate`. Everything is
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

**In the app** (Settings, Backups, Restore from a backup; or a new install's first screen), the same checks apply,
and a few more, because a page can be reached by anyone on your network while a command needs the machine:
- Restoring replaces every record, so an existing studio asks for your current password as well as the master key,
  and wrong passwords and wrong master keys count toward the same limits as sign-in.
- A new install, which has no password yet, asks for the setup code from the data folder instead (as owner setup
  does), so only someone who can open the machine's folders can claim it with a backup. Once a studio exists, the
  new-install restore page is gone.
- Before the setup code is typed, that page lists only the backups in the folder the app itself offers (`/vault`,
  or a vault folder beside the data): the names, dates, and sizes of the sealed files in its `db` folder, nothing
  inside them. The threat: a folder typed into the address, such as the top of the disk, made the app read every
  folder under it, and show which backups lie where, for anyone on an allowed network. The fix: any other folder is
  listed only once the setup code is given with it, and only its own `db` folder is ever read, never anything under
  or beside it.
- The backup is opened in a scratch folder inside the data folder; nothing live changes until you confirm. The plain
  copy of your settings that each backup holds is deleted from that folder at once (the database already holds them).
- The data it replaces is moved, whole and still encrypted, into `set-aside` in the data folder, so a wrong restore
  can be undone (choose it under Data set aside at an earlier restore). Like a backup, each one keeps clients you have
  since deleted, so the app removes it 30 days after it was made (never while the clock check has paused automatic
  deletions), and Today says so five days before.
- The face photos are encrypted under the data key, so they come back from the faces file made with the backup (its
  fingerprint checked like the backup's), and the ones here are set aside with the rest.
- A new master key waiting out its 48 hours refuses a restore: the restored keyring and session keys belong to the
  key the backup was sealed to, and the wait would then apply a key to data it was never asked for. Cancel it, or
  restore after it takes effect. A wait recorded inside the restored backup is dropped, never applied (it may have
  been canceled since). A rotation the studio still asks for (a replaced master key) is still asked for afterwards.
- The app then locks and restarts, so every part of it starts again from the restored data.

Opening with the master key proves a backup was sealed to it, not who sealed it: `age` has no sender authenticity, and
sealing takes only the master key's public half, which the app and its database hold. So when a backup is made, its
fingerprint (SHA-256 of the sealed file) is recorded, and `restore` on this install refuses a backup whose bytes differ
(swapped, rolled back, or altered). Where there is no record to compare with (a new machine restoring with `--from`, a
backup the restored database never heard of, a file chosen from your computer in the app, or an archive opened with
`open-orphan`), the command and the app's review screen say first that it was not checked; use it only if it came from your own vault folder, and check its date and contents.

If the data key was rotated after the backup was made, `python -m vault.cli relink-photos` puts each photo's current
key back into the restored database. The key sealed beside each photo is sealed to the master *public* key, so anyone
able to write to the photos folder could write one, with a photo to match. Relink only takes a key whose record carries
a tag made with the key the restored database holds (each rotation adds one, with the key it retires); anything else is
refused and left for `recover-photos <folder> --from <photos folder>`, which decrypts photos into a separate folder without touching the database.
Photos rotated under 1.0.0 have no such tag, so relink refuses them too; that command still opens them.

## Plugins
A plugin runs inside Lumen Aureum with full access: once you sign in, it can read and change every record and see your
keys. So adding one is held to these rules:
- **The signature is the boundary.** Settings, Plugins adds a plugin only when it carries Lumen Aureum's signature: an
  Ed25519 signature over its name, its version, the oldest app version it runs on, and the size and SHA-256 of every
  file in it. The public key is part of the app. There is no setting to add another key or to skip the check. A plugin
  that is unsigned, signed with any other key, or holds a file the signature doesn't cover is refused whole. A
  signature says who published a plugin; it can't make a plugin harmless.
- **Owner only, and confirmed.** Adding or removing a plugin asks for your current password and a two-factor code, as
  a new master key does, and wrong ones count toward the sign-in limits.
- **One download, nothing about you.** Add from the link fetches the link's small manifest, checks its signature, and
  then fetches the package it names: over HTTPS only (a redirect to anything else is refused), each with a size cap,
  a timeout on every read, and one deadline for the whole add. Those two requests are the only traffic, and they carry
  nothing about your clients or your studio. Add from the file downloads nothing. A package is read with limits on its
  size, the number of files, and each file's unpacked size, and a file that would land outside the plugin's own
  folder, or a link, is refused.
- **Checked again at every start.** An added plugin is kept in two places: its package in the encrypted database, so
  every backup carries it and a restore brings it back, and a copy in `plugins/` in the data folder, which is what
  loads (the studio starts locked, so the database can't be read then). At every start that copy is checked again:
  the signature, every file's size and SHA-256, and nothing in its folder the signature doesn't list. The code that
  runs is the very bytes that were checked. A plugin that fails isn't loaded: Settings, Plugins says it didn't load,
  and the next sign-in puts its folder back from the database's copy, to load at the next start.
- **No updates on their own.** The studio never fetches a plugin by itself or looks for a newer one. A newer signed
  version replaces the one you have only when you add it; an older one is refused.
- **Off until you turn it on.** An added plugin stays off until you turn it on in Settings, Plugins. While it is off,
  none of it runs: the app doesn't execute its code at all. The switch is kept in the database, and mirrored by an
  empty `.on` file in the plugin's folder so it can be read at start, before you sign in. That file is the only one
  the start-time check allows beyond the signed list, and it can only start a plugin that passes every check; at
  sign-in it is put back in line with the switch in the database. Turning a plugin on or off restarts the studio.
- **Its name is its own.** An added plugin can't take the name of a plugin built into Lumen Aureum, or of one loaded
  from `CRM_PLUGIN_DIRS`.
- `CRM_PLUGIN_DIRS` stays the developer's way in: plugins in those folders load unsigned, as before, because whoever
  can change the container's mounts and settings already controls the app.

## One process
The data key lives in the memory of one process. Run the app as a single process (threads are fine); several worker
processes would each need their own sign-in.

Adding or removing a plugin restarts that process gracefully: the server starts a new one, then the old one finishes
what it is doing and stops (the server itself keeps running). The new process holds no key, so the studio is locked
until you sign in again. Where the app can't ask the server for this, Settings, Plugins says to restart the studio.

## What is in the image
Only what the app runs on: Python, the libraries PDFs are drawn with (Pango and the fonts), and time zone data. Each
extra system package is one more set of security fixes to keep up with. From 1.6.0 the image has no curl: it, and the
LDAP, Kerberos, SSH, and RTMP libraries it pulls in, carried every critical known vulnerability in the image. The health
check is the app's own probe (`healthcheck.py`, standard library only), which asks `/healthz` on the container's own
address and nothing else. Every image build takes the Debian security fixes published up to that day.

If your compose file or app catalog runs its own health check with curl, it fails from 1.6.0: use
`python /app/healthcheck.py` instead (it works on 1.5.2 and later).
