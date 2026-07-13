#!/usr/bin/env python3
"""Run the real Foglight static/HTTP shell with isolated temporary state."""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19876)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    runtime = Path(tempfile.mkdtemp(prefix="foglight-browser-tests-"))
    atexit.register(shutil.rmtree, runtime, True)

    os.environ["FOGLIGHT_APP_DIR"] = str(root)
    os.environ["FOGLIGHT_CACHE_DIR"] = str(runtime / "cache")
    os.environ["FOGLIGHT_STATE_DIR"] = str(runtime / "state")
    os.environ["FOGLIGHT_LOG_DIR"] = str(runtime / "logs")

    import foglight_server

    sys.argv = [sys.argv[0], str(args.port)]
    foglight_server.main()


if __name__ == "__main__":
    main()
