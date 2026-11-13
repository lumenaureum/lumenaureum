# Lumen Aureum: one process, non-root, read-only code. Data lives in /data (database, keyring, faces, vault by
# default), photos in /photos. Intel/AMD (x86-64).
# Base image pinned by digest (python:3.13-slim-trixie on 2026-11-11); CI updates it deliberately, never silently.
FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# WeasyPrint (every PDF) needs Pango; Liberation fonts stand in for Times/Helvetica in the documents.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-liberation fonts-dejavu-core tzdata curl \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CRM_DB=/data/crm.db CRM_STORAGE_CFG=/data/storage.json CRM_PHOTOS_ROOT=/photos CRM_GALLERY_ROOT=/data/galleries \
    CRM_PORT=8780

WORKDIR /app
# Exact versions, each checked against its hash (requirements.lock, from requirements.in)
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock \
 && pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /usr/local/lib/python3.13/site-packages/setuptools* /usr/local/bin/pip*
# pip is only for building: removed from the image (with the copies of other libraries it bundles), nothing installs later

COPY . .
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin lumen \
 && mkdir -p /data /photos && chown lumen:lumen /data /photos

ARG LUMEN_BUILD=""
ENV LUMEN_BUILD=${LUMEN_BUILD}
USER lumen
VOLUME ["/data", "/photos"]
EXPOSE 8780
# Healthy means "answering"; /healthz also says whether the data is locked (after a restart, until you sign in).
# curl, not a Python process on every check: light on a NAS. HTTPS with the app's own certificate on loopback.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD curl -fsk --max-time 4 "https://127.0.0.1:${CRM_PORT:-8780}/healthz" >/dev/null || curl -fs --max-time 4 "http://127.0.0.1:${CRM_PORT:-8780}/healthz" >/dev/null || exit 1
# HTTPS by default (certificate made on first start; fingerprint in the log). One process, threads. No access log.
CMD ["python", "serve.py"]
