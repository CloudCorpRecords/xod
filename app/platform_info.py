"""What system Xod is running on, in words for prompts and tool descriptions.

The agent's instructions were written on a Mac (Finder, Spotlight, the Trash, zsh). On Windows, localize() rewrites
them so the model reaches for File Explorer, the Recycle Bin and PowerShell instead, and never for AppleScript.
"""
import re
import sys

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
OS_NAME = "Windows" if WINDOWS else "macOS" if MAC else "Linux"
COMPUTER = "PC" if WINDOWS else "Mac" if MAC else "computer"
MAC_ONLY_TOOLS = {"computer_run_applescript"}

_WIN_WORDS = [
    (r"\bmacOS Finder\b", "File Explorer"), (r"\bFinder\b", "File Explorer"),
    (r"\bmacOS Trash\b", "Recycle Bin"), (r"\bthe Trash\b", "the Recycle Bin"), (r"\bTrash\b", "Recycle Bin"),
    (r"native macOS Spotlight indexing \(mdfind\)", "a fast search of the folder"),
    (r"Spotlight search", "file search"), (r"via Spotlight \(`mdfind`\)", "by name"), (r"\bSpotlight\b", "file search"),
    (r"\(`pbpaste`/`pbcopy`\)", ""), (r"Apple Silicon/Intel chip", "CPU"),
    (r"\bmacOS\b", "Windows"), (r"\bthe user's Mac\b", "the user's PC"), (r"\byour Mac\b", "your PC"), (r"\bthe Mac\b", "the PC"),
    (r"'~/\.zshrc', ", ""), (r"\bthis Mac\b", "this PC"), (r"\bhost Mac\b", "host PC"), (r"\bMac\b(?= (Desktop|Documents|Downloads|files|apps|screen))", "PC"), (r"\bon a Mac\b", "on a PC"),
    (r"call `computer_run_applescript`", "call `computer_run_command` with a PowerShell command"),
    (r"`computer_run_applescript`[^\n]*", "`computer_run_command`: runs Windows PowerShell commands."),
]


def localize(text: str) -> str:
    """Mac wording → this system's (unchanged on a Mac)."""
    if not WINDOWS or not isinstance(text, str):
        return text
    for pat, rep in _WIN_WORDS:
        text = re.sub(pat, rep, text)
    return text


import os  # noqa: E402

# Windows can keep Desktop, Documents, etc. somewhere other than the home folder: OneDrive backup moves them to
# C:\Users\you\OneDrive\Desktop (often leaving an empty C:\Users\you\Desktop behind), and people move them to other
# drives. Windows says where they really are.
_KNOWN = {"Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
          "Downloads": "374DE290-123F-4565-9164-39C4925E467B", "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
          "Music": "4BD8D571-6D19-48D3-BE97-422220080E43", "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}
_known_cache: dict = {}


def known_folders() -> dict:
    """{"Desktop": real path, ...} on Windows (from SHGetKnownFolderPath); {} elsewhere or if Windows won't say."""
    if not WINDOWS:
        return {}
    if _known_cache:
        return _known_cache
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    for name, gid in _KNOWN.items():
        try:
            u = uuid.UUID(gid)
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                _known_cache[name] = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
        except Exception:
            continue
    return _known_cache


_ALIASES = {"movies": "Videos", "my documents": "Documents"}


def real_folder(path: str) -> str:
    """~/Desktop/x, C:\\Users\\you\\Desktop\\x → where Windows really keeps the Desktop, + \\x. Other paths (and every
    path on a Mac) come back as they were. Only rewrites when the folder really lives elsewhere."""
    folders = known_folders()
    if not folders or not isinstance(path, str) or not path:
        return path
    home = os.path.expanduser("~")
    full = os.path.expanduser(path)
    rel = os.path.relpath(full, home) if os.path.isabs(full) and os.path.normcase(full).startswith(os.path.normcase(home + os.sep)) else None
    if not rel:
        return path
    first, _, rest = rel.replace("/", "\\").partition("\\")
    name = next((k for k in folders if k.lower() == first.lower()), _ALIASES.get(first.lower()))
    real = folders.get(name) if name else None
    if not real or os.path.normcase(real) == os.path.normcase(os.path.join(home, first)):
        return path
    return os.path.join(real, rest) if rest else real


def folder_note() -> str:
    """The real Desktop/Documents/Downloads, for the model, when any of them isn't in the home folder."""
    home = os.path.expanduser("~")
    moved = {k: v for k, v in known_folders().items() if os.path.normcase(v) != os.path.normcase(os.path.join(home, k))}
    return (" On this PC these folders are not in the home folder: " + "; ".join(f"{k} is {v}" for k, v in moved.items()) + ".") if moved else ""


SHELL_NOTE = ("Terminal commands run in Windows PowerShell on this PC (use PowerShell syntax: Get-ChildItem, Select-String, $env:USERPROFILE, ';' "
              "between commands; Get-CimInstance, not wmic, which newer Windows doesn't have). AppleScript and Mac apps aren't available here. "
              "For this PC's hardware (memory, graphics card) use computer_hardware_overview rather than a command. "
              # File tools take paths, not PowerShell: give the model the real home folder so it doesn't write $env:... there.
              f"In file tools, use real paths: the user's home folder is {os.path.expanduser('~')} "
              f"(Downloads is {known_folders().get('Downloads') or os.path.join(os.path.expanduser('~'), 'Downloads')}); '~' works too."
              + folder_note()) if WINDOWS else ""
