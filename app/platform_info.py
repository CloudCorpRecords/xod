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

SHELL_NOTE = ("Terminal commands run in Windows PowerShell on this PC (use PowerShell syntax: Get-ChildItem, Select-String, $env:USERPROFILE, ';' "
              "between commands; Get-CimInstance, not wmic, which newer Windows doesn't have). AppleScript and Mac apps aren't available here. "
              "For this PC's hardware (memory, graphics card) use computer_hardware_overview rather than a command. "
              # File tools take paths, not PowerShell: give the model the real home folder so it doesn't write $env:... there.
              f"In file tools, use real paths: the user's home folder is {os.path.expanduser('~')} "
              f"(Downloads is {os.path.join(os.path.expanduser('~'), 'Downloads')}); '~' works too.") if WINDOWS else ""
