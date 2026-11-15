# Third-party licenses

Lumen Aureum is licensed under the GNU Affero General Public License v3.0 or later (see LICENSE). It contains no vendored
third-party code. It uses, without modification, the following, all under licenses compatible with the
AGPL. Versions are those in the published container image; each package's full license text ships with the package.

## Python packages (installed in the container)
| Package | License |
|---|---|
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click | BSD-3-Clause |
| blinker | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| cffi | MIT-0 |
| pycparser | BSD-3-Clause |
| pyrage (age encryption) | MIT |
| sqlcipher3 (bundles SQLCipher, BSD-3-Clause, and OpenSSL libcrypto, Apache-2.0) | Zlib |
| pillow | MIT-CMU |
| weasyprint, pydyf, tinycss2, cssselect2, webencodings | BSD-3-Clause |
| tinyhtml5 | MIT |
| pyphen | GPL-2.0+ / LGPL-2.1+ / MPL-1.1 (used under LGPL-2.1+) |
| fonttools, brotli | MIT |
| zopfli | Apache-2.0 |
| segno (QR codes) | BSD-3-Clause |
| gunicorn (web server) | MIT |
| paramiko (SFTP) | LGPL-2.1 |
| bcrypt, PyNaCl | Apache-2.0 |
| invoke | BSD-2-Clause |
| boto3, botocore, s3transfer (S3) | Apache-2.0 |
| jmespath, urllib3, six | MIT |
| python-dateutil | Apache-2.0 / BSD-3-Clause |

## System packages in the container (Debian)
Python 3.13 (PSF-2.0), Pango and HarfBuzz (LGPL-2.0+ / MIT), Liberation fonts (OFL-1.1), DejaVu fonts (Bitstream
Vera / public domain), tzdata (public domain).

## Bundled data and fonts
| Item | Source | License |
|---|---|---|
| `zip_latlon.json.gz` (ZIP code to latitude/longitude) | US Census Bureau, 2020 Gazetteer ZCTA file | Public domain (US government work) |
| `static/fonts` Material Symbols | Google | Apache-2.0 |
| `plugins/gallery_assets/fonts/great-vibes.woff2` Great Vibes (subset to Latin) | The Great Vibes Pro Project Authors, via Google Fonts | SIL Open Font License 1.1 (`great-vibes-OFL.txt` beside it) |
| `plugins/gallery_assets/fonts/cormorant-garamond.woff2` Cormorant Garamond (regular weight, subset to Latin) | The Cormorant Project Authors, via Google Fonts | SIL Open Font License 1.1 (`cormorant-garamond-OFL.txt` beside it) |
| `brand/` wordmark (Fraunces, outlined) | Undercase Type | SIL Open Font License 1.1 |

The Lumen Aureum name and logo are not covered by the software license; see brand/README.md.
