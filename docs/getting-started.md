# Getting started

The first time Lumen Aureum starts, it opens a short setup. Nothing else works until the required steps are done,
so a fresh install can't be claimed by someone else or used half-configured. Every answer can be changed later in
Settings, but a few are hard to change once real clients exist. The app tags those **hard to change later**; here
they are marked **Decide carefully**.

## What you need

- **A computer you own that is always on and runs Docker**, such as a NAS or home server. You use Lumen Aureum from
  a web browser on any computer in your home or studio. See [where-it-runs.md](where-it-runs.md) for installing it.
- **A phone with an authenticator app**, for two-factor sign-in.
- **Somewhere for backups**: a second disk, a share on another machine, or an SFTP or S3 account. Setup checks it.
- **For online galleries (optional)**: a web hosting plan. Lumen Aureum uploads only encrypted gallery files to it,
  over SFTP, to an S3-compatible bucket, or to a folder a web server on your network serves. Galleries stay online
  when your computer is off. Use an address only for galleries, such as `photos.example.com`, not a folder on your
  main website (see [Security](security.md)). A folder on this machine must also be mounted into the container: see
  [Folders outside the container](where-it-runs.md#folders-outside-the-container). A host that runs PHP also lets galleries expire on time and send the client's picks back
  by themselves. With a folder on your own network, the web server must be able to write in it (run it as the app's
  user, or give it the folder's group with write permission); otherwise first visits and picks aren't recorded and
  galleries stay up a year. In that folder alone, Lumen Aureum makes its gallery files readable by everyone and
  writable by the group (folders 775, files 664), so the web server can serve them; they are all encrypted, and the
  app never follows a link out of the folder. Everything else it writes is readable by its own user only.
- **A printer** for the master key, which you keep on paper.

The software is free.

## The setup steps (in order)
The first three steps are required. The rest are marked optional: **Use the defaults** skips one, and you can come
back to it in Settings.

### 1. Owner account and master key
The **setup code** comes first. It shows you are the person who installed the app, and it stops working once setup is
done. It is in the file `SETUP-CODE.txt` in your `data` folder, which only the app's user can read: use
`sudo cat data/SETUP-CODE.txt`, or find it in the container log with `docker compose logs lumen`. Then your name,
email, password, and two-factor (an authenticator app), then your **master key**, shown once: save it in a password
manager and print it. You also get recovery codes to print.
The first person to finish this step owns the install; the setup page closes to everyone else.

Your client records, settings, backups and uploaded photos are encrypted, and your password is what unlocks them; see
[Security](security.md).
The master key, with a code the app writes to your data folder, lets you set a new password if the password is ever
lost, and it is the only way to open a backup.

**Decide carefully: the master key is unrecoverable.** It is never stored by the app. Lose it and the password, and
no one (you included) can open your data or any backup. Keep two copies in different places.

### 2. Your business
- Your name, the business name that appears on contracts and invoices, and a brand name if it is different.
- Business email, phone and mailing address, printed on your documents. On a gallery host that runs PHP, the client's
  picks come back to the app by themselves. On any other host, the client's picks arrive by email at this address.
- **Time zone**, and your home ZIP code (or latitude and longitude), used for sunset and golden hour times.

**Decide carefully: time zone.** Every due date, shoot time and deadline is stored in it.

### 3. Backups
- **Backups**: a folder on this machine or a mounted share, an SFTP server, or an S3-compatible bucket. This is the
  vault: sealed backups and photo archives go here, encrypted. Use a different disk from the app's data.
- **Second copy, in another place** (optional): it receives a copy once a day.
- **Daily backup time.**
- Each location is tested (write, read, delete) before it is accepted.
- A folder on this machine works only if it is mounted into the container in `compose.yaml`. The vault folder is
  already there (`/vault`). For a second disk, add a line: see
  [Folders outside the container](where-it-runs.md#folders-outside-the-container).

Photos are not set up here: you upload them on each session page (below), and the app stores them encrypted in its
photos folder.

**Decide carefully: the backup location.** Moving it later means copying every sealed file to the new place yourself;
pick storage you expect to keep.

### 4. What you shoot (optional)
Choose the session types you offer. Each starts with a workflow, a length and starter prices; set your own prices.
You get only the types you check. With none checked, you keep the types you already have (from an imported settings
file, say), or start with portraits, families, and group events.
Fee schedules are versioned, so editing prices never changes an existing quote.

### 5. Payments and terms (optional)
Deposit to hold a date, deposit for events (a percent of the price), when the balance is due for each type (before
the session, on delivery, or after an invoice), payment methods, reschedule fee, how long a quote is good for, and
travel and mileage. These answers apply to every session type they cover, weddings included. A type's own deposit
(Settings, Session types) is used only once you change it on that type's page.
Changes apply to new quotes; a quote already sent keeps the terms it was sent with.

### 6. Sales tax (optional)
None, or one rate for every client. Tax is counted when payments are received.

### 7. Documents (optional)
Who owns the photos once paid, for each session type: **Client** (the copyright transfers once the balance is paid)
or **Me (license)** (you keep the copyright and the client gets a license). This applies to new sessions; documents
already signed keep theirs. Then **which law applies**, which fills the "The law of ___ applies" line in the
agreement (for example, Oregon).

The agreement, photo release, delivery document, invoice, receipt and price list are filled in from your answers.
Every client document prints **SAMPLE** (supplied example wording) until you review it: open Settings, Documents,
Review status; read each document's wording on its own page, change what you need, then press **Reviewed - remove
SAMPLE**. Saving is not a review. Once reviewed, a document stays reviewed ("Revert to default" puts the shipped
wording back, marked SAMPLE); a session type given its own wording is reviewed on its own. A settings file never
brings reviews with it.

The agreement, release and other documents that come with Lumen Aureum are examples only. They are not legal advice, and the makers of Lumen Aureum make no promise that they suit your business or hold up where you work. Read them, change them for your business and the laws where you work, and have a lawyer review them before you use them. You are responsible for the documents you give your clients.

Signed documents keep the wording they were signed with.

### 8. Online galleries (optional)
The gallery host (web hosting over SFTP, an S3 bucket with a public web address, or a folder a web server on this
machine serves) and its public web address. A test upload confirms it works. Then how long a gallery stays up after
the client first opens it, and when a gallery nobody opens comes down. One extension can be given from the session
page. A folder on this machine must be mounted into the container first: see
[Folders outside the container](where-it-runs.md#folders-outside-the-container).

**Decide carefully: the gallery address.** Each QR letter you mail contains it. If the address changes, links in
letters already sent stop working. Use a domain you control and intend to keep.

### 9. Who can reach this (optional)
By default, devices on your local network, over HTTPS, using an address or a name listed in `CRM_HOSTNAMES`. Every
device on your network can reach the sign-in page. To narrow it, add `CRM_ALLOW_ONLY` with the addresses that may
connect, as a line under `environment:` in `compose.yaml`. Keep it on your local network; do not expose it to the
internet.

## After a restart
Lumen Aureum starts **locked**: your data stays encrypted until you sign in. Backups, gallery checks and scheduled
deletions wait until then. On a web host that runs PHP with Apache rewrite rules, galleries still expire on time;
on other hosts they come down once you sign in. If the machine restarts on its
own, for example after a system update, sign in once to unlock. To have it unlock itself instead, see "Unattended
unlock" in [Security](security.md).

## After setup
Today shows a short "finish setting up" list: review your documents, set your own prices, add a second backup
location and online galleries if you skipped them, and add your first client. Each line goes away once it is done, and
the list can be hidden. Settings holds every answer from this setup, grouped in tabs down the left.

## Shoot day: your photos
"I have 100 RAWs on a memory card. What now?"
1. Open the session (Today, or search for the client). Under **Photos**, drop the RAW files onto **RAWs** (or press
   choose). Each file shows its progress and reads **Sealed** when done. Leave the page open until they finish; if
   the connection drops, drop the same files again and they pick up where they stopped.
2. Edit as usual on your computer. To edit a file again later, open **Show files** and download it.
3. Drop the finished JPEGs onto **Edited exports**, then **Build gallery** under Gallery. The gallery is made from
   those uploads; nothing is left readable on the NAS.
4. After delivery, the session's photos are sealed into the vault and deleted when their time is up (Photo storage
   on the session page shows when).

The copy on your own computer and on your memory cards is yours to look after: delete it, or keep that computer's
disk encrypted.

## Paperwork: how clients sign
Agreements and releases are signed outside the app. Print the document, or download the PDF and send it by email or
text, then get the signed copy back. Open the document and press **Mark signed** once you have it. A wedding where both
people sign has one agreement per signer, each with its own **Mark signed**. A signed document keeps the wording it was
signed with.

**Email it** on a document opens a draft in your mail app, addressed to the client. It doesn't attach anything:
download the PDF and attach it before you send.

## Galleries: proofing or delivery
Client galleries are for session types with a Gallery step: portraits, seniors, families, weddings, sports,
newborns, engagements, headshots, pets, and group events. Real estate, products, food, and personal projects have no
gallery: deliver those yourself. On the session page, open **Show files** under Photos, download the edited files,
and send them the way you and the client agreed (a file transfer service, say).

A gallery is one of two kinds, set by the proof mark in Settings, General, Client galleries:
- **Proofing gallery (a proof mark on).** The photos carry your mark, and the client picks the ones to retouch. The
  gallery goes online as soon as it is built, without waiting for the balance, and the QR letter says the proofs are
  ready to pick from.
- **Delivery gallery (no proof mark).** The finished photos, released on payment: while a balance is due, the
  gallery waits. To put it online before payment anyway, press **Upload before payment**.

Rebuilding a gallery that is already online updates it at the same link. The old photo files stay on the host until
the app's next gallery check (about 30 minutes later), so a page the client already has open keeps working; a page
left open longer than that asks the client to reload.

## Hard to change later, at a glance
| Decision | Why it is sticky |
|---|---|
| Master key | With the password also lost, the data and every backup are gone |
| Gallery address | Printed in every QR letter already mailed |
| Backup location (the vault) | Moving copies every sealed file |
| Time zone | Every date and deadline is stored in it |
