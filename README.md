<p align="center"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logo-horizontal-dark-bg.svg">
  <img src="brand/logo-horizontal-light-bg.svg" alt="Lumen Aureum" width="360">
</picture></p>

# Lumen Aureum

Free, open-source software that runs the business side of a photography studio, locally, not in the cloud. Take the tour: [lumenaureum.org](https://lumenaureum.org/).

Lumen Aureum keeps track of clients and sessions, builds quotes from your own price list, handles agreements,
scheduling, invoices, and receipts, and delivers finished photos through proofing galleries. Today lists what needs
you: today's shoots, new website inquiries, money past due, and the next step for each client.

Client records are encrypted on disk and open only when you sign in.
Backups, archived sessions and client galleries are encrypted before they are stored or sent anywhere, so a lost
laptop or a stolen drive exposes no client records.

## What it does
- **Today**: today's shoots; the Inbox, website inquiries not yet filed under a client, one line each, opened in
  place to reply, add or mark as spam; and Tasks, one line per session with its next action on it (money past due
  first, new picks and inquiries tagged New). Filter chips with counts narrow Tasks by status, due date, kind of task,
  label or a search.
- **Clients and households**: one page per client, showing what needs you first. People, children and their
  details, referrals, notes, your own labels, and a history of every change. The Clients list is a table for finding
  someone: name, phone, email, where they stand and what they owe, sorted by any column and filtered as you type.
- **Sessions**: each kind of session has its own steps, each with a due date counted from the shoot. The stage changes
  when something happens: you send a quote, press Book it or Shoot done, the client's picks arrive, or the session is
  paid in full.
- **Quotes, agreements, invoices and receipts** from your own prices and terms, as PDFs. Starter prices and wording
  print SAMPLE until you approve them, which you can do right where you use them. A booked quote is read-only;
  Replace the quote starts a new one.
- **Scheduling**: a month calendar with a day view, sunset and golden hour for each session, and a check for clashes.
- **Billing and finances**: deposits, payments, refunds, mileage, expenses, and sales tax counted when payments are
  received; each payment keeps its tax, so a closed year never changes. Last year stays open through January 31 for
  money that arrived in December (or until you close it on Finances). Every amount says what it is for and shows
  cents. A payment can be made any way, a trade included, at the value you received. Tips are their own kind of
  payment, never taxed (a tip given back lowers the tips only). A balance you waive is written off. Finances gives
  your accountant a one-page year summary as a PDF, with the details as CSV files.
- **Proofing and delivery galleries**, encrypted in the browser; the key is in the link printed on the letter you mail. Galleries
  come down after the window you set: by themselves on a web host that runs PHP and keeps gallery files behind the
  gallery's gate (Setup checks), otherwise the next time the app is unlocked. A session type can ask for a codeword the client chooses, so the link alone
  doesn't open the gallery. A gallery already on your web host can be adopted into a session and brought up to date.
  What a link can and can't protect is in [docs/security.md](docs/security.md).
- **Photos**: drop RAWs and edited JPEGs onto the task row they belong to (or Browse for them). They are encrypted as they arrive and
  shown as thumbnails in the app. Galleries are built from them, and after delivery they are sealed into the vault and deleted when
  their time is up. **Choose how to deliver** the finished photos: the QR letter, an emailed link, or in person.

## What it connects to, and what it leaves to you
Lumen Aureum keeps your studio on your own machine, but it isn't cut off:
- **Backups** go to a folder, another machine over SFTP, or S3-compatible storage.
- **Galleries and your studio page** upload to your web host over SFTP, to an S3-compatible bucket, or to a folder a
  web server serves. Clients' picks, inquiries and print orders come back from there.
- **Plugins** add features you can switch on or off. Print orders are the first.
- **Notices** (optional): a short ntfy notice to your phone when the studio restarts and stays locked.

Some things it leaves to you on purpose, for now: it records payments rather than taking cards, and it doesn't send
email or sync with an outside calendar or accounting program. Documents are PDFs you send from your own email, and
Finances gives you a year summary and CSV files for your accountant.

## What you shoot
Setup asks what you shoot: portraits, seniors, families, weddings, sports, newborns, engagements, headshots, pets,
group events, real estate, products and brands, food, or personal projects. Pick any mix. Each choice adds a session type with its steps and starter prices.
You get only the types you pick. Skip it and you start with portraits, families, and group events. Change any of it,
or add your own types, in Settings.

Settings, prices and session types export to a single file (Settings, Export settings), and setup can start from one,
so you can move to a new machine or start over without entering them again.

## Privacy and security
- Client records, settings and backups are encrypted; signing in unlocks them, and they are locked again after every
  restart until you sign in again. The app signs out after a time with no activity (8 hours unless you
  change it). Photos are uploaded into the app and encrypted as they arrive; they are never stored readable.
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
64-bit processor (Intel or AMD, or 64-bit ARM such as a Raspberry Pi 4 or 5 or an ARM NAS) and about 1 GB of memory.
Docker Desktop on macOS and Windows is not supported: it hides where each connection comes from, so Lumen Aureum
refuses them. See [docs/where-it-runs.md](docs/where-it-runs.md).

**On a TrueNAS:** install it from the app catalog (Apps, Discover, Lumen Aureum). Make three datasets for it first,
so your data stays if the app is ever removed: see
[The easiest way: the TrueNAS app catalog](docs/where-it-runs.md#the-easiest-way-the-truenas-app-catalog).

**Anywhere else, with Docker Compose** (64-bit Intel, AMD or ARM):

```
mkdir lumenaureum && cd lumenaureum
curl -O https://raw.githubusercontent.com/lumenaureum/lumenaureum/main/compose.yaml
echo "LUMENAUREUM_HOST_IP=192.168.1.10" > .env      # the address your computers reach this machine at
# edit compose.yaml: point the /vault line at a folder on a different disk from ./data
mkdir data photos /path/to/another/disk/lumenaureum-vault
sudo chown 10001:10001 data photos /path/to/another/disk/lumenaureum-vault   # or set "user" in compose.yaml to their owner
docker compose up -d
docker compose logs lumenaureum          # shows the certificate fingerprint and the setup code
```

The app sees only the folders mounted under `volumes:` in compose.yaml. A second backup disk, or a folder a web server
on this machine serves for galleries, needs a line there too: see
[Folders outside the container](docs/where-it-runs.md#folders-outside-the-container).

Then open `https://<that machine's address>:8780` from a device on your network. Your browser will warn that it
doesn't know the certificate: check that the fingerprint it shows matches the one in the log, then accept it. Setup
asks for the setup code (in the container log, and in the file `SETUP-CODE.txt` in your `data` folder, which only
the app's user can read: `sudo cat data/SETUP-CODE.txt`), then your account, two-factor, your master key, your business details, where
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

## Help
Ask questions and share ideas in [Discussions](https://github.com/lumenaureum/lumenaureum/discussions). Report bugs in
[Issues](https://github.com/lumenaureum/lumenaureum/issues). Report security problems privately to
contact@lumenaureum.org, not in public.

## Contributing
Issues and ideas are welcome. Code contributions are not accepted. This repository gets one commit per release; the changes in each
release are listed in [CHANGELOG.md](CHANGELOG.md).

## If the project stops
Lumen Aureum runs on your own machine and needs nothing from this project to keep working: it doesn't check in with
any server of ours, and the version you have keeps running as long as your machine does. Your data doesn't depend on
the app either. Backups and the keyring are standard `age` files that the stock `age` tool opens with your master
key, and the database is SQLCipher. The photo, gallery and sealed-item formats are written down in
[docs](docs/photo-format.md), with test vectors, so another program can read them. The license (AGPL) lets anyone
take the code and carry it on.
