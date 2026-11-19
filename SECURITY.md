# Security

Lumen Aureum holds photographers' client records, so security reports are taken seriously and handled privately.

## Reporting a problem

Please do not open a public issue for a security problem. Instead, either:

- use **Report a vulnerability** on this repository's Security tab (GitHub private vulnerability reporting), or
- write to contact@lumenaureum.org with "Security" in the subject.

Include what you found, how to reproduce it, and which version you tested. You'll get a reply within 7 days. Once a
fix ships, you'll be credited in the release notes unless you'd rather not be.

## Supported versions

Only the latest release gets security fixes. Fixes go into the next release; older releases are not patched, so
update to the latest release and test against it before reporting.

## In scope

- The app itself: sign-in, the encrypted database, backups, restores, and key rotation.
- Client galleries: the link files on the web host and the viewer that opens them in the browser.
- The photo file format (LAP1) and its key handling, described in [docs/photo-format.md](docs/photo-format.md).
- The sealed message format (LAS1) and link files, described in [docs/sealed-format.md](docs/sealed-format.md).
- The container image published to ghcr.io.

The design, threat model, and known limits are in [docs/security.md](docs/security.md). Problems that need someone
already signed in as the photographer, or with full control of the machine the app runs on, are covered there and
are generally out of scope.
