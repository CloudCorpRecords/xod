"""
Moving a built app around by asking (agent/builder.py, manage mode): "move it into a folder called games",
"rename it to …", "copy it", "zip it up", "delete the game" (asks first), "show it in Finder", "open main.js in
VS Code", "open it in Chrome", plus routing: game requests and follow-ups go to the builder, not the planner.

A throwaway app in a temp folder and a throwaway data folder. Move, copy, rename and zip use Xod's real file
tools (so Activity records them); Trash, Finder and opening apps are recorded, not done. No model needed:
  .venv/bin/python tests/smoke/run_builder_manage_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-manage-", dir="/private/tmp"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"

from app.agent import builder  # noqa: E402
from app.agent.tools import execute_tool as real_execute  # noqa: E402

results = []
calls = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


async def execute(name, args):
    calls.append((name, args))
    if name in ("computer_reveal_finder", "computer_open_item") or (name == "computer_manage_file" and args.get("action") == "trash"):
        return {"success": True}
    return await real_execute(name, args)


def history(app_dir, extra=None):
    return [{"role": "user", "content": "make a 3d game"},
            {"role": "assistant", "content": "Built. " + builder.MARK + json.dumps({"path": str(app_dir), "title": "Strike Ops", **(extra or {})}) + " -->"}]


async def say(q, app_dir, extra=None):
    msgs = history(app_dir, extra) + [{"role": "user", "content": q}]
    d = builder.route(q, msgs, None)
    if not d:
        return None, ""
    text, events = "", []
    async for ev in builder.stream(q, msgs, None, execute, d):
        events.append(ev)
        if ev["type"] == "content":
            text += ev["content"]
    return d, text


def where(text):
    return json.loads(text.split(builder.MARK, 1)[1].split("-->", 1)[0])


async def main():
    for q in ["lets make a video game", "i want a racing game", "make me a 3d game like call of duty",
              "can you create a new folder in documents for the game and then make a 3d game like call of duty?"]:
        check(f"'{q}' goes to the builder", (builder.route(q, [{"role": "user", "content": q}], None) or {}).get("mode") == "new")
    for q in ["what's the best racing game to buy", "i want a game recommendation for my switch"]:
        check(f"'{q}' doesn't", builder.route(q, [{"role": "user", "content": q}], None) is None)
    check("games default to three.js", builder.is_three({}, "make a snake game") and not builder.is_three({}, "make a snake game with phaser"))
    check("'a new folder in documents' puts it in Documents", builder.PLACE_RE.search("create a new folder in documents for the game").group(1).lower() == "documents")

    app = TMP / "Projects" / "strike-ops"
    app.mkdir(parents=True)
    (app / "index.html").write_text("<!doctype html><title>x</title><script type=module src=main.js></script>")
    (app / "main.js").write_text("console.log('hi')")
    (app / ".xod-app.json").write_text(json.dumps({"title": "Strike Ops", "three": True}))

    for q in ["launch it", "start!", "test it", "make it harder"]:
        d = builder.route(q, history(app) + [{"role": "user", "content": q}], None)
        check(f"after a build, '{q}' stays with the builder ({d and d['mode']})", bool(d) and d["mode"] in ("launch", "run", "change"))

    d, text = await say("move it into a folder called games", app)
    moved = TMP / "Projects" / "games" / "strike-ops"
    check("'move it into a folder called games' moves it", d and d["mode"] == "manage" and (moved / "main.js").is_file() and not app.exists(), text[:200])
    check("…and the next message knows where it is now", where(text)["path"] == str(moved))
    from app.services import activity
    acts = activity.recent(10) if hasattr(activity, "recent") else []
    check("…and it's in Activity (for Undo)", not hasattr(activity, "recent") or any("strike-ops" in json.dumps(a) for a in acts), json.dumps(acts)[:300])

    d, text = await say("rename it to Strike Ops 2", moved)
    renamed = moved.parent / "Strike Ops 2"
    check("'rename it to Strike Ops 2' renames the folder", renamed.is_dir() and not moved.exists(), text[:200])

    d, text = await say("copy it", renamed)
    check("'copy it' makes a copy next to it", (renamed.parent / "Strike Ops 2 copy" / "main.js").is_file() and renamed.is_dir(), text[:200])

    d, text = await say("zip it up", renamed)
    check("'zip it up' makes a zip next to it", (renamed.parent / "Strike Ops 2.zip").is_file(), text[:200])

    d, text = await say("move it to /Library/Games", renamed)
    check("it won't move apps into Library", renamed.is_dir() and "home folder" in text, text[:200])

    calls.clear()
    d, text = await say("delete the game", renamed)
    check("'delete the game' asks first and does nothing yet", not calls and "Say **yes**" in text and where(text).get("pending") == "trash", text[:200])
    d, text = await say("yes", renamed, {"pending": "trash"})
    check("…'yes' moves it to the Trash", d and d.get("confirmed") and any(c[1].get("action") == "trash" and c[1]["src"] == str(renamed) for c in calls), str(calls))
    check("'yes' without a pending delete isn't a delete", (builder.route("yes", history(renamed) + [{"role": "user", "content": "yes"}], None) or {}).get("mode") != "manage")

    calls.clear()
    await say("show it in finder", renamed)
    check("'show it in finder' reveals the folder", calls and calls[-1] == ("computer_reveal_finder", {"path": str(renamed)}), str(calls))
    calls.clear()
    await say("open main.js in vscode", renamed)
    check("'open main.js in vscode' opens that file in VS Code", calls and calls[-1][1] == {"target": str(renamed / "main.js"), "app_name": "Visual Studio Code"}, str(calls))
    calls.clear()
    await say("open it in chrome", renamed)
    check("'open it in chrome' opens the running app in Chrome", calls and calls[-1][1]["app_name"] == "Google Chrome" and calls[-1][1]["target"].startswith("http"), str(calls))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
