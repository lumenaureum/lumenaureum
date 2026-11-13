"""Encrypted storage for Lumen Aureum (design: vault/DESIGN.md).

Write-only home: the CRM holds only the master PUBLIC key, so it can seal but never open sealed data. Every object is
`age`-encrypted before it leaves the process and lands under a random name on a pluggable backend (local path / NAS,
SFTP, S3-compatible). Sessions get their own key pair so a session can be crypto-shredded by destroying its key."""
