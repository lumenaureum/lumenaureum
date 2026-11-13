"""Photos uploaded into the app are sealed on arrival: the photo's bytes never reach the disk readable, not even part
way through an upload that breaks; an upload resumes where it stopped; a download gives back exactly what went in."""
import io
import os
import secrets
import tempfile
import time

import pytest

from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Smith')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'shot')", (hid,)).lastrowid
    con.commit(); con.close()
    return A, c, sid, tmp_path


def photo(n):
    """A 'RAW' whose every 64 bytes carry a marker, so any readable piece of it on disk is found."""
    mark = b"PLAINTEXT-RAW-MARKER-" + secrets.token_hex(8).encode()
    unit = (mark + b"." * 64)[:64]
    return (unit * (n // 64 + 1))[:n], mark


def leaked(mark, roots, since):
    hits = []
    for root in roots:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    if os.path.getmtime(p) < since - 1:
                        continue
                    with open(p, "rb") as f:
                        if mark in f.read():
                            hits.append(p)
                except OSError:
                    pass
    return hits


def upload(c, sid, name, data, kind="raw", piece=None):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name}, environ_base=E).get_json()
    at, piece = st["received"], piece or st["piece"]
    while at < len(data):
        r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}", data=data[at:at + piece],
                   headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
        assert r.status_code == 200, r.get_data(as_text=True)
        at = r.get_json()["received"]
    return at


def test_upload_is_sealed_resumes_and_downloads_back(env):
    A, c, sid, tmp = env
    since = time.time()
    data, mark = photo(3 * (1 << 20) + 12345)                       # three whole records and a short last one
    import photostore
    con = A.connect()
    row = photostore.start(con, sid, "raw", "IMG_0001.NEF", len(data))

    class Breaks(io.BytesIO):                                         # the network drops part way through a piece
        def read(self, n=-1):
            if self.tell() >= (1 << 20) + 5000:
                return b""
            return super().read(min(n, (1 << 20) + 5000 - self.tell()) if n >= 0 else n)
    with pytest.raises(photostore.UploadError):
        photostore.append(con, row, 0, Breaks(data[:8 << 20]), min(len(data), 8 << 20))
    row = con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()
    assert row["received"] == 1 << 20 and row["state"] == "partial"  # one whole record kept; the broken one dropped
    con.close()
    roots = [str(tmp / "data"), str(tmp / "photos"), tempfile.gettempdir()]
    assert not leaked(mark, roots, since)
    r = c.post(f"/session/{sid}/photos/upload?kind=raw&size={len(data)}&offset=0", data=data[:100],   # wrong place
               headers={"X-File-Name": "IMG_0001.NEF", "Content-Type": "application/octet-stream"}, environ_base=E)
    assert r.status_code == 409 and r.get_json()["received"] == 1 << 20
    assert upload(c, sid, "IMG_0001.NEF", data, piece=(1 << 20) + 777) == len(data)   # resumes; odd piece sizes too
    assert not leaked(mark, roots, since)
    con = A.connect()
    row = con.execute("SELECT * FROM photo_files WHERE session_id=?", (sid,)).fetchone()
    con.close()
    assert row["state"] == "done"
    got = c.get(f"/photo/{row['id']}", environ_base=E)
    assert got.status_code == 200 and got.data == data
    assert "attachment" in got.headers["Content-Disposition"]
    with A.app.test_request_context():
        url = A.stage_url(sid)
    stage = c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "IMG_0001.NEF" in stage and 'id="photos"' in stage


def test_tampered_or_truncated_files_do_not_open(env):
    A, c, sid, tmp = env
    data, _ = photo((1 << 20) + 10)
    upload(c, sid, "a.jpg", data, kind="export")
    import photostore
    con = A.connect()
    row = con.execute("SELECT * FROM photo_files WHERE session_id=?", (sid,)).fetchone()
    p = photostore.path_of(row["id"])
    raw = p.read_bytes()
    p.write_bytes(raw[:-1])                                          # cut short
    with pytest.raises(Exception):
        photostore.plain_bytes(row)
    p.write_bytes(raw[:30] + bytes([raw[30] ^ 1]) + raw[31:])        # one bit changed
    with pytest.raises(Exception):
        photostore.plain_bytes(row)
    p.write_bytes(raw)
    assert photostore.plain_bytes(row) == data
    photostore.delete(con, row)
    assert not p.exists() and not con.execute("SELECT 1 FROM photo_files").fetchone()
    con.close()


def test_names_and_sizes_are_checked(env):
    A, c, sid, tmp = env
    for name, size in (("", 10), ("x.nef", 0), ("x.nef", (4 << 30) + 1)):
        r = c.get(f"/session/{sid}/photos/status?kind=raw&size={size}", headers={"X-File-Name": name}, environ_base=E)
        assert r.status_code == 400
    import photostore
    assert photostore.clean_name("../../etc/passwd") == "passwd" and photostore.clean_name("C:\\x\\y.NEF") == "y.NEF"


def test_gallery_builds_from_uploaded_exports(env):
    """The gallery is made from the uploaded edited JPEGs, decrypted in memory; no folder is involved."""
    A, c, sid, tmp = env
    from PIL import Image
    for i in (2, 10, 1):
        b = io.BytesIO()
        Image.new("RGB", (64, 48), (i * 20, 90, 120)).save(b, "JPEG")
        upload(c, sid, f"img{i}.jpg", b.getvalue(), kind="export")
    since = time.time()
    r = c.post(f"/session/{sid}/gallery/build", data={"from": "uploads"}, environ_base=E)
    assert r.status_code == 302
    for _ in range(200):
        st = c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    con = A.connect()
    assert con.execute("SELECT n_files FROM galleries WHERE session_id=?", (sid,)).fetchone()[0] == 3
    con.close()


def test_photos_are_mirrored_once_fetched_back_and_unmirrored_on_delete(env):
    """One encrypted copy per photo on the backup location; a lost local file comes back from it; deleting the photo
    deletes the copy."""
    A, c, sid, tmp = env
    from vault import backends as B
    import photostore
    store = tmp / "mirror"; store.mkdir(); B.LocalBackend.init_root(str(store))
    be = B.LocalBackend(str(store))
    data, mark = photo(2 * (1 << 20) + 5)
    upload(c, sid, "b.NEF", data)
    con = A.connect()
    assert photostore.mirror_pass(con, be) == (1, 0, 0)
    assert photostore.mirror_pass(con, be) == (0, 0, 0)                   # a mirror, not a new copy each night
    row = con.execute("SELECT * FROM photo_files").fetchone()
    names = [n_ for n_ in be.list(photostore.MIRROR) if n_.endswith(".lap")]
    assert len(names) == 1 and not leaked(mark, [str(store)], 0)          # the copy is the ciphertext
    photostore.path_of(row["id"]).unlink()
    assert photostore.mirror_pass(con, be) == (0, 1, 0)
    assert photostore.plain_bytes(row) == data
    photostore.delete(con, row)
    assert photostore.mirror_pass(con, be) == (0, 0, 1) and not [n_ for n_ in be.list(photostore.MIRROR) if n_.endswith(".lap")]
    con.close()
