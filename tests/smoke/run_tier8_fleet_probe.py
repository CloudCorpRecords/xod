"""
Tier 8 fleet probe: trust changes travel between real processes.

The signed family registry file is the one fleet artifact two Xod
processes share today. This probe runs separate processes against
one registry file and checks the trust story end to end:

  founder   seeds a signed registry and approves its own member set
  joiner    writes its own signed record into the file; the write
            alone grants nothing: it reads itself as untrusted and
            the founder's approval no longer covers the set
  approver  the founder process countersigns the new member set
  joiner    now reads itself as a trusted member
  revoker   the founder process signs a revocation of the joiner
  joiner    honors the revocation over its record's claimed status

Identities are synthetic (fixed byte ranges, as in the enroll suite);
no real keys or devices are involved.

Run: .venv/bin/python tests/smoke/run_tier8_fleet_probe.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAMILY, ACCOUNT = "probe-family", "ProbeAccount"
SEEN = "2026-11-06T09:00:00-08:00"


def make_ident(which):
    from app.services import mail_crypto as mc
    a, b = {"a": (0, 32), "b": (64, 96)}[which]
    return mc.Identity.from_secrets(mc.b64e(bytes(range(a, a + 32))),
                                    mc.b64e(bytes(range(b, b + 32))))


def base_record(did, name, platform):
    return {"id": did, "name": name, "platform": platform, "endpoint": None,
            "capabilities": ["probe"], "account": ACCOUNT,
            "last_seen": SEEN, "status": "active"}


def read_data(reg):
    return json.loads(reg.read_text(encoding="utf-8"))


def write_data(reg, data):
    reg.write_text(json.dumps(data), encoding="utf-8")


def facts(reg, fr):
    view = fr.load_registry()
    mine = None
    for d in view["devices"]:
        if d["id"] == os.environ["XOD_FAMILY_DEVICE_ID"]:
            mine = d
    return {
        "approval_verified": bool(view["approval_verified"]),
        "trusted_a": fr.trusted_member(view, "probe-a") is not None,
        "trusted_b": fr.trusted_member(view, "probe-b") is not None,
        "my_status": (mine or {}).get("status"),
        "revoked": view["revoked_by_registry"],
    }


def child(role, tmp):
    sys.path.insert(0, str(ROOT))
    from app.services import family_registry as fr
    reg = Path(os.environ["XOD_FAMILY_REGISTRY_PATH"])
    me = os.environ["XOD_FAMILY_DEVICE_ID"]
    ident = make_ident("a" if me == "probe-a" else "b")
    if role == "founder":
        data = {"family": FAMILY, "account": ACCOUNT, "version": 1,
                "updated": SEEN,
                "devices": [fr.sign_record(base_record("probe-a", "Probe A", "Windows"),
                                           ident, FAMILY, ACCOUNT)]}
        write_data(reg, fr.sign_approval(data, ident, "probe-a"))
    elif role == "joiner_write":
        data = read_data(reg)
        data["devices"].append(fr.sign_record(
            base_record("probe-b", "Probe B", "Android"), ident, FAMILY, ACCOUNT))
        write_data(reg, data)
    elif role == "approver":
        write_data(reg, fr.sign_approval(read_data(reg), ident, "probe-a"))
    elif role == "revoker":
        data = read_data(reg)
        entry = fr.sign_revocation(data, ident, "probe-a", "probe-b")
        data.setdefault("revocations", []).append(entry)
        write_data(reg, data)
    print("FACT " + json.dumps(facts(reg, fr)), flush=True)
    os._exit(0)


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--child":
        child(sys.argv[2], Path(sys.argv[3]))
        return
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{detail}]" if detail and not ok else ""), flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="xod_tier8_probe_"))
    me = str(Path(__file__).resolve())

    def run(role, device):
        env = dict(os.environ)
        home = tmp / device
        home.mkdir(parents=True, exist_ok=True)
        (home / "data").mkdir(exist_ok=True)
        env.update({
            "XOD_DATA_DIR": str(home / "data"),
            "HOME": str(home),
            "XOD_NO_UPDATE_CHECK": "1",
            "XOD_FAMILY_REGISTRY_PATH": str(tmp / "registry.json"),
            "XOD_FAMILY_DEVICE_ID": device,
            "PYTHONUTF8": "1",
        })
        out = subprocess.run([sys.executable, me, "--child", role, str(tmp)],
                             env=env, cwd=str(ROOT), capture_output=True,
                             text=True, timeout=120)
        for line in out.stdout.splitlines():
            if line.startswith("FACT "):
                return json.loads(line[5:])
        raise RuntimeError(f"no FACT from {role}: " + out.stdout[-300:]
                           + out.stderr[-300:])

    f = run("founder", "probe-a")
    check("founder process: its own member set is approved and trusted",
          f["approval_verified"] and f["trusted_a"], json.dumps(f))
    f = run("joiner_write", "probe-b")
    check("joiner process: writing its own signed record grants no trust",
          not f["trusted_b"] and not f["approval_verified"], json.dumps(f))
    check("joiner process: the founder is untrusted too until re-approval",
          not f["trusted_a"], json.dumps(f))
    f = run("approver", "probe-a")
    check("approver process: countersigning the set trusts both members",
          f["approval_verified"] and f["trusted_a"] and f["trusted_b"],
          json.dumps(f))
    f = run("joiner_check", "probe-b")
    check("joiner process: reads itself trusted after the countersign",
          f["trusted_b"], json.dumps(f))
    f = run("revoker", "probe-a")
    check("revoker process: founder stays trusted, joiner is revoked",
          f["trusted_a"] and not f["trusted_b"]
          and "probe-b" in f["revoked"], json.dumps(f))
    f = run("joiner_check", "probe-b")
    check("joiner process: honors the revocation over its claimed status",
          not f["trusted_b"] and f["my_status"] == "revoked",
          json.dumps(f))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  {n}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
