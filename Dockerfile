FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-liberation fonts-dejavu-core fonts-droid-fallback tzdata \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && printf '<?xml version="1.0"?>\n<fontconfig><dir>/usr/share/fonts-droid-fallback/truetype</dir></fontconfig>\n' \
      > /etc/fonts/conf.d/66-droid-sans-fallback-korean.conf \
 && fc-cache -f

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CRM_DB=/data/crm.db CRM_STORAGE_CFG=/data/storage.json CRM_PHOTOS_ROOT=/photos CRM_GALLERY_ROOT=/data/galleries \
    CRM_PORT=8780

WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock \
 && pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /usr/local/lib/python3.13/site-packages/setuptools* /usr/local/bin/pip*

COPY . .
RUN python -m compileall -q /usr/local/lib/python3.13 /app \
 && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin lumenaureum \
 && mkdir -p /data /photos && chown lumenaureum:lumenaureum /data /photos

ARG LUMENAUREUM_BUILD=""
ENV LUMENAUREUM_BUILD=${LUMENAUREUM_BUILD}
USER lumenaureum
VOLUME ["/data", "/photos"]
EXPOSE 8780
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD ["python", "-I", "-S", "/app/healthcheck.py"]
CMD ["python", "serve.py"]
