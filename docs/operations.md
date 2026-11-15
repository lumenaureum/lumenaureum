# Running it: upgrades, rollback, and if something goes wrong

## Upgrading
1. Read what changed in the new version on its release page.
2. Make sure a recent backup exists (Settings, Backups shows the last one) and that you have your master key.
3. In `compose.yaml`, change the image tag to the new version (from the old version number to the new one).
4. `docker compose pull && docker compose up -d`, then sign in to unlock.

The database records which version of its layout it has. When a new version needs to change it, the change runs at
the first sign-in, after the app has made an encrypted **upgrade** backup (listed under Backups); if that backup
can't be made, nothing is changed and sign-in says why. The change runs all at once or not at all. After an update,
Today shows a one-time note with the new version number, and says when a backup was made before it. An older
version never opens a database a newer one has changed: it says so and points here.

Images are signed. To check one before running it:
`cosign verify ghcr.io/lumenaureum/lumenaureum:<version> --certificate-identity-regexp 'https://github.com/lumenaureum/lumenaureum/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Rolling back
1. Set the previous tag in `compose.yaml` and run `docker compose up -d`.
2. If the newer version didn't change the database, that's all.
3. If it did, the older version refuses to open the database and says so. Put the newer tag back and run
   `docker compose up -d` (the older version can't read the list of backups). Then follow **Restoring a backup**
   below, steps 1 to 9. In step 1, pick the line whose kind is `upgrade`: the backup made at the first sign-in on the
   newer version, just before it changed anything. After step 3, set the older tag in `compose.yaml`, so step 8
   starts the older version.

Those steps move only the restored files into your existing data folder. Everything else in it stays, including the
`tls` folder that holds the app's HTTPS certificate, so your devices don't warn about a new certificate. Keep the data
folder's own `deletions.json` and `doc-numbers.json` (step 6), so clients deleted since the upgrade stay deleted and
numbers don't repeat. (A version from before 1.0 reads these as `deletions.enc` and `doc-numbers.enc`, so on those,
check the clients list and the next invoice number.)

Going back to an earlier version is always this restore: each version that changes the database updates it at the
first sign-in (1.0.1 turned refunds recorded the 1.0.0 way into credits; 1.1 added the gallery draft fields; 1.1.1
keeps a copy of each issued invoice and receipt; 1.2 marks the data as using download galleries), and an earlier
version refuses the updated database. Anything
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

This section is the one place with the restore steps; docs/security.md points here. A restore needs the command
line: there is no restore button in the app yet. Settings, Backups has **Check the newest backup opens**, which tests
a backup with your master key without changing anything. Each step below is one command or one file move; type them
as shown, in a terminal on the machine that runs Docker.

**The files.** A restore writes these into a new folder, each in the place shown:

| File | What it is | Where it goes |
|---|---|---|
| `crm.db` | Your data: clients, sessions, money, and settings | Into the data folder |
| `keyring.json` | The key that opens `crm.db` | Into the data folder |
| `storage.json` | Where backups go and how to sign in there | Into the data folder |
| `deletions.json` | The clients you deleted, so they stay deleted | Step 6 decides |
| `doc-numbers.json` | The highest invoice, receipt, session, and client numbers used, so none repeat | Step 6 decides |
| `settings.json` | A copy of your settings, for Settings, Import settings | Leave it; `crm.db` already holds your settings |
| `deletions.unverified.json` | Deletions that could not be confirmed. Never in a backup | Leave the data folder's own copy where it is |

A backup made before you ever deleted a client has no `deletions.json`. That is expected: there was nothing to list.
Backups from before settings were included have only `crm.db` and `keyring.json`.

**Everything (on this machine, or after a loss).** Run each command in the folder that holds your `compose.yaml`.
1. **Find the backup's name.** `docker compose exec lumen python -m vault.cli backups`. It asks for your password
   (or the master key), because the list is kept in the encrypted database. Each line shows the date, the kind, the
   size, and last the name, for example `db/3f9c1e7a5b2d4c6e8a0b1c2d3e4f5a6b.age`. Pick a line whose kind is `db`,
   `manual`, or `upgrade` (`faces` lines hold only face photos) and copy the whole name, `db/` included. Settings,
   Backups (the Vault page) shows the same dates and sizes, so you can match them up there. On a new
   machine there is no database to ask yet: start the app once (`docker compose up -d`) and use the newest file in
   the `db` folder of your vault.
2. **Restore it.** `docker compose exec lumen python -m vault.cli restore db/3f9c...6b.age /data/restored`, with your
   backup's full name. It asks for your master key. On a new machine, add `--from /vault` (the vault folder as the
   app sees it). The files appear in a folder named `restored` inside your data folder.
3. **Stop the app.** `docker compose stop`.
4. **Move three files.** Move `crm.db`, `keyring.json`, and `storage.json` from `restored` into the data folder,
   replacing the ones there.
5. **Leave `settings.json`** in `restored`. You need it only for a settings-only restore (below).
6. **`deletions.json` and `doc-numbers.json`.**
   - The data folder still has its own: keep those. They are newer than any backup.
   - The data folder lost them: move the restored ones in.
   - Restoring an older backup after a loss: restore the newest backup too, into another folder (for example
     `/data/restored-newest`), and take these two files from it.
   - Either way, leave `deletions.unverified.json` where it is, if the data folder has one.

   With these, clients deleted after the backup was made are deleted again, and numbers carry on instead of
   repeating.
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

## If you suspect a compromise
Signs: sign-in attempts you didn't make (you are told at sign-in), devices you don't recognize, settings you didn't
change (every change is in each client's History and in the audit log).
1. **Contain.** Stop the container, or take the machine off the network.
2. **Rotate the data key.** `docker compose run --rm lumen python -m vault.cli rotate` (the app stopped): everything is
   re-encrypted under a new key; old copies of the keyring stop working.
3. **New password and recovery codes; sign out everywhere** (Settings > Security). Re-add two-factor in your
   authenticator app if the phone or its backup may be exposed.
4. **Replace the master key** if it may have been seen: `python -m vault.cli new-master` (asks for the old one).
   Backups made before that still open only with the old key until they age out (the 30 most recent daily backups are kept).
5. **Change the storage credentials** (SFTP key, S3 keys) at your provider, then enter the new ones in setup.
6. **Review** the history for changes you didn't make, and take down any gallery you didn't publish.
7. **Tell people if you have to.** If client information may have been seen by someone else, you may have legal duties
   to tell your clients (and in some places a regulator) within a set time. Check the rules where you work; a
   template is below.

### Template: telling a client
These are examples only, not legal advice. Adapt them for your business and the laws where you work, and have them reviewed.

> Dear [name], on [date] I learned that [what happened]. The information involved may include [what]. It did not
> include [what it did not]. I have [what you did]. You may want to [what they can do]. If you have questions,
> contact me at [contact]. I am sorry this happened.

## A privacy notice you can give clients (edit to fit)
These are examples only, not legal advice. Adapt them for your business and the laws where you work, and have them reviewed.

> **How I keep your information.** I keep your contact details, session details, agreements and photos to do your
> session and deliver your photos. I keep them encrypted on equipment I own. No online service stores them. Your online
> gallery is encrypted; only the link in your letter opens it, and it is removed [N] days after you first open it.
> I keep your original photo files for one year after delivery, then delete them; you can ask me to delete them
> sooner, in writing. I keep your client record for [N] years for my business records, then delete it. You can ask me
> what I hold about you, ask me to correct it, or ask me to delete it. I do not sell or share your information.
> Contact: [your details].

## Who can do what
Lumen Aureum has one owner account. Keep your master key, password manager and recovery codes where someone you trust
could reach them if you can't (an executor, a business partner), with instructions; there is no other way in.
