# Changes

New features, behavior changes, security fixes, and other fixes in each release.

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
