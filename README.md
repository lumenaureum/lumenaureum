<p align="center"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logo-horizontal-dark-bg.svg">
  <img src="brand/logo-horizontal-light-bg.svg" alt="Lumen Aureum" width="360">
</picture></p>

# Lumen Aureum

Free, open-source software that runs the business side of a photography studio, locally, not in the cloud. Take the tour: [lumenaureum.org](https://lumenaureum.org/).

Lumen Aureum keeps track of clients and sessions, builds quotes from your own price list, handles agreements,
scheduling, invoices, and receipts, and delivers finished photos through proofing galleries. A daily view shows
what each client needs next.

Client records are encrypted on disk and open only when you sign in.
Backups, archived sessions and client galleries are encrypted before they are stored or sent anywhere, so a lost
laptop or a stolen drive exposes no client records.

## What it does
- **Clients and households**: people, children and their details, referrals, notes, and a history of every change.
- **Sessions**: each kind of session has its own steps, each with a due date.
- **Quotes, agreements, invoices and receipts** from your own prices and terms, as PDFs. Price changes never alter a
  quote already sent.
- **Scheduling**: a month calendar, sunset and golden hour for each session, and a check for clashes.
- **Billing and finances**: deposits, payments, refunds, mileage, expenses, and sales tax counted when payments are received.
- **Proofing galleries**, encrypted in the browser; the key is in the link printed on the letter you mail. Galleries
  come down after the window you set: by themselves on a web host that runs PHP with Apache rewrite rules, otherwise
  the next time the app is unlocked.
- **Photos**: drop RAWs and edited JPEGs onto the session page; they are encrypted as they arrive, galleries are
  built from them, and after delivery they are sealed into the vault and deleted when their time is up.

## What you shoot
Setup asks what you shoot: portraits, seniors, families, weddings, sports, newborns, engagements, headshots, pets,
group events, real estate, products and brands, or food. Pick any mix. Each choice adds a session type with its steps and starter prices.
Skip it and you get a small neutral set. Change any of it, or add your own types, in Settings.

Settings, prices and session types export to a single file (Settings, Export settings), and setup can start from one,
so you can move to a new machine or start over without entering them again.

## Privacy and security
- Client records, settings and backups are encrypted; signing in unlocks them, and they are locked again after every
  restart. Photos are uploaded into the app and encrypted as they arrive; they are never stored readable.
- It speaks HTTPS from the first start, with its own certificate; you confirm its fingerprint once.
- Sign-in uses a password and a code from an authenticator app. Only someone who can read its data folder or its
  container log (the person who installed it) can set up the owner account.
- A master key, printed once and never stored by the app, recovers everything.
- Daily backups go to an encrypted vault on your NAS, an SFTP server or S3-compatible storage, with an optional
  second copy.
- By default it answers devices on your local network and refuses the rest, and only under the names you give it.
  Every device on your network can reach the sign-in page, so a strong password matters. Keep it on your local
  network; do not expose it to the internet.

The details, including what is not encrypted (the copy of your photos on your own computer), are in [docs/security.md](docs/security.md).
Upgrades, rollback, restoring, and what to do if you suspect a compromise: [docs/operations.md](docs/operations.md).

**No warranty.** Lumen Aureum is free software provided as is (see the license). You are responsible for your
clients' information: keep it updated, keep your master key safe, and follow the privacy rules where you work.
It has not had an independent security audit.

## Running it
Lumen Aureum runs in a container on an always-on machine: a NAS or home server with Docker, or a Linux desktop. It needs a
64-bit Intel or AMD processor and about 1 GB of memory. See [docs/where-it-runs.md](docs/where-it-runs.md).

```
mkdir lumen && cd lumen
curl -O https://raw.githubusercontent.com/lumenaureum/lumenaureum/main/compose.yaml
echo "LUMEN_HOST_IP=192.168.1.10" > .env      # the address your computers reach this machine at
# edit compose.yaml: point the /vault line at a folder on a different disk from ./data
mkdir data photos /path/to/another/disk/lumen-vault
sudo chown 10001:10001 data photos /path/to/another/disk/lumen-vault   # or set "user" in compose.yaml to their owner
docker compose up -d
docker compose logs lumen          # shows the certificate fingerprint
```

Then open `https://<that machine's address>:8780` from a device on your network. Your browser will warn that it
doesn't know the certificate: check that the fingerprint it shows matches the one in the log, then accept it. Setup
asks for the setup code (in the file `SETUP-CODE.txt` in your `data` folder, and in the container log), then your account, two-factor, your master key, your business details, where
backups go, your terms and prices. If you reach it by a name (for example `nas.local`) rather than an address, add
that name to `CRM_HOSTNAMES` in compose.yaml.
[docs/getting-started.md](docs/getting-started.md) walks through it and marks the choices that are hard to change later.

## Example documents
The agreement, release and other documents that come with Lumen Aureum are examples only. They are not legal advice, and the makers of Lumen Aureum make no promise that they suit your business or hold up where you work. Read them, change them for your business and the laws where you work, and have a lawyer review them before you use them. You are responsible for the documents you give your clients.

## License
Copyright (C) 2026 Walk Up Web LLC.

Lumen Aureum is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE](LICENSE). Third-party components are listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Contributing
Issues and ideas are welcome. Code contributions are not accepted.
