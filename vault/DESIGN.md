# Encrypted storage (the vault)

Goal: client data at rest is ciphertext wherever it lands. The live database, face photos and storage settings are
encrypted under the data key (see `datakey.py` and docs/security.md); this document covers what leaves the data
folder: it is ciphertext wherever it lands (local disk / NAS, an SFTP
host, an S3-compatible bucket). A compromise of the storage does not expose what is sealed in it. While the owner is signed in, the live
database is open in the app's memory; the sealed session archives stay closed to the app itself.

## Principles
1. Write-only. The app holds only the master PUBLIC key: it can seal, never open, sealed data.
2. Only ciphertext under random names leaves the database. No client name, date or type appears in an object name.
3. Deletion = key destruction (crypto-shred). Copies on versioned buckets, snapshots or forgotten disks become unreadable.
4. Standard format. Everything is `age` (X25519, ChaCha20-Poly1305, authenticated), so the stock `age` tool can
   recover it without this code.
5. Backends never see keys; every backend passes the same contract tests.

## Keys
- **Master key pair**, one per installation, created during owner setup. The private key is shown once for the owner
  to store (password manager and paper); the app never writes it anywhere.
- **Session key pair**, one per session, created when the session is first sealed. The archive is encrypted to the
  session public key; the session private key is stored only encrypted to the master (`vault_keys`).
- Shredding a session deletes its `vault_keys` row (and its objects). Database backups older than the shred still
  hold that wrapped key for as long as they are kept (the 30 most recent daily backups), readable only with the master key.

## What gets sealed
| Data | When | Encrypted to | Object name |
|---|---|---|---|
| Daily database backup: encrypted database + keyring, as one tar (+ face photos, already encrypted) | at the time set in Settings > Vault | master | `db/<random>.age` |
| Pre-reset snapshot | on reset | master | `db/<random>.age` |
| Session RAWs + exports | 7 days after the SD card ships, or "Seal now" | session key | `s/<random>.age` |
The backup's database is still SQLCipher inside the seal; its keyring holds the data key wrapped to the master key, so
the master key alone restores it.

## Backends (one contract: put, get, list, stat, delete, verified delete)
- `local`  - a folder that carries a `.vault-root` marker (an unmounted share is refused, never written into).
- `sftp`   - key auth, host key pinned from known_hosts (needs `requirements-sftp.txt`).
- `s3`     - any S3-compatible endpoint, bucket-scoped key, versioning off (needs `requirements-s3.txt`).
Config: `data/storage.json` (mode 0600): `primary`, optional `secondary` (copied once a day, never mirrored live),
`gallery_host`. Credentials live only there.

## Flows
- **Seal session**: tar (stream) -> age -> primary -> size check -> record -> remove the plaintext.
- **Open for a reprint**: master key typed in -> session key -> every archive of the session unpacked to a folder that
  removes itself after 7 days.
- **Restore**: `python -m vault.cli restore <name> <new-folder>` with the master key: crm.db + keyring.json, opened and
  integrity-checked before it is accepted.
- **Retention / early deletion / session delete**: delete objects, then shred the session key.

## Commands
`python -m vault.cli init-store <dir>` (mark a local folder), `backups`, `backup`, `restore`, `new-master` (needs the
current master key; backups made before it still need the old key, so keep that until they age out), and `rotate`
(stop the app first; an interrupted rotation blocks start until the command is run again).
