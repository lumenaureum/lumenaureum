# Changes

New features, behavior changes, security fixes, and other fixes in each release.

## 1.2.0 (2026-11-15)

### Features

- Runs on 64-bit ARM.
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
