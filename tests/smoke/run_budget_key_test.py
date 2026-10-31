"""
Completion-budget parameter: OpenAI's GPT-5 family and o-series refuse
"max_tokens" (HTTP 400: use "max_completion_tokens" instead). Auto mode picked
gpt-5-nano in the Brain v1 hardening round (2026-10-30) and a mission failed in
planning on exactly this. The LLM layer now picks the key each model accepts,
renames reactively when an endpoint teaches it otherwise, and grows whichever
key is present.

Run: .venv/bin/python tests/smoke/run_budget_key_test.py
"""
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_budget_key_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app.services import llm

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    check("gpt-5 family uses max_completion_tokens",
          llm._budget_key({"provider": "openai", "model": "gpt-5-nano"}) == "max_completion_tokens")
    check("o-series uses max_completion_tokens",
          llm._budget_key({"provider": "openai", "model": "o3-mini"}) == "max_completion_tokens")
    check("gpt-4o keeps max_tokens",
          llm._budget_key({"provider": "openai", "model": "gpt-4o"}) == "max_tokens")
    check("other providers keep max_tokens",
          llm._budget_key({"provider": "anthropic", "model": "claude-sonnet-5"}) == "max_tokens")
    check("proxied gpt-5 keeps max_tokens (not the direct API)",
          llm._budget_key({"provider": "openrouter", "model": "openai/gpt-5-nano"}) == "max_tokens")

    body = ('{"error": {"message": "Unsupported parameter: \'max_tokens\' is not supported '
            'with this model. Use \'max_completion_tokens\' instead."}}')
    payload = {"max_tokens": 800}
    info = {"provider": "openai", "model": "gpt-6-pro"}
    check("a naming 400 renames the budget", llm._budget_param_rejected(payload, info, 400, body) is True)
    check("the value moved, the old key is gone", payload == {"max_completion_tokens": 800}, str(payload))
    check("the pair is remembered", llm._budget_key(info) == "max_completion_tokens")

    other = {"max_tokens": 800}
    check("an unrelated 400 changes nothing",
          llm._budget_param_rejected(other, {"provider": "openai", "model": "gpt-4o"}, 400, "bad request") is False
          and other == {"max_tokens": 800})

    payload2 = {"max_completion_tokens": 100}
    llm._quiet_thinking(payload2, {"provider": "openai", "model": "gpt-5-nano"}, False)
    check("quiet thinking grows the completion key, never adds max_tokens",
          payload2 == {"max_completion_tokens": 100 + llm.CLOUD_THINK_HEADROOM}, str(payload2))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
