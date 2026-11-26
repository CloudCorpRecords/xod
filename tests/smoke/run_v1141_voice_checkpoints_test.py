"""Smoke tests for v1.1.41: Instant Checkpoints & Undo, Screen-Aware Voice Calls, and macOS Screen Audio fix."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import checkpoints, control_center
from app.services.system_control import system_control_service


def test_checkpoints_edit_and_undo():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cp_dir = tmp_path / "cp_store"
        f = tmp_path / "demo.txt"
        f.write_text("line 1\nline 2\n", encoding="utf-8")

        cid = checkpoints.capture_before(str(f), tool="edit_file", base_dir=cp_dir)
        assert cid
        f.write_text("line 1\nline 2 EDITED\n", encoding="utf-8")
        checkpoints.finalize_after(cid, str(f), base_dir=cp_dir)

        items = checkpoints.list_checkpoints(limit=10, base_dir=cp_dir)
        assert len(items) == 1
        assert items[0]["id"] == cid
        assert "-line 2" in items[0]["diff_preview"]
        assert "+line 2 EDITED" in items[0]["diff_preview"]

        res = checkpoints.undo_checkpoint(cid, base_dir=cp_dir)
        assert res["ok"] is True
        assert res["action"] == "restored"
        assert f.read_text(encoding="utf-8") == "line 1\nline 2\n"

        # Second undo should be rejected cleanly
        res2 = checkpoints.undo_checkpoint(cid, base_dir=cp_dir)
        assert res2["ok"] is False
    print("  [PASS] Checkpoint snapshot, diff preview & 1-click undo (modified file)")


def test_checkpoints_created_file_undo():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cp_dir = tmp_path / "cp_store"
        f = tmp_path / "brand_new.txt"
        assert not f.exists()

        cid = checkpoints.capture_before(str(f), tool="write_file", base_dir=cp_dir)
        f.write_text("hello new world\n", encoding="utf-8")
        checkpoints.finalize_after(cid, str(f), base_dir=cp_dir)
        assert f.exists()

        res = checkpoints.undo_checkpoint(cid, base_dir=cp_dir)
        assert res["ok"] is True
        assert res["action"] == "removed_created_file"
        assert not f.exists()
    print("  [PASS] Checkpoint undo removes newly created file")


def test_system_control_hooks_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "hook_test.txt"
        r1 = system_control_service.write_file(str(f), "alpha\nbeta\n")
        assert r1["success"] is True
        assert r1.get("checkpoint_id")

        r2 = system_control_service.edit_file(str(f), "beta", "gamma")
        assert r2["success"] is True
        assert r2.get("checkpoint_id")
        assert f.read_text(encoding="utf-8") == "alpha\ngamma\n"

        u = checkpoints.undo_checkpoint(r2["checkpoint_id"])
        assert u["ok"] is True
        assert f.read_text(encoding="utf-8") == "alpha\nbeta\n"
    print("  [PASS] system_control write_file & edit_file auto-create undoable checkpoints")


def test_routes_and_control_center():
    snap = control_center.snapshot()
    assert "checkpoints" in snap
    assert isinstance(snap["checkpoints"], list)

    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for required in ("/api/checkpoints", "/api/checkpoints/{checkpoint_id}/undo", "/api/voice-call/screen-context", "/api/voice-call/summarize"):
        assert required in paths, f"Missing route {required}"
    print("  [PASS] Control Center snapshot & v1.1.41 FastAPI routes")


if __name__ == "__main__":
    print("Running v1.1.41 Voice Calls, Checkpoints & macOS Capture Fix smoke tests...")
    test_checkpoints_edit_and_undo()
    test_checkpoints_created_file_undo()
    test_system_control_hooks_checkpoint()
    test_routes_and_control_center()
    print("ALL v1.1.41 TESTS PASSED!")
