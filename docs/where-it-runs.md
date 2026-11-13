# Where Lumen Aureum runs

Lumen Aureum is a small web app that you run yourself and open in a browser on your computer, tablet or phone.

## The machine
It needs a machine that is **always on**: the daily backup, gallery checks and scheduled deletions run in the
background. Suitable machines:
- **A NAS that runs containers** (Docker). Intel or AMD models only: many budget NAS models use ARM processors,
  which aren't supported.
- **A home server or mini PC** running Linux with Docker.
- **A desktop computer running Linux with Docker** works, but only while it is on and awake; scheduled work waits
  until it is.

Docker Desktop on macOS and Windows is not supported: it hides where each connection comes from, so Lumen Aureum
refuses them.
A laptop that sleeps or travels is a poor choice.

After any restart the app is locked until you sign in (see [Security](security.md)).

Minimum: a 64-bit Intel or AMD processor (x86-64), 1 GB of free memory, and room for your photos. In our tests the app used about 150 MB of
memory even while sealing a 12 GB session; the database stays small (under a few megabytes for hundreds of sessions).

## A server in the cloud
It runs on any machine that runs Docker, including a rented server. We recommend a computer you own, for one reason.
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

## Client galleries
Galleries must be reachable by clients over the internet, so they are published (encrypted) to web space you choose:
any web host with SFTP access, or an S3-compatible bucket with a public web address. The app itself does not need to be exposed
to the internet. On a web host that runs PHP (most shared hosting), each gallery also
enforces its own expiry date, so it comes down on time even if the app is locked or off, and the client's picks come
back to the app by themselves. A plain S3 bucket can't;
there the app removes galleries, and a bucket lifecycle rule (delete after, say, 400 days) is a good outer limit.

## Reaching the app
On your home network, open `https://<address>:8780` in a browser and accept its certificate once, after checking the
fingerprint against the container log. Keep it on your local network; do not expose it to the internet.

- **Names.** If you reach it by a name rather than an address, list the name in `CRM_HOSTNAMES`; requests for other
  names are refused (this stops malicious web pages from talking to it through DNS tricks).
- **Your own certificate.** Mount it and set `CRM_TLS_CERT` and `CRM_TLS_KEY` to the certificate and key files, for
  example a certificate your NAS already manages. The files must be readable by the user the container runs as.
- **Fewer browser warnings.** List the address and names you use in `CRM_TLS_SAN`, so the certificate matches them;
  the browser then asks only once per device. Add an address later and the next start makes a new certificate
  that names it (the log shows its fingerprint, to check again). The app's certificate lasts two years and renews
  itself a month before it ends.
- **Which address.** Publish the port on the address your computers use to reach the machine. A NAS on more than one
  network (for example a separate storage network) has an address on each: use the one on your computers' network.
- **Never plain HTTP.** The app serves HTTPS only.
- **When Docker hides where a visitor came from.** Some connections reach the app through Docker's port proxy,
  which replaces the visitor's address with Docker's: connections from the machine running Docker, connections over
  IPv6 when the port is published on every interface, and every connection on Docker Desktop (macOS, Windows) and
  rootless Docker. The app refuses them, because it can't tell who they are. The safe fixes, in order: publish the
  port on the machine's LAN IPv4 address (`"192.168.1.10:8780:8780"`, as in the example compose file) and open it by
  that address. Allowing the gateway address
  with `CRM_ALLOW` lets in anyone who reaches the published port, so do it only when nothing outside this machine can.
- **Your proxy must append to `X-Forwarded-For`**, not pass on what the visitor sent.

## Settings in compose.yaml
Set these under `environment:` in `compose.yaml`; leave out any you don't need.

| Setting | What it does |
|---|---|
| `CRM_ALLOW_ONLY` | Only these addresses or ranges may connect, for example `192.168.1.0/24` (replaces the default of every private network) |
| `CRM_ALLOW` | Addresses or ranges to allow in addition to the default |
| `CRM_HOSTNAMES` | Names you reach the app by, besides its address, for example `nas.local` |
| `CRM_TLS_SAN` | Addresses and names to put in the app's certificate, so the browser asks only once per device |
| `CRM_TLS_CERT`, `CRM_TLS_KEY` | Your own certificate and its key, instead of the one the app makes |
| `CRM_HSTS` | `1` tells browsers to always use HTTPS for this address |
| `CRM_UNLOCK_KEY_FILE` | Unattended unlock: a key file on a different volume from the data folder (see [Security](security.md)) |
| `CRM_PORT` | The port inside the container (default 8780) |
| `TZ` | The time zone to start with, for example `America/Chicago`, until you choose one in setup |
| `CRM_THREADS` | Request threads (default 8) |
| `CRM_GALLERY_SYNC` | `0` turns off the gallery check that runs every 30 minutes (first visits, picks, expiry) |
| `CRM_PLUGIN_DIRS`, `CRM_PLUGINS_OFF` | Plugin folders, and plugins to turn off (see `plugins/README.md`) |

## On a TrueNAS (Install via YAML)
TrueNAS's "Install via YAML" screen takes the compose file but has no `.env` file next to it, so `LUMEN_HOST_IP` is
never set and the app refuses to start. Write the address straight into the ports line:
`- "192.168.1.10:8780:8780"` (your NAS's address on the network your computers use). Create datasets for the data,
the vault and the photos, owned by the user in the compose file's `user` line (TrueNAS apps commonly run as 568:568).
The app keeps its own data, backups and photos encrypted, so the datasets need nothing special; keep the photos
dataset out of any share, since nothing there is meant to be opened by hand.
