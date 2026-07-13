import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_RUNTIME = Path(tempfile.mkdtemp(prefix="foglight-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("cache", "state", "logs"):
    (_RUNTIME / name).mkdir()

os.environ["FOGLIGHT_APP_DIR"] = str(Path(__file__).resolve().parents[1])
os.environ["FOGLIGHT_CACHE_DIR"] = str(_RUNTIME / "cache")
os.environ["FOGLIGHT_STATE_DIR"] = str(_RUNTIME / "state")
os.environ["FOGLIGHT_LOG_DIR"] = str(_RUNTIME / "logs")
os.environ["FOGLIGHT_SESSION_TOKEN"] = "foglight-test-session-token"


@atexit.register
def _cleanup_runtime():
    shutil.rmtree(_RUNTIME, ignore_errors=True)
