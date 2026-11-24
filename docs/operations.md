# Running it: upgrades, rollback, and if something goes wrong

## Upgrading
1. Read what changed in the new version on its release page.
2. Make sure a recent backup exists (Settings, Backups shows the last one) and that you have your master key.
3. In `compose.yaml`, change the image tag to the new version (from the old version number to the new one).
4. `docker compose pull && docker compose up -d`, then sign in to unlock.

At the first sign-in after every update (any change of version, newer or older), the app makes an encrypted backup
before it changes anything, listed under Settings, Backups as **Before the update**, with the version it came from (its
kind is `upgrade`). If that backup can't be made, nothing is changed and sign-in says why. The database also records
which version of its layout it has; when a new version needs to change it, the change runs right after that backup. The change runs all at once or not at all. After an update,
Today shows a note with the new version number, a link to what's new (also on About), and which backup was made
before it. The note stays until you hide it or until the next day. An older
version never opens a database whose layout number a newer one has moved on: it says so and points here.

### Galleries online get each new version's page
Whenever a new version changes the gallery page or the host's small scripts, the app gives every gallery online the
new ones, one at a time, by itself (the photos are never touched). Before replacing anything on a host it checks the
host runs PHP 7.4 or later, keeps the gallery's current page and scripts as a way back, and puts the scripts up before
the page. It then proves the new scripts are the ones running, reads the new page back at its public address, and,
for the first gallery on each host, opens it the way a client would. If that check fails, that gallery goes back to its
previous page and scripts, the others on that host wait, and Today says why. A gallery whose client is picking right
now waits until they have been quiet for 6 hours (at most a week); its Gallery block says so, with **Update it now**. If a client
says "Send" failed in that moment, ask them to reload the page: their choices are still on their device.

### The 1.6 update
1.6 adds tables and columns to the database and changes nothing that was there, so the database's layout number stays
the same. Its first sign-in still makes the Before the update backup first, as above. 1.5.4 does not refuse the
database afterwards, but it reads none of what 1.6 added (taxes by name, payment plans, corrections to a closed year,
deposit credits, crew). To go back to 1.5.4, restore the Before the update backup the app made at your first 1.6.0
sign-in (see [Rolling back](#rolling-back)); never just set the older tag.

Then, from the first sign-in:
- **Taxes.** Your one sales tax rate becomes a tax named Sales tax, added on top of the price and applying to every
  session (Settings, Taxes). Booked sessions keep the rate they were booked with.
- **Closed years.** Each year now closes after the next January 31 (or sooner, when you close it on Finances in
  January). Nothing more can be dated in a closed year, and its payments and expenses are corrected with a new entry
  in the open year instead of being changed (see [Closing a year](getting-started.md#closing-a-year)).
- **Galleries.** Every gallery online gets 1.6's page and scripts the usual way (above): from then on each device
  keeps its own picks.
- **Health check.** The image has no curl: see the note on your own health check, just above Rolling back.

### The 1.5 update
1.5 changes the database (new tables only), so its first sign-in makes the **upgrade** backup first, as above, and
1.4.1 refuses the database afterwards. Then, once:
- **Stages.** The new stage rules are applied to every open session. A session that already qualifies moves or
  closes (picks already in move a session to Delivered; delivered and paid in full, with nothing owed either way,
  closes it), each change is written in that session's History, and the note on Today lists them. Open
  steps are dated from the shoot again, never from the update day. The clocks that delete photos after delivery
  start from the day the files were delivered.
- **Prices.** A price list already used on a sent quote counts as approved. Any other prints SAMPLE until you
  approve it, and Today shows **Review your prices** until you do.
- **Unattended unlock is removed.** An install that had it on starts locked, and the sign-in page says once why.
  The old key file is no longer used: delete it from its separate volume or USB key, and take the
  `CRM_UNLOCK_KEY_FILE` line and its volume line out of `compose.yaml` (1.5 ignores them). To hear about a locked restart instead, set up
  the notice in Settings, Notifications (see [Notices while locked](security.md#notices-while-locked)).
- **Signing in.** The update signs every device out; each one asks you to sign in again.
- **Galleries.** Every gallery online gets 1.5's page and scripts the usual way (above): print ordering now closes 24
  hours before a gallery does, and the gallery credit carries the Lumen Aureum mark, including on galleries built
  before, without a rebuild.

### The 1.4 update
1.4 gives every gallery a new address and seals the client's picks and drafts, and studio page inquiries, in one new
format. Each gallery's folder on your web host gets a new name, its link ID (`g-` and 23 random characters). At the
first sign-in after the update, a job (shown in the jobs panel, and on Today until it is done: "Updating galleries: 12
of 15 done") moves everything over:
- A gallery not online (never uploaded, taken down, gone dark) just takes its new ID here. A gone-dark gallery's page
  stays at its old address and comes down with it.
- Each gallery online gets a new folder beside its old one, on the same host: this version's page and scripts, its
  photos and Download all pieces as they are online, its days, first-visit stamp, codeword and one-time download. The
  app then opens it the way a client would; only if that works does the gallery take its new address. Its old folder
  then stops taking anything, gets a small page that sends the browser on to the new folder (the key in the client's
  link comes along), and what the client sent there (picks, a draft, a print order) is read and carried over before
  anything else in that folder is removed. Nothing a client sent is lost or announced twice. The link your client
  already has keeps working for the gallery's whole life; the old folder comes down when the gallery does.
- Copies a move left on an earlier web host are read the same way, once; anything newer there comes over.
- The studio page's form gets its 1.4 script; inquiries that were waiting are collected first, as usual.

A gallery whose host can't be reached stays exactly as it was (the 1.3 page keeps working at its address, and the
client's picks wait on the host, read at every check) and is tried again at each check; Today names the host. If that
host is gone for good, say so on Move galleries. A host running PHP below 7.4 keeps its galleries on 1.3's page and
scripts at their old addresses; Today names it and its version. A gallery whose client is picking right now waits until
they are done (its Gallery block says so, with Update it now). Going back to 1.3.1 after 1.4 has run is refused, as for every
update; restore the upgrade backup instead.

Images are signed. To check one before running it:
`cosign verify ghcr.io/lumenaureum/lumenaureum:<version> --certificate-identity-regexp 'https://github.com/lumenaureum/lumenaureum/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

From 1.4.0, each image also carries two signed records made by the release build: how it was built (this repository, its release workflow and the exact commit) and a list of everything inside it (an SPDX software bill of materials). To check both with the GitHub command-line tool:
`gh attestation verify oci://ghcr.io/lumenaureum/lumenaureum:<version> --owner lumenaureum`
(add `--predicate-type https://spdx.dev/Document/v2.3` to check the list of contents).

The release build also checks that list's system libraries (the Debian packages under the app, such as OpenSSL and the
ones PDFs are drawn with) against known vulnerabilities, at every severity and whether or not a fix exists yet. The
findings go in the release run's summary on GitHub. This is a report, not a gate: the image scan before it already
stops a release on any fixable critical or high finding. Python packages are checked on every change instead.
A build of your own takes the Debian fixes published up to the day its system-library step last ran. Docker reuses
that step from an earlier build when nothing above it changed, so build with `docker build --no-cache` to pick up
newer fixes.

The container's health check is the app's own probe, `python /app/healthcheck.py` (Docker runs it every minute). From
1.6.0 the image has no curl ([What is in the image](security.md#what-is-in-the-image)), so if your compose file or app
catalog sets its own health check with curl, change it to `["CMD", "python", "/app/healthcheck.py"]` before updating
(it works on 1.5.2 and later). The `curl` commands elsewhere in these docs run on your own computer, not in the container.

## Rolling back
Going back to an earlier version is always a restore, never just setting the older tag: the app backs up at the first
sign-in after every update, and going back means restoring that backup.
1. Keep the newer version running (an older one may refuse the database or misread it). Follow **Restoring a backup**
   below, steps 1 to 9. In step 1, pick the line whose kind is `upgrade` (Before the update, from the version you are
   going back to): the backup made at the first sign-in on the newer version, just before it changed anything.
2. After step 3, set the older tag in `compose.yaml`, so step 8 starts the older version.

Those steps move only the restored files into your existing data folder. Everything else in it stays, including the
`tls` folder that holds the app's HTTPS certificate, so your devices don't warn about a new certificate. Keep the data
folder's own `deletions.json` and `doc-numbers.json` and move the restored copies in beside them (step 6): they are
merged at sign-in, so clients and sessions deleted since the upgrade stay deleted and numbers don't repeat. (A version from before 1.0 reads these as `deletions.enc` and `doc-numbers.enc`, so on those,
check the clients list and the next invoice number.)

Going back is always this restore because each version that changes the database updates it at the first sign-in (1.0.1 turned refunds recorded the 1.0.0 way into credits; 1.1 added the gallery draft fields; 1.1.1
keeps a copy of each issued invoice and receipt; 1.2 marks the data as using download galleries; 1.3 keeps each payment's sales tax and each statement charge as recorded; 1.5 adds tables for client labels, End
contact, replaced quotes and the studio page's kept files, and applies the new stage rules once; 1.6 adds tables and columns an earlier version would ignore), and an earlier
version refuses the updated database or reads only part of it. Anything
entered after the upgrade is not in that backup: before rolling back, look through each client's History and the
audit log for what changed since, and note it. A rollback cannot undo a leak: if you rolled back because of a security
problem, also follow "If you suspect a compromise".

## Restoring a backup
Each nightly backup holds your data (the database, with clients, money and settings), the key that opens it, your
settings as a separate file, and your storage settings (where backups go and how to sign in there).
Keep a copy of your `compose.yaml`. Uploaded photos are not in the nightly backup (they are large). Instead each one
is mirrored, still encrypted, to your second backup location (or the vault when there is none): one copy, kept while
the photo is kept and deleted with it, so photos take twice their size. Their keys are in the database, so after
restoring the database the app fetches any photo missing from the photos folder back from the mirror.

This section is the one place with the restore steps; docs/security.md points here. Settings, Backups has **Check
the newest backup opens**, which tests a backup with your master key without changing anything.

### In the app
**An existing studio:** Settings, Backups, **Restore from a backup**.
1. Choose a backup from the list (newest first), or a backup file from this computer, and paste your master key
   (the one the studio had when the backup was made). Press **Check this backup**. The app opens it in a scratch
   folder in the data folder and checks it: a backup from the list must match the fingerprint recorded when it was
   made; a file from your computer has no record to compare with, so the next screen says it was not checked.
2. The next screen shows what changes, side by side: clients, sessions, galleries online, payments, uploaded photos,
   the newest client and session, and the last change. Type your current password and press **Restore and restart**.
3. The data here now (`crm.db`, `keyring.json`, `storage.json`, the face photos, and a copy of `deletions.json` and
   `doc-numbers.json`) moves into `set-aside/<date and time>` in the data folder, with a README.txt saying what it is.
   The face photos made with the backup come back with it. A file from your computer holds no face photos: choose the
   face photos file made at the same time in **Face photos backup (if you have it)** and they come back too (when the
   studio has a record of the backup, that file must match the fingerprint recorded for it, or nothing is restored);
   without it, the face photos here stay. The review screen says which. Nothing is deleted at once: the app removes a set-aside folder after 30 days, and Today says so five days
   before. Copy it somewhere else to keep it longer. The backup takes its place, with its two `.restored.json` lists (step 6 below happens for you),
   and Lumen Aureum restarts. Sign in with the password you had when the backup was made (or reset it with the
   master key).

Galleries stay in step with your web host: a gallery online now that the backup doesn't have online comes down at
the next gallery check (Today lists it until then), and one the backup has online that was taken down since shows
as taken down, with Put back online. Backups made after the restored one stay listed, so you can go forward again.
To undo a restore, do the same with the copy under **Data set aside at an earlier restore** on the first screen, within
30 days. A new master key waiting to take effect stops a restore: cancel it, or restore after it takes effect.

**A new install** (a new machine, or after a loss): mount your old backup folder at `/vault` (the example compose
file and the TrueNAS app already do), start the app, and on the first screen choose **Restore from a backup**
instead of setting up the account. Type the setup code (in `SETUP-CODE.txt` in the data folder, or the app's log),
pick the backup (the list is the folder's `db` files, newest first; if one turns out to hold only face photos, pick
the one made at the same time), paste the master key, check it, and restore. It was not checked (a new machine has no
record of the backup), so make sure it came from your own backup folder.

A backup file chosen from your computer can be up to 40 MB. Larger ones, and anything below, use the command line.

### On the command line
Each step below is one command or one file move; type them as shown, in a terminal on the machine that runs Docker.

**The files.** A restore writes these into a new folder, each in the place shown:

| File | What it is | Where it goes |
|---|---|---|
| `crm.db` | Your data: clients, sessions, money, and settings | Into the data folder |
| `keyring.json` | The key that opens `crm.db` | Into the data folder |
| `storage.json` | Where backups go and how to sign in there | Into the data folder |
| `deletions.restored.json` | The backup's copy of `deletions.json`: the clients and sessions you deleted, so they stay deleted | Into the data folder (step 6) |
| `doc-numbers.restored.json` | The backup's copy of `doc-numbers.json`: the highest invoice, receipt, session, and client numbers used, so none repeat | Into the data folder (step 6) |
| `settings.json` | A copy of your settings, for Settings, Import settings | Leave it; `crm.db` already holds your settings |
| `deletions.unverified.json` | Deletions that could not be confirmed. Never in a backup | Leave the data folder's own copy where it is |

A backup made before you ever deleted a client has no `deletions.restored.json`. That is expected: there was nothing to list.
Backups from before settings were included have only `crm.db` and `keyring.json`.

**Everything (on this machine, or after a loss).** Run each command in the folder that holds your `compose.yaml`.
1. **Find the backup's name.** `docker compose exec lumenaureum python -m vault.cli backups`. It asks for your password
   (or the master key), because the list is kept in the encrypted database. Each line shows the date, the kind, the
   size, and last the name, for example `db/3f9c1e7a5b2d4c6e8a0b1c2d3e4f5a6b.age`. Pick a line whose kind is `db`,
   `manual`, or `upgrade` (`faces` lines hold only face photos: the optional picture on a client's page) and copy the whole name, `db/` included. Settings,
   Backups (the Vault page) shows the same dates and sizes, so you can match them up there. On a new
   machine there is no database to ask yet: start the app once (`docker compose up -d`) and use the newest file in
   the `db` folder of your vault.
2. **Restore it.** `docker compose exec lumenaureum python -m vault.cli restore db/3f9c...6b.age /data/restored`, with your
   backup's full name. It asks for your master key. On a new machine, add `--from /vault` (the vault folder as the
   app sees it). The files appear in a folder named `restored` inside your data folder. On a new machine it first says
   the backup was not checked: there is no record here of the backup's fingerprint to compare with, so make sure it
   came from your own vault folder (see Restoring a backup in docs/security.md).
3. **Stop the app.** `docker compose stop`.
4. **Move three files.** Move `crm.db`, `keyring.json`, and `storage.json` from `restored` into the data folder,
   replacing the ones there. If the data folder has `crm.db-wal` or `crm.db-shm`, delete them: they belong to the
   database you are replacing.
5. **Leave `settings.json`** in `restored`. You need it only for a settings-only restore (below).
6. **`deletions.restored.json` and `doc-numbers.restored.json`.** Move them into the data folder too, and keep the
   data folder's own `deletions.json` and `doc-numbers.json` if it still has them. At the next sign-in the two are
   merged: every deletion in either list is kept (each is checked before it is applied), and every number carries on
   from the higher of the two. The `.restored.json` files go once merged.
   - Restoring an older backup after the data folder was lost: restore the newest backup too, into another folder
     (for example `/data/restored-newest`), and move its two `.restored.json` files in instead: they are the newest lists.
   - Leave `deletions.unverified.json` where it is, if the data folder has one.
   - Going back to a version before 1.3, which doesn't merge: keep the data folder's own two files, or if it lost
     them, move the restored ones in under the names `deletions.json` and `doc-numbers.json`.

   With these, clients and sessions deleted after the backup was made are deleted again, and numbers carry on instead
   of repeating.
7. **Check the owner (only if you copied files some other way).** `restore` writes as the app's own user, so moving
   is enough. Otherwise give the files the same owner as the rest of the data folder (see it with
   `ls -ln <data folder>`; 568 on TrueNAS, 10001 with the example compose file), for example
   `sudo chown 10001:10001 <data folder>/*.db <data folder>/*.json`.
8. **Start and sign in.** `docker compose up -d`, then sign in with the password that was current when the backup
   was made (or reset it with the master key).
9. **Tidy up.** Delete the `restored` folder.

After restoring:
- **If you rotated the data key after this backup was made**, the photos' keys in the restored database are older
  than the photos. Run `python -m vault.cli relink-photos` (it asks for your master key): each photo's current key,
  sealed beside it to your master key, goes back into the database. It takes only keys that trace back to the
  key the restored database holds; any it refuses are listed, and `python -m vault.cli recover-photos <folder> --from <photos folder>` still
  decrypts those into a separate folder.
- **If Today says clients you may have deleted are back**, this copy of your data couldn't confirm some deletions
  (usually a backup from before 1.0.1 restored after a key rotation). Open the list, and either apply the deletions
  or keep the clients. Nothing is deleted until you choose.

**Settings only.** Add `--settings-only` to the restore command. It writes just `settings.json`; import it in
Settings (Import settings). Your clients and money are not touched.

**Data, keeping today's settings.** Export your settings first (Settings, Export settings), restore everything as
above, then import the file you exported.

### On a TrueNAS catalog install
In the app, nothing differs: the app sees the data and vault datasets as `/data` and `/vault`, as with compose, and
restarts itself (the Apps screen shows it running throughout). On the command line, the same steps, with the app's shell in place of `docker compose exec lumenaureum`, the Apps screen in place of
`docker compose stop` and `up -d`, and the TrueNAS shell for moving files.
1. Open the app's shell: Apps, lumenaureum, then in **Workloads** the shell button beside the container. Type the
   commands of steps 1 and 2 above without `docker compose exec lumenaureum`, for example
   `python -m vault.cli backups`, then `python -m vault.cli restore db/3f9c...6b.age /data/restored` (on a new install,
   add `--from /vault`). The files appear in a folder named `restored` inside your data dataset.
2. Stop the app: Apps, lumenaureum, **Stop**.
3. Move the files in the TrueNAS shell (System, Shell). Go to the data dataset you chose under Storage, for
   example `cd /mnt/tank/lumenaureum/data`, then:
   `mv restored/crm.db restored/keyring.json restored/storage.json .` and, if they are there,
   `mv restored/deletions.restored.json restored/doc-numbers.restored.json .` (step 6 above explains them). Delete
   `crm.db-wal` and `crm.db-shm` if they are there. The restored files already belong to the app's user (568); check
   with `ls -ln`, and if any shows another owner, `chown 568:568` it.
4. Start the app (Apps, lumenaureum, **Start**), sign in, and delete the `restored` folder
   (`rm -r restored` in the TrueNAS shell).

## Behind a reverse proxy
The app serves HTTPS itself, so most installs need no proxy. If you put your own HTTPS reverse proxy in front of it
(`CRM_TLS=off`, with the proxy's address in `CRM_TRUSTED_PROXIES`), the proxy must **replace** two headers on every
request, never add to what the visitor sent:
- `X-Forwarded-For`: one address, the visitor's.
- `X-Forwarded-Proto`: one word, `https` (or `http`), how the visitor reached the proxy.

**Why.** The app believes these headers only from the addresses in `CRM_TRUSTED_PROXIES`, and refuses any request that
carries them from anywhere else. It reads `X-Forwarded-For` from the right, passes over addresses listed in
`CRM_TRUSTED_PROXIES`, and takes the first one that isn't listed as the visitor (if all are listed, it takes the first).
That address decides whether the allow-list lets the visitor in, and which address failed sign-ins lock out.
- A proxy that adds its own entry keeps whatever the visitor typed in front of it. That is safe only while the visitor's
  address is never in `CRM_TRUSTED_PROXIES`. List a whole network there (`192.168.1.0/24`), or sign in from the
  proxy's own machine, and the visitor's real address is passed over, so the address they made up is taken instead.
- A proxy that passes the visitor's headers on unchanged lets every visitor choose their address, and lets a visitor
  who reached the proxy over plain HTTP claim HTTPS, so the app answers a connection that isn't encrypted.
- `X-Forwarded-Proto` must be exactly `http` or `https`. A value with more than one entry (`https, http`) is not
  believed, and the request is refused as not HTTPS.

So set `CRM_TRUSTED_PROXIES` to the proxy's own address only, not a network, and make the proxy replace both headers.
If your proxy can only add to `X-Forwarded-For`, listing only its own address keeps the visitor's real address the one
the app reads.

**nginx.** Use `$remote_addr`, not `$proxy_add_x_forwarded_for` (that one adds to what the visitor sent):
```nginx
location / {
    proxy_pass http://192.168.1.10:8780;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Caddy.** From version 2.5, Caddy replaces both headers by itself unless the visitor is in its own `trusted_proxies`,
so leave your visitors' networks out of that. The two `header_up` lines say it outright:
```caddy
studio.example.com {
    reverse_proxy 192.168.1.10:8780 {
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

**Traefik.** Traefik replaces both headers by itself unless the entry point trusts the sender. Leave
`forwardedHeaders.insecure` off, and leave your visitors' networks out of `forwardedHeaders.trustedIPs`.

**To check it,** from a computer on your network (not the proxy's machine), run
`curl -H "X-Forwarded-For: 203.0.113.9" https://studio.example.com/healthz`. The answer should be
`{"locked":...,"ok":true}`: the made-up address was not believed. A 403 means the app took the made-up address as the
visitor's: fix the proxy before using the app this way.

## Watching it from outside
The locked-studio notice ([Notices while locked](security.md#notices-while-locked)) comes from the app itself, so it
can't tell you the app has stopped, or that the whole machine is off. A monitor elsewhere can: it asks the app's
`/healthz` address every minute or so, and sends a notice when the app doesn't answer or answers locked.

`/healthz` answers without signing in: `{"locked":false,"ok":true}` while the studio is unlocked, and
`{"locked":true,"ok":true}` while it is locked. It carries nothing else. Like every page, it answers only the networks
the app allows (every private network, unless `CRM_ALLOW_ONLY` says otherwise). Never open it to the internet.

Run the monitor on a different machine from the app if you can (a small computer that stays on), so a machine that is
off is noticed too; for the same reason, a notice server on that same machine goes quiet with it.

**With Uptime Kuma** (it is also in the TrueNAS app catalog):
1. Add a new monitor of type **HTTP(s) - Keyword**.
2. URL: the address you open the app by, with `/healthz`, for example `https://192.168.1.10:8780/healthz` (through
   your proxy if you use one).
3. Keyword: `"locked":false`, quotes included.
4. If the app uses the certificate it made itself, tick **Ignore TLS/SSL error for HTTPS websites**.
5. Under notifications, set up ntfy: the server address, a topic, and an access token if the server takes one. Give
   the monitor a name that says nothing about your clients, such as "Studio": the notice shows it.

That one monitor goes down both when the app stops answering and when it answers locked. To tell the two apart, add a
second monitor of type **HTTP(s)** with the same address and no keyword: it goes down only when the app stops
answering. After a restart you may hear twice, from the monitor and from the app's own locked-studio notice.

**Any other monitor** works the same way: fetch `/healthz` over HTTPS, accept the app's own certificate, and alert when
the answer isn't a 200 or doesn't contain `"locked":false`. From a scheduled job, for example:
`curl -fsk https://192.168.1.10:8780/healthz | grep -q '"locked":false' || curl -H "Authorization: Bearer <token>" -d "Studio down or locked" https://ntfy.example.com/<topic>`

A 403 means the app doesn't allow the monitor's address, or a name it doesn't know: see
[Reaching the app](where-it-runs.md#reaching-the-app).

## If you suspect a compromise
Signs: sign-in attempts you didn't make (you are told at sign-in), devices you don't recognize, settings you didn't
change (every change is in each client's History and in the audit log).
1. **Contain.** Stop the container, or take the machine off the network.
2. **Rotate the data key.** `docker compose run --rm lumenaureum python -m vault.cli rotate` (the app stopped): everything is
   re-encrypted under a new key; old copies of the keyring stop working.
3. **New password and recovery codes; sign out everywhere** (Settings > Security). Re-add two-factor in your
   authenticator app if the phone or its backup may be exposed.
4. **Replace the master key** if it may have been seen: `python -m vault.cli new-master` (asks for the old one). If
   the old one is lost instead, use Settings > Security > Replace a lost master key (see Security in the docs).
   Backups made before that still open only with the old key until they age out (the 30 most recent daily backups are kept).
   Then **rotate the data key again** (step 2): a replaced master key still opens the data key through every keyring
   from before, so until a rotation after the replacement, the old key plus an earlier backup opens your data today.
5. **Change the storage credentials** (SFTP key, S3 keys) at your provider, then enter the new ones in setup.
6. **Review** the history for changes you didn't make, and take down any gallery you didn't publish.
7. **Tell people if you have to.** If client information may have been seen by someone else, you may have legal duties
   to tell your clients (and in some places a regulator) within a set time. Check the rules where you work; a
   template is below.

### Template: telling a client
This template is one of the documents that come with Lumen Aureum: see the notice under
[Example documents](../README.md#example-documents).

> Dear [name], on [date] I learned that [what happened]. The information involved may include [what]. It did not
> include [what it did not]. I have [what you did]. You may want to [what they can do]. If you have questions,
> contact me at [contact]. I am sorry this happened.

## A privacy notice you can give clients (edit to fit)
This template is one of the documents that come with Lumen Aureum: see the notice under
[Example documents](../README.md#example-documents).

> **How I keep your information.** I keep your contact details, session details, agreements and photos to do your
> session and deliver your photos. I keep them encrypted on equipment I own. No online service stores them. Your online
> gallery is encrypted; only the link in your letter opens it, and it is removed [N] days after you first open it.
> I keep your original photo files for one year after delivery, then delete them; you can ask me to delete them
> sooner, in writing. I keep your client record for [N] years for my business records, then delete it. You can ask me
> what I hold about you, ask me to correct it, or ask me to delete it. I do not sell or share your information.
> [If provided to me, I may keep a photo of you on your client record so I recognize you when we meet. It stays
> encrypted on my own equipment and is never shown anywhere else.]
> Contact: [your details].

## Who can do what
Lumen Aureum has one owner account. Keep your master key, password manager and recovery codes where someone you trust
could reach them if you can't (an executor, a business partner), with instructions; there is no other way in.
