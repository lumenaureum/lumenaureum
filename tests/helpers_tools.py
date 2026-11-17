import os
import shutil

import pytest

def need_tool(name):

    path = shutil.which(name)
    if path:
        return path
    msg = f"{name} is not installed"
    if os.environ.get("CI") or os.environ.get("LA_REQUIRE_TOOLS"):
        pytest.fail(msg + ": CI and the release check require it", pytrace=False)
    pytest.skip(msg)
