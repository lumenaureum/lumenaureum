# Changes

New features, behavior changes, security fixes, and other fixes in each release.

## 1.6.1 (2026-11-24)

### Changed
- On a desktop, sections that don't fit in the top bar move into More.
- Rebuild all shows which gallery it is on.
- A tax change updates quotes not yet accepted.

### Fixed
- Restore can't pick the wrong backup.
- Client import keeps notes and reads Google and Outlook files.
- Taxes are no longer charged twice, and tax-exempt clients pay none on prints.
- Group events never bill twice.
- Payment plans no longer mark earlier bookings overdue.
- Late installments show on Today.
- Print orders keep their files after a rebuild.
- Wedding statements bill the couple.
- Older galleries no longer mark evening uploads as new (UTC vs local time).
- The calendar no longer shows inquiries as shoots.
- A gallery being rebuilt stays online, and one taken down early shows as closed.
- Layout fixes on phone and desktop.

### Security
- Changing the time zone can't shorten the 48-hour master key wait.
- Removing the phone notice server keeps the master key notice on.

## 1.6.0 (2026-11-23)

### New
- Restore a backup in the app, face photos included, even on a new install.
- Import clients from a spreadsheet, with a preview first.
- Studio-wide History, with search and filters.
- Taxes by name: several taxes, on top or included, per fee and session; exempt clients by certificate.
- Payment plans: up to 4 monthly payments, dated on the agreement, quote and invoice; add or waive the reschedule fee; a canceled deposit kept or turned into credit; call the deposit your own word.
- Group events: bill the organizer once; record several families paid at once.
- Sell prints from a proofing gallery; the full-size files stay with you.
- Deliver sealed photos on an SD card or USB drive.
- Your inquiry form, or a Book a session button in your studio's colors, on a website made elsewhere (Squarespace, WordPress and the like).
- Pencil in a lead's date, and subscribe your phone's calendar to your sessions.
- Replace a lost master key in Settings, Security, after a 48-hour wait any device can cancel.
- Optional plugins: Equipment with packing lists, and Crew with call sheets.

### Changed
- Web hosts and backups: pick your provider by name; web host SFTP can use a password; an S3 host asks about gallery codewords.
- Years close after the next January 31; later corrections go in the open year.
- Overdue follows your payment terms.
- Weddings: both partners get drafts, are billed and sign by default.
- After a studio look change, rebuild earlier galleries in place, links kept.
- Some settings pages show Unsaved changes with Discard; switches save on tap, with Undo.
- Faster pages for large studios; one-step photo delete; quicker small uploads.
- Sessions with nothing to pay say "No charge" and leave money terms out of the agreement.
- Payment reminders by email or text; more optional phone notices.
- Changing who pays for a group event leaves who owns the photos as it was.
- Releases match what each session type licenses; proofs are not the delivered photos; a privacy line under the inquiry form.
- Demo data includes a wedding couple with an engagement on the same agreement.

### Fixed
- Galleries: several fixes after a move or reset; cleaning old copies off your web host runs in the background.
- Couples and families keep the right signer and Bill to; golden hour uses the shoot's place.
- Chinese, Japanese and Korean names print correctly on every PDF.
- Photos: several upload and sealing fixes.
- Agreements: minor status follows the shoot date.
- Money and group events: several display fixes.

### Security
- A lost master key's replacement signs out every device; sign-ins say so for 30 days.
- Restore says when it could not check that a backup came from your app.
- Sign-in, gallery pages, setup, settings files and error messages: several protections tightened (details in the security guide).
- A gallery link can save picks from a limited number of devices.
- The app's image carries fewer system libraries.

### Before you update
- Plugin authors: a plugin using the regional tax hook must move to tax rules.
- Phone notices: the new sign-in change notice starts on (Settings, Notifications).
- New phone notice titles: update any filters (list in the security guide).
- Behind a reverse proxy? It must replace forwarded headers (operations guide).
- Custom curl health check? Use `python /app/healthcheck.py` (operations guide).
- To go back to 1.5.4, restore the backup the app makes at your first 1.6.0 sign-in (Rolling back).

## 1.5.4 (2026-11-21)

### New
- A Galleries page with every gallery in one place.
- Optional phone notices for new inquiries, picks, print orders, and failed backups.
- Search by label in filter boxes, such as label:personal.

### Changed
- Reply by email or text, and note how you replied.
- Free sessions no longer mention deposits or fees.
- Gallery codewords: strong codewords are offered to choose from.

### Fixed
- Several small fixes to quotes, discounts, and wording.

## 1.5.3 (2026-11-20)

### New
- A bell in the top bar lists anything Lumen Aureum itself needs fixed, such as an untested backup or a session type not set up yet.
- Reuse another session type's answer with one tap.

### Changed
- Today shows client work only; setup reminders moved to the bell.
- A session type's walk-through now comes first and covers every setting.
- Turning off the gallery codeword for a type asks first, and a warning stays while it is off.

## 1.5.2 (2026-11-19)

### New
- Close a gallery now, or set the day it closes. Clients see the closing notice before it.
- Bring an adopted gallery's photos home as the session's own edited photos.
- Remind them: a reminder email for an unpaid deposit or balance, from the client page and Finances.
- Client labels can be renamed and merged.
- One search box for everything, always in the top bar, on phones too.
- Session types: reuse an answer you gave for another type with one tap.
- The session type walk-through shows every step on one screen and ends with approving the type's document.

### Changed
- Backup destinations, the web host and the notification server show as locked cards with Test now and Replace.
- Backups list each copy with a check for every destination.
- New quotes and price lists are numbered by date, such as 2026112001.
- On a draft invoice, the email, download and print buttons say they issue it, and ask first.
- Galleries and print orders show your brand name, with your business name on agreements and invoices.
- Gallery uploads show their progress, file by file.
- Finances marks overdue balances and says whether each is a deposit or a balance.
- Notifications can use a plain http:// server on your own network.
- Phone layouts: fewer short lines stacked where one line fits.
- A session type's steps turn off and on in place, and can be reset to the defaults.
- The walk-through shows what the client reads as you type, and has Next, Back and Accept all defaults.

### Fixed
- On two pages, field tooltips were always shown, covering their fields.
- Recording a deposit opened with the whole balance as the amount.
- Sessions moved on by the 1.5 update showed "since today".
- Today could say a group event's families were done while steps were still open.
- On a phone, the background job notice could cover a Save button.

### Before you update
- Steps that a session needs, such as the agreement or deposit, now ask before you turn them off; the gallery step stays on.
- Home base is no longer used: your home ZIP code sets sunset times and the free travel distance.

## 1.5.1 (2026-11-19)

### New
- Each session type has its own icon on its sessions, its settings page and in session lists.

### Changed
- Settings: the client gallery look has its own page.
- Mileage rate is now set on Payments and terms.

## 1.5.0 (2026-11-18)

### Changed
- User experience and workflow improvements: Today, the client page, quotes and booking, shoot day, photos and
  galleries, delivery, payments, clients, calendar, and settings.

### New
- Optional ntfy notices for basic studio status.

### Security
- Signs you out after a time with no activity.
- Account activity lists sign-ins and other security events.
- A ntfy notice tells you when the studio restarts and stays locked.

### Before you update
- The update applies new stage rules to existing sessions once and notes each change in its History.
- A session the update moves to Delivered or Closed gets no photo deletion date from that move. You can set one on
  its page.
- Prices already used on a sent quote count as approved.
- The unattended unlock option is removed. After a restart, the studio stays locked until you sign in.
- Earlier versions can't open your data once 1.5 has updated it. The update makes a backup first.

## 1.4.1 (2026-11-17)

### New
- Replace a gallery link that was lost or shared: the old link stops working and the client gets a new one.
- The app warns you if your web host changes the gallery page it published.
- Back up to S3 storage on your own network, including over plain http:// with a warning.

### Changed
- Gallery codewords are on by default for every session type.
- On TrueNAS, the certificate chosen in the app's settings is used for HTTPS.
- Sample documents say they may be binding once signed.

### Security
- Self-signed S3 certificates are pinned; a changed certificate stops backups until you confirm it.
- PDFs never load anything from the internet.

### Fixed
- Receipts mention a license only when a signed agreement is on file.
- Phone layout fixes: Billing, Finances, session cards, the timeline.

### Before you update
- Codewords turn on for session types set up earlier too; turn them off per type in Settings if you prefer.
- If you copied the sample compose.yaml for 1.4.0, it still named image 1.3.1; set the image to 1.4.1.

## 1.4.0 (2026-11-17)

### New
- Print orders, optional and off until you turn them on: clients order prints from their download gallery and choose the crop.
- You review each print order, invoice whoever ordered, then download lab-ready files and an order sheet for any lab.
- Settings, Print prices: your sizes and prices, optional lab costs and markup, shipping, and tax on prints.
- Orders from someone who isn't your client wait for you to bill them as a guest or add them as a client.
- Today and Finances show print orders and print money, kept apart from sessions.
- Settings, Plugins: every plugin, with an on/off switch for the optional ones.
- Setup checks your web host and shows what works there; Today tells you if that changes.
- A gallery online updates itself when a new version changes the gallery page.

### Changed
- Every gallery gets a new address; old links lead to the new one.
- Galleries and the studio page's inquiry form need a web host with PHP 7.4 or later.
- Setup, Settings, Today and the guides all say "web host".
- A print order invoice not paid in 30 days expires; reopen the order if they pay later.
- Every refund asks whether the gallery should stay held until paid again.
- Email drafts the app writes give the document's key facts in the email and no longer say a file is attached.
- A client page left open from before the update needs a reload before it can send picks.

### Security
- Picks, drafts and website inquiries are sealed in one new format, each with its own ID.
- The web host's certificate is checked every time the app reaches the host.
- Setup checks that a gallery's files can't be opened without its key.
- Published images carry a signed record of how they were built and what they contain.
- Changing your password or recovery codes, or signing out other devices, also stops sign-ins waiting for two-factor.
- The app never writes its memory to disk if it crashes.

### Fixed
- Setup: failed tests keep what you typed, and SFTP and S3 connection messages are clearer.
- Refunds now show correctly on invoices, receipts and canceled bookings.
- Changing the time zone no longer pauses automatic deletions.

### Before you update
- The first sign-in after updating makes a backup, then moves every gallery to its new address. Today shows progress.
- Galleries on an unreachable host, or one with an untrusted certificate, wait until you fix the host or confirm the certificate.
- Going back to 1.3.1 needs the backup made before the update.
- If you keep your own `compose.yaml`, add `ulimits:` with `core: 0` under the service.
- A codeword gallery on a host that serves files without the key is easier to guess; Setup and Today say so.

## 1.3.1 (2026-11-16)

### Behavior changes

- When a gallery's days end, the copy an earlier move left on your old gallery host comes down too.
- What a failed upload leaves on a gallery host is listed on Move galleries and cleaned up.
- Payments and expenses can't be dated before 2000, and expenses can't be dated in the future.
- Miles must be above 0, and an expense needs an amount or miles.
- A quantity that isn't a number is refused instead of becoming 1.
- When a money entry is refused, the form keeps what you typed.

### Security

- Wrong codeword tries on a gallery are refused at once and counted: 10 in an hour from one address, then that
  address waits an hour. Before, each wrong try held the web host busy for a second, so a flood of them could slow or
  stop the host. A gallery gets this when it is next uploaded.
- Settings > Security shows how strong your password's protection is on this machine, and how to strengthen it on a
  faster computer (`python -m vault.cli rewrap-password`).
- The docs explain why Forgot your password skips two-factor.

### Fixed

- After moving galleries away and back, an expired gallery's old photos could stay on the host under its dark page.
- An upload approved after its gallery was taken down could show as failed instead of removed.
- A gallery whose upload failed partway could drop out of Move galleries.
- An unreachable gallery host is now remembered by every part of the app, so it is tried only once a day.
- Studio page: switched off with a settings problem, it now still shows the problem, client photos to remove, and Remove it anyway.
- Studio page: the preview's "not found" page is styled at any folder depth.
- Your own font's light file is now used for light text on the studio page and in galleries, even when the regular file is a variable font.
- Studio page: after an inquiry is sent, the Send button no longer reads "Sending...".
- Canceling a booking with money paid wrote three statement lines instead of one.
- Deleting a session could leave a 0.00 row on Finances.
- Odd statement months, such as 2026-1, were accepted.
- A password set on a much faster computer, which this one can't check, now says so at sign-in instead of "Wrong
  email or password".

### Upgrading

- Updating from 1.2: payments marked "tip" by hand now count as 1.2 counted them, and show as Other.

## 1.3.0 (2026-11-15)

### Features

- Galleries warn clients in their last week, then show a "gone dark" page with your email.
- Put back online: reopen a closed gallery for a few days, or for one download.
- Move galleries: change gallery hosts with one button. Links and picks carry over.
- Studio page: a small public website in your galleries' look, with a private inquiry form.
- Your own font for galleries and the studio page.
- Finances: export a year for your accountant.
- Tips, recorded apart from the price.
- Today reminds you to check your master key.

### Behavior changes

- Past years and past statements never change, whatever you edit later.
- A canceled or lost booking keeps its deposit; the rest is refund due.
- Waived writes off the balance and releases delivery.
- Prices stay editable after booking.
- Gallery closing times follow your time zone.

### Fixed

- Deleting a session with its gallery online could lose photos.
- Settings could fail with "database is locked".
- A double click could record a payment twice.
- Statements showed false credits.
- Plus many smaller fixes.

### Security

- Many hardening fixes, including limits on wrong master keys.

### Upgrading

- 1.2 can't open your data once 1.3 has updated it. The update makes a backup first.

## 1.2.0 (2026-11-15)

### Features

- Runs on 64-bit ARM, such as a Raspberry Pi 4 or 5 or an ARM NAS, in addition to Intel and AMD.
- Download galleries for sessions delivered by Download link: full-size photos, Download, and Download all.
- Download all shows the time left and can be canceled. Large galleries download in parts.
- Retouched finals: after proofs, the download gallery is built from them.
- Deliver without picks, for a client who never sends picks.
- Add your website in Settings. Galleries link to it.
- Email the gallery link from the session.

### Behavior changes

- The delivery method can be chosen from the first inquiry on. Changes are noted in History.
- A client's first open of a download gallery counts as Files delivered.
- Agreements for Download link sessions say how long the link stays open.
- Galleries use the session's title when it has one.
- Delivered files name your studio as the creator, and carry your copyright notice when you keep the copyright.
- An open gallery page notices a rebuild and asks the client to reload.

### Upgrading

- 1.1.1 can't open your data once 1.2 has updated it. The update makes a backup first.
- Rebuild a gallery to get the new gallery page.

## 1.1.1 (2026-11-14)

### Features

- Today says once when the app was updated, and that a backup was made first.

### Behavior changes

- Issued invoices and receipts keep their exact wording, even after an update.
- With a proof mark on, a gallery can go online before the balance is paid.
- Setup's deposit answers apply to every type they cover, weddings included.
- Setup adds only the session types you pick.
- The deposit is due the day the agreement is signed, as the agreement says.
- The QR letter says "your family" only to family clients.
- Updating a gallery that's online shows its progress, and clients who have it open aren't cut off.

### Security

- An expired gallery is always removed, whatever the client's picks file says.
- Settings import checks logos the same way as a logo upload.
- The gallery host refuses picks after the gallery closes.
- A gallery shows Sent only when the host holds the picks.
- The security guide explains the gallery host's checks, and which need Apache.

### Fixed

- Each signer of an agreement can be marked signed separately.
- Picks sent from a gallery made before 1.1 showed as not sent after a rebuild.
- An invoice for a paid session showed as sent.
- Issuing an invoice or receipt showed code instead of a message.
- Photo drop boxes stopped working after an upload.
- The upgrade backup showed its time in UTC.
- Today showed a finished session's shoot as next.

### Upgrading

- Upgrading takes an encrypted backup first. To go back to 1.1.0, restore that backup; 1.1.0 won't open a 1.1.1 database.
- Invoices and receipts issued before 1.1.1 keep the wording they show on the first sign-in after the update.
- Galleries online before 1.1.1 get these fixes when you rebuild them.
- If you serve galleries from a folder on this machine, mount it in compose.yaml. The docs show how.

## 1.1.0 (2026-11-14)

### Features

- A menu across the top of every page: Today, Clients, Sessions, Calendar, and Finances.
- Choose the gallery font and background color in Settings.
- Add a logo for dark backgrounds.
- Add a proof mark to gallery photos.
- Copy a client's gallery link from the session page.
- Client galleries have quick filters for retouch, suggested, and don't show.
- Clients can remove a pick from the grid.
- Clients can pick on one device and finish on another.
- The session shows when a client is still picking.
- Galleries credit your studio.

### Behavior changes

- Rebuilding a gallery that is already online updates it. The client's link stays the same.
- Booked sessions show when the deposit or agreement is still open.
- Times read the same way everywhere, like 4:10 pm.
- Finances shows months like Oct 2026.
- Clients are asked before leaving a gallery with unsent picks.
- Right-click saving and dragging photos out of a gallery are off.
- On phones, the large photo fills the screen and can be zoomed.

### Fixed

- A session could say Quoted while its page still said Inquiry.
- Pressing Enter could choose the wrong button on some forms.
- Adding a billing line lost your place on the page.
- Every page now has its own title in the browser tab.
- Screen readers now hear button names, form labels, and gallery picks.
- Unsent picks stay on the right photos after a gallery is rebuilt.
- Retouch instructions show when a client declined portfolio use.

### Upgrading

- Upgrading takes an encrypted backup first. To go back to 1.0.1, restore that backup; 1.0.1 won't open a 1.1.0 database.
- Galleries built before 1.1.0 keep their look until you rebuild them.
- Saving picks across devices works for galleries on a web host that runs PHP.

## 1.0.1 (2026-11-13)

### Features

- None in this release.

### Behavior changes

- Invoices and receipts are numbered when issued, not when opened. Download, Print, and Email issue a draft first.
- Issued invoices and receipts no longer change. Later payments add a note above the document instead.
- A refund no longer lowers the quoted price; refunded money is shown as not owed again.
- "Send quote" is now "Prepare quote", and pressing it twice no longer makes a second quote.
- Quote numbers are never reused after a session is deleted.
- A session with payments can't be deleted until they are voided or refunded.
- Saving from an out-of-date tab is refused, with your typing put back in the form beside the latest saved values.
- A tab left open on something deleted can't save into something else.
- Voiding a payment, and recording more than the balance, now ask first.
- New installs take their time zone from your browser; existing installs without one are asked once.

### Security

- Client galleries check for the gallery key before the host serves any files. Existing galleries are unchanged.
- Suggested codewords are now five words instead of two.
- Deletions are signed; a tampered deletions file can no longer delete clients.
- If a restored backup can't confirm earlier deletions, the app asks before applying them.
- Gallery storage folders can no longer be used to redirect the app's file writes or deletes elsewhere.
- The one-time setup code file is readable only by the app.
- Very long or unusual names typed into forms can no longer slow the app down.
- `relink-photos` refuses photos that don't belong to your install, including photos rekeyed under 1.0.0. Use
  `recover-photos` for those.

### Fixed

- Documents could be dated tomorrow in the evening, because the app ignored the container's time zone.
- "Delete the demo clients" failed when a demo client had photos.
- Setup suggested a backup folder it then refused, and "Use the defaults" on the last step didn't finish.
- The client gallery reports a lost connection instead of a wrong codeword, and offers Try again.
- A codeword refused as too easy to guess stays in the field.

### Upgrading

- Upgrading takes an encrypted backup first. To go back to 1.0.0, restore that backup; 1.0.0 won't open a 1.0.1 database.

## 1.0.0 (2026-11-13)

First public release.
