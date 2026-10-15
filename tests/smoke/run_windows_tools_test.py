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
    from app.agent.tools import _execute_tool
    f3 = await _execute_tool("computer_find_files", {"query": str(folder / "sub" / "*.pdf")})
    check("…and by a full path with a pattern (C:\\…\\*.pdf), as models write it", f3.get("count") == 1, str(f3)[:300])

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

    # Telemetry without Mac commands (no os.statvfs, ps or sysctl on Windows).
    sc.get_system_telemetry()
    await asyncio.sleep(1.5)
    tel = sc.get_system_telemetry()
    check("disk telemetry (shutil, not os.statvfs)", (tel.get("disk") or {}).get("total_gb", 0) > 0, str(tel.get("disk")))
    procs = tel.get("top_processes") or []
    check("busiest processes (psutil, not ps)", len(procs) > 0 and all(p.get("command") for p in procs), str(procs)[:300])
    check("uptime is the time since boot (not today's date)", 0 < tel.get("uptime_seconds", 0) < 10 * 365 * 86400, str(tel.get("uptime_seconds")))

    # Desktop/Documents where Windows really keeps them (OneDrive moves them).
    from app import platform_info as pi
    kf = pi.known_folders()
    check("Windows says where Desktop, Documents and Downloads are", all(k in kf and os.path.isdir(kf[k]) for k in ("Desktop", "Documents", "Downloads")), str(kf))
    saved = dict(pi._known_cache)
    home = os.path.expanduser("~")
    pi._known_cache.update(Desktop=os.path.join(home, "OneDrive", "Desktop"))
    from app.agent.tools import _expand_path_args
    a = _expand_path_args({"path": "~/Desktop/notes.txt", "directory": "%USERPROFILE%\\Desktop", "query": "~/Desktop"})
    want = os.path.join(home, "OneDrive", "Desktop")
    check("a Desktop OneDrive moved: tool paths go to the real one", a["path"] == os.path.join(want, "notes.txt") and a["directory"] == want
          and a["query"] == "~/Desktop", str(a))
    check("…other folders and other drives are left alone", pi.real_folder("~/Downloads/x") == "~/Downloads/x" and pi.real_folder(r"D:\Desktop") == r"D:\Desktop")
    check("…and the model is told where it is", "OneDrive" in pi.folder_note())
    pi._known_cache.clear(); pi._known_cache.update(saved)

    # Xod's own browser is ended by its profile folder (no pkill/lsof on Windows); other browsers are left alone.
    import subprocess as sp
    from app.services import browser_sandbox as bs
    mine = sp.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={bs.DATA_DIR}"])
    other = sp.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={tempfile.gettempdir()}\\someone-else"])
    await asyncio.sleep(0.5)
    n = bs._kill_own_browser()
    await asyncio.sleep(0.5)
    check("a leftover Xod browser is ended, another browser isn't", n >= 1 and mine.poll() is not None and other.poll() is None, f"n={n} {mine.poll()} {other.poll()}")
    other.kill()

    # "Open Notepad": Start menu apps, Store apps too.
    from app.agent import operator
    apps = operator._windows_apps()
    first = next(iter(apps.values()), None)
    hit = operator.find_launch(f"open {first[0]}") if first else None
    check("Start menu apps are found by name ('open …')", bool(apps) and hit and hit["kind"] == "windows", f"{len(apps)} apps; {hit}")
    check("…but 'open my report.pdf' isn't an app", operator.find_launch("open the report.pdf") is None)

    # Folder sizes for the space review (no du on Windows).
    from app.services import mac_review
    big = Path(tempfile.mkdtemp(prefix="xod-size-"))
    (big / "a").mkdir()
    (big / "a" / "f.bin").write_bytes(b"\0" * 300_000)
    (big / "g.bin").write_bytes(b"\0" * 200_000)
    kb = mac_review._du_kb(big, 5)
    check("folder sizes are added up (no du)", kb is not None and 480 <= kb <= 500, str(kb))

    # The doctor checks Ollama for real, and offers to start Ollama (not LM Studio).
    from app.config import settings
    from app.services.doctor import Doctor
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()
    keep = (settings.llm_provider, settings.llm_base_url, settings.llm_model)
    settings.llm_provider, settings.llm_base_url, settings.llm_model = "ollama", f"http://127.0.0.1:{closed}/v1", "qwen2.5:7b"
    c = await Doctor()._check_chat_model()
    settings.llm_provider, settings.llm_base_url, settings.llm_model = keep
    check("Health: Ollama off is a failure, with 'Start Ollama'", c["level"] == "fail" and "isn't running" in c["detail"] and c["fix"] == "start_ollama", str(c))

    # Get to know me: the Windows sources.
    from app.services import user_memory
    ids = [x["id"] for x in user_memory.SOURCES]
    check("'Get to know me' has no Contacts on Windows, and says Windows", "contacts" not in ids and
          any("Windows" in x["label"] for x in user_memory.SOURCES), str(ids))
    try:
        user_memory._from_account(); user_memory._apps(); ok = True
    except Exception as e:
        ok = str(e)
    check("…the account name and app list read without errors", ok is True, str(ok))

    from app.services import soul
    check("battery check (psutil, not pmset)", isinstance(soul._on_power(), bool))

    # Captions on videos: Windows paths inside ffmpeg's filter (the drive's colon broke it).
    try:
        from app.services import media_studio as ms
        ff = await ms.ffmpeg_bin()
        clip = Path(tempfile.mkdtemp(prefix="xod caption ")) / "clip.mp4"
        sp.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5", "-t", "1", "-pix_fmt", "yuv420p", str(clip)], capture_output=True, check=True)
        o = await ms.LocalMediaService().add_text_overlay(str(clip), "50% done: it's ok", "bottom", 16)
        check("a caption on a video (Windows paths in the filter)", o.get("success") and Path(o["output_path"]).exists(), str(o)[:300])
    except Exception as e:
        check("a caption on a video (Windows paths in the filter)", False, str(e))

    # The page's scripts are stamped with their file's time, so an updated file is never served stale.
    from fastapi.testclient import TestClient
    from app.main import app as web
    import re as _re
    page = TestClient(web, base_url="http://127.0.0.1").get("/").text     # Xod only answers on localhost
    stamps = _re.findall(r'/static/js/[\w./-]+\.js\?v=(\w+)', page)
    check("scripts carry their file's change time (?v=…)", stamps and all(x.isdigit() for x in stamps), str(stamps[:5]))

    # ---- the agent: things a small model got wrong on Windows, held by Xod rather than by the model ----
    from app.paths import APP_ROOT
    for bad in ("...", ".", "", "receipt_file_path", os.path.expanduser("~"), os.path.join(os.path.expanduser("~"), "Desktop"), "C:\\",
                str(APP_ROOT), str(APP_ROOT / "app" / "main.py"), r"C:\Windows\System32\drivers\etc\hosts"):
        r = await sc.manage_file("trash", bad)
        if r.get("success") or not r.get("refused"):
            check(f"deleting {bad!r} is refused", False, str(r))
            break
    else:
        check("deleting a placeholder ('...'), a top folder, Xod itself or a system file is refused", True)
    check("…and Xod's code is still there", (APP_ROOT / "app" / "main.py").exists())
    ok_file = folder / "fine.txt"
    ok_file.write_text("x")
    check("…while an ordinary file still goes to the Recycle Bin", (await sc.manage_file("trash", str(ok_file))).get("success") and not ok_file.exists())

    from app.services.filesystem import filesystem_service
    browse = folder / "browser delete.txt"
    browse.write_text("x")
    d = filesystem_service.delete_item(str(browse))
    check("the file browser's Delete goes to the Recycle Bin (not erased)", d.get("success") and d.get("recoverable") and not browse.exists(), str(d))
    check("…and refuses the home folder", "error" in filesystem_service.delete_item(os.path.expanduser("~")))

    from app.agent import tools as T
    check("a plain question gets no tools that change files", not any(t["function"]["name"] in T.CHANGE_TOOLS
          for t in T._select_builtin_tools("What is the very last line of big.log in my Documents?")))
    check("…but 'Do it.' and 'add a line to it' aren't questions", not T.only_asks("Do it.") and not T.only_asks("Now add a second line to it"))
    log = folder / "big.log"
    log.write_text("\n".join(f"line {i}" for i in range(1, 9001)) + "\nTHE END\n", encoding="utf-8")
    tails = [(await T._execute_tool("computer_read_file", {"filepath": str(log), **a})).get("content", "").strip().endswith("THE END")
             for a in ({"tail": 1}, {"line_range": "last"}, {"start_line": -1})]
    full = await T._execute_tool("computer_read_file", {"filepath": str(log)})
    check("the end of a long file can be read (tail, 'last', -1), and a cut-off read shows its end", all(tails) and "THE END" in full.get("last_lines", ""), f"{tails} {full.get('last_lines', '')[-30:]}")
    notes = folder / "todo.txt"
    await T._execute_tool("computer_write_file", {"filepath": str(notes), "content": "buy bread\\nwalk the dog"})
    code = folder / "x.py"
    await T._execute_tool("computer_write_file", {"filepath": str(code), "content": 'print("a\\nb")'})
    check("'\\n' a model wrote becomes a real new line in a notes file, not in code",
          notes.read_text().splitlines() == ["buy bread", "walk the dog"] and code.read_text() == 'print("a\\nb")', notes.read_text())

    rec = folder / "Receipts"
    rec.mkdir()
    (rec / "a.pdf").write_bytes(b"%PDF a")
    (rec / "b.pdf").write_bytes(b"%PDF b")
    z = await T._execute_tool("computer_manage_file", {"action": "compress", "src": str(rec)})
    import zipfile
    zipped = z.get("success") and zipfile.ZipFile(z["dst"]).namelist() == ["Receipts/a.pdf", "Receipts/b.pdf"]
    u = await T._execute_tool("computer_manage_file", {"action": "extract", "src": z.get("dst", ""), "dst": str(folder / "out")})
    check("zip a folder and unzip it (as 'compress' / 'extract' too)", zipped and u.get("success") and (folder / "out" / "Receipts" / "b.pdf").exists(), f"{z} {u}")
    evil = folder / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escaped.txt", "x")
    e = await T._execute_tool("computer_manage_file", {"action": "unzip", "src": str(evil)})
    check("…a zip that would write outside its folder is refused", not e.get("success") and not (folder.parent / "escaped.txt").exists(), str(e))
    o = await T._execute_tool("computer_batch_organize", {"action": "zip", "src": str(rec)})
    check("a stray call to 'sort a folder' doesn't sort Downloads", not o.get("success"), str(o))

    from app.agent.researcher import AgenticResearcher as R
    from app.agent.builder import wants_build
    check("'Create todo.txt on my Desktop' writes a file (doesn't build a to-do app)", not wants_build("Create todo.txt on my Desktop with the line: buy bread")
          and wants_build("Make me a todo app"))
    rw = R()
    rw._carried["_found"] = ["Receipts", "Receipts.zip", "receipt1.pdf", "receipt2.pdf"]
    asks = lambda q, src: bool(rw._which_one("computer_manage_file", {"action": "trash", "src": src}, q))
    check("'delete the receipt' with several receipts (and a Receipts folder) asks which one",
          asks("Delete the receipt", r"C:\x\Receipts") and asks("Delete the receipt", r"C:\x\Receipts\receipt1.pdf")
          and not asks("Delete receipt2.pdf", r"C:\x\Receipts\receipt2.pdf") and not asks("Delete both receipts", r"C:\x\Receipts\receipt1.pdf"))
    cw = await T._execute_tool("computer_clipboard", {"action": "write", "content": "Xod clip via content"})
    check("copying to the clipboard works whatever the model calls the text", cw.get("success") and (await sc.read_clipboard()).get("text") == "Xod clip via content", str(cw))
    r0 = R()
    check("questions about this PC skip the quick web answer", not r0._fast_lane_ok("Which programs are using the most memory on this PC right now?", False, False, None)
          and r0._fast_lane_ok("What's the weather in Tokyo right now?", False, False, None))
    check("'now add a line to it' goes to the tools, not a quick (made-up) answer", not r0._fast_lane_ok("Now add a second line to it: walk the dog", False, False, None)
          and r0._fast_lane_ok("Add 2 and 2", False, False, None))
    check("a quick answer claiming it changed a file is caught", R._CLAIMS_DONE_RE.search("I have added the second line to your todo.txt file.")
          and R._CLAIMS_DONE_RE.search("The file has been created.") and not R._CLAIMS_DONE_RE.search("Recursion is when a function calls itself."))
    from app.agent import operator as op
    multi = {"content": '{\n  "id": "x-apple-reminder://aa11",\n  "title": "Buy milk"\n}\n{\n  "id": "x-apple-reminder://bb22",\n  "title": "Buy eggs"\n}'}
    seen = {}
    op._remember_items(multi, seen)
    fixed = [op._fix_ids({"reminder_id": v}, seen)["reminder_id"] for v in ("aa11", "Buy eggs", "x-apple-reminder:/")]
    check("connected apps: a list of items is read (one JSON object after another)", len(seen) == 2, str(seen))
    check("…a mistyped id or a title becomes the real id, an ambiguous one doesn't", fixed == ["x-apple-reminder://aa11", "x-apple-reminder://bb22", "x-apple-reminder:/"], str(fixed))
    a1, err1 = op._check_ids("mcp__mac__reminders_update", {"reminder_id": "1234567890"}, seen, "check off the milk")
    a2, err2 = op._check_ids("mcp__mac__reminders_update", {"reminder_id": "1234567890"}, seen, "check off that one")
    check("…a made-up id becomes the item the request names, or comes back with the real list",
          a1["reminder_id"] == "x-apple-reminder://aa11" and not err1 and err2 and len(err2.get("items") or []) == 2, f"{a1} {err1} {err2}")
    check("…a reply that says 'press Approve' without a tool call is caught", op.SOUNDS_DONE_RE.search("Ready to delete the reminder: press **Approve** on the card above")
          and op.ASKS_CHANGE_RE.search("delete the eggs reminder"))
    net = sc._network()
    check("the local IP address is the real one (not 169.254…)", net.get("local_ip") and not net["local_ip"].startswith(("169.254", "127.")), str(net))

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
