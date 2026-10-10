"""
Scores a trained specialist on held-out teacher examples. Run as a subprocess so MLX
memory is released when it finishes:

    python -m app.services.specialist_eval --model <base> --adapter <dir> --test test.jsonl \
        --validator json_action --out eval.json

Metrics (each test example is a teacher answer the student never trained on):
    valid_json_rate    student produced parseable JSON with the required field
    action_match_rate  same decision as the teacher (browser: same action + same element)
    exact_match_rate   identical JSON (ignoring plan/notes/reasoning text)
    avg_latency_ms     time per answer
"""
import argparse
import json
import time
from pathlib import Path

FREE_TEXT_KEYS = {"reasoning", "plan", "remember", "answer", "question", "thought", "direct_response", "plan_title"}


def _parse(text: str):
    from app.services.llm import parse_llm_json
    return parse_llm_json(text or "")


def _same_decision(validator: str, student: dict, teacher: dict) -> bool:
    if validator == "json_action":
        from app.services.specialists import ACTION_FIELDS
        if student.get("action") != teacher.get("action"):
            return False
        # Compare only what defines this action; free text (questions, answers) can differ in wording.
        for key in ACTION_FIELDS.get(str(teacher.get("action")), ()):
            if key in ("question", "answer", "press_enter"):
                continue
            norm = lambda v: str(v).strip().lower().rstrip("/") if v is not None else ""
            if norm(student.get(key)) != norm(teacher.get(key)):
                return False
        return True
    if validator == "json_decision":
        if student.get("decision") != teacher.get("decision"):
            return False
        if teacher.get("decision") == "execute_plan":
            first = lambda d: ((d.get("plan_steps") or [{}])[0].get("tools") or [None])[0]
            return first(student) == first(teacher)
        return True
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter")
    ap.add_argument("--test", required=True)
    ap.add_argument("--validator", default="json_action")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=400)
    args = ap.parse_args()

    from mlx_lm import load, generate
    model, tokenizer = load(args.model, adapter_path=args.adapter)
    rows = [json.loads(l) for l in Path(args.test).read_text().splitlines() if l.strip()]

    valid = match = exact = 0
    latencies, samples = [], []
    for row in rows:
        msgs = row["messages"]
        teacher = _parse(msgs[-1]["content"])
        prompt = tokenizer.apply_chat_template(msgs[:-1], add_generation_prompt=True, tokenize=False)
        t = time.time()
        out = generate(model, tokenizer, prompt=prompt, max_tokens=args.max_tokens, verbose=False)
        latencies.append((time.time() - t) * 1000)
        student = _parse(out)
        required = "action" if args.validator == "json_action" else "decision"
        ok_json = bool(student) and bool(student.get(required))
        same = ok_json and _same_decision(args.validator, student, teacher)
        from app.services.specialists import clean_action
        strip = lambda d: {k: v for k, v in clean_action(d).items() if k not in FREE_TEXT_KEYS}
        valid += ok_json
        match += same
        exact += ok_json and strip(student) == strip(teacher)
        if len(samples) < 8:
            samples.append({"teacher": strip(teacher), "student": strip(student) if student else out[:200], "match": same})

    n = max(1, len(rows))
    result = {
        "test_examples": len(rows),
        "valid_json_rate": round(valid / n, 3),
        "action_match_rate": round(match / n, 3),
        "exact_match_rate": round(exact / n, 3),
        "avg_latency_ms": int(sum(latencies) / n) if latencies else None,
        "samples": samples,
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}))


if __name__ == "__main__":
    main()
