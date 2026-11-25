# Getting started

The first time Lumen Aureum starts, it opens a short setup. Nothing else works until the required steps are done,
so a fresh install can't be claimed by someone else or used half-configured. Every answer can be changed later in
Settings, but a few are hard to change once real clients exist. The app tags those **hard to change later**; here
they are marked **Decide carefully**.

## What you need

- **A computer you own that is always on:** a TrueNAS (install it from the app catalog), or any NAS or home server
  that runs Docker on a 64-bit Intel, AMD, or ARM processor. You use Lumen Aureum from a web browser on any computer in your home or studio. See
  [where-it-runs.md](where-it-runs.md) for installing it.
- **A phone with an authenticator app**, for two-factor sign-in.
- **Somewhere for backups**: a second disk, a share on another machine, or an SFTP or S3 account. Setup checks it.
- **For online galleries (optional)**: a web hosting plan. Lumen Aureum uploads only encrypted gallery files to it,
  over SFTP, to an S3-compatible bucket, or to a folder a web server on your network serves. Galleries stay online
  when your computer is off. Give galleries a folder of their own on your web host, such as `/g/` (Setup fills that in from
  the host you type, and warns if galleries are set at the top of a website); an address of
  their own (`photos.example.com`, say) works too, but isn't needed (see [Security](security.md)). A folder on this machine must also be mounted into the container: see
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
`sudo cat data/SETUP-CODE.txt`, or find it in the container log with `docker compose logs lumenaureum` (from the
TrueNAS app catalog: Apps, lumenaureum, Logs, or `SETUP-CODE.txt` in the data dataset). Then your name,
email, password, and two-factor (an authenticator app), then your **master key**, shown once: save it in a password
manager and print it. You also get recovery codes to print.
The first person to finish this step owns the install; the setup page closes to everyone else.

Your client records, settings, backups, and uploaded photos are encrypted, and your password is what unlocks them; see
[Security](security.md).
The master key, with a code the app writes to your data folder, lets you set a new password if the password is ever
lost, and it is the only way to open a backup.

**Decide carefully: the master key is unrecoverable.** It is never stored by the app. Lose it and the password, and
no one (you included) can open your data or any backup. Keep two copies in different places.

A week after setup, and every 90 days after that, Today asks you to check you still have it: paste it into the page
it links to (also under Settings, Security) and the app tells you whether it is the master key this install uses.
The key is checked in memory and forgotten; only the date of the check is kept. "Not now" asks again in a week.

### 2. Your business
- Your name, the business name that appears on contracts and invoices, and a brand name if it is different.
- Business email, phone, and mailing address, printed on your documents. Your website, if you add one in Settings,
  is linked from your client galleries. On a web host that runs PHP, the client's
  picks come back to the app by themselves. On any other host, the client's picks arrive by email at this address: paste
  the email on the client page with "Paste their picks email" (on the Gallery block) and the app records them.
- **Time zone**, and your home ZIP code (or latitude and longitude), used for sunset and golden hour times. A session whose
  place names a US state and ZIP (Moab, UT 84532) gets that place's times, in its local time; any other shoot says
  the times are for home.

**Decide carefully: time zone.** Every due date, shoot time, and deadline is stored in it.

### 3. Backups
- **Backups**: a folder on this machine or a mounted share (the usual choice), a drive you plug in, your NAS, cloud
  storage (Backblaze B2, Cloudflare R2, Wasabi, Amazon S3, or another S3-compatible service), or another server you
  sign in to over SFTP. This is the vault: sealed backups and photo archives go here, encrypted. Use a different disk
  from the app's data.
- **Second copy, in another place** (optional): it receives a copy once a day. Plug in a drive, choose your NAS, or
  pick cloud storage.
- Choosing a provider by name fills in what is certain about it and says, in two lines, where its control panel shows
  the rest. Port, sign-in key file, region, folder prefix, and bucket addressing wait under **Advanced**; the app tries
  both ways of addressing a bucket itself and keeps the one your storage answers.
- **Daily backup time.**
- **Test and continue** tests each location (write, read, delete) before it is accepted; nothing is saved unless it
  works. It is the one test: there is no separate test button.
- An SFTP server the app hasn't connected to before isn't tested yet: the step shows the server's key fingerprint
  and a **Trust this server** box. Check the fingerprint matches the one your host gives, tick the box, and press Test
  and continue again. From then on only that key is accepted. If the server's key ever changes, the step says so and
  shows the new fingerprint the same way; trust it only if your host confirms the change.
- If a test fails, everything you typed stays in the form. A secret key you typed is kept by the app for the next try
  (the field says "kept from your last try"); it is never shown back.
- A folder on this machine works only if it is mounted into the container. The vault folder is already there
  (`/vault`), and the step fills it in for you when it is mounted. For a second disk, add a line: see
  [Folders outside the container](where-it-runs.md#folders-outside-the-container).

Photos are not set up here: you upload them on each client's page (below), and the app stores them encrypted in its
photos folder.

**Decide carefully: the backup location.** Moving it later means copying every sealed file to the new place yourself;
pick storage you expect to keep.

### 4. What you shoot (optional)
Choose the session types you offer. Each starts with a workflow, a length, and starter prices; set your own prices.
You get only the types you check. With none checked, you keep the types you already have (from an imported settings
file, say), or start with portraits, families, and group events.
The starter prices print **SAMPLE** until you approve them, the same as the starter wording (step 7). Each approved
price list is named by the day it takes effect ("Prices as of Nov 18, 2026"), and editing prices never changes a quote
already made.

### 5. Payments and terms (optional)
Deposit to hold a date, deposit for events (a percent of the price), when the balance is due for each type (before
the session, on delivery, or after an invoice), payment methods, reschedule fee, how long a quote is good for, and
travel and mileage. The payment terms name the business checks are made out to (your business name from step 2).
These answers apply to every session type they cover, weddings included. A type's own deposit
(Settings, What you shoot) is used only once you change it on that type's page. Once setup is done, this page
also lets you call the deposit something else, such as retainer or booking fee: quotes, agreements, invoices, and
your pages then use your word.
A type's page can also split the balance after the deposit into installments: equal payments, one a month on the
booking's day of the month, the last on the day the balance is due (spread evenly when there are fewer months than payments).
The dates are kept once the session is booked: moving the shoot date later does not move them unless you set them again
on the plan. A reschedule fee, if you add one, goes on the last payment. A session billed on another session's quote
(an engagement on the wedding's) has its fee added to that quote.
A plan has at most 4 payments; a plan set before that limit keeps its schedule. The agreement, the quote, and the invoice
list the dated amounts under the balance. One session can have its own plan: **Pay in installments** on its Billing.
A plan changed on a type's page or in its walkthrough reaches only sessions whose quote is not sent yet; the others keep the plan they had.
Changes apply to new quotes; a quote already sent keeps the terms it was sent with.
The app counts an amount as overdue only once its own due date under these terms has passed: the deposit's,
each installment's, or the balance's. After the shoot and before the balance is due, the balance reads "due".
An issued invoice keeps the due date printed on it, even after the terms change.
A late installment shows on Today, with Remind them, before the shoot too.

### 6. Taxes (optional)
No taxes, or a list of them. Each tax has a name as your invoices show it (Sales tax, VAT, GST), a rate, and how it is
charged: **Added on top of the price** (its own line on the quote and invoice) or **Included in the price** (no line;
the quote and invoice say "Includes" with its name and amount). **Applies to every session** is ticked to start with;
untick it for a tax that applies only to the sessions you switch it on for. Tax is counted when payments are received.
With print orders on (Settings, Plugins) and at least one tax: whether to charge tax on prints and on shipping (your
decision with your accountant). After setup this page is Settings, Taxes.

- **Per fee.** The price list (Settings, Prices) has a tick for each fee, one column per tax when you have several:
  untick a tax a fee does not carry.
- **Per session.** On an open quote, **Discount, portfolio, tax** on Billing lists each tax with a switch, and
  **Tax exempt, certificate on file** for that session only.
- **Per client.** **Tax exempt** in the client's Details, with an optional exemption certificate number: their quotes
  carry no tax on every session not yet booked.
- Once the client says yes, the session keeps each tax as it was: its name, rate, and whether it applied. Changing a
  rate, or a client's exemption, later changes open quotes only. Saving Settings, Taxes updates every open quote at
  once, and History notes each quote whose total changed: send it again if the client already has it.

### 7. Documents (optional)
Who owns the photos once paid, for each session type: **Client** (the copyright transfers once the balance is paid)
or **Me (license)** (you keep the copyright and the client gets a license). This applies to new sessions; documents
already signed keep theirs. Then **which law applies**, which fills the "The law of ___ applies" line in the
agreement (for example, Oregon).

The agreement, photo release, delivery document, invoice, receipt, and price list are filled in from your answers.
Every client document prints **SAMPLE** (supplied example wording) until you review it, and you can do that where
you are about to use it. A SAMPLE document opened from a client's page has **Approve this wording** and **Edit the
wording**: the editor opens in the same window, and after Save you carry on sending from there. A quote on starter
prices has **Approve these prices**. Approving, or saving an edit, counts as your review: a warning says so first,
and the document is then final and stops printing SAMPLE. To review everything at once, open Settings, Documents,
Review status, read each document, change what you need, then press **Reviewed - remove SAMPLE**. Saving there is
not a review. Once reviewed, a document stays reviewed ("Revert to default" puts the shipped
wording back, marked SAMPLE); a session type given its own wording is reviewed on its own. A settings file never
brings reviews with it.

The agreement, release, and other documents that come with Lumen Aureum are examples only. They are not legal advice, and the makers of Lumen Aureum make no promise that they suit your business or hold up where you work. Read them, change them for your business and the laws where you work, and have a lawyer review them before you use them. You are responsible for the documents you give your clients.

Signed documents keep the wording they were signed with.

### 8. Online galleries (optional)
**Where are your galleries hosted?** DreamHost, SiteGround, or Bluehost (what is certain about each is filled in,
and two lines say where its control panel shows the rest), a web server at home (a folder a web server on this
machine serves), or something else: another web host's login (SFTP), or cloud storage with a public web address.
Then **Where your galleries live online**: the folder's public web address. The port and a mounted key file wait
under **Advanced**. **Test and continue** uploads a small test file to confirm it works;
for an SFTP host the app hasn't connected to before, it first shows the server's key fingerprint and a **Trust this
server** box, as on the Backups step. An SFTP host signs in with the app's own key, or with a password if the host
has no way to add a key (Online galleries then offers **Switch to the app's own key**). Then how long a gallery stays up after
the client first opens it, and when a gallery nobody opens comes down. One extension can be given from the gallery's
closing line on the client page. In its last week (or the last quarter of a shorter window), a gallery on a host that runs PHP opens with a notice, on every
visit, that it is about to sunset: the date it closes and your email (and Download all, for a download gallery). Once a gallery comes down, its link says the gallery has gone dark
and gives your email, since you may still have the photos. That page stays a year, then goes too. To put a gallery back
online, use **Put back online** on its closing line: for a number of days, or (a download gallery on a host that runs PHP) for one download, after
which it goes dark again. Only Download all counts as that download; saving single photos doesn't. The client's sent
picks come back online with it. A fixed last day ends at midnight in your time zone (Business and time zone), on the
host as in the app. The email is the
one in Settings, so set it before galleries go online. A folder on this machine must be mounted into the container first: see
[Folders outside the container](where-it-runs.md#folders-outside-the-container).

After saving, the step shows **What this host can do**: whether galleries there close on their last day by
themselves, whether clients' picks come back by themselves, whether the studio page's inquiry form can work there, and
whether clients can order prints from download galleries (see [Print orders](print-orders.md)). They all need a host that runs PHP 7.4 or later; a plain bucket gives none of them
(see [Choosing a web host](where-it-runs.md#choosing-a-web-host)). It also says whether galleries with a codeword
can go there (not on a host that hands out gallery files without asking for the key), and whether a proxy or CDN sits
in front of it. **Check again** asks the host once more; the app also asks once a day, and Today says if anything
changed for the worse.

If the web address has a certificate that isn't from a public authority (a self-signed one, as a web server on your
home network often has), the step then shows **The web host's certificate**: its SHA-256 fingerprint, who it was
issued to and by, and when it ends. Check the fingerprint matches the one your web server shows, then press **Trust
this certificate**. The app then accepts only that certificate's key at that address (a renewal with the same key
keeps working); the certificate must name the address you typed. If the key ever changes, the app stops and
asks you here again (see [The web host's certificate](where-it-runs.md#the-web-hosts-certificate)).

**Decide carefully: the gallery address.** Each QR letter you mail contains it. If the address changes, links in
letters already sent stop working. Use a domain you control and intend to keep.

Moving to another host later is covered in [If your web host goes away](#if-your-web-host-goes-away), and prints
in [Print orders](#print-orders).

### 9. Who can reach this (optional)
By default, devices on your local network, over HTTPS, using an address or a name listed in `CRM_HOSTNAMES`. Every
device on your network can reach the sign-in page. To narrow it, set `CRM_ALLOW_ONLY` to the addresses that may
connect, then restart the app: with `compose.yaml`, as a line under `environment:`; on a TrueNAS app, in the app's
Edit screen, under Additional Environment Variables. The step shows the address your request came from; devices
behind a router or VPN can share one address, so it may be the router's rather than your device's. Keep it on your
local network; do not expose it to the internet.

## After a restart
Lumen Aureum always starts **locked**: your data stays encrypted until you sign in. Backups, gallery checks, and
scheduled deletions wait until then. On a web host that runs PHP and keeps gallery files behind the gallery's gate,
galleries still expire on time; on other hosts they come down once you sign in. If the machine restarts on its own,
for example after a system update, sign in once to unlock. To hear about it when you are away, turn on the
locked-studio notice (see [Notices while locked](security.md#notices-while-locked)).

## After setup
Today ends with a **Setting up** section: review your prices, check you still have your master key, walk through your
session types, add a second backup location and online galleries if you skipped them, and add your first client. Each
line goes away once it is done. **Dismiss** hides the section for good; anything in it that later becomes urgent
shows on Today as its own line. Settings holds every answer from this setup, grouped in tabs down the left, with a
search box at the top that finds a setting by its name or a common word ("tax", "mileage", "checks") and opens it.

## Today
Today lists what needs you, in this order: **Shooting today**; the **Inbox**; the filter chips; **Tasks**; and
**Setting up** last.

The **Inbox** holds website inquiries not yet filed under a client, one line each like an email list: who, how it came,
the kind of session, when, and the first words of the message, bold while new. Tap a line to open it in place: the
full message, their contact details, the preferred date, then **Reply**, **Add as a client** (or **Add to** a client
whose email or phone it matches), **Add to an existing client...** (a quick search, for a client writing from a new
address) and **Spam**. Filed under an existing client, the message stays theirs: it shows who really sent it, how and
when, and History says "Inquiry from Riley Carter, filed under Grant Family". When the sender isn't a person on that
client yet, **Add Riley Carter to this client as a contact** (ticked) keeps their email and phone. Filed under the
wrong client? On that client's page, under Details and history, **Move to another client** or **Make it a new client**
(the sender becomes a client of their own) takes the session, the message, and the contact added at filing with it,
with a line in History on both clients. It can move while it is an Inquiry or Quoted with nothing issued and no
payment recorded; otherwise the page says why not. The count beside Today in the menu bar is the number of unread
Inbox items.

**Tasks** is one line per session with its next action on it: money past due first, then the rest of the work, then,
folded, what waits on the client and the next two weeks. A new inquiry already filed under a client, and picks that
just arrived ("Retouch 3 photos"), show here tagged **New** until you open that client's page.

A task's due date sits right after its title ("Cull · Due Mon Nov 16"), red once it has passed ("Was due"), the
same on Today and the client page. A fold says how much it holds in its title, such as "Next two weeks (8)".

The filter chips sit above Tasks and narrow only Tasks: **All**, **Mine**, **Waiting** (on the client), **Due today**
and **Due in 7 days** (today included), each with its count, the kinds of work with theirs, and your client labels,
plus a search box. They work in place and never change the counts in the header or the menu bar. The Sessions list
has the same filters.

## From first message to closed
Each client has one page. The top says who they are and where things stand. The first block is what needs you now,
with each action on the thing it acts on (Reply on their message, Record payment on the balance), and a row's other
actions in its **More** menu. A row names the step ("Agreement") and its button the action ("Send the agreement"). **Details and history**, folded at the bottom, holds the details forms, the less used
actions, and History. A (?) beside a word opens a short explanation of it. A session moves through Inquiry, Quoted, Booked, Shot, Delivered, and Closed as the work happens:

- **Inquiry.** Their message reads like an email: who, how it came, the date and time, the preferred date, then their
  words. The actions are **Reply**, **Build a quote** and **Do not book**; **Book without a quote** is under Details and history.
  **Send the intake questions** opens a draft in your mail app with the session type's questions; when they answer,
  **Fill in their answers**.
- **Quoted.** Sending the quote moves the session to Quoted. Five days later with no reply logged, Today offers
  **Follow up**. When they say yes, press **Book it**. **Do not book** closes the inquiry and asks why: They
  went elsewhere, Not a fit for me, No answer, or Other (with a few words). The reason is kept in History, and it can
  be undone. On a booked session, **Cancel the booking** asks for a reason the same way, and, when a deposit was paid,
  whether to keep it (the Terms) or hold it as a credit toward a future session. The credit shows on that client's
  next session, with **Use $100.00 of the credit**: a payment there and money moved out of the canceled session, both
  dated the day it is used. Until it is used it counts as the kept deposit.
- **Booked.** The booked quote is read-only, its discount included. **Replace the quote** (under Details and history) cancels it,
  keeps it in History and starts a new one: a deposit already paid counts toward the new quote, a signed agreement
  stays on file and the new quote needs a fresh signature, and the session stays Booked with "New quote waiting for
  their OK" at the top until you press Book it again. A step the client does waits on them only after you send it;
  until then the row is yours: **Send the agreement**, **Send the deposit invoice**. **Send the session sheet** gives
  the client the when, where, and what to bring. A missing date, start time, or place is its own row, with **Set date, time,
  and place**; later, tap the date at the top to change it. The session's length comes from the quote.
- **Rescheduled.** A booked date that moves counts as a reschedule. The type's free reschedule asks nothing. After
  that, Billing asks once per reschedule: **Add the $50.00 fee** (a line on the balance, taxed like any other, never discounted) or **Waive it**.
- **Business clients** (a company name, or a business session type) get a PO number for the invoice and license terms
  with the quote. **Business client** on Edit client details changes it either way.
- **Shoot day.** Once the start time has passed, **Shoot done** moves the session to Shot. If something from before the
  shoot is still open, such as an unsigned agreement, the app lists what is open and asks before moving. **Skip to**
  the next stage (under Details and history) asks the same way.
- **Shot.** Anything left from before the shoot comes first, then Upload original photos, Cull, Upload edited photos, Build gallery, the
  invoice, the balance, and the delivery. An unpaid deposit folds into the balance, but a proofing gallery still waits
  for it (below).
- **Delivered** when the client's picks arrive. **Closed** by itself once every step is done and nothing is owed
  either way: the balance paid or waived, retouched photos sent, files delivered. **Reopen** brings it back.

Doing a later step settles the earlier ones it covers, and a step whose fact is already recorded closes on its own.

## Shoot day: your photos
"I have 100 RAWs on a memory card. What now?"
1. On the client's page, drop the RAW files onto the **Upload original photos** row, or press **Browse RAW files**. The row
   says how many files were added, and each file reads **Sealed** when done. Leave the page open until they finish; if the connection drops, drop the same files again
   and they pick up where they stopped.
2. With a second backup location set up, Upload original photos closes by itself once every RAW is on both locations. With
   only one, the row says **Only one copy so far**, with **Add a backup location**. **I backed it up another way**,
   under More, closes it after a warning that a copy made outside the app isn't encrypted by it.
3. Cull and edit on your computer. The **Photos** section on the client's page is one line of what is in (such as
   "RAWs 812 files, 18.4 GB, sealed"), then the latest upload as up to 12 thumbnails, newest first (RAWs by their own
   preview, JPEG, PNG, and GIF files as themselves, anything else as a file icon with its name); tap one to see it large,
   fitted to the window, with previous and next. **+ N more from this upload** opens the rest one at a time.
   **Find a file** shows only the files whose name matches what you type. The x on a thumbnail deletes that file in
   place, after asking. Tick the box on any thumbnails for **Delete the N selected**; **Delete this upload** deletes the
   whole latest upload, and **Delete the N files that match** every file Find a file matched. Each asks first, and
   History gets one line for the lot. Thumbnails stay inside the app; to browse the whole set the client's way, preview the gallery.
4. Drop the finished JPEGs onto the **Upload edited photos** row (this also closes Cull and Upload edited photos), then **Build gallery** in the
   Gallery block. Nothing is left readable on the NAS.
5. After delivery, the photos are sealed into the vault and deleted when their time is up (Photo storage shows when).

The copy on your own computer and on your memory cards is yours to look after: delete it, or keep that computer's
disk encrypted.

## Paperwork: how clients sign
Agreements and releases are signed outside the app. **Send the agreement** opens it: print it, or download the PDF and
send it by email or text, then press **Mark sent**. When the signed copy comes back, press **Mark signed**. A wedding
where both people sign has one agreement per signer, each with its own **Mark signed**. On Today, **Mark signed**
signs the one document still waiting; when several people still have to sign, it opens the session's paperwork
instead. A signed document keeps the wording it was signed with.

**Email it** on a document opens a draft in your mail app, addressed to the client, with the document's key facts: an
invoice's number, total, what it is for, what is paid so far, and how to pay; a receipt's number and total; a quote's
total and the day it is good through. A mail link can't attach a file, and the draft never says anything is attached:
download the PDF and attach it, if you like. **Text it** sends the same first line.

Names written in Chinese, Japanese, or Korean print on every PDF. The font that draws them (Droid Sans Fallback) has
one weight, so where a document prints a name in bold, those characters print at regular weight.

## Galleries: proofing or delivery
Client galleries are for session types with a Gallery step: portraits, seniors, families, weddings, sports,
newborns, engagements, headshots, pets, and group events. They are also for any session delivered by **Download
link** (the default for real estate, products, and food; choose it per session under **Files delivered by**). Without
either, deliver the photos yourself (**Find a file** under Photos finds any one of them).

A gallery is one of three kinds. The proof mark in Settings, Branding, Client galleries chooses between the first two,
and delivery by Download link makes the third:
- **Proofing gallery (a proof mark on).** The photos carry your mark, and the client picks the ones to retouch. The
  gallery goes online once the deposit is in, without waiting for the balance. Until then the Gallery block says
  "Collect the $100.00 deposit first", with **Upload before the deposit**.
- **Delivery gallery (no proof mark).** The finished photos, released on payment. To put it online before payment
  anyway, press **Upload before payment**.
- **Download gallery (delivery by Download link).** The final photos at full size, for the client to keep. Each photo
  has a Download button, and **Download all** saves every photo in one zip (several zips of about 500 MB each for a
  large gallery). No picking and no proof mark. Released on payment, like a delivery gallery. The client's first open
  ticks **Files delivered**, and the gallery's days count from then. Each full-size file names your studio as its
  creator, and carries your copyright notice when you keep the copyright. Nothing else from your files goes along:
  no location, no camera details.

The Gallery block lists what is true about the gallery, each line with its actions: the photos ("The Sample Family can
download 24 photos · made Mon Nov 16", **Preview the gallery**, and **Add them** when you uploaded more since); the
link (**Copy link**, **Email link**, **Replace this link**); the client's activity and when the app last looked
(**Look for client activity now**); and the closing date in its phase ("Online until Wed Dec 16 · 28 days left",
"Closing Wed Dec 16 · in 3 days · the client sees a warning", "Closed Wed Dec 16, off the web"), with **Extend** or
**Put back online**. **Offer prints from this gallery** is there too, when print orders are on.

A session delivered by Download link builds its download gallery straight away when the type has no picking round
(real estate, products, food). A type with picks builds a proofing gallery first. When the picks come in, **Retouch 3
photos** heads Tasks, with each pick and the client's note on the row. Drop the retouched photos on that row,
then press **Build download gallery** (or **Deliver without picks** if the client never sends any). The download
gallery takes the proofs' place at the same link when you press **Upload**, after payment. It holds every photo twice
(one by one and in the zip), so it needs about twice as much space on the web host as the photos themselves.

The delivery row, **Get the photos to** and the client's name, has one button, **Choose how to deliver**, which opens
the ways in place, the type's usual one first: **Mail the QR letter** (print it, then **Mark mailed**), **Email the
link** (after a warning that an emailed link is only as safe as their inbox), or **Handed over in person** on a USB
drive or SD card, with the day and a note. Whichever you use closes the step, and History records how. Until the
gallery is online, the letter and the email can't be chosen and say why: the letter's QR code is the gallery's link.
**Print a draft letter**, under the letter's More, prints one marked DRAFT for checking the layout; it can't be
recorded as mailed.

Rebuilding a gallery that is already online updates it at the same link. The old photo files are removed from the
host as soon as the new build is up. A page the client already has open notices the new build and asks them to
reload.

**A gallery already online.** A gallery made by Lumen Aureum that this install has no record of (made on another
install, say) can be adopted: on the client's page, under Details and history, **Adopt a gallery already online**, then paste its
full link. The app adopts it only if its folder is on your own web host, the app can write there with its own
credentials, and the link's key opens it; otherwise it says why. Nothing on the host changes. An adopted gallery is a
download gallery with prints off and a closing date like any other. **Bring this gallery up to date** rebuilds it at
the same link with the current gallery page, from the finals uploaded to the session or, without them, from the
gallery's own photos. After that, each new version updates it like any other gallery.

## If your web host goes away
When your web host closes, or you move: set up the new host in Settings, Online galleries, then open **Move
galleries** from Today. One button puts every gallery there, built from the copies on this computer, with its days,
the client's picks and the same link. Point your gallery address at the new host and every link keeps working. If the
address has to change, the same page lists the clients who need the new link. When the earlier host can still be
reached, Move first takes the client's latest picks from it, and its copies stop taking picks (they say the gallery
has moved and give your email). Until you take the old copies down from the same page, each check still looks there
for picks sent anyway (an earlier host that can't be reached is tried once a day).

Every earlier host is kept, each with what may still be online there, until its copies are down: changing hosts twice
before moving keeps both. Turning galleries off in Online galleries moves nothing and takes nothing down; turning them on
again with the same host carries on where things were. A folder on this machine (or a mounted share) is the same host
wherever it is mounted: the app keeps its own id in the folder's marker file, so after a mount path changes, or a backup
of it is restored into another folder, type the new path in Online galleries and nothing needs moving. If that folder is a copy and the original is still
there, Setup says so. Old gallery folders on the web host in use (any kind) that no gallery owns any more (left by an earlier
move) are looked for once after the update and listed on Move galleries, to remove or keep; nothing is removed unless you ask. Deleting a session or client takes its gallery down from every
host that holds a copy; one that can't be reached then is listed on Move galleries (and on Today) and comes down at a
later check, or with Take the old copies down. If an earlier host is gone for good (the account closed), use **This
host is gone for good: forget it** there: History names any gallery that may still be online on it. Changing your
time zone in Settings moves the closing instant of every gallery online at the next check.

## Print orders
Print orders are off until you turn them on in **Settings, Plugins**. Then clients can order prints from their
download gallery on a host that runs PHP: in Settings, Print prices, check the sample price list and your shipping,
press Save, then turn prints on. To sell prints without giving the files away, a proofing gallery can offer prints
from its proofs (**Offer prints from these proofs** in its Gallery block): the full-size files stay on your computer.
Turning print orders off later keeps everything recorded (and print money stays in Finances). See
[Print orders](print-orders.md).

## Equipment and Crew (optional)
Two more parts are off until you turn them on in **Settings, Plugins**. Turning one off again hides it and keeps
everything recorded.

- **Equipment and packing lists.** Equipment, in the top bar, lists what your studio owns (serial numbers, prices,
  receipts). A session type's page gets a **Packing list**; booked sessions of that type show it in The shoot, to tick
  off on the shoot day.
- **Crew and call sheets.** A booked session shows a **Crew** fold in The shoot. **Add someone new to the crew** (a
  name, and if you like a role, phone, email, and call time) puts them on the session and on **Your crew list**; anyone
  on the list is added with **Add to this session**. **Pay** with their first name adds what you paid them as an
  ordinary expense on that session, and nothing more: there is no payroll. **Show the call sheet** opens one page for
  the crew, to print or download as a PDF: the date and start, the places, each person's call time, role, and contact,
  a wedding's timeline, and your **Notes for the crew**. The client's phone and email are on it only when you tick
  **Put the client's phone and email on the call sheet** for that session. The app never sends the call sheet: you
  print it or share the PDF. The crew list is kept in the encrypted database and its backups, not in the settings file.

## Adding a plugin
Lumen Aureum publishes some plugins on their own. To add one, open **Settings, Plugins** and go to **Add a plugin**:
paste the link Lumen Aureum gives and press **Add from the link**, or choose the plugin file you downloaded and press
**Add from the file** (nothing is downloaded then). Both ask for your current password and a two-factor code. Only
plugins signed by Lumen Aureum are accepted: anything else is refused, and nothing changes.

The studio then restarts to load the plugin, so sign in again. The plugin stays off until you turn it on in the list,
and none of it runs while it is off. Turning it on or off restarts the studio again, so sign in once more.
It is kept in your studio's database, so your backups carry it and a restore brings it back. It never updates itself:
to get a newer version, add it again the same way. To remove one, open **Remove** under it in **Added plugins**; what
it recorded stays in your records.

## Getting paid
Every amount says what it is for and shows cents. Nothing is owed until you send the invoice: until then the row is
"Invoice ($100.00 balance, Headshots session)" with **Send the invoice**, and Finances counts it as To invoice. After that
it reads "The Sample Family owes $100.00 · invoice 2026-014 sent Mon Nov 16", with **Record payment ($100.00)**. When
your terms release the photos on payment, the delivery row reads "Collect the $100.00 balance first", with the same
**Record payment ($100.00)**.

**Record payment** offers the payment methods you set, and **Other (say what)** for a trade or anything else, at the
value you received. **Waive the balance**, under More on the balance, writes it off with a reason. Finances counts it as a
write-off, and the gallery is released. If the client pays after all, record it as usual and the write-off is reversed
by as much. When a canceled booking has money to give back, its row
reads like this: "The Sample Family canceled. Refund owed: $10.00. Deposit of $100.00 is retained." **Record refund
issued** is on the same row.

In Finances, **Year summary for your accountant** makes a one-page PDF: your business details, the year, the basis
(cash: money received), income by kind, refunds, sales tax collected, expenses, miles driven, and balances waived.
**Details (CSV)** gives the CSV files.

### Closing a year
The money year is the calendar year. Last year stays open through January 31, so a check that arrived in late December
can still be dated in December. In January, Finances says "2026 is open through January 31." with **Close 2026** beside
it, for closing it sooner once everything is in (it asks first, and can't be undone). On February 1 the year closes on
its own. From January 25, Today reminds you to enter last year's money first.

Nothing more can be dated in a closed year, and its payments and expenses can't be voided, restored, or removed, so its
figures stay as they are. Instead:
- **Correct**, on the payment's or expense's row, asks what the amount should have been and why, and records the
  difference as a new entry in the open year, naming the one it corrects. Finances lists these under **Corrections to
  closed years**, and the CSV files have a "correction of" column.
- **Add a late expense**, on the closed year's Finances, is for an expense found after the year closed: it is recorded
  today, in the open year, marked as from the closed one.

## Clients and the calendar
Clients is a table for finding someone: **Name**, **Phone**, **Email**, **Where they stand** (such as "Booked · shoot
Sun Apr 18"), **Owes** (invoiced and not paid yet, in red once overdue) and **To invoice** (a balance whose invoice
hasn't gone out yet). Tap a column's header to sort by it, and again to reverse. One filter box above it narrows
the table as you type, across every column; digits match phone numbers whatever their punctuation. The email and
phone are links that start a message or a call; tap anywhere else on a row to open the client. On a phone each row
shows the name and where they stand.

Labels, **Check in** (a short mail draft to a past client) and **End contact** are on the client's page. Labels are
your words for a client; they are part of what a client gets if they ask for their data, and never show on a
document, a gallery, or an email.

On the month calendar, tap a day to see its shoots and steps, under the month on a phone and beside it
on a wider screen. A crowded day shows the first few and
"+ 3 more". A lead's date shows lighter, as "Penciled in" or, when you have not penciled it, "Not penciled"; the year
view and its free Saturdays count only bookings and penciled dates.

### Your sessions on your phone's calendar (optional)
Settings, Calendar link, **Turn on the calendar link** makes a private link your phone's calendar app can subscribe
to (on an iPhone, Add Subscribed Calendar, among the calendar accounts in the phone's Settings; on Android, an app
that subscribes to calendar links, such as ICSx5). **Subscribe on this device** does it in one tap where the phone offers it. Each
session shows as its type, day, start time, and length, and nothing else: never a name, a place, or a note. A
penciled date shows as tentative. It is off until you turn it on.

The phone reaches the link only where it can reach the studio itself: on your home network, or a private network you
set up for it (Tailscale, for example). Away from both, the phone keeps what it last had and catches up when it is
back. Calendar services that fetch links from their own servers, such as Google Calendar on the web, can't reach it.
The phone must trust the studio's certificate (a certificate for a name you own, or the studio's own, installed on the
phone). After a restart the link answers again once you sign in. **Make a new link** stops the old one at once (if it
got out, or a phone was lost); **Turn off the calendar link** stops it altogether. See
[security.md](security.md#the-calendar-link).

## Your studio page (optional)
A small public website for your studio, posted to your web host in the folder above your galleries: a few photos
you pick, an About page, how to reach you, and an inquiry form. Off until you switch it on in Settings, Studio page.
See [studio-page.md](studio-page.md).

## Hard to change later, at a glance
| Decision | Why it is sticky |
|---|---|
| Master key | With the password also lost, the data and every backup are gone |
| Gallery address | Printed in every QR letter already mailed |
| Backup location (the vault) | Moving copies every sealed file |
| Time zone | Every date and deadline is stored in it |
