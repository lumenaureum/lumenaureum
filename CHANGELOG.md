# Changes

New features, behavior changes, security fixes, and other fixes in each release.

## 1.4.0 (2026-11-17)

### Features

- Settings, Plugins: every plugin and whether it is on, with an on/off switch for the ones that have one (print orders
  so far). CRM_PLUGINS_OFF still wins: a plugin it names stays off whatever the switch says.
- Print orders are optional and off until you turn them on in Settings, Plugins (new installs and updates alike).
  Turning them off asks first when orders are still open, hides the print pages, and takes each gallery's print offer
  off your web host at the next gallery check; every order, payment and lab cost is kept and comes back when you turn
  them on again. Orders a client sends anyway are collected and kept (Today says how many), and print money always
  stays in Finances, statements and the accountant export.
- Print orders: clients order prints from their download gallery, choosing sizes and the crop on each photo. Each
  order is sealed in their browser so only your app can read it, comes to you to review (each print shown as it will
  be cut), and gets its own invoice billed to whoever ordered. Once it is paid, Prepare for the lab downloads the
  print-ready files and an order sheet to your browser, and you order the prints at any lab you like
  ([Print orders](docs/print-orders.md)).
- Settings, Print prices: your sizes and prices (a sample list to start from), an optional lab cost that shows your
  margin and never changes a price by itself, a Fill prices from costs button that sets prices at your markup (3
  times by default, rounded up to the whole dollar) when you press it and lists what it will change first, shipping per destination (at cost, cost plus handling, or a
  flat amount), and tax on prints and on shipping in Settings, Sales tax.
- Orders from someone who isn't your client wait on a review list: bill them as a guest, add them to the client, or
  make a new client. Nothing becomes a client by itself. Make a new client makes them a client of their own (their
  own record and statement, with a note naming the family whose gallery they ordered from).
- Today lists new print orders, orders waiting for payment (after 7 days), paid orders ready for the lab, and orders
  not shipped after 14 days. Finances shows print sales, shipping, sales tax collected, lab cost and profit, kept apart
  from your sessions; the accountant export adds them.
- A lab can be connected by a plugin (plugins/README.md, The lab hook); ordering by hand always stays available.
- Setup's Online galleries step checks your web host (where your galleries, client links and studio page are published) and
  shows what works there: galleries closing on time,
  picks coming back by themselves, the inquiry form, print ordering. Today tells you if that changes later (PHP turned
  off, say).
- When a new version changes the gallery page, every gallery online gets the new page by itself, one at a time. A
  gallery whose client is picking right now waits until they are done (its session says so, with Do it now).
- Every refund asks "After this refund, the gallery:" with two choices, "releases once nothing is owed" and "stays
  held until paid again". A partial refund (some money still paid toward the price after it) starts on the first, a
  refund of everything paid on the second; pick either. The gallery panel shows a gallery held after a refund, with
  Release now, and one that will release, with Hold again. History records the choice with the refund, and every
  change.

### Behavior changes
- Today no longer repeats a Finances link in its row of figures; Finances stays one tap away in the menu.

- Setup's Backups and Online galleries steps have one test: Test and continue (Test and save in Settings). The
  separate Test connection and Test upload buttons are gone.
- Setup, Who can reach this: the address shown is "the address this request came from" (devices behind a router or
  VPN can share one), and the hint says where CRM_ALLOW_ONLY goes on a TrueNAS app as well as in compose.yaml.
- Setup's example values (e.g. /vault, e.g. https://example.com/g/) say "e.g." and look lighter, so they can't be
  mistaken for filled-in values. When the app's container has a /vault folder, the Backups step fills it in.
- The sample agreement's "Your information" section now says "my web host" (it said "my gallery host"). Only new
  installs get the new sample: an agreement you edited keeps your words, and one you reviewed and kept as the sample
  keeps the words you approved (they become your own wording at the update).
- Recording a print order payment over its balance asks first, as a session payment does ("That is $5.00 more than
  the balance. Record it anyway?"); once recorded, the order says how much to give back.
- A client who sends a sixth print order within an hour is told it is a limit and when to try again ("You've sent
  several orders in the last hour. Please try again after 3:40 PM."), not that the order couldn't be taken.
- A reopened print order whose ship-to was already reduced to city, state and ZIP says "Shipping address incomplete"
  and asks for the full address; Prepare for the lab and any lab hand-off wait until it is entered.
- A print order paid in parts: Finances splits each payment into prints, shipping and tax from the running total, so
  the last payment takes any leftover cent and the parts add up exactly to the order's invoice.
- Print lab costs are recorded when and as they are paid, and never rewritten: canceling an order keeps its lab cost
  unless the lab order itself was canceled or refunded (a new "The lab canceled or refunded this" records that, on the
  day the money came back); a remake's cost is recorded on its own (a free remake is $0) and never replaces the first
  order's; each cost stays in the year it was paid. Finances and the accountant export's print-lab-costs.csv (now
  "date paid", with "what" and "lab order" columns) follow.
- Print orders: after Change the order, the shipping charge is worked out again from your shipping rules, unless you
  set it by hand; a hand-set charge is kept and the order's review says "Shipping set by hand: $X (rules now say $Y)".
- Every email draft the app writes (invoice, receipt, quote, session details, agreement, release, price list, and the
  print order mails) now gives the document's key facts in the email itself: its number, total, what it is for, and
  how to pay or where it stands. A mail link can't attach a file, so the draft no longer says anything is attached;
  attaching the PDF for their records is up to you. Text it sends the same first line, without promising a PDF.
- While a download gallery offers prints, or one of its orders is open, the gallery's copy at home is kept: the
  scheduled deletion of the shoot's files waits, and Today says so.
- An invoice not paid in 30 days expires (its number is never reused); you can reopen the order if they pay later.
- A session that holds print payments can't be deleted until they are refunded or voided.
- Only two kinds of print ship-to address are reduced to city, state and ZIP, a year after the order closes: an
  address sent to someone else, and a guest's address when the guest was never accepted as a client. Your clients'
  own addresses are never reduced, and issued invoices never change.
- A print order billed to a client of another family (matched by email) is on that client's own statement.
- Galleries and the studio page's form need a web host with PHP 7.4 or later for anything beyond showing photos. On
  an older PHP, galleries keep working as they are and Today names the host.
- Every gallery gets a new address. Each gallery's folder on your web host now has a longer name, starting with
  "g-" (later, other kinds of links get their own letter). The update moves every gallery to its new folder. The link
  your client already has keeps working for the gallery's whole life: it opens a page that sends them straight on to
  the new address, key and all. Copy link and Email link give the new address. A gallery's old folder comes down
  when the gallery does.
- A client page left open from before the update can't send picks until it is reloaded; after a reload it opens at
  the new address with the picks they made on that device.
- Setup, Settings, Today, History and the guides all call it the "web host": the one place your galleries, client
  links and studio page are published (earlier versions said "gallery host"). History lines written before the update
  keep their words.

### Security
- Each published image now carries a signed record of how it was built and a signed list of everything inside it. See Operations for how to check both.

- Picks, drafts and website inquiries are sealed in one new format: each is bound to what it is and to its gallery
  (the kind of link included), so a draft can't pass as sent picks, one gallery's picks as another's, or anything as
  something the link can't carry, and an inquiry names the studio key it is sealed to. The formats are published with
  test vectors ([Sealed formats](docs/sealed-format.md)).
- Each picks list, draft and inquiry a browser sends has its own ID: the app takes each in once, and a web host that
  hands back an older one can never replace a newer one.
- No gallery folder is removed from a host (going dark, a take-down, an update, a move) before every print order,
  pick and draft waiting there has been collected.
- The web host's certificate is checked every time the app checks the host over its https address. A certificate
  from a public authority works as it is. Any other (self-signed, or from your own authority) is shown in Setup with
  its SHA-256 fingerprint, issuer and end date, and accepted only once you confirm it; from then on only that
  certificate's key is accepted at that address (a renewal with the same key keeps working), and the certificate must
  name the address. A new key stops every check of the host until you confirm or refuse it in Setup. The checks never
  follow a redirect and never wait without a time limit. Certificate checks are never turned off: there is no setting
  for it.
- A gallery's sealed files (its photo list, print offer, and the client's picks and draft) are kept on your web host
  under names only the gallery's key gives, and its own small files (the dates it closes, the first visit, a one-time
  download) print nothing when fetched. So nobody without the key can download them, on any web server, whether or
  not it reads `.htaccess` files, and someone holding only the link can't test codewords offline. Galleries already
  online get the new names when the 1.4 update reaches them.
- Setup's host check now fetches a real gallery's files without the key, the way a stranger would. A web host that
  hands them out anyway (or runs no PHP) can't take galleries with a codeword, and Setup and the gallery say so in
  plain words: someone with only the link could try codewords there as fast as their computer allows.
- Nobody is locked out of a gallery any more because someone else on the same address sent wrong keys. A right key
  always opens it. After 10 wrong tries at a codeword in an hour, every try takes about a second of work in the
  browser first, so guessing gets slow and a client sharing a network or a proxy with someone guessing still gets in.
- The studio page's inquiry form never turns a visitor away because others share their address (an office, a phone
  network, a proxy): past a few inquiries from one address, the next one takes a few seconds of work in the browser
  instead of being refused. The host check also tells you when a proxy or CDN sits in front of your web host; the
  inquiry form and print orders then stop counting visitors by address (behind a proxy everyone shares one), and a
  full inquiries folder there refuses more rather than drop an inquiry already waiting.
- What the gallery page keeps in the client's browser (their picks and notes, the print cart, the gallery's colors)
  is sealed under the gallery's key, under names that don't say which gallery it is. Other scripts on the same website
  can't read it, and nothing readable about a client stays on their device. Older entries are sealed the first time
  the gallery opens.
- The inquiries folder's rate list prints nothing when fetched, and the inquiries and print orders folders hold an
  empty page, so a web server that lists folders shows nothing there.
- Changing your password, using your master key to set a new one, making new recovery codes or signing out every
  other device now also stops any sign-in that was waiting for its two-factor code.
- The app never writes its memory to disk if it crashes: core dumps are turned off in the app and in `compose.yaml`
  (`ulimits: core: 0`), so no key or client data can end up in a crash file.

### Fixed

- Test and continue on an SFTP server the app hadn't connected to before said "Add it to known_hosts first", which a
  photographer can't do. It now shows the server's key fingerprint and a Trust this server box on the step itself, for
  the web host and both backup locations; tick it and continue. A server whose key changed says so in plain words and
  offers the new key the same way. No message mentions known_hosts any more.
- A failed test on a setup step no longer clears what you typed: every field, choice and tick stays. A secret key you
  typed is kept by the app for the next try (never shown back), so you don't type it again.
- An S3 address that doesn't answer with HTTPS, or has a self-signed certificate, is explained in plain words instead
  of a raw SSL error.
- A web host with a self-signed certificate, or one from your own authority (a web server on your home network, say),
  no longer keeps the update of your galleries and the studio page's inquiry form waiting forever. Its checks
  stopped on the certificate, and Today said only that the host "cannot be reached". Setup now shows the certificate
  to confirm once (on the Online galleries step, first-run setup included: Trust this certificate goes on to the next
  step, like Trust this server), and Today names the reason: "The web host's certificate isn't trusted. Check it in
  Setup." Other network problems keep their words.
- An invoice or receipt numbered by 1.0.0 on a session with a refund, opened for the first time after updating, now
  keeps the Refund line 1.0.0 showed (for example: 300 + 50, Refund -40, Total 310), so it matches the copy your
  client may have and adds up. Before, it was kept without that line (Total 350 against 310 paid, Balance due 0). The
  money in your records was never affected.
- Choosing your time zone in setup or Settings no longer pauses automatic deletions and asks you to confirm the date
  when the change only moves today by a day (an evening setup on a machine set to UTC did this).
- A session paid in full and then partly refunded (Balance $0, "$40 refunded, not owed again") can put its gallery
  online again (put back online, Rebuild, Move galleries). Since 1.3.0 it said "$40 still due" and the client's link
  stayed dark. After a refund, delivery now does what you chose on the refund: release once nothing is owed (the
  balance the session shows, so a refund that leaves money owed still holds the photos for that), or stay held until
  the full price is paid again. A tip still never counts as paying for the photos. Sessions with no refund are held
  and released exactly as before.
- An invoice or receipt issued after a refund now has a line "Refunded, not owed again", so it adds up (Total $375,
  payments $350, refunded $25). Documents already issued keep the copy they were given.
- A canceled booking's summary at the top of its page shows what its Billing section shows (Refund due, or Deposit
  kept) instead of a balance it no longer owes. A session paid beyond its quote shows Credit there, as Billing does.
- Setup no longer accepts a galleries' folder with a name the studio page uses for its own files (photos/, assets/,
  about/, contact/, inquiries/); the studio page couldn't be uploaded beside it. Move galleries won't move galleries
  into such a folder either and says why. A folder you already use is never renamed.

### Upgrading

- If you updated from 1.0.0 to 1.0.1 or later before this release and have already opened such an invoice, it keeps
  the copy it was given then, without the Refund line (an issued document never changes). Payments, balances and
  Finances are right either way; the copy you sent your client before updating is the one that shows the refund.
- At the first sign-in, after the usual upgrade backup, the app moves every gallery to its new address and the new
  format, and the inquiry form to the new format (with everything a new studio page has, such as the empty page in its
  inquiries folder). Each gallery's new folder is built and checked before anyone is sent
  to it; its days, first visit, codeword and one-time download carry over. Today shows the progress. Picks, drafts,
  print orders and inquiries sent before or during the update are all kept, once each.
- A gallery whose host can't be reached stays as it was until it can; Today names the host.
- A gallery whose web host's certificate isn't publicly trusted also stays as it was until you confirm the
  certificate in Setup, Online galleries (Today's line links there); the update then goes on by itself.
- Going back to 1.3.1 afterwards needs the backup made before the update.
- If you keep your own `compose.yaml`, add `ulimits:` with `core: 0` under the service (as the new one has); the app
  turns core dumps off by itself either way.
- A gallery with a codeword that is already on a web host which hands out gallery files without the key stays online,
  but someone with its link could try codewords there much faster. Setup and Today say so; take it down or move to a
  web host that passes the check. A new gallery with a codeword can't go there.
- Sessions refunded before this update get the choice the refund form would have started on, so nothing changes
  by itself: a session with money still paid toward the price after its refund releases once nothing is owed; a
  session refunded everything it paid stays held until paid again. Change either on the session's gallery panel.

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
