"""strftime patterns without leading zeros ("3:05 PM", "Oct 4") on every system: "%-I" is "%#I" on Windows."""
import sys


def nopad(pattern: str) -> str:
    return pattern.replace("%-", "%#") if sys.platform == "win32" else pattern
