"""
Xod's computer tools on Windows (services/system_control_win.py, speech.py, platform_info.py), on a real Windows PC.
CI runs it on a clean Windows machine; on other systems it says so and passes.

  python tests/smoke/run_windows_tools_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp(prefix="xod-wintools-"))
os.environ.setdefault("XOD_SECRETS_SERVICE", "Xod test keys")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


async def main():
    if sys.platform != "win32":
        print("Not Windows: nothing to check here.\n\nSUMMARY PASS []")
        return
    from app.services.system_control import system_control_service as sc

    r = await sc.run_command("Get-Date -Format yyyy; $PSVersionTable.PSVersion.Major")
    check("commands run in PowerShell", r.get("exit_code") == 0 and r.get("stdout", "").strip()[:2] == "20", str(r)[:300])
    work = tempfile.mkdtemp(prefix="xod cd test ")       # a space in the name, like real Windows folders
    r = await sc.run_command(f'cd "{work}"')
    r2 = await sc.run_command("(Get-Location).Path")
    # (Windows may write the temp folder with its short 8.3 name, RUNNER~1: compare the folder's own name.)
    check("cd sticks between commands (folders with spaces too)", os.path.basename(work).lower() in r2.get("stdout", "").lower(), f"{r} {r2}"[:400])

    w = await sc.write_clipboard("Xod clipboard ✓ héllo")
    rd = await sc.read_clipboard()
    check("clipboard round-trip (with accents and symbols)", w.get("success") and rd.get("text") == "Xod clipboard ✓ héllo", f"{w} {rd}")

    folder = Path(tempfile.mkdtemp(prefix="xod-find-"))
    (folder / "sub").mkdir()
    (folder / "sub" / "Quarterly Report 2026.pdf").write_bytes(b"%PDF-1.4")
    f = await sc.find_files("quarterly report", str(folder))
    f2 = await sc.find_files("*.pdf", str(folder))
    check("finding files by name and by pattern", f.get("count") == 1 and f2.get("count") == 1, f"{f} {f2}"[:300])

    victim = folder / "delete me.txt"
    victim.write_text("bye")
    t = await sc.manage_file("trash", str(victim))
    check("'trash' goes to the Recycle Bin", t.get("success") and not victim.exists(), str(t))

    hw = sc.get_hardware_overview()
    check("hardware overview", hw.get("success") and hw.get("ram_gb", 0) > 0 and "Windows" in hw.get("os", ""), str(hw)[:300])
    ports = sc.get_listening_ports()
    check("listening ports (psutil)", isinstance(ports, list), str(ports)[:200])
    a = await sc.run_applescript('tell application "Finder" to activate')
    check("AppleScript says it's Mac only (and points at PowerShell)", not a.get("success") and "PowerShell" in a.get("error", ""))
    tel = sc.get_system_telemetry()
    check("memory telemetry", (tel.get("memory") or {}).get("total_gb", 0) > 0, str(tel.get("memory")))

    from app.agent.tools import TOOL_DEFINITIONS
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    descs = " ".join(t["function"].get("description", "") for t in TOOL_DEFINITIONS if t["function"]["name"].startswith("computer_"))
    check("tools: no AppleScript, and they talk about File Explorer and PowerShell", "computer_run_applescript" not in names
          and "PowerShell" in descs and "File Explorer" in descs and "Finder" not in descs, descs[:300])
    from app.agent.prompts import PERSONA_PROMPTS
    check("the agent's instructions say Windows", "Windows" in PERSONA_PROMPTS["ultimate_researcher"] and "macOS" not in PERSONA_PROMPTS["ultimate_researcher"])

    from app.services import speech
    voices = await speech.list_voices(refresh=True)
    check("Windows voices are listed", len(voices) > 0, str(voices))
    if voices:
        wav = await speech.synthesize("Hello from Xod on Windows.")
        check("a spoken reply is made (WAV)", wav[:4] == b"RIFF" and len(wav) > 10000, str(len(wav)))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
