import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "plugins" / "sealed_assets" / "sealed.js"
COPIES = (REPO / "plugins" / "gallery_assets" / "viewer.html", REPO / "plugins" / "studio_assets" / "inquiry.js")
BEGIN, END = "// sealed.js begin\n", "// sealed.js end\n"

def block():
    src = SOURCE.read_text()
    if not (src.startswith(BEGIN) and src.endswith(END)):
        raise SystemExit("sealed.js must start with the begin line and end with the end line")
    return src

def embedded(text):

    i = text.find(BEGIN)
    j = text.find(END, i + 1)
    if i < 0 or j < 0 or text.count(BEGIN) != 1 or text.count(END) != 1:
        return None
    return text[i:j + len(END)]

def synced(text):
    have = embedded(text)
    if have is None:
        raise SystemExit("no single sealed.js block to replace")
    return text.replace(have, block(), 1)

def main(argv):
    bad = []
    for p in COPIES:
        text = p.read_text()
        if "--check" in argv:
            if embedded(text) != block():
                bad.append(p)
        else:
            new = synced(text)
            if new != text:
                p.write_text(new)
                print("updated", p.name)
    if bad:
        print("out of date:", ", ".join(p.name for p in bad))
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
