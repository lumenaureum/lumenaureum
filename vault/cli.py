USAGE = """Command line for the vault. The master key is read from the terminal (not echoed), used once, never stored.

    python -m vault.cli backups                 list sealed database backups (newest first)
    python -m vault.cli restore <name> <dir> [--from <vault folder>] [--settings-only]
                                                open a sealed backup into a NEW folder (crm.db + keyring.json) with
                                                the master key alone. On a new machine (no install), copy or mount the
                                                vault folder and pass it with --from.
    python -m vault.cli new-master              replace the master key (it was lost or may have leaked): asks for the
                                                password and the OLD master key, shows the new one once
    python -m vault.cli rotate                  new data key: re-encrypt the database, faces and storage settings
                                                (stop the app first; asks for the password and the master key)
    python -m vault.cli backup                  seal a backup now
    python -m vault.cli init-store <dir>        mark a local/NAS folder as a vault root (refused until marked)
    python -m vault.cli orphans [--from <vault folder>]
                                                sealed session archives this database doesn't know (after restoring an
                                                older backup), with what each is; the master key opens them
    python -m vault.cli open-orphan <archive> <dir> [--from <vault folder>]
                                                open one of those archives into a NEW folder with the master key
    python -m vault.cli recover-photos <dir> [--from <photos folder>]
                                                uploaded photos this database doesn't know: decrypted into a NEW folder
                                                with the master key (each photo's key is sealed beside it)
    python -m vault.cli relink-photos           after restoring a backup older than a key rotation: puts each photo's
                                                current key (sealed beside it to the master key) back into the database,
                                                only when it traces back to the key this database holds
"""
import getpass
import os
import sys

from . import backup as BK

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datakey
import db as D

DB = os.environ.get("CRM_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crm.db"))

def _unlock():

    if datakey.is_unlocked():
        return
    secret = getpass.getpass("Password (or the master key): ")
    key = datakey.unwrap_master(secret) if secret.strip().startswith("AGE-SECRET-KEY-") else datakey.unwrap_password(secret)
    if not key:
        sys.exit("That password doesn't open this install.")
    datakey.set_unlocked(key)

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE); return 0
    cmd = argv[0]
    if cmd == "init-store" and len(argv) == 2:
        from . import backends as B
        B.LocalBackend.init_root(argv[1]); print("vault root ready:", argv[1]); return 0
    if cmd == "backups":
        _unlock()
        con = D.connect(DB)
        for name, kind, created, size in con.execute("""SELECT name, kind, MIN(created), MAX(size) FROM vault_objects
                WHERE kind IN ('db','faces','reset','manual','manual-faces','upgrade','upgrade-faces','found') AND deleted IS NULL
                GROUP BY name ORDER BY MAX(id) DESC"""):
            print(f"{created}  {kind:13}  {(size or 0):>12,}  {name}")
        return 0
    if cmd == "backup":
        _unlock()
        print("\n".join(BK.sealed_backup(DB, os.path.join(os.path.dirname(DB), "faces"))))
        return 0
    settings_only = "--settings-only" in argv
    argv = [a_ for a_ in argv if a_ != "--settings-only"]
    if cmd == "restore" and len(argv) in (3, 5) and (len(argv) == 3 or argv[3] == "--from"):
        from . import backends as B
        secret = getpass.getpass("Master key: ")
        if len(argv) == 5:
            backend = B.LocalBackend(argv[4], read_only=True)
        elif datakey.exists():
            datakey.set_unlocked(datakey.unwrap_master(secret))
            backend = None
            try:
                c_ = D.connect(DB)
                expected = BK.recorded_sha(c_, argv[1])
                c_.close()
            except Exception:
                expected = None
        else:
            print("No install here: copy or mount the vault folder and add --from <folder>."); return 2
        if backend is not None:
            expected = None
            print("No record of this backup on this machine to compare with: check its date and contents before you use it.")
        BK.restore(argv[1], secret, argv[2], backend=backend, settings_only=settings_only, expected_sha=expected)
        if settings_only:
            print("restored", os.path.join(argv[2], "settings.json"), "- import it in Settings (Import settings)")
        else:
            print("restored to", argv[2], "(crm.db, keyring.json, and settings.json, storage.json, deletions.json and "
                  "doc-numbers.json when the backup has them; the master key opens it, and so does the password that was "
                  "current when the backup was made). See Restoring a backup in docs/operations.md for the next steps.")
        return 0
    if cmd in ("orphans", "open-orphan"):
        from . import backends as B, seal as S, web as W
        frm = argv[argv.index("--from") + 1] if "--from" in argv else None
        args = [a_ for i_, a_ in enumerate(argv) if a_ != "--from" and (i_ == 0 or argv[i_ - 1] != "--from")]
        secret = getpass.getpass("Master key: ")
        if frm:
            backend, con = B.LocalBackend(frm, read_only=True), None
        else:
            datakey.set_unlocked(datakey.unwrap_master(secret))
            backend, con = W.store_backends()[0], D.connect(DB)
        if cmd == "orphans":
            found = S.orphans(con, secret, backend)
            for m in found:
                print(f"{m['made'][:10]}  {m['archive']}  {m['label']}")
            print(f"{len(found)} archive(s) not in this database.")
            return 0
        if len(args) == 3:
            m = S.open_orphan(args[1], secret, backend, args[2])
            print(f"opened {m['label']} into {args[2]}")
            return 0
    if cmd == "relink-photos":
        import json
        import pyrage
        import photostore as P
        from . import keys as K
        secret = getpass.getpass("Master key: ")
        ident = K.parse_secret(secret)
        datakey.set_unlocked(datakey.unwrap_master(secret))
        con = D.connect(DB)
        P.ensure_trail(con)
        fixed = same = missing = bad = untraced = 0

        class _Null:
            def write(self, b):
                return len(b)
        for r in con.execute("SELECT id, fkey, size FROM photo_files WHERE state='done'").fetchall():
            kp = P.key_path(r[0])
            if not kp.exists():
                missing += 1
                continue
            m = json.loads(pyrage.decrypt(kp.read_bytes(), [ident]))
            if m.get("id") != r[0] or int(m.get("size") or -1) != int(r[2]):
                continue
            if m["fkey"] == r[1]:
                same += 1
                continue

            import hmac
            want = P.trail_tag(r[1], r[0])
            trail = m.get("trail") if isinstance(m.get("trail"), list) else []
            if not any(isinstance(t_, str) and hmac.compare_digest(t_, want) for t_ in trail):
                print(f"skipped {r[0]}: its key record does not trace back to the key this database holds "
                      "(recover-photos <folder> --from <photos folder> still opens it with the master key)")
                untraced += 1
                continue
            try:
                P.read_file_plain(P.path_of(r[0]), r[0], m["fkey"], _Null(), size=r[2])
            except Exception:
                print(f"skipped {r[0]}: its key record does not open the photo")
                bad += 1
                continue
            con.execute("UPDATE photo_files SET fkey=?, mirrored=NULL, key_trail=? WHERE id=?",
                        (m["fkey"], json.dumps([t_ for t_ in trail if isinstance(t_, str)]), r[0]))
            fixed += 1
        con.commit()
        con.close()
        print(f"{fixed} photo key(s) restored, {same} already right, {missing} with no sealed key beside them"
              + (f", {bad} refused (the key did not open the photo)" if bad else "")
              + (f", {untraced} refused (the key record did not trace back to this database)" if untraced else "") + ".")
        return 0
    if cmd == "recover-photos" and len(argv) in (2, 4):
        import json
        import pyrage
        from pathlib import Path
        import photostore as P
        from . import keys as K
        root = Path(argv[3]) if len(argv) == 4 and argv[2] == "--from" else P.ROOT
        if (root / "sealed").is_dir():
            root = root / "sealed"
        secret = getpass.getpass("Master key: ")
        ident = K.parse_secret(secret)
        known = set()
        if datakey.exists() and len(argv) == 2:
            datakey.set_unlocked(datakey.unwrap_master(secret))
            con = D.connect(DB)
            known = {r[0] for r in con.execute("SELECT id FROM photo_files")} if con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='photo_files'").fetchone() else set()
            con.close()
        n = failed = 0
        for kp in sorted(root.glob("*/*.key")) + sorted(root.glob("*.key")):
            lap = kp.with_suffix(".lap")
            if kp.stem in known or not lap.exists():
                continue
            try:
                m = json.loads(pyrage.decrypt(kp.read_bytes(), [ident]))
            except Exception as e:
                print(f"skipped {kp.name}: its key record could not be opened ({e.__class__.__name__})")
                failed += 1
                continue
            if str(m.get("id")) != kp.stem:
                print(f"skipped {kp.name}: its key record names another file")
                continue

            base = Path(argv[1]).resolve()
            label = P.clean_name(str(m.get("label") or f"session {m.get('session')}"))
            kind = m.get("kind") if m.get("kind") in P.KINDS else "other"
            out = (base / label / kind / P.clean_name(str(m.get("name") or kp.stem))).resolve()
            if base not in out.parents:
                print(f"skipped {kp.name}: unsafe path")
                continue
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stem_, k_ = out.stem, 2
            while out.exists():
                out = out.with_name(f"{stem_} ({k_}){out.suffix}")
                k_ += 1
            try:
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    P.read_file_plain(lap, m["id"], m["fkey"], f, size=m.get("size"))
            except Exception as e:
                print(f"skipped {kp.name}: the photo did not decrypt ({e.__class__.__name__}); nothing kept from it")
                out.unlink(missing_ok=True)
                failed += 1
                continue
            n += 1
            print(out)
        print(f"{n} photo(s) recovered." + (f" {failed} could not be (listed above)." if failed else ""))
        return 0
    if cmd == "new-master":
        from . import keys as K
        _unlock()
        old = getpass.getpass("OLD master key: ")
        try:
            ok = datakey.unwrap_master(old) == datakey.dk()
        except ValueError:
            ok = False
        if not ok:
            sys.exit("That isn't the current master key for this install. Nothing was changed.")
        secret, pub = K.generate_master()
        con = D.connect(DB)
        try:
            n = K.replace_master(con, old, pub)
        finally:
            con.close()
        datakey.replace_master(pub)
        print("\nYour NEW master key (shown once; save it in your password manager and print it):\n\n  " + secret + "\n")
        print(f"{n} sealed session key(s) moved to it. Backups made BEFORE now still need the OLD key until they age")
        print("out of the vault (30 days); keep the old key until then, then destroy it.")
        return 0
    if cmd == "rotate":
        import rotate
        pw = getpass.getpass("Password: ")
        key = datakey.unwrap_password(pw)
        if not key:
            sys.exit("That password doesn't open this install.")
        try:
            rotate.rotate(key, pw)
        except rotate.AppRunning as e:
            sys.exit(str(e))
        print("New data key in place. Older backups still open with the master key.")
        return 0
    print(USAGE); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
