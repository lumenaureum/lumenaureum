import os
import secrets
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_open_and_close_from_many_threads_never_freezes(tmp_path):
    import db as D
    p, key = str(tmp_path / "t.db"), secrets.token_bytes(32)
    c = D.connect(p, key=key)
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    n, stop = [0, 0, 0], threading.Event()

    def work(i):
        while not stop.is_set():
            c_ = D.connect(p, key=key)
            c_.execute("SELECT count(*) FROM t").fetchone()
            if n[i] % 3 == 0:
                c_.execute("INSERT INTO t VALUES (?)", (i,))
                c_.commit()
            c_.close()
            n[i] += 1
    ts = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(3)]
    for t in ts:
        t.start()
    seen = []
    for _ in range(4):
        time.sleep(1)
        seen.append(sum(n))
    stop.set()
    assert all(b > a for a, b in zip(seen, seen[1:])), seen
