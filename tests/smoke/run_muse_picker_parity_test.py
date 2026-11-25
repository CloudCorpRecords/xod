"""
Parity test between Xod's own approval card picker (app/services/muse_app.py)
and Xod Partner's frozen port (partner/partner_ax.py).

Both modules carry parallel copies of the toggle/negative/action-verb tables
and button picker so Partner can run standalone with zero Xod imports. Any
Muse UI tweak (like filtering "See details" on scheduled-task cards) must land
in both places; this test fails immediately if they ever drift apart.

  .venv/bin/python tests/smoke/run_muse_picker_parity_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-picker-parity-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "partner"))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.services import muse_app
    import partner_ax

    print("P1 rule table parity between muse_app and partner_ax")
    check("_TOGGLE_LABELS match exactly",
          muse_app._TOGGLE_LABELS == partner_ax._TOGGLE_LABELS,
          f"muse={muse_app._TOGGLE_LABELS} partner={partner_ax._TOGGLE_LABELS}")
    check("_NEGATIVE_LABELS match exactly",
          muse_app._NEGATIVE_LABELS == partner_ax._NEGATIVE_LABELS,
          f"muse={muse_app._NEGATIVE_LABELS} partner={partner_ax._NEGATIVE_LABELS}")
    check("_ACTION_VERBS match exactly",
          muse_app._ACTION_VERBS == partner_ax._ACTION_VERBS,
          f"muse={muse_app._ACTION_VERBS} partner={partner_ax._ACTION_VERBS}")

    print("P2 _candidate_buttons and _pick_button parity across real card shapes")
    cases = [
        ("scoped Always allow for this recipient",
         ["Send", "Always allow for this recipient", "Don't send"],
         "Always allow for this recipient"),
        ("scoped Always allow for this site",
         ["Allow once", "Always allow for this site", "Deny"],
         "Always allow for this site"),
        ("Allow all once beats Allow once",
         ["Allow once", "Allow all once", "Deny all"],
         "Allow all once"),
        ("Send beats Allow",
         ["Allow", "Send", "Don't send"],
         "Send"),
        ("Allow once beats Allow",
         ["Allow", "Allow once", "Deny"],
         "Allow once"),
        ("Allow for this task beats Allow",
         ["Allow", "Allow for this task", "Deny"],
         "Allow for this task"),
        ("scoped Allow <X> for this task",
         ["Allow Gmail for this task", "Deny"],
         "Allow Gmail for this task"),
        ("unscoped Always allow loses to one-time",
         ["Always allow everything", "Allow once", "Deny"],
         "Allow once"),
        ("unscoped Always allow alone is never picked",
         ["Always allow for all sites", "Deny"],
         None),
        ("Allow always alone is never picked",
         ["Allow always", "Deny"],
         None),
        ("Needs review scheduled-task Gmail Reply card with long See details",
         ["Tree wants to reply to Eric with thanks for his response and feedback on the concept, "
          "acknowledge the in-house setup, and wish him luck with the next TAMUhack. "
          "This is for the scheduled task \u201cCatchup Sweep Twice Daily\u201d. See details",
          "Reply", "Don't reply"],
         "Reply"),
        ("short See details body button never competes with Reply",
         ["Short summary. See details", "Reply", "Don't reply"],
         "Reply"),
        ("short Hide details / Show details toggles filtered",
         ["Short summary. Hide details", "Show details", "Create", "Don't create"],
         "Create"),
        ("See details alone with negative button returns None",
         ["See details", "Don't reply"],
         None),
        ("Deny only returns None",
         ["Deny"],
         None),
        ("Deny all + Don't send returns None",
         ["Deny all", "Don't send"],
         None),
        ("action verb with argument (Pay $25)",
         ["Pay $25", "Don't pay"],
         "Pay $25"),
        ("Continue vs Cancel",
         ["Continue", "Cancel"],
         "Continue"),
        ("two unranked unknown actions hold (return None)",
         ["Frobnicate", "Zangle"],
         None),
        ("single remaining unknown action is picked",
         ["Frobnicate", "Cancel"],
         "Frobnicate"),
        ("empty and whitespace labels",
         ["", "   "],
         None),
    ]

    for name, labels, expected in cases:
        m_cands = muse_app._candidate_buttons(labels)
        p_cands = partner_ax._candidate_buttons(labels)
        m_pick = muse_app._pick_button(labels)
        p_pick = partner_ax.pick_button(labels)
        check(f"{name}",
              m_cands == p_cands and m_pick == p_pick == expected,
              f"expected={expected!r} muse=({m_pick!r}, {m_cands}) partner=({p_pick!r}, {p_cands})")

    print("P3 card_type parity")
    for title in ("Approval request for Gmail", "Approval request for Browser", "Approval request for Shell command", ""):
        check(f"card_type({title!r}) matches",
              muse_app._card_type(title) == partner_ax.card_type(title),
              f"muse={muse_app._card_type(title)!r} partner={partner_ax.card_type(title)!r}")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
