"""Storage backends. One small contract, identical for every backend:

    put_stream(name, reader) -> (size, sha256)   write the stream under `name`; nothing is visible until complete
    get_stream(name, writer)                       copy the object into a writable file object
    stat(name) -> size or None                     None when absent
    list(prefix="") -> [names]
    delete(name)                                   absent afterwards (verified by stat)

Names are opaque (random hex + ".age"); backends never see keys or plaintext."""
import hashlib
import base64
import os
import posixpath
import secrets
import shutil

CHUNK = 1024 * 1024


class _Hashing:
    """Wraps a reader so size and sha256 are computed as the bytes flow past."""
    def __init__(self, reader):
        self.r, self.h, self.n = reader, hashlib.sha256(), 0

    def read(self, n=-1):
        b = self.r.read(CHUNK if n is None or n < 0 else n)
        self.h.update(b); self.n += len(b)
        return b


def _check(name):
    if not name or name.startswith("/") or ".." in name.split("/") or "\\" in name:
        raise ValueError(f"bad object name: {name!r}")
    return name


class Backend:
    kind = "base"

    def put_stream(self, name, reader, content_type=None): raise NotImplementedError
    def get_stream(self, name, writer): raise NotImplementedError
    def stat(self, name): raise NotImplementedError
    def list(self, prefix=""): raise NotImplementedError
    def delete(self, name): raise NotImplementedError

    def remove_dir(self, prefix):
        """Drop an emptied folder where the backend has folders (no-op for object stores)."""

    def delete_verified(self, name):
        self.delete(name)
        if self.stat(name) is not None:
            raise IOError(f"{self.kind}: {name} still present after delete")


class LocalBackend(Backend):
    """Any mounted path, including the NAS share. Writes to a hidden temp file, then renames into place."""
    kind = "local"

    MARKER = ".vault-root"

    def __init__(self, root, read_only=False, public=False):
        """The root must already exist and carry the marker file: an unmounted NAS path (an empty folder on the VM's own
        disk) must never quietly receive archives that plaintext is then deleted on the strength of. A read-only root
        (restoring from a copied vault folder) needs no marker: nothing is ever written to it."""
        self.root = os.path.abspath(root)
        self.public = public          # a folder a web server serves: its files must be readable by that server's user
        if read_only:
            if not os.path.isdir(self.root):
                raise IOError(f"{self.root} is not a folder")
            return
        if not os.path.isfile(os.path.join(self.root, self.MARKER)):
            raise IOError(f"vault root {self.root} is not initialized (no {self.MARKER}); is the share mounted?")

    @classmethod
    def init_root(cls, root):
        os.makedirs(root, exist_ok=True)
        open(os.path.join(root, cls.MARKER), "a").close()
        return cls(root)

    def _p(self, name):
        return os.path.join(self.root, *_check(name).split("/"))

    def put_stream(self, name, reader, content_type=None):
        dst = self._p(name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = os.path.join(os.path.dirname(dst), f".{secrets.token_hex(6)}.part")
        hr = _Hashing(reader)
        try:
            with open(tmp, "wb") as f:
                while True:
                    b = hr.read(CHUNK)
                    if not b:
                        break
                    f.write(b)
                f.flush(); os.fsync(f.fileno())
            if self.public:                       # gallery files are encrypted: a web server sharing the group may read and write
                os.chmod(tmp, 0o664)
                d_ = os.path.dirname(dst)
                while d_.startswith(self.root) and d_ != self.root:   # never the host's own root folder
                    try:
                        os.chmod(d_, 0o775)   # group-writable: a web server in the group records visits and picks
                    except OSError:               # a folder the app doesn't own: leave it as its owner set it
                        pass
                    d_ = os.path.dirname(d_)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return hr.n, hr.h.hexdigest()

    def get_stream(self, name, writer):
        with open(self._p(name), "rb") as f:
            shutil.copyfileobj(f, writer, CHUNK)

    def stat(self, name):
        try:
            return os.path.getsize(self._p(name))
        except FileNotFoundError:
            return None

    def list(self, prefix=""):
        out = []
        for d, _, fs in os.walk(self.root):
            for fn in fs:
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(d, fn), self.root).replace(os.sep, "/")
                if rel.startswith(prefix):
                    out.append(rel)
        return sorted(out)

    def delete(self, name):
        try:
            os.remove(self._p(name))
        except FileNotFoundError:
            pass

    def remove_dir(self, prefix):
        try:
            os.rmdir(self._p(prefix.rstrip("/")))
        except OSError:
            pass


class SFTPBackend(Backend):
    """Any SFTP host. Key auth only, host key pinned from known_hosts (an unknown or changed host key is refused)."""
    kind = "sftp"

    def __init__(self, host, user, key_file, root, port=22, known_hosts="~/.ssh/known_hosts", private_key=None, host_key=None):
        self.host, self.user, self.key_file, self.root, self.port = host, user, os.path.expanduser(key_file or ""), root.rstrip("/"), port
        self.known_hosts = os.path.expanduser(known_hosts)
        self.private_key, self.host_key = private_key, host_key     # the app's own key; the server key pinned at first use

    def _open(self):
        try:
            import paramiko
        except ImportError:
            raise ImportError("SFTP storage needs paramiko: pip install -r requirements-sftp.txt") from None
        cli = paramiko.SSHClient()
        if self.host_key:                       # pinned when the owner trusted it: only that key is accepted
            kt, b64 = self.host_key.split()[:2]
            cli.get_host_keys().add(self.host if int(self.port) == 22 else f"[{self.host}]:{self.port}", kt,
                                    paramiko.PKey.from_type_string(kt, base64.b64decode(b64)))
        else:
            cli.load_host_keys(self.known_hosts)
        cli.set_missing_host_key_policy(paramiko.RejectPolicy())
        pkey = load_private_key(self.private_key) if self.private_key else None
        cli.connect(self.host, port=self.port, username=self.user, pkey=pkey, key_filename=None if pkey else self.key_file,
                    look_for_keys=False, allow_agent=False, timeout=20)
        return cli, cli.open_sftp()

    def _p(self, name):
        return posixpath.join(self.root, _check(name))

    def _mkdirs(self, sf, d):
        parts, cur = d.split("/"), ""
        for p_ in parts:
            if not p_:
                cur = "/" if not cur else cur
                continue
            cur = posixpath.join(cur, p_) if cur else p_
            try:
                sf.stat(cur)
            except IOError:
                sf.mkdir(cur)

    def put_stream(self, name, reader, content_type=None):
        cli, sf = self._open()
        dst = self._p(name)
        tmp = posixpath.join(posixpath.dirname(dst), f".{secrets.token_hex(6)}.part")
        hr = _Hashing(reader)
        try:
            self._mkdirs(sf, posixpath.dirname(dst))
            with sf.open(tmp, "wb") as f:
                f.set_pipelined(True)
                while True:
                    b = hr.read(CHUNK)
                    if not b:
                        break
                    f.write(b)
            try:
                sf.posix_rename(tmp, dst)
            except IOError:
                sf.rename(tmp, dst)
            return hr.n, hr.h.hexdigest()
        except Exception:
            try:
                sf.remove(tmp)
            except IOError:
                pass
            raise
        finally:
            sf.close(); cli.close()

    def get_stream(self, name, writer):
        cli, sf = self._open()
        try:
            with sf.open(self._p(name), "rb") as f:
                f.prefetch()
                shutil.copyfileobj(f, writer, CHUNK)
        finally:
            sf.close(); cli.close()

    def stat(self, name):
        cli, sf = self._open()
        try:
            return sf.stat(self._p(name)).st_size
        except FileNotFoundError:
            return None
        except IOError as e:
            if getattr(e, "errno", None) == 2:
                return None
            raise            # permission or connection trouble is not "absent"
        finally:
            sf.close(); cli.close()

    def list(self, prefix=""):
        cli, sf = self._open()
        out = []

        def walk(d, rel):
            try:
                entries = sf.listdir_attr(d)
            except IOError:
                return
            import stat as st_
            for e in entries:
                if e.filename.startswith("."):
                    continue
                r = f"{rel}/{e.filename}" if rel else e.filename
                if st_.S_ISDIR(e.st_mode):
                    walk(posixpath.join(d, e.filename), r)
                elif r.startswith(prefix):
                    out.append(r)
        try:
            walk(self.root, "")
        finally:
            sf.close(); cli.close()
        return sorted(out)

    def delete(self, name):
        cli, sf = self._open()
        try:
            sf.remove(self._p(name))
        except FileNotFoundError:
            pass
        except IOError as e:
            if getattr(e, "errno", None) != 2:
                raise
        finally:
            sf.close(); cli.close()

    def remove_dir(self, prefix):
        cli, sf = self._open()
        try:
            sf.rmdir(self._p(prefix.rstrip("/")))
        except IOError:
            pass
        finally:
            sf.close(); cli.close()


class S3Backend(Backend):
    """Any S3-compatible store (AWS, Cloudflare R2, Backblaze B2, Wasabi, MinIO). Use a key scoped to this one
    bucket, and turn bucket versioning OFF (old versions would outlive deletes; crypto-shred still covers them)."""
    kind = "s3"

    def __init__(self, bucket, access_key, secret_key, endpoint=None, region=None, prefix=""):
        import re as _re
        if endpoint and not (endpoint.startswith("https://") or _re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", endpoint)):
            raise ValueError("The S3 endpoint must start with https://")      # credentials and files never cross in the clear
        try:
            import boto3
        except ImportError:
            raise ImportError("S3 storage needs boto3: pip install -r requirements-s3.txt") from None
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.s3 = boto3.client("s3", aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                               endpoint_url=endpoint or None, region_name=region or None)

    def _k(self, name):
        _check(name)
        return f"{self.prefix}/{name}" if self.prefix else name

    def put_stream(self, name, reader, content_type=None):
        hr = _Hashing(reader)
        extra = {"ContentType": content_type} if content_type else None
        self.s3.upload_fileobj(hr, self.bucket, self._k(name), ExtraArgs=extra)   # visible only once complete
        return hr.n, hr.h.hexdigest()

    def get_stream(self, name, writer):
        self.s3.download_fileobj(self.bucket, self._k(name), writer)

    def stat(self, name):
        from botocore.exceptions import ClientError
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=self._k(name))["ContentLength"]
        except ClientError as e:
            if str(e.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise            # 403 and friends are not "absent"

    def list(self, prefix=""):
        out, pre = [], self._k(prefix) if prefix else (self.prefix + "/" if self.prefix else "")
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=pre):
            for o in page.get("Contents", []):
                k = o["Key"][len(self.prefix) + 1:] if self.prefix else o["Key"]
                out.append(k)
        return sorted(out)

    def delete(self, name):
        self.s3.delete_object(Bucket=self.bucket, Key=self._k(name))


# ---------------------------------------------------------------- SSH keys made and pinned by the app
def new_ssh_key():
    """A fresh Ed25519 key pair: (private key in OpenSSH form, public line to paste into authorized_keys)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    k = ed25519.Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    return priv, public_line(priv)


def public_line(private_text):
    from cryptography.hazmat.primitives import serialization
    k = serialization.load_ssh_private_key(private_text.encode(), password=None)
    return k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() + " lumen-aureum"


def load_private_key(private_text):
    import io as _io
    import paramiko
    return paramiko.Ed25519Key(file_obj=_io.StringIO(private_text))


def fetch_host_key(host, port=22, timeout=15):
    """The server's host key, read without logging in: ("type base64", "SHA256:..."), for the owner to check once."""
    import paramiko
    t = paramiko.Transport((host, int(port)))
    try:
        t.start_client(timeout=timeout)
        k = t.get_remote_server_key()
        return f"{k.get_name()} {k.get_base64()}", host_key_fingerprint(f"{k.get_name()} {k.get_base64()}")
    finally:
        t.close()


def host_key_fingerprint(line):
    import hashlib
    raw = base64.b64decode(line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def from_config(cfg):
    """{"type": "local", "path": ...} | {"type": "sftp", "host", "user", "key_file", "root"} | {"type": "s3", ...}"""
    t = cfg.get("type")
    if t == "local":
        return LocalBackend(cfg["path"], public=bool(cfg.get("public")))
    if t == "sftp":
        return SFTPBackend(cfg["host"], cfg["user"], cfg.get("key_file", ""), cfg["root"], cfg.get("port", 22),
                           cfg.get("known_hosts", "~/.ssh/known_hosts"), cfg.get("private_key"), cfg.get("host_key"))
    if t == "s3":
        return S3Backend(cfg["bucket"], cfg["access_key"], cfg["secret_key"], cfg.get("endpoint"), cfg.get("region"),
                         cfg.get("prefix", ""))
    raise ValueError(f"unknown backend type {t!r}")
