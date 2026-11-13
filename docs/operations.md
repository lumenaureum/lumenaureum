# Running it: upgrades, rollback, and if something goes wrong

## Upgrading
1. Read what changed in the new version on its release page.
2. Make sure a recent backup exists (Settings > Backups shows the last one) and that you have your master key.
3. In `compose.yaml`, change the image tag to the new version (from the old version number to the new one).
4. `docker compose pull && docker compose up -d`, then sign in to unlock.

The database records which version of its layout it has. When a new version needs to change it, the change runs at
the first sign-in, after the app has made an encrypted **upgrade** backup (listed under Backups); if that backup
can't be made, nothing is changed and sign-in says why. The change runs all at once or not at all. An older version
never opens a database a newer one has changed: it says so and points here.

Images are signed. To check one before running it:
`cosign verify ghcr.io/lumenaureum/lumenaureum:<version> --certificate-identity-regexp 'https://github.com/lumenaureum/lumenaureum/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Rolling back
Set the previous tag in `compose.yaml` and `docker compose up -d`. If the newer version changed the database, the
older one refuses to open it: restore the **upgrade** backup made just before the upgrade (below) into a fresh data
folder and point `./data` at it. Copy `deletions.json` and `doc-numbers.json` from the current data folder into the
new one, so clients deleted since the upgrade stay deleted and numbers don't repeat. (A version from before 1.0 reads
these as `deletions.enc` and `doc-numbers.enc`, so on those, check the clients list and the next invoice number.)
A rollback cannot
undo a leak: if you rolled back because of a security problem, also follow "If you suspect a compromise".

## Restoring a backup
Each nightly backup holds your data (the database, with clients, money and settings), the key that opens it, your
settings as a separate file, and your storage settings (where backups go and how to sign in there).
`python -m vault.cli backups` lists them. Keep a copy of your `compose.yaml`. Uploaded photos are not in the
nightly backup (they are large). Instead each one is mirrored, still encrypted, to your second backup location (or the
vault when there is none): one copy, kept while the photo is kept and deleted with it, so photos take twice their
size. Their keys are in the database, so after restoring the database the app fetches any photo missing from the
photos folder back from the mirror.

**Everything (after a loss).** `docker compose exec lumen python -m vault.cli restore <backup name> /data/restored`
(or, on a new machine with a copy of the vault folder, add `--from <folder>`). It asks for your master key and writes
`crm.db`, `keyring.json`, `settings.json`, `storage.json`, `deletions.json` and `doc-numbers.json` into
`restored` inside your data folder, so you can see them from the NAS too. Then:
1. Stop the app (`docker compose stop`).
2. Move `crm.db`, `keyring.json` and `storage.json` from `restored` into the data folder itself, replacing the ones
   there.
3. **`deletions.json` and `doc-numbers.json`:** if the data folder still has its own, keep those (they are newer than
   any backup). If it doesn't, use the restored ones. With them, clients deleted after the backup was made are
   deleted again, and invoice and receipt numbers carry on instead of repeating. Restoring an older backup after a
   loss? Restore the newest backup too, into another folder, and take these two files from it.
4. `restore` writes the files as the app's own user, so moving them is enough. Only if you copied files in some other
   way: give them the same owner as the rest of the data folder (see it with `ls -ln <data folder>`; 568 on TrueNAS,
   10001 with the example compose file), for example `sudo chown 568:568 <data folder>/*.db <data folder>/*.json`.
5. Start it (`docker compose up -d`) and sign in with the password that was current when the backup was made (or
   reset it with the master key). Delete the `restored` folder afterward.
- **If you rotated the data key after this backup was made**, the photos' keys in the restored database are older
  than the photos. Run `python -m vault.cli relink-photos` (it asks for your master key): each photo's current key,
  sealed beside it to your master key, goes back into the database.

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
