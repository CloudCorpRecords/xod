"""
Where Xod keeps things.

Code lives in the repository (or inside Xod.app). Everything personal lives outside it, in one
private folder that only your user account can read:

  macOS   ~/Library/Application Support/Xod
  other   $XDG_DATA_HOME/xod  (default ~/.local/share/xod)

Set XOD_DATA_DIR to use another folder (tests do this to stay away from real data).
Inside: xod.db (chats, memories, tasks, settings), projects/, models/, loras/, media_studio/,
the agent's browser profile, training data, logs, and .env for non-secret settings.
API keys and saved logins are not here; they are in the macOS Keychain.
"""
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent      # folder that contains app/


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Xod"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "xod"


DATA_DIR = Path(os.environ.get("XOD_DATA_DIR") or _default_data_dir()).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
except OSError:
    pass

ENV_PATH = DATA_DIR / ".env"            # settings the app writes
DEV_ENV_PATH = APP_ROOT / ".env"        # optional .env in a source checkout (read, keys moved to Keychain)
