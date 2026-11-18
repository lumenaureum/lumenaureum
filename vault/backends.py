import contextlib
import hashlib
import base64
import os
import posixpath
import secrets
import shutil

CHUNK = 1024 * 1024

class _Hashing:

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

def contained(root, path):

    base = os.path.realpath(root)
    real = os.path.realpath(path)
    if real != base and not real.startswith(base.rstrip(os.sep) + os.sep):
        raise ValueError(f"path leaves its root: {path!r}")
    return path

class Backend:
    kind = "base"

    def put_stream(self, name, reader, content_type=None, cache_control=None):

        raise NotImplementedError
    def get_stream(self, name, writer): raise NotImplementedError
    def stat(self, name): raise NotImplementedError
    def list(self, prefix=""): raise NotImplementedError
    def delete(self, name): raise NotImplementedError

    def remove_dir(self, prefix):
        pass

    @contextlib.contextmanager
    def session(self):

        yield self

    def delete_verified(self, name):
        self.delete(name)
        if self.stat(name) is not None:
            raise IOError(f"{self.kind}: {name} still present after delete")

class LocalBackend(Backend):

    kind = "local"

    MARKER = ".vault-root"

    def __init__(self, root, read_only=False, public=False, marked=True):

        self.root = os.path.abspath(root)
        self.public = public
        if read_only or not marked:
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
        return contained(self.root, os.path.join(self.root, *_check(name).split("/")))

    def put_stream(self, name, reader, content_type=None, cache_control=None):
        dst = self._p(name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        contained(self.root, dst)
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
            if self.public:
                os.chmod(tmp, 0o664)
                d_ = os.path.dirname(dst)
                while d_.startswith(self.root) and d_ != self.root:
                    try:
                        os.chmod(d_, 0o775)
                    except OSError:
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

    kind = "sftp"

    def __init__(self, host, user, key_file, root, port=22, known_hosts="~/.ssh/known_hosts", private_key=None, host_key=None):
        self.host, self.user, self.key_file, self.root, self.port = host, user, os.path.expanduser(key_file or ""), root.rstrip("/"), port
        self.known_hosts = os.path.expanduser(known_hosts)
        self.private_key, self.host_key = private_key, host_key
        self._held = None
        self._depth = 0

    @contextlib.contextmanager
    def session(self):

        self._depth += 1
        try:
            yield self
        finally:
            self._depth -= 1
            if not self._depth and self._held:
                cli, sf = self._held
                self._held = None
                try:
                    sf.close()
                finally:
                    cli.close()

    @staticmethod
    def _dropped(e):

        import socket
        try:
            import paramiko
            ssh_err = (paramiko.SSHException,)
        except ImportError:
            ssh_err = ()
        return isinstance(e, (EOFError, ConnectionError, socket.timeout) + ssh_err)

    def _run(self, fn):

        if not self._depth:
            cli, sf = self._open()
            try:
                return fn(sf)
            finally:
                sf.close(); cli.close()
        for attempt in (0, 1):
            if self._held is None or not self._held[0].get_transport() or not self._held[0].get_transport().is_active():
                if self._held:
                    try:
                        self._held[0].close()
                    except Exception:
                        pass
                self._held = self._open()
            try:
                return fn(self._held[1])
            except Exception as e:
                if attempt or not self._dropped(e):
                    raise
                try:
                    self._held[0].close()
                except Exception:
                    pass
                self._held = None

    def _open(self):
        try:
            import paramiko
        except ImportError:
            raise ImportError("SFTP storage needs paramiko: pip install -r requirements-sftp.txt") from None
        cli = paramiko.SSHClient()
        if self.host_key:
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

    def put_stream(self, name, reader, content_type=None, cache_control=None):
        dst = self._p(name)
        start = reader.tell() if hasattr(reader, "seek") and hasattr(reader, "tell") else None

        def put(sf):
            if start is not None:
                reader.seek(start)
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
            except Exception as e:
                if not self._dropped(e):
                    try:
                        sf.remove(tmp)
                    except (IOError, OSError):
                        pass
                if start is None and self._dropped(e):
                    raise IOError(f"sftp: connection dropped while writing {name}") from e
                raise
        return self._run(put)

    def get_stream(self, name, writer):
        def get(sf):
            with sf.open(self._p(name), "rb") as f:
                f.prefetch()
                shutil.copyfileobj(f, writer, CHUNK)
        return self._run(get)

    def stat(self, name):
        def st(sf):
            try:
                return sf.stat(self._p(name)).st_size
            except FileNotFoundError:
                return None
            except IOError as e:
                if getattr(e, "errno", None) == 2:
                    return None
                raise
        return self._run(st)

    def list(self, prefix=""):
        return self._run(lambda sf: self._list(sf, prefix))

    def _list(self, sf, prefix):
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
        walk(self.root, "")
        return sorted(out)

    def delete(self, name):
        def rm(sf):
            try:
                sf.remove(self._p(name))
            except FileNotFoundError:
                pass
            except IOError as e:
                if getattr(e, "errno", None) != 2:
                    raise
        return self._run(rm)

    def remove_dir(self, prefix):
        def rmd(sf):
            try:
                sf.rmdir(self._p(prefix.rstrip("/")))
            except IOError:
                pass
        return self._run(rmd)

class S3Backend(Backend):

    kind = "s3"

    def __init__(self, bucket, access_key, secret_key, endpoint=None, region=None, prefix="", addressing="auto",
                 pins=None):
        from vault import s3
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.s3 = s3.Client(bucket, access_key, secret_key, endpoint=endpoint or None, region=region or None,
                            addressing=addressing or "auto", pins_table=pins)

    def _k(self, name):
        _check(name)
        return f"{self.prefix}/{name}" if self.prefix else name

    def put_stream(self, name, reader, content_type=None, cache_control=None):
        hr = _Hashing(reader)
        self.s3.upload(self._k(name), hr, content_type=content_type, cache_control=cache_control)
        return hr.n, hr.h.hexdigest()

    def get_stream(self, name, writer):
        self.s3.download(self._k(name), writer)

    def stat(self, name):
        return self.s3.head(self._k(name))

    def list(self, prefix=""):
        pre = self._k(prefix) if prefix else (self.prefix + "/" if self.prefix else "")
        return sorted(k[len(self.prefix) + 1:] if self.prefix else k for k in self.s3.list(pre))

    def delete(self, name):
        self.s3.delete(self._k(name))

def new_ssh_key():

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

    t = cfg.get("type")
    if t == "local":
        return LocalBackend(cfg["path"], public=bool(cfg.get("public")))
    if t == "sftp":
        return SFTPBackend(cfg["host"], cfg["user"], cfg.get("key_file", ""), cfg["root"], cfg.get("port", 22),
                           cfg.get("known_hosts", "~/.ssh/known_hosts"), cfg.get("private_key"), cfg.get("host_key"))
    if t == "s3":
        return S3Backend(cfg["bucket"], cfg["access_key"], cfg["secret_key"], cfg.get("endpoint"), cfg.get("region"),
                         cfg.get("prefix", ""), cfg.get("addressing") or "auto")
    raise ValueError(f"unknown backend type {t!r}")
