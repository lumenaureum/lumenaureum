# Where Lumen Aureum runs

Lumen Aureum is a small web app that you run yourself and open in a browser on your computer, tablet, or phone.

## The machine
It needs a machine that is **always on**: the daily backup, gallery checks, and scheduled deletions run in the
background. Suitable machines:
- **A NAS that runs containers** (Docker), with an Intel, AMD, or 64-bit ARM processor. On a TrueNAS, install it from
  the app catalog: see [The easiest way](#the-easiest-way-the-truenas-app-catalog).
- **A home server or mini PC** running Linux with Docker.
- **A Raspberry Pi 4 or 5** with a 64-bit operating system (Raspberry Pi OS 64-bit, or another 64-bit Linux) and
  Docker (the 32-bit system does not work). Choose a model with at least 2 GB of memory, so 1 GB stays free for the
  app. Keep the app's data and your photos on an SSD (USB or
  NVMe), not on the SD card: SD cards are slow and wear out under the constant small writes of a database.
- **A desktop computer running Linux with Docker** works, but only while it is on and awake; scheduled work waits
  until it is.

Docker Desktop on macOS and Windows is not supported: it hides where each connection comes from, so Lumen Aureum
refuses them.
A laptop that sleeps or travels is a poor choice.

After any restart the app is locked until you sign in (see [Security](security.md)). An optional ntfy notice tells
your phone when that happens (see [Notices while locked](security.md#notices-while-locked)).

Minimum: a 64-bit processor (Intel or AMD x86-64, or 64-bit ARM), 1 GB of free memory, and room for your photos. In
our tests the app used about 70 MB at rest and about 150 MB while sealing a 12 GB session. Building a client gallery
takes the most: about 310 MB for 52 photos of 4,000 pixels, and up to about 470 MB for 200 photos of 24 megapixels.
The app hands that memory back once the build ends. `docker stats` also counts the system's file cache, so it can read well above these figures.
The database stays small (under a few megabytes for hundreds of sessions).

## The easiest way: the TrueNAS app catalog
On a TrueNAS, Lumen Aureum installs from the app catalog like any other app (community train). Anywhere else, use
Docker Compose, below.

1. **Make three folders for it first,** so your data stays even if the app is ever removed: in Datasets, add one for
   its data, one for its backups (the vault: on another pool if you have one) and one for photos. Choose the **Apps**
   preset for each; it gives the app's user permission to use them. Don't share the photos one.
2. **Apps, Discover,** search for Lumen Aureum, **Install.**
3. Under Storage, for Data, Vault and Photos choose **Host Path** and pick the folders you made. (Don't choose
   ixVolume: TrueNAS deletes an ixVolume when the app is deleted.)
4. **Certificate (recommended).** Under Network, set **Certificate ID** to a certificate TrueNAS manages for you
   (Credentials, Certificates), for example one from ACME for a name you own. The app serves HTTPS with it and
   accepts the names on it, so browsers open it with no warning and nothing more to set. Without one, the app makes
   its own certificate, which each browser asks you to accept once (check its fingerprint in the app's log first).
5. Open the app at the address the Apps screen shows, or at the name on your certificate. Setup asks for a **setup
   code**: it is in the app's log (Apps, lumenaureum, Logs).

You don't need Host IPs, IPv6, or `CRM_HOSTNAMES` for a catalog install on your home network. If a browser is refused,
see [Reaching the app](#reaching-the-app). To restore a backup on a catalog install, see
[Restoring a backup](operations.md#on-a-truenas-catalog-install).

Updates arrive on the TrueNAS Apps screen after each release. The catalog's "Version" (1.0.1, say) is the package's
number; the app's own version shows as App Version. Settings that compose.yaml would hold, such as `CRM_ALLOW_ONLY`,
go in the app's Edit screen under Additional Environment Variables. ntfy, for the optional locked-studio notice, is in
the same catalog.

## A server in the cloud
It runs in Docker on any 64-bit Intel, AMD, or ARM machine, including a rented server. We recommend a computer you own, for one reason.
Everything Lumen Aureum stores is encrypted, so a disk image or a snapshot taken from a provider is useless on its
own. But while the app is unlocked, the key that opens your data is in the server's memory, and on a rented server
the provider controls that memory: its staff, its tools, and its snapshots of a running machine. On your own
computer, nobody else has that access. That is the difference between data that is encrypted and data that stays
in your hands.

If you run it on a rented server anyway:
- **Keep it off the public internet.** Reach it over a private network (a VPN) from your own computers, and list
  those addresses in `CRM_ALLOW_ONLY`. The app is built for one studio's own network, and every address it answers
  is one more that can try to sign in.
- **Use a certificate your browser trusts**, through `CRM_TLS_CERT` and `CRM_TLS_KEY`, or your own HTTPS proxy with
  `CRM_TLS=off` and `CRM_TRUSTED_PROXIES` (see below), so the browser shows no warning to click past.
- **Keep backups somewhere else**: a second provider, or a disk at home. A copy on the same server is lost with it.
- **Expect to sign in after every restart.** The server holds no key while it is locked, and the provider's
  maintenance restarts count.

No NAS? Any always-on computer at home works, such as a small mini PC or an old desktop running Linux (see above).
Galleries live on a web host either way, encrypted, so the one part that must be online already is.

## Storage
- **App data** (the database and settings): a few megabytes; keep it on storage you back up.
- **Photos and the vault**: plan for the size of your RAW files. Uploaded photos are kept encrypted in the photos
  folder; the vault holds encrypted archives of delivered sessions until their retention ends.
- **One disk or one pool.** The vault should sit on a different disk from the app's data, so one failed disk can't
  take both. A NAS with a single pool can't do that: a separate dataset for the vault is fine, and then the second
  backup location, somewhere else (another machine, SFTP, S3), is what really protects you. Setup warns when the second
  copy is on the same disk as the first, and saves it only when you confirm.
- **Optional second location** for the daily backup copy: another disk or NAS, an SFTP server, or an S3-compatible
  bucket.

### S3 storage
Backups, and galleries, can go to any S3-compatible storage: AWS, another online service, or the S3 service of a
NAS at home. Give the bucket's name, its service address (the endpoint; blank for AWS), an access key, and its secret
key. Choosing Backblaze B2, Cloudflare R2, Wasabi, or Amazon S3 by name says where that provider shows each one. Use a
key that can reach only that bucket, and turn bucket versioning off. Region, folder prefix, and **Bucket address** are
under **Advanced**; Bucket address stays on Automatic unless your storage's instructions ask for path style or virtual
host style.

- **A self-signed certificate** (a NAS at home, say) is trusted once, the same way as the web host's certificate
  below: Setup shows its fingerprint when you test the location, you check it against the one your NAS shows, and
  press **Trust this certificate**. From then on only that certificate's key is accepted at that address. If the key
  changes, backups there stop and Setup shows both certificates until you decide.
- **Plain `http://`** is accepted only for storage on your own network (an address like `192.168.20.5`, or a name
  that points to one), never for an address on the internet, and Setup shows a warning. Your backups are encrypted
  before they leave, but the requests themselves cross your network unencrypted: use `https://` where your storage
  offers it.

## Client galleries
Galleries must be reachable by clients over the internet, so they are published (encrypted) to web space you choose:
any web host with SFTP access, or an S3-compatible bucket with a public web address. The app itself does not need to be exposed
to the internet. On a web host that runs PHP (most shared hosting), each gallery also
enforces its own expiry date, so it comes down on time even if the app is locked or off, and the client's picks come
back to the app by themselves. A plain S3 bucket can't;
there the app removes galleries, and a bucket lifecycle rule (delete after, say, 400 days) is a good outer limit.

### Choosing a web host
Galleries need a host's small scripts for everything but showing the photos, and those scripts are PHP only: there is
no version for other script platforms. What each kind of host gives you:

| | A web host that runs PHP 7.4 or later | A plain bucket or static host (no PHP) |
|---|---|---|
| Galleries close on their last day, even with Lumen Aureum off | yes | no: the app removes them when it next checks |
| Clients' picks come back by themselves (sealed) | yes | no: the client sends them by email, with a fingerprint the app checks; you paste it on the client page (Paste their picks email) |
| The studio page's inquiry form | yes | no: the Contact page shows your email |
| Print ordering from galleries ([Print orders](print-orders.md)) | yes | no: galleries say "For prints, email" with your address |
| Galleries with a codeword | yes, when the host check finds the gate holds | no: refused, with the reason on the client page |

Setup's Online galleries step checks the host you connect (that PHP runs there, its version, that it keeps a real
gallery's files behind the gallery's gate, and whether a proxy or CDN sits in front of it) and shows which of these
work. A host that hands out gallery files without asking for the key, or runs no PHP, can't take galleries with a
codeword (someone with only the link could try codewords there as fast as their computer allows): Setup says so, and
such a gallery is refused there with the reason. A proxy or CDN in front of the host is fine: the inquiry form and
print orders then use limits for everyone instead of limits per visitor, so nobody is turned away because of someone
else. The check runs again once a day; if something changes for the worse
(PHP turned off, say), Today says so. On a host below PHP 7.4, galleries keep the scripts they have and Today names the
host and its version.

An S3-compatible bucket is always a static host, and a static host can't check codewords: galleries there stay sealed,
and anyone with the link can open them. While codewords are on, choosing S3 in Setup (or Online galleries) offers two
ways on: **Turn codewords off for galleries here**, or **Choose a host that can check codewords**. Nothing changes until
you choose. A gallery with a codeword is never put on an S3 host by any path (Upload, an update, Move galleries): it is
refused with the reason, and Move galleries leaves it on the earlier host, where its codeword is still checked.

### Signing in to an SFTP web host
Setup signs in with the app's own key by default: you paste its public line into the host's SSH keys once. If your
web host's control panel has no way to add a key, choose **A password** under Sign in with, and type the SFTP
password the host gave you. Choosing DreamHost or Bluehost by name starts on A password, since that is the sign-in
their panels hand out; SiteGround starts on the key, with its port filled in. The port and **A key file I mounted**
are under **Advanced**. It is kept encrypted with your data and never shown again; the server's key fingerprint
is still checked and trusted the same way, and the password is sent only to that server. A key stays the safer
choice: see [Reaching the web host](security.md#reaching-the-web-host). Once a web host is connected with a password,
Online galleries offers **Switch to the app's own key**: add the key on the host, then press it. The key is tested
first, and the password is forgotten only once the key works.

### The web host's certificate
The app checks your web host the way a client's browser does: over its `https://` address. A host with a
certificate from a public authority (what shared hosting, and Let's Encrypt, give you) needs nothing more.

A web server on your home network often has a self-signed certificate, or one from your own authority. The app
doesn't trust that by itself. After you save the Online galleries step, Setup shows the certificate it found: its
SHA-256 fingerprint (and its key's), the names it was issued to, who issued it, and the date it is valid until.
Check the fingerprint against the one your web server shows for its certificate, then press **Trust this
certificate**. From then on the app accepts that certificate's key at that address (the host and port you typed), and
no other: a renewal with the same key keeps working by itself. It works the same way as the server key of an SFTP
host.

The certificate must name the address you typed (an IP address or a name, in its subject alternative names, or in its
common name when it has none). One made for another name isn't offered: Setup says which names it carries, and you
make one on the web server that names the address. The app also never follows a redirect when it checks the host, so
a check can't end up on another address.

- **If the key changes** (you made a new certificate with a new key, or something sits between the app and the
  host), or the certificate no longer names the address, the app stops
  checking the host and never accepts the new one by itself. Today says "The web host's certificate changed. Check it
  in Setup.", and Setup shows the certificate you confirmed and the new one, side by side. Trust the new one only if
  you changed it yourself; **Keep the old one** leaves the checks stopped until the host shows the one you confirmed.
  A host that moves to a publicly trusted certificate: confirm it once and the app goes back to public trust, so its
  renewals never stop anything.
- **If your confirmed certificate expires**, the app still accepts it (it is the one you confirmed), and Today notes
  the date it expired. Clients' browsers may warn about it, so renew it on the web server (a renewal with a new key
  asks you to confirm it in Setup).
- **Until a certificate is trusted**, the host's checks wait: Today says "The web host's certificate isn't trusted.
  Check it in Setup." and links there. Galleries already online stay as they are meanwhile.

There is no setting that turns certificate checks off.

On S3, each gallery file is uploaded with its own Cache-Control. The page and `manifest.bin` get `no-cache`, so a
rebuilt gallery shows at once. The photo files get a year, since their names are never used twice. If a CDN sits in
front of the bucket, set it to follow the origin's Cache-Control. A CDN that keeps the page or the manifest longer
shows clients an old gallery after a rebuild, with photos that are no longer there.

Galleries can sit in a folder of your main website or at an address of their own; either is safe. What the gallery
page keeps in the client's browser (their picks and notes, the print cart) is sealed under the gallery's key, so no
other script on the same address (a website plugin, an analytics script) can read it.

Any web server that runs PHP works: Apache, nginx, LiteSpeed. Nothing a client needs kept private depends on
`.htaccess` files or on rules you add: each gallery's sealed files sit under names only its key gives, and the files
that keep its state print nothing when fetched. A web server that ignores `.htaccess` hands a gallery's files to
anyone who has its link and asks for them by name, so the gallery doesn't close on its last day by itself (the app
takes it down at its next check) and can't have a codeword; Setup's check says when that is so. See
[Security](security.md), "The PHP files on a web host".

When you rebuild a gallery that is already online, the new files go up first, and the old photo files are removed as
soon as the new page is up. A page a client already has open notices the new build and asks them to reload.

## Folders outside the container
The app runs inside a container and sees only the folders `compose.yaml` mounts into it. A folder that exists on the
machine, such as a USB disk at `/mnt/usb-backup`, doesn't exist for the app until a line under `volumes:` maps it in.
Setup says "That folder doesn't exist" until then. The example compose file mounts three: `./data` as `/data`, your
vault folder as `/vault`, and `./photos` as `/photos`.

To add one, put a line under `volumes:` in the form `- <folder on this machine>:<name inside the container>`, then type
the name inside the container in setup. For example:

```
    volumes:
      - ./data:/data
      - /path/to/another/disk/lumenaureum-vault:/vault
      - ./photos:/photos
      - /mnt/usb-backup/lumenaureum:/backup2       # a second backup copy: type /backup2 in setup, Backups
      - /srv/www/galleries:/galleries        # a folder a web server here serves: type /galleries in setup, Online galleries
```

Then:
1. Create the folder on the machine and give it the owner in the compose file's `user` line, for example
   `sudo chown 10001:10001 /mnt/usb-backup/lumenaureum`. For a gallery folder, the web server must also be able to write in
   it: run the web server as that user, or give the folder a group the web server is in, with group write permission.
2. Run `docker compose up -d`. The app restarts with the new folder, and you sign in again.
3. In setup (or Settings), choose "A folder on this machine or a mounted share" for backups, or "A folder a web server
   here serves" for galleries, and type the name inside the container (`/backup2`, `/galleries`).

## Reaching the app
On your home network, open `https://<address>:8780` in a browser (or the port you published, if you changed it) and accept its certificate once, after checking the
fingerprint against the container log. Keep it on your local network; do not expose it to the internet.

- **Names.** If you reach it by a name rather than an address, list the name in `CRM_HOSTNAMES`; requests for other
  names are refused (this stops malicious web pages from talking to it through DNS tricks).
- **Your own certificate.** Mount it and set `CRM_TLS_CERT` and `CRM_TLS_KEY` to the certificate and key files, for
  example a certificate your NAS already manages. The files must be readable by the user the container runs as. The
  names on it are accepted without listing them in `CRM_HOSTNAMES`.
- **Fewer browser warnings.** List the address and names you use in `CRM_TLS_SAN`, so the certificate matches them;
  the browser then asks only once per device. Add an address later and the next start makes a new certificate
  that names it (the log shows its fingerprint, to check again). The app's certificate lasts two years and renews
  itself a month before it ends.
- **Which address.** Publish the port on the address your computers use to reach the machine. A NAS on more than one
  network (for example a separate storage network) has an address on each: use the one on your computers' network.
- **Never plain HTTP.** The app serves HTTPS only.
- **When Docker hides where a visitor came from.** Some connections reach the app through Docker's port proxy,
  which replaces the visitor's address with Docker's: opening `localhost` or `127.0.0.1` on the machine running
  Docker (when the port is published there), connections over IPv6 when the port is published on every interface, and
  every connection on Docker Desktop (macOS, Windows) and rootless Docker. The app refuses them, because it can't tell
  who they are. Opening the machine's LAN address from the machine itself usually works. The safe fixes, in order: publish the
  port on the machine's LAN IPv4 address (`"192.168.1.10:8780:8780"`, as in the example compose file) and open it by
  that address. Allowing the gateway address
  with `CRM_ALLOW` (a line under `environment:` in `compose.yaml`) lets in anyone who reaches the published port, so do
  it only when nothing outside this machine can.
- **Behind your own reverse proxy**, the proxy must replace `X-Forwarded-For` and `X-Forwarded-Proto`, never add to
  what the visitor sent: see [Behind a reverse proxy](operations.md#behind-a-reverse-proxy), with examples for nginx,
  Caddy and Traefik.

## Settings in compose.yaml
Set these under `environment:` in `compose.yaml`; leave out any you don't need.

| Setting | What it does |
|---|---|
| `CRM_ALLOW_ONLY` | Only these addresses or ranges may connect, for example `192.168.1.0/24` (replaces the default of every private network) |
| `CRM_ALLOW` | Addresses or ranges to allow in addition to the default |
| `CRM_HOSTNAMES` | Names you reach the app by, besides its address, for example `nas.local` |
| `CRM_TLS_SAN` | Addresses and names to put in the app's certificate, so the browser asks only once per device |
| `CRM_TLS_CERT`, `CRM_TLS_KEY` | Your own certificate and its key, instead of the one the app makes |
| `CRM_TLS` | `off` only behind your own HTTPS reverse proxy (see [Behind a reverse proxy](operations.md#behind-a-reverse-proxy)) |
| `CRM_TRUSTED_PROXIES` | Your reverse proxy's own address; forwarded headers from anyone else are refused |
| `CRM_HSTS` | `1` tells browsers to always use HTTPS for this address |
| `CRM_PORT` | The port inside the container (default 8780) |
| `TZ` | The time zone to start with, for example `America/Chicago`, until you choose one in setup |
| `CRM_THREADS` | Request threads (default 8) |
| `CRM_GALLERY_SYNC` | `0` turns off the gallery check that runs every 30 minutes (first visits, picks, expiry) |
| `CRM_PLUGIN_DIRS`, `CRM_PLUGINS_OFF` | Folders of plugins you write yourself, and plugins to turn off (see `plugins/README.md`). Plugins from Lumen Aureum need neither: add them in Settings, Plugins |

## On a TrueNAS (Install via YAML)
To run the compose file yourself on a TrueNAS instead of installing from the catalog, use these steps.

TrueNAS's "Install via YAML" screen takes the compose file but has no `.env` file next to it, so `LUMENAUREUM_HOST_IP` is
never set and the app refuses to start. Write the address straight into the ports line:
`- "192.168.1.10:8780:8780"` (your NAS's address on the network your computers use). Create datasets for the data,
the vault and the photos, owned by the user in the compose file's `user` line (TrueNAS apps commonly run as 568:568).
The app keeps its own data, backups, and photos encrypted, so the datasets need nothing special; keep the photos
dataset out of any share, since nothing there is meant to be opened by hand.
