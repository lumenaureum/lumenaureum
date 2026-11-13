"""The sealed photo format (docs/photo-format.md) against its published test vectors: the app's own writer produces
exactly the vector files, the app's reader opens them, and a decoder written only from the specification (below, not
using photostore) opens them too and rejects reordered, swapped, truncated and extended files."""
import hashlib
import io
import json
import os
import sys

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
VEC = json.load(open(os.path.join(ROOT, "tests", "vectors", "photo-format.json")))


def plaintext(v):
    if "plaintext_hex" in v:
        return bytes.fromhex(v["plaintext_hex"])
    return bytes(i % 251 for i in range(v["plaintext_len"]))


def spec_decode(blob, file_id, key):
    """From the specification alone: magic, then length-prefixed records; nonce = 4 zero bytes + 8-byte big-endian
    record number; associated data = '<file id>:<record number>:last|more'."""
    if blob[:4] != b"LAP1":
        raise ValueError("magic")
    recs, at = [], 4
    while at < len(blob):
        if at + 4 > len(blob):
            raise ValueError("truncated length")
        n = int.from_bytes(blob[at:at + 4], "big")
        ct = blob[at + 4:at + 4 + n]
        if len(ct) != n:
            raise ValueError("truncated record")
        recs.append(ct)
        at += 4 + n
    if not recs:
        raise ValueError("empty")
    aes, out = AESGCM(key), bytearray()
    for i, ct in enumerate(recs):
        last = i == len(recs) - 1
        out += aes.decrypt(b"\0" * 4 + i.to_bytes(8, "big"), ct, f"{file_id}:{i}:{'last' if last else 'more'}".encode())
    return bytes(out)


def records(blob):
    out, at = [], 4
    while at < len(blob):
        n = int.from_bytes(blob[at:at + 4], "big")
        out.append(blob[at:at + 4 + n])
        at += 4 + n
    return out


@pytest.mark.parametrize("v", VEC["vectors"], ids=lambda v: v["name"])
def test_the_writer_produces_the_vector_and_both_readers_open_it(v, tmp_path, monkeypatch):
    import photostore
    monkeypatch.setattr(photostore, "ROOT", tmp_path / "sealed")      # no reload: other tests keep their module
    assert photostore.REC == VEC["record_bytes"] and photostore.MAGIC == VEC["format"].encode()
    pt = plaintext(v)
    assert hashlib.sha256(pt).hexdigest() == v["plaintext_sha256"]

    class Con:                                          # just enough of a database for append()
        def execute(self, *a):
            return type("R", (), {"rowcount": 1})()

        def commit(self):
            pass
    row = dict(id=v["file_id"], size=len(pt), received=0, records=0, ct_len=0, state="partial", fkey=v["key_hex"])
    photostore.append(Con(), row, 0, io.BytesIO(pt), len(pt))
    blob = photostore.path_of(v["file_id"]).read_bytes()
    assert len(blob) == v["file_len"] and hashlib.sha256(blob).hexdigest() == v["file_sha256"]
    if "file_hex" in v:
        assert blob.hex() == v["file_hex"]
    key = bytes.fromhex(v["key_hex"])
    assert spec_decode(blob, v["file_id"], key) == pt
    done = dict(row, state="done", records=len(records(blob)))
    assert b"".join(photostore.iter_plain(done)) == pt


def test_tampering_is_detected():
    v = next(v_ for v_ in VEC["vectors"] if v_["name"] == "two-records")
    pt, key, fid = plaintext(v), bytes.fromhex(v["key_hex"]), v["file_id"]
    AES = AESGCM(key)
    rec0 = AES.encrypt(b"\0" * 4 + (0).to_bytes(8, "big"), pt[:VEC["record_bytes"]], f"{fid}:0:more".encode())
    rec1 = AES.encrypt(b"\0" * 4 + (1).to_bytes(8, "big"), pt[VEC["record_bytes"]:], f"{fid}:1:last".encode())
    frame = lambda ct: len(ct).to_bytes(4, "big") + ct
    good = b"LAP1" + frame(rec0) + frame(rec1)
    assert spec_decode(good, fid, key) == pt
    for bad in (b"LAP1" + frame(rec0),                          # cut short: record 0 is not marked last
                b"LAP1" + frame(rec1) + frame(rec0),            # reordered
                good + frame(rec1),                             # extended
                good[:-1]):                                     # truncated
        with pytest.raises(Exception):
            spec_decode(bad, fid, key)
    with pytest.raises(Exception):
        spec_decode(good, "ffeeddccbbaa99887766554433221100", key)   # the same records under another file's id
    flipped = bytearray(good)
    flipped[100] ^= 1
    with pytest.raises(Exception):
        spec_decode(bytes(flipped), fid, key)
