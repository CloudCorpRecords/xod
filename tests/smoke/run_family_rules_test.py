"""
Family shared rules: the reader and write path in services/family_rules.py,
and their exposure through the capability manifest (build_manifest's
family_rules section and the summary_text sessions read).

The setup this covers: one rulebook, family-rules.json, lives in the rdrive
family shared folder next to tree-inbox/. The owner sets a rule once, with
either of his agents, and both follow it. The checks prove: the file is
found by discovery in the rdrive space; only the rules scoped xod or both
reach this Xod (a tree-scoped rule never appears in the manifest section
or the summary text); response-discretion rules are grouped under their
own heading; a rule stated in a chat appends with its source recorded and
a restated rule supersedes by id with the old version kept in history;
and a missing, broken, or stale file degrades to defaults and honest
state, never to an exception.

Runs against a throwaway home folder with a fake rdrive space. Nothing
real is read and nothing leaves the machine.

  .venv/bin/python tests/smoke/run_family_rules_test.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-family-rules-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def rule(rid, text, scope, kind, days_ago=0):
    return {"id": rid, "text": text, "scope": scope, "kind": kind,
            "source": {"stated_by": "owner", "via": "tree-memory-seed", "at": "2026-11-06"},
            "version": 1, "supersedes": None, "updated_at": iso(days_ago)}


SEED = {"schema": "family-rules/v1", "updated_at": iso(), "history": [],
        "rules": [
            rule("no-early-meetings", "No meetings before 9 AM Pacific.", "both", "constraint"),
            rule("boardy-first", "Answer Boardy email promptly.", "xod", "respond"),
            rule("tree-lane-bodies", "Tree-only rule about lane job bodies.", "tree", "constraint"),
            rule("mariane-social", "Leave Mariane's social posts alone.", "both", "do_not_respond"),
        ]}


def main():
    from app.services import family_rules as fr
    from app.services import capability_manifest as cm

    space = TMP / "Documents" / "rdrive" / "data" / "files" / "cloudtheboy"
    space.mkdir(parents=True)
    target = space / "family-rules.json"
    target.write_text(json.dumps(SEED), encoding="utf-8")

    def fresh():
        fr._cache.update(loaded=None, at=0.0)
        cm._cache.update(manifest=None, at=0.0)

    # ---- discovery and scoping ------------------------------------------------
    fresh()
    check("rules file found by rdrive space discovery", fr.rules_path() == target)
    doc = fr.load_rules()
    check("load state ok with all four rules", doc["state"] == "ok" and len(doc["rules"]) == 4)
    check("fresh file is not stale", doc["stale"] is False)
    mine = fr.for_xod(doc["rules"])
    check("scoping keeps xod and both, drops tree", len(mine) == 3
          and all(r["scope"] in ("xod", "both") for r in mine))
    sec = fr.manifest_section(doc)
    check("manifest section reports state and totals",
          sec["state"] == "ok" and sec["total"] == 4 and len(sec["rules"]) == 3)
    check("tree-scoped rule never enters the manifest section",
          "Tree-only rule" not in json.dumps(sec))

    # ---- summary text ----------------------------------------------------------
    block = fr.summary_block(sec)
    check("summary block lists the standing rules", "No meetings before 9 AM Pacific." in block)
    check("summary block groups response discretion",
          "RESPONSE DISCRETION" in block and "[ANSWER] Answer Boardy email promptly." in block
          and "[LEAVE ALONE] Leave Mariane's social posts alone." in block)
    check("tree-scoped rule never enters the summary block", "Tree-only rule" not in block)

    # ---- through the capability manifest ---------------------------------------
    fresh()
    m = cm.build_manifest()
    check("build_manifest carries the family_rules section",
          m.get("family_rules", {}).get("state") == "ok" and len(m["family_rules"]["rules"]) == 3)
    text = cm.summary_text(m)
    check("manifest summary_text carries the family rules",
          "FAMILY RULES FOR THIS HOUSEHOLD" in text and "No meetings before 9 AM Pacific." in text)
    check("manifest summary_text never shows tree-scoped rules", "Tree-only rule" not in text)

    # ---- the chat write path -----------------------------------------------------
    fresh()
    res = fr.append_rule("Always water the plants on Fridays.", scope="both", kind="preference")
    check("append from a chat stores the rule", res.get("ok") is True
          and res["rule"]["source"]["via"] == "xod-chat")
    doc = fr.load_rules()
    check("appended rule is in the file on re-read",
          any(r["text"] == "Always water the plants on Fridays." for r in doc["rules"]))
    res = fr.append_rule("No meetings before 10 AM Pacific.", rule_id="no-early-meetings")
    check("a restated rule supersedes by id", res.get("ok") is True and res["rule"]["version"] == 2)
    doc = fr.load_rules()
    live = next(r for r in doc["rules"] if r["id"] == "no-early-meetings")
    check("the live version is the newest statement",
          live["text"] == "No meetings before 10 AM Pacific." and live["supersedes"]["version"] == 1)
    check("the old version is kept in history",
          len(doc["history"]) == 1 and doc["history"][0]["text"] == "No meetings before 9 AM Pacific.")

    # ---- failure behavior: missing, invalid, stale -------------------------------
    gone = TMP / "nowhere" / "family-rules.json"
    os.environ["XOD_FAMILY_RULES_PATH"] = str(gone)
    fresh()
    doc = fr.load_rules()
    check("missing file reports missing, never raises", doc["state"] == "missing" and doc["rules"] == [])
    check("missing file gives an empty summary block", fr.summary_block(fr.manifest_section(doc)) == "")
    res = fr.append_rule("A rule with nowhere to go.")
    check("append with no reachable space reports honestly",
          res.get("ok") is False and res["state"] == "unreachable")

    bad = TMP / "bad-rules.json"
    bad.write_text("{not json", encoding="utf-8")
    os.environ["XOD_FAMILY_RULES_PATH"] = str(bad)
    fresh()
    doc = fr.load_rules()
    check("unparseable file reports invalid", doc["state"] == "invalid")
    res = fr.append_rule("A rule into a broken file.")
    check("append refuses a broken file instead of overwriting it",
          res.get("ok") is False and res["state"] == "invalid"
          and bad.read_text(encoding="utf-8") == "{not json")

    old = TMP / "old-rules.json"
    stale_doc = dict(SEED, updated_at=iso(days_ago=30))
    old.write_text(json.dumps(stale_doc), encoding="utf-8")
    os.environ["XOD_FAMILY_RULES_PATH"] = str(old)
    fresh()
    doc = fr.load_rules()
    check("old file still applies but flags stale", doc["state"] == "ok" and doc["stale"] is True)
    check("stale flag reaches the summary block", "may be stale" in fr.summary_block(fr.manifest_section(doc)))
    os.environ.pop("XOD_FAMILY_RULES_PATH", None)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
