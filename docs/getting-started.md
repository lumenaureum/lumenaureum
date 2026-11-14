# Getting started

The first time Lumen Aureum starts, it opens a short setup. Nothing else works until the required steps are done,
so a fresh install can't be claimed by someone else or used half-configured. Every answer can be changed later in
Settings, but a few are hard to change once real clients exist. Those are marked **Decide carefully**.

## What you need

- **A computer you own that is always on and runs Docker**, such as a NAS or home server. You use Lumen Aureum from
  a web browser on any computer in your home or studio. See [where-it-runs.md](where-it-runs.md) for installing it.
- **A phone with an authenticator app**, for two-factor sign-in.
- **Somewhere for backups**: a second disk, a share on another machine, or an SFTP or S3 account. Setup checks it.
- **For online galleries (optional)**: a web hosting plan. Lumen Aureum uploads only encrypted gallery files to it,
  over SFTP, to an S3-compatible bucket, or to a folder a web server on your network serves. Galleries stay online
  when your computer is off. A host that runs PHP also lets galleries expire on time and send the client's picks back
  by themselves. With a folder on your own network, the web server must be able to write in it (run it as the app's
  user, or give it the folder's group with write permission); otherwise first visits and picks aren't recorded and
  galleries stay up a year. In that folder alone, Lumen Aureum makes its gallery files readable by everyone and
  writable by the group (folders 775, files 664), so the web server can serve them; they are all encrypted, and the
  app never follows a link out of the folder. Everything else it writes is readable by its own user only.
- **A printer** for the master key, which you keep on paper.

The software is free.

## Required steps (in order)

### 1. Owner account and master key
The **setup code**, from the file `SETUP-CODE.txt` in your `data` folder or the container log (it shows you are the
person who installed it; it stops working once setup is done), then name, email, password, and
two-factor (an authenticator app), then your **master key**, shown once: save it in a
password manager and print it. You also get recovery codes to print.
The first person to finish this step owns the install; the setup page closes to everyone else.

Your client records, settings, backups and uploaded photos are encrypted, and your password is what unlocks them; see
[Security](security.md).
The master key, with a code the app writes to your data folder, lets you set a new password if the password is ever
lost, and it is the only way to open a backup.

**Decide carefully: the master key is unrecoverable.** It is never stored by the app. Lose it and the password, and
no one (you included) can open your data or any backup. Keep two copies in different places.

### 2. Your business
- Photographer name, and the business name that appears on contracts and invoices.
- Mailing address and business email (clients' gallery picks are emailed here).
- Home base ZIP code (travel and mileage are measured from it) and **time zone**.

**Decide carefully: time zone.** Every due date, shoot time and deadline is stored in it.

### 3. Where your data lives
- **The vault (primary storage)**: a folder on this machine or a NAS share, an SFTP server, or an S3-compatible
  bucket. Sealed backups and photo archives go here, encrypted.
- **Photos**: you upload them on each session page (below); the app stores them encrypted in its photos folder.
- **Daily backup time**, and optionally a **second location** that receives a copy once a day.
- Each location is tested (write, read, delete) before it is accepted.

**Decide carefully: the vault location.** Moving it later means copying every sealed file to the new place yourself;
pick storage you expect to keep.

### 4. Payments and terms
Deposit (amount, and percent for events), when the balance is due (at delivery, or net days for business clients),
payment methods, reschedule fee, how long a quote is good for.
Changes apply to new quotes; a quote already sent keeps the terms it was sent with.

### 5. Sales tax
None, or one rate for every client. Tax is counted when
payments are received.

### 6. What you shoot
Choose the session types you offer. Each starts with a workflow, a length and starter prices; set your own prices.
Fee schedules are versioned, so editing prices never changes an existing quote.

### 7. Documents
Starter agreement, photo release, delivery document (copyright transfer or photo license), invoice, receipt and price
list, filled in from your answers. Every client document prints **SAMPLE** (supplied example wording) until you review it: open Settings,
Documents, Review status; read each document's wording on its own page, change what you need, then press **Reviewed -
remove SAMPLE**. Saving is not a review. Once reviewed, a document stays reviewed ("Revert to default" puts the shipped wording back, marked SAMPLE); a
session type given its own wording is reviewed on its own. A settings file never brings reviews with it.
- **Which law applies** fills the "The law of ___ applies" line in the agreement (for example, Pennsylvania).

The agreement, release and other documents that come with Lumen Aureum are examples only. They are not legal advice, and the makers of Lumen Aureum make no promise that they suit your business or hold up where you work. Read them, change them for your business and the laws where you work, and have a lawyer review them before you use them. You are responsible for the documents you give your clients.

- For each session type, choose the delivery document: **copyright transfer** (for example, the client owns the
  photos once paid) or **license** (for example, you keep the copyright).
- Signed documents keep the wording they were signed with.

### 8. Client galleries
The gallery host (SFTP web space, an S3 bucket with a web address, or a local web folder) and its public address.
A test upload confirms it works. Gallery window (days after first visit, one extension).

**Decide carefully: the gallery address.** Each QR letter you mail contains it. If the address changes, links in
letters already sent stop working. Use a domain you control and intend to keep.

### 9. Who can reach the app
By default, devices on your local network, over HTTPS, using an address or a name listed in `CRM_HOSTNAMES`. Every
device on your network can reach the sign-in page. To narrow it, set `CRM_ALLOW_ONLY` to the addresses that may
connect. Keep it on your local network; do not expose it to the internet.

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

## Hard to change later, at a glance
| Decision | Why it is sticky |
|---|---|
| Master key | With the password also lost, the data and every backup are gone |
| Gallery address | Printed in every QR letter already mailed |
| Vault location | Moving copies every sealed file |
| Time zone | Every date and deadline is stored in it |
| Copyright model per type | Applies to new sessions; signed documents keep theirs |
