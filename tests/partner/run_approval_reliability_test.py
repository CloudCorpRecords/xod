"""Deterministic approval regressions; no desktop actions or model calls."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory(prefix="xod-approval-")
os.environ.update(XOD_DATA_DIR=TMP.name, XOD_NO_UPDATE_CHECK="1",
                  PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring")
from app.services import muse_app, partner_client, run_mode
from partner import partner_ax


class ApprovalReliability(unittest.TestCase):
    def setUp(self):
        muse_app._save_pending(set())

    def test_pending_intent_survives_memory_reset(self):
        muse_app._save_pending({"card"})
        muse_app._state["press_marks"] = {}
        self.assertEqual(muse_app._pending_presses(), {"card"})

    def test_concurrent_intent_only_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        with patch.object(muse_app, "active", return_value=True):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(muse_app._claim_press, ["same"] * 8))
        self.assertEqual(sum(results), 1)

    def test_paused_claim_cannot_execute(self):
        with patch.object(muse_app, "active", return_value=False):
            self.assertFalse(muse_app._claim_press("same"))
        self.assertEqual(muse_app._pending_presses(), set())

    def test_button_contract(self):
        for labels, expected in [
            (["Always allow for this recipient", "Send"], "Always allow for this recipient"),
            (["Always allow for this site"], "Always allow for this site"),
            (["Always allow for all sites", "Allow once"], "Allow once"),
            (["Always allow for every recipient", "Send"], "Send"),
            (["Always allow for all recipients", "Allow"], "Allow"),
            (["Always allow for every site", "Allow for this task"], "Allow for this task"),
            (["Always allow for this recipient and all sites", "Send"], "Send"),
            (["Always allow for this site or every recipient", "Allow once"], "Allow once"),
            (["Always allow all sites"], None),
            (["Always allow for every recipient"], None),
            (["  ALWAYS ALLOW FOR THIS SITE ", "Allow"], "ALWAYS ALLOW FOR THIS SITE"),
            (["Allow always", "Deny"], None),
            (["Allow for this task", "Deny"], "Allow for this task"),
            (["Allow all web access for this task"], "Allow all web access for this task"),
            (["Allow once", "Deny all"], "Allow once"),
            (["Allow all once", "Allow"], "Allow all once"),
            (["Don't send"], None),
        ]:
            with self.subTest(labels=labels):
                self.assertEqual(muse_app._pick_button(labels), expected)
                self.assertEqual(partner_ax.pick_button(labels), expected)

    def test_partner_policy_revalidated(self):
        raw = {"card_id": "one", "title": "Approval request for Browser",
               "what": "A test request", "button": "Allow", "buttons": ["Allow"]}
        with patch.object(partner_client, "status", return_value={"partner_trusted": True, "approval_policy": "always-allow-v1"}), \
             patch.object(partner_client, "scan", return_value={"cards": [raw]}), \
             patch.object(partner_client, "press") as press, \
             patch.object(muse_app, "active", return_value=False):
            cards = muse_app._partner_scan_press()["approved"]
            press.assert_not_called()
            self.assertIn("paused", cards[0]["reason"])

    def test_partner_behavior_must_match_advertised_policy(self):
        raw = {"card_id": "one", "button": "Allow",
               "buttons": ["Always allow for this site", "Allow"]}
        with patch.object(partner_client, "status", return_value={"partner_trusted": True, "approval_policy": "always-allow-v1"}), \
             patch.object(partner_client, "scan", return_value={"cards": [raw]}), \
             patch.object(partner_client, "press") as press:
            result = muse_app._partner_scan_press()
            press.assert_not_called()
            self.assertIn("policy", result["approved"][0]["reason"])

    def test_timeout_is_uncertain_not_failed(self):
        raw = {"card_id": "one", "button": "Allow", "buttons": ["Allow"]}
        with patch.object(partner_client, "status", return_value={"partner_trusted": True, "approval_policy": "always-allow-v1"}), \
             patch.object(partner_client, "scan", return_value={"cards": [raw]}), \
             patch.object(partner_client, "press", return_value=None), \
             patch.object(muse_app, "active", return_value=True):
            self.assertTrue(muse_app._partner_scan_press()["approved"][0]["uncertain"])

    def test_explicit_skip_cannot_start_watcher(self):
        with patch.object(run_mode, "skip", return_value=True), \
             patch.object(muse_app.asyncio, "create_task") as create:
            muse_app.start()
            create.assert_not_called()

    def test_lightweight_keeps_core_approval_services(self):
        with patch.object(run_mode, "lightweight_active", return_value=True):
            self.assertFalse(run_mode.skip("muse_app"))
            self.assertFalse(run_mode.skip("partner_watchdog"))
            self.assertTrue(run_mode.skip("autoconnect"))

    def test_legacy_partner_cannot_receive_a_click(self):
        with patch.object(partner_client, "status", return_value={"partner_trusted": True}), \
             patch.object(partner_client, "scan") as scan, \
             patch.object(partner_client, "press") as press:
            self.assertFalse(muse_app._partner_delegated())
            self.assertIsNone(muse_app._partner_scan_press())
            scan.assert_not_called()
            press.assert_not_called()

    def test_client_uses_versioned_policy_route(self):
        with patch.object(partner_client, "_call", return_value={}) as call:
            partner_client.press("test-card", button="Always allow for this site")
        self.assertEqual(call.call_args.args[:2], ("POST", "/ax/press-policy"))
        self.assertEqual(call.call_args.args[2]["approval_policy"], "always-allow-v1")
        self.assertEqual(call.call_args.args[2]["expected_button"], "Always allow for this site")

    def test_one_time_partner_is_not_silently_reinterpreted(self):
        with patch.object(partner_client, "status", return_value={
                "partner_trusted": True, "approval_policy": "one-time-v1"}), \
             patch.object(partner_client, "press") as press:
            self.assertFalse(muse_app._partner_delegated())
            self.assertIsNone(muse_app._partner_scan_press())
            press.assert_not_called()

    def test_permanent_button_appearing_after_scan_is_never_clicked(self):
        from tests.partner.run_partner_ax_test import FakeAx, card_tree, _core, _serve, _call
        ax = FakeAx(card_tree(["Allow"]))
        walk = partner_ax.Walk(ax, pid_of=lambda: 4242)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, core = _core(tmp, True)
            server, base = _serve(cfg, core, walk)
            try:
                _, status = _call(base, "/status", token=core.token)
                self.assertTrue(partner_client.approval_compatible(status))
                _, scan = _call(base, "/ax/scan", token=core.token, body={})
                cid = scan["cards"][0]["card_id"]
                # Same request, but a permanent button appears after the scan.
                ax.root = card_tree(["Always allow for this recipient", "Allow"])
                code, result = _call(base, "/ax/press-once", token=core.token,
                                     body={"card_id": cid})
                self.assertEqual(code, 200)
                self.assertTrue(result["pressed"])
                self.assertEqual(ax.pressed, ["Allow"])
                ax.pressed.clear()
                ax.root = card_tree(["Always allow for this recipient"])
                _, result = _call(base, "/ax/press-once", token=core.token,
                                   body={"card_id": cid})
                self.assertFalse(result["pressed"])
                self.assertEqual(ax.pressed, [])
            finally:
                server.shutdown()
                server.server_close()

    def test_delegation_suite_preserves_callers_data(self):
        import subprocess
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            # Model an existing user's settings and unresolved action.
            marker = Path(tmp) / "xod.db"
            db = sqlite3.connect(marker)
            db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            db.executemany("INSERT INTO settings VALUES (?, ?)", [
                ("muse_auto_approve", "off"),
                ("muse_pending_presses", '["existing-pending-action"]'),
            ])
            db.commit()
            db.close()
            before = marker.read_bytes()
            result = subprocess.run(
                [sys.executable, str(ROOT / "tests/partner/run_partner_delegation_test.py")],
                env={**os.environ, "XOD_DATA_DIR": tmp}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), before)

    def test_email_always_allow_survives_restart_mid_press(self):
        import subprocess
        import json
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "XOD_DATA_DIR": str(Path(tmp) / "data"),
                   "HOME": tmp, "XDG_CONFIG_HOME": str(Path(tmp) / "config"),
                   "XOD_MUSE_APP_DRY_RUN": ""}
            def run(scenario):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tests/partner/approval_restart_probe.py"), scenario, tmp],
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(run("lost-response")["pending"])
            self.assertFalse(run("same-card")["pressed"])
            # A real new interpreter, not just cleared in-memory marks.
            self.assertEqual((Path(tmp) / "presses").read_text().splitlines(),
                             ["Always allow for this recipient"])
            self.assertFalse(run("unscoped-card")["pressed"])
            self.assertFalse(run("expired-card")["pressed"])
            self.assertFalse(run("switch-off")["pressed"])
            self.assertEqual(len((Path(tmp) / "presses").read_text().splitlines()), 1)
            self.assertFalse(run("absence")["pending"])
            self.assertTrue(run("same-card")["pressed"])
            self.assertEqual(len((Path(tmp) / "presses").read_text().splitlines()), 2)

    def test_policy_route_rejects_mismatch_and_changed_button(self):
        from tests.partner.run_partner_ax_test import FakeAx, card_tree, _core, _serve, _call
        ax = FakeAx(card_tree(["Always allow for this recipient", "Send"]))
        walk = partner_ax.Walk(ax, pid_of=lambda: 4242)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, core = _core(tmp, True)
            server, base = _serve(cfg, core, walk)
            try:
                _, scan = _call(base, "/ax/scan", token=core.token, body={})
                cid = scan["cards"][0]["card_id"]
                body = {"card_id": cid, "approval_policy": "one-time-v1",
                        "expected_button": "Send"}
                code, _ = _call(base, "/ax/press-policy", token=core.token, body=body)
                self.assertEqual(code, 409)
                self.assertEqual(ax.pressed, [])
                body.update(approval_policy="always-allow-v1", expected_button="Always allow for this recipient")
                ax.root = card_tree(["Send"])
                _, result = _call(base, "/ax/press-policy", token=core.token, body=body)
                self.assertFalse(result["pressed"])
                self.assertEqual(ax.pressed, [])
                ax.root = card_tree(["Always allow for this recipient", "Send"])
                _, result = _call(base, "/ax/press-policy", token=core.token, body=body)
                self.assertTrue(result["pressed"])
                self.assertEqual(ax.pressed, ["Always allow for this recipient"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
