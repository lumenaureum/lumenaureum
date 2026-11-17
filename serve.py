import os
import sys

import netsec
import nodump

def tls_args(cert, key):

    return ["--certfile", str(cert), "--keyfile", str(key), "--config", "python:gunicorn_tls"]

if __name__ == "__main__":
    problem = netsec.startup_problem()
    if problem:
        sys.exit(problem)
    port = os.environ.get("CRM_PORT", "8780")
    names = [n.strip() for n in os.environ.get("CRM_TLS_SAN", "").split(",") if n.strip()]
    import datakey
    where = (f"https://{names[0] if names else '<this machine>'}:{port} (or the port you published in compose.yaml,"
             " if it maps another)")
    if not datakey.exists():
        print(f"Lumen Aureum is new here: open {where} to set it up.", file=sys.stderr, flush=True)
    else:
        print(f"Lumen Aureum starts locked, as it does after every restart: open {where} and sign in to unlock."
              " Until then, backups and gallery checks wait.", file=sys.stderr, flush=True)
    args = ["gunicorn", "--bind", f"0.0.0.0:{port}", "--workers", "1", "--threads", os.environ.get("CRM_THREADS", "8"),
            "--worker-class", "gthread", "--timeout", "120", "--graceful-timeout", "30",
            "--no-control-socket"]
    if netsec.tls_mode() == "on":
        cert, key = netsec.ensure_cert()
        args += tls_args(cert, key)
    nodump.harden()
    os.execvp("gunicorn", args + ["app:app"])
