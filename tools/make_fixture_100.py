import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixture

if __name__ == "__main__":
    sys.exit(make_fixture.main(["--version", "1.0.0"] + sys.argv[1:]))
