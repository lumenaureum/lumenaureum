# Studio page

A small public website for your studio, made from what Lumen Aureum already has, in the same look as your client
galleries. It is one fixed design, not a website builder: a home page with a few photos you pick, shown one after
another; an About page; a Contact page with an inquiry form, which also tells clients their gallery opens from its private
link; a "not found" page; and the small files search engines, modern search tools, and browsers look for (robots.txt,
sitemap.xml, llms.txt, and an icon).

It is off until you switch it on in **Settings, Studio page**.

## What it needs
Online galleries set up (Settings, Online galleries), in their own folder under your web address, such as
`https://example.com/g/`. The studio page goes to the same web host, one folder up: the top of your site
(`https://example.com/`). Your galleries stay where they are.

The folder name on the host and the end of the gallery address must match (`g/` and `/g/`), so Lumen Aureum can tell
where the top of your site is. If they don't, or your galleries are at the very top of your web address, the page
says so and nothing is uploaded.

## Setting it up
1. **Settings, Studio page**: tick **Use the studio page**.
2. **Words**: the name at the top (your brand name by default), a line under it (for example
   `Photographer · Your town`), one line about your work, a short description for search results and link previews, and
   your About text. In the About text, a blank line starts a new paragraph (the first one is shown larger), a line
   starting with `## ` is a small heading, and `[words](https://example.com)` is a link. Leave it empty for no About
   page.
3. **Contact**: your email, phone, and mailing address come from Settings, Profile. You can leave the phone or the
   address off the site.
4. **Look**: the font and colors follow your client galleries' look. The background can be the galleries' own or any
   of the four swatches. To use your own font (one you already use for your brand), add its file in Settings,
   Client galleries; the site and your galleries then both use it, and carry the file with them. A variable font
   (one file with a range of weights) is used across its whole range, so headings and buttons get the font's own
   heavier weight rather than one the browser makes up. The **Accent color** (gold, or your own) draws the thin lines
   and underlines on the page and the Book a session button you can paste on another website.
5. **Photos**: up to 24, shown in the order listed. Give each one a few words saying what it shows, for people who
   can't see it. The page warns while a photo has none.
   - **Your own photos**: JPEG or PNG. Only the pixels are kept, at a size for the web.
   - **From a client session**: only sessions whose client allows portfolio use are offered. A photo on the client's
     "Please don't show" list is never offered, and never more photos from one session than your agreement allows
     (2 by default). If the client later declines portfolio use, or asks you not to show a photo, the next build leaves
     it out, and Today reminds you while it is still on your host, whether the page is switched on or not.
   - **How the Don't show list is read**: one entry per line or after a comma, and each entry must be exactly one
     file name of that session, as the gallery sends it (`IMG_0042.jpg`, or `IMG_0042.jpg (photo 4)`), with or without
     the extension, in any case. A name also holds back every version of the photo whose name starts with it and goes
     on with anything but a digit: the retouched `IMG_0042-Edit.jpg`, `IMG_0042 copy.jpg`, `IMG_0042-2.jpg`,
     `IMG_0042v2.jpg` (never `IMG_00421.jpg`). An entry naming a version (`IMG_0042-Edit`, `IMG_0042 copy`,
     `IMG_0042-2`, `IMG_0042_final`) holds back the photo it is a version of, `IMG_0042`, and all its versions. If an entry is anything else (a number such as "photo 3" or "#2", words,
     two names in one entry such as "IMG_0042 and IMG_0043" or "IMG_0042.jpg IMG_0043.jpg", a range such as
     "IMG_0042 - IMG_0045" or "IMG_0042 thru 45", a name followed by words, a name that is none of the session's
     photos), it can't be matched safely, so none of that session's photos are offered, and the page says why. To
     offer the others, write the list as the names of the photos not to show (the client page, Delivery).
6. **Icon**: the small picture on the browser tab and a phone's home screen. A square PNG works best. Without one,
   the site uses the Lumen Aureum mark; **Remove the icon** brings the mark back.
7. **Copyright mark**: on by default. A small `© <year> <your name>` in the bottom-left corner of each photo, in
   near-white letters with a dark outline, sized to the photo. The year is the year the photo first went online.

8. **A summary for modern search tools (llms.txt)**: on by default. A short text file that modern search tools read: the studio's
   name, the line under it, the line about your work, the first paragraph of About, the address the site shows, and
   the site's pages with how to send an inquiry. It holds only what the site already shows, and never a gallery.
   Untick the box to leave it out; the next upload removes it.

The site always has its "not found" page. robots.txt, sitemap.xml, and llms.txt need the site's address, which comes
from your gallery address (see What it needs, above).

## Every photo, every time
Each photo is decoded and saved again from its pixels at display size (at most 1100 pixels on its longest side,
JPEG quality 86), so nothing from the original file survives: no camera details, no location, no color profile, no
comments. Your originals are never published.

## Preview, then upload
**Preview** builds the site and opens it in its own window, here in the app. Nothing leaves your computer. The
preview is removed when you upload, take the site down, or the app locks or restarts; the build made for an upload is
removed once it has gone up. The
inquiry form in the preview shows, but sends nothing.

What goes online is what is on the screen. The words save when you press **Save the words** (the page says
"Unsaved changes" until then, with **Discard changes**, and asks before you leave); the ticks save as you tick them,
with **Undo** for a few seconds. While there are unsaved words or photo descriptions, Upload says **Save and put
online**: it saves them first, then uploads. Preview asks you to save first.

**Upload** builds the site fresh and puts it on your web host. A `gallery_upload` guard also applies to the studio page
(see plugins/README.md). It never happens on its own. When it goes ahead, every photo is checked again against what its client allows now: if a client declined portfolio use, or
asked you not to show a photo, while the upload waited, the site is built again without it before anything is sent.
Before the site is built, and again when the upload goes ahead, the client's latest picks (their Don't show list
among them) are read from your web host, so a list the client sent since the app last checked counts too. If they
can't be read, nothing is uploaded and the page says so.

What it writes, and what it never touches:
- It never writes over a file it didn't put there without asking. If your host already has an `index.html` (an earlier
  website, say), the upload stops before anything is written and names the files, with **Replace these files**. That
  asks first, then keeps a copy of each old file in the app and puts the studio page's own files in their place. The
  page then lists the files it replaced and when, with **Put the old files back**, which puts the kept copies back
  on your host and stops counting those names as the studio page's own. A file larger than 20 MB can't be kept, so
  it can't be replaced: move it aside or delete it on your host, then press Upload again. If the files on your host
  change between the offer and your answer, nothing is replaced and the page says what changed. A file it did put there once, but which was changed on your host since and so let go, stops the
  upload the same way, and the page names it as such rather than as someone else's. So does a file it put there under
  earlier web host settings that couldn't be confirmed to reach this same folder (recognized by its fingerprint);
  and the page names first, on their own, any client photos on your host that their client no longer allows.
- It never writes into, or removes anything from, your galleries' folder.
- It keeps a list of every file it uploaded, with a fingerprint (SHA-256) of each. Only those are ever replaced or
  removed, and only while the host's copy still matches its fingerprint: a file someone edited on the host since,
  even by one letter, is never written over or removed, and the page names it.
- A client's photo is the exception that stays on the list: if its client no longer allows it and its copy on the host
  was changed there (your host recompressed it, say), Upload and Take the site down leave it, so Today keeps reminding
  until it is gone from the host, and the Studio page offers **Remove it anyway** for that file (through the same
  guard). Deleting it on the host yourself works too; the next upload notices it has gone.
- When the web host settings change (another SFTP user name, port, or root, the server's name written another way,
  another path to the same folder), the app tells whether it is still the same place: the same server, port, user name,
  and folder written another way is the same host (a local folder is the same host at any path: its marker file keeps
  its id), and otherwise a small probe file written through the new settings
  and looked for through the earlier ones tells (it is removed right away). The same place keeps its list. Another
  place never makes the app forget what it put on the earlier one: those files stay listed as **left on an earlier
  host**, Today keeps reminding while a client photo there is no longer allowed, and the Studio page offers **Take down
  there** (through the same guard; never the folder in use, which a probe checks first) and, for a host that is gone
  for good, **Gone for good**, which forgets it without touching anything.
- An `.htaccess` file already at the top of your site is left as it is. For the site's own "not found" page, it then
  needs the line `ErrorDocument 404 /404.html` (the page tells you the exact line).

**Take the site down** removes the files on that list, and nothing else. With the inquiry form on, it first collects
any inquiries still waiting on the host, then removes the inquiries folder's own files (sealed inquiries, the rate
file, unfinished writes) and the folder. A file of anyone else's there is left, and so is the folder's `.htaccess`, to
keep it closed to the web; an inquiry that couldn't be collected is left too, and the page says so. Uploading with the
form switched off does the same.

**Switching the page off** in Settings doesn't take the site down. While its files are still on your host, the Studio
page keeps showing **Take the site down** and any photo a client no longer allows; Today keeps its reminders; and
inquiries sent through the form still online are still collected and wait on Today.

## The inquiry form
On the Contact page. A visitor gives a name, an email or a phone number, the kind of session, a preferred date, and a
message. What they type is sealed in their browser to your studio's own key ([Sealed format](sealed-format.md), purpose
inquiry), so only this app can read it: the web
host keeps a sealed file it can't open, until Lumen Aureum collects it (every 15 minutes, or **Check for inquiries**).
Collected inquiries wait in the Inbox on Today, one line each, bold while new; tap one to open it. Press **Add as a client** to make it a new client
with an Inquiry session, with "Inquiry from your website" in its History; **Spam** removes it. Nothing from the form becomes a client record until you add it. The file
is removed from the host only once the inquiry is saved here.

The review list helps with the rest:
- An inquiry with the same email (case aside) or phone number as a client you have says **Matches** and the client's
  name, with **Add to** that client (**Add to an existing client...** finds any other client by name): a new Inquiry session for them instead of a second household. The
  message keeps its real sender, and a sender who isn't a person on that client yet can be added as a contact (ticked
  to start with). A misfiled one moves later from the client's page (Filed under the wrong client?). One with the same
  email or phone as another inquiry waiting says so.
- The form's kinds of session are your session types, "Not sure yet", and "Other (explain below)". When the visitor
  didn't pick one, the line says "Kind of session: not given", and Add asks which (the first kind is chosen to start
  with).
- A message longer than 4000 characters is kept to its first 4000, and the list says so.
- **Add all** asks first, and adds only the inquiries on the list as you saw it that match no client (those wait for
  your choice). **Spam all** asks first and removes only those shown, never one that arrived since.
- Each Spam writes one line in History; the names in spam are never written there.

Details:
- The key is made the first time the site is built. Its private half stays in this app's encrypted database; the page
  carries only the public half.
- Spam: the form has a hidden field people never see and fill in, and a form isn't sent sooner than 3 seconds after
  the page opened (pressed sooner, the Send button says "Sending..." and the form waits, then sends). Both are checked
  again here; inquiries with the hidden field filled in, or sent sooner, are dropped and counted in History.
- Files on the host named as this version's inquiries (`.las`) that aren't an inquiry sealed to your key are removed and
  counted, never shown. Files of any other name in the folder are never touched by the collector (the 1.4 update
  collects the ones 1.3's form left).
- The host takes 5 inquiries an hour from one sender (an IPv4 address, or all of one IPv6 /56, the block one
  subscriber usually gets), at most 10 waiting from one sender, and at most 200 waiting in all. When the folder is
  full, the newest inquiry of the sender with the most waiting makes room (only when that sender has at least two more
  waiting than the one sending now), so one network can't fill the folder and turn everyone else away. Senders are
  kept only as a hash keyed with a random value for your install, in the rate file and at the start of each waiting
  file's name; never the address itself.
- Before sending, the visitor's browser does about a second of work (a proof of work the host checks), so sending
  inquiries in bulk takes real time.
- Up to 200 inquiries are collected at each check (the host's whole folder), with no daily limit: they wait on the
  review list, sealed in the encrypted database, and none becomes a client until you add it. When more than 10 arrive
  in a day, Today says "Many website inquiries at once; check for spam".
- The inquiries folder keeps nothing readable, on any web server: the inquiries are sealed, the rate list
  (`.rate.php`: hashed addresses and times) begins with a PHP exit line so fetching it shows nothing, and an empty
  `index.html` keeps a web server that lists folders from listing it. Its `.htaccess` closes it once more where the
  server reads it.
- Visitors who share an address (an office, a phone network, a proxy or CDN in front of your host) are never turned
  away because of each other: past a few inquiries from one address, each further one needs a little more work in the
  visitor's browser (a few seconds on a phone, asked for by the host and done by the form). Behind a proxy or CDN that
  Setup's host check saw, senders aren't counted at all and every inquiry needs that work; a full folder there refuses
  more rather than drop an inquiry already waiting.
- The form needs a host that runs PHP (SFTP web hosting, or a folder a PHP web server serves). On a host that doesn't,
  such as an S3 bucket, the Contact page shows your email and phone instead.
- No captcha, nothing from other sites.

## The inquiry form on another website
Made your main website elsewhere (Squarespace, WordPress, Showit, and the like)? Under **Use the inquiry form on another
website**, turn on **Show the inquiry form on another website**, list that website's address (up to 5; the www. version
of each is allowed too; https:// only), press **Save the addresses**, then **Copy the code**. Paste it into that website where it takes HTML code
(in Squarespace an Embed or Code block, in WordPress a Custom HTML block). It goes online with the next **Upload**.
- The code is one `<iframe>` of `inquiry-form.php`, a page on your own web host: the same form, the same sealing in the
  visitor's browser, and the same `inquiry.php` as the Contact page. Inquiries arrive in the Inbox on Today like any
  other. The code holds only the page's address: nothing secret, no script.
- Only the websites you list can show it in a frame; anywhere else, browsers refuse.
- Tick **Show a Book a session button instead of the whole form** for a button that opens the form in a tab of its
  own, on your web host. No addresses are needed then, and no website can show the form in a frame.
  The button takes your Accent color, with white or dark words, whichever reads clearly on it (4.5 to 1 or better).
- It needs what the form needs: the inquiry form ticked under Words, and a host that runs PHP. The preview and a draft
  don't build it.

## What it doesn't do
No page builder or layouts, no blog, no booking or payments, no help with domains or DNS, no search tuning beyond the
basic page information, and no analytics or tracking. Nothing on the site loads from anywhere else: the fonts are the
galleries' own files. robots.txt doesn't list your galleries' folder (which would name it to anyone reading the
file); each gallery page tells search engines not to index it.

## Building a draft by hand
`build_site(out_dir, conf, photos)` in `plugins/studio_page.py` takes `draft=True` for a look before going live from
any folder (for example `https://example.com/draft/`): every link is relative, every page says `noindex, nofollow`,
there is no robots.txt, sitemap.xml, llms.txt, .htaccess, or inquiry.php, and the form says sending is off.
