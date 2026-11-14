import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

class Store:

    def __init__(self, kind, put_fails=False, size_off=0, corrupt=False):
        self.kind, self.put_fails, self.size_off, self.corrupt = kind, put_fails, size_off, corrupt
        self.objects, self.deleted = {}, []

    def put_stream(self, name, reader, content_type=None):
        import hashlib
        data = b""
        while True:
            b = reader.read(65536)
            if not b:
                break
            data += b
            if self.put_fails:
                raise IOError("connection dropped")
        if self.corrupt:
            data = bytes([data[0] ^ 1]) + data[1:]
        self.objects[name] = data
        return len(data), hashlib.sha256(data).hexdigest()

    def get_stream(self, name, writer):
        writer.write(self.objects[name])

    def stat(self, name):
        return len(self.objects[name]) + self.size_off if name in self.objects else None

    def delete(self, name):
        self.deleted.append(name)
        self.objects.pop(name, None)

@pytest.fixture
def seal():
    fresh_modules()
    from vault import keys as K, seal as S
    _, pub = K.generate_master()
    return S, [K.recipient(pub)]

def _reader(S, data=b"payload " * 5000):
    return lambda: (io.BytesIO(data), S._noop(), {})

def test_a_failed_upload_raises_and_deletes(seal):
    S, rcpt = seal
    a = Store("local", put_fails=True)
    with pytest.raises(IOError):
        S.put_sealed([a], "db/x.age", _reader(S), rcpt)
    assert a.deleted == ["db/x.age"] and not a.objects

def test_a_short_landing_raises_and_deletes(seal):
    S, rcpt = seal
    a = Store("local", size_off=-1)
    with pytest.raises(IOError, match="stored size"):
        S.put_sealed([a], "db/x.age", _reader(S), rcpt)
    assert "db/x.age" in a.deleted and not a.objects

def test_a_second_copy_that_differs_is_removed_everywhere(seal):
    S, rcpt = seal
    a, b = Store("local"), Store("sftp", corrupt=True)
    with pytest.raises(IOError, match="did not verify"):
        S.put_sealed([a, b], "db/x.age", _reader(S), rcpt)
    assert not a.objects and not b.objects
    assert "db/x.age" in a.deleted and "db/x.age" in b.deleted

def test_a_second_copy_that_fails_is_removed_everywhere(seal):
    S, rcpt = seal
    a, b = Store("local"), Store("s3", put_fails=True)
    with pytest.raises(IOError, match="did not verify"):
        S.put_sealed([a, b], "db/x.age", _reader(S), rcpt)
    assert not a.objects and not b.objects

def test_a_second_copy_that_lands_short_is_removed_everywhere(seal):
    S, rcpt = seal
    a, b = Store("local"), Store("s3", size_off=5)
    with pytest.raises(IOError):
        S.put_sealed([a, b], "db/x.age", _reader(S), rcpt)
    assert not a.objects and not b.objects

def test_a_failing_source_raises_and_deletes(seal):
    S, rcpt = seal

    def broken(wf):
        wf.write(b"partial")
        raise RuntimeError("the folder vanished")
    a = Store("local")
    with pytest.raises(RuntimeError, match="vanished"):
        S.put_sealed([a], "s/x.age", lambda: S._pipe_thread(broken), rcpt)
    assert "s/x.age" in a.deleted and not a.objects

def test_good_copies_match(seal):
    S, rcpt = seal
    a, b = Store("local"), Store("sftp")
    done = S.put_sealed([a, b], "db/x.age", _reader(S), rcpt)
    assert [d[0] for d in done] == [a, b] and done[0][1:] == done[1][1:]
    assert a.objects["db/x.age"] == b.objects["db/x.age"]
