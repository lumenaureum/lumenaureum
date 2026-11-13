"""At unlock: any face photo or storage file found unencrypted (for example a storage file written by hand) is
encrypted in place under the data key, and the plaintext overwritten."""
import json
import os
from pathlib import Path

import datakey


def _overwrite(p):
    try:
        n = p.stat().st_size
        with open(p, "r+b") as f:
            f.write(b"\0" * n)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def encrypt_faces(faces_dir, key=None):
    """Each plaintext face photo -> encrypted in place, tied to its household. Returns how many."""
    n = 0
    d = Path(faces_dir)
    if not d.is_dir():
        return 0
    for f in d.glob("*.jpg"):
        if datakey.is_sealed_file(f):
            continue
        data = f.read_bytes()
        tmp = f.with_name(f.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(datakey.seal_bytes(data, f"face:{f.stem}", label="faces", key=key))
        _overwrite(f)
        os.replace(tmp, f)
        n += 1
    return n


def encrypt_storage(cfg_path, key=None):
    p = Path(cfg_path)
    if not p.exists() or datakey.is_sealed_file(p):
        return False
    data = p.read_bytes()
    json.loads(data)                                          # refuse to wrap something that isn't the config
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(datakey.seal_bytes(data, "storage", label="storage", key=key))
    _overwrite(p)
    os.replace(tmp, p)
    return True
