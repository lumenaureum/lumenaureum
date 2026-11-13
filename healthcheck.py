"""Container health check: is the app answering? (HTTPS with its own certificate, or plain HTTP when CRM_TLS=off.)"""
import os
import ssl
import sys
import urllib.request

import netsec

scheme = "https" if netsec.tls_mode() == "on" else "http"
ctx = ssl.create_default_context()
ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE      # our own self-signed certificate, on loopback
try:
    r = urllib.request.urlopen(f"{scheme}://127.0.0.1:{os.environ.get('CRM_PORT', '8780')}/healthz", timeout=4, context=ctx)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
