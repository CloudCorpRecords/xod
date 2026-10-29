"""
Switching this computer's model from the phone or another Xod (services/remote_models.py, phone_api models.*): only
what the model switcher here offers can be picked, a switch says who made it, and nothing new (a key, a server
address) can be added from outside. The model switcher and the switch itself are faked: nothing is changed.
  .venv/bin/python tests/smoke/run_remote_models_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod-remotemodels-"), "XOD_NO_UPDATE_CHECK": "1"})
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main():
    from app.services import phone_api, remote_models, setup_wizard, task_queue
    now = {"provider": "fireworks", "model": "glm"}

    async def quick():
        rows = [("Online", "fireworks", "glm", "Fireworks · glm"), ("LM Studio", "lm_studio", "gemma", "gemma"),
                ("In use", "openai", "gone", "OpenAI · gone")]
        return {"choices": [{"group": g, "provider": p, "model": m, "label": l, "current": (p, m) == (now["provider"], now["model"]),
                             **({"missing": True} if g == "In use" else {})} for g, p, m, l in rows],
                "lm_studio_online": True, "ollama_online": False}
    switched, told = [], []

    def choose(provider, model, base_url="", how="you"):
        switched.append((provider, model, base_url))
        now.update(provider=provider, model=model)
        return {"success": True}
    setup_wizard.quick, setup_wizard.choose = quick, choose
    task_queue.notify = lambda t, m: told.append(m)

    c = await remote_models.choices()
    check("the choices are what the switcher offers, minus a model whose key or server is gone",
          [x["model"] for x in c["choices"]] == ["glm", "gemma"] and c["current"]["model"] == "glm", str(c))
    r = await remote_models.use("custom", "anything", "your phone")
    check("a model the switcher doesn't offer is refused, nothing is switched", not r["success"] and not switched, str(r))
    r = await remote_models.use("lm_studio", "gemma", "your phone")
    check("one it offers is switched to, with no server address from outside", r["success"] and switched == [("lm_studio", "gemma", "")],
          str(switched))
    check("…and a notification says who changed it", told and "your phone" in told[-1] and "gemma" in told[-1], str(told))
    r = await remote_models.use("lm_studio", "gemma", "your phone")
    check("picking the one in use again changes nothing", r["success"] and len(switched) == 1)
    out = await phone_api.run("models.list", {})
    check("the phone can list them (models.list)", out["ok"] and len(out["data"]["choices"]) == 2, str(out))
    out = await phone_api.run("models.use", {"provider": "fireworks", "model": "glm"})
    check("the phone can switch (models.use) and gets the new list back", out["ok"] and out["data"]["current"]["model"] == "glm", str(out))
    out = await phone_api.run("models.use", {"provider": "nope", "model": "x"})
    check("the phone gets a plain error for a model that isn't offered", not out["ok"] and "isn't one" in out["error"], str(out))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
