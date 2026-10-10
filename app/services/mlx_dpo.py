"""
Preference training (DPO) for Xod's specialist students, on MLX.

Instead of copying the teacher, the student learns "this, not that" from pairs:
    chosen   = an action that worked (or the teacher's correction of a failure)
    rejected = an action that failed, or the student's own wrong answer
It continues training the student's existing LoRA adapter, with the starting student as the
frozen reference, plus a small supervised term on the chosen answer so it stays anchored.

Before training it can mine the student's own mistakes ("on-policy" pairs): it answers the
training prompts, and wherever it disagrees with a teacher answer that worked, that becomes
a (teacher, student) pair. Those target exactly the errors this student makes.

    python -m app.services.mlx_dpo --model <base> --init-adapter <sft_dir> --pairs pairs.jsonl \
        --mine train.jsonl --adapter-path <out_dir> [--iters N] [--beta 0.1] [--sft-weight 0.5]

Prints "Iter N: Train loss X, Pref acc Y, Peak mem Z GB" and "Iter N: Val loss X, Val pref acc Y"
so the Model Lab can follow along. Memory caps come from XOD_MLX_CACHE_GB / XOD_MLX_MEMORY_GB.
"""
import argparse
import json
import os
import random
import shutil
import time
from pathlib import Path

import mlx.core as mx

mx.set_cache_limit(int(float(os.environ.get("XOD_MLX_CACHE_GB", "0.5")) * 1e9))
if os.environ.get("XOD_MLX_MEMORY_GB"):
    mx.set_memory_limit(int(float(os.environ["XOD_MLX_MEMORY_GB"]) * 1e9))

import mlx.nn as nn  # noqa: E402
import mlx.optimizers as optim  # noqa: E402
from mlx.utils import tree_flatten  # noqa: E402
from mlx_lm import generate, load  # noqa: E402
from mlx_lm.tuner.trainer import grad_checkpoint  # noqa: E402
from mlx_lm.tuner.utils import linear_to_lora_layers, load_adapters  # noqa: E402

from app.services.llm import parse_llm_json  # noqa: E402
from app.services.specialists import clean_action  # noqa: E402

DEFAULT_LORA = {"rank": 8, "dropout": 0.0, "scale": 20.0}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- tokens & scores
def encode(tokenizer, prompt_msgs, response: str, max_len: int):
    """Full chat sequence plus where the answer starts. None if it doesn't fit (never cut the answer)."""
    prompt = tokenizer.apply_chat_template(prompt_msgs, add_generation_prompt=True, tokenize=True)
    full = tokenizer.apply_chat_template(prompt_msgs + [{"role": "assistant", "content": response}], tokenize=True)
    if list(full[:len(prompt)]) != list(prompt):
        full = list(prompt) + tokenizer.encode(response) + [tokenizer.eos_token_id]
    if len(full) > max_len or len(full) <= len(prompt):
        return None
    return mx.array(full), len(prompt)


def answer_logprob(model, tokens: mx.array, start: int):
    """Sum of log-probabilities of the answer tokens only.
    Projects to the vocabulary just for answer positions: a few hundred rows instead of
    thousands, which is most of the memory a full-sequence pass would use."""
    hidden = model.model(tokens[None, :-1])[:, start - 1:]
    if getattr(model.args, "tie_word_embeddings", False):
        logits = model.model.embed_tokens.as_linear(hidden)
    else:
        logits = model.lm_head(hidden)
    logits = logits.astype(mx.float32)
    logp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
    targets = tokens[None, start:]
    picked = mx.take_along_axis(logp, targets[..., None], axis=-1).squeeze(-1)
    return picked.sum(), picked.shape[1]


# ---------------------------------------------------------------- minimal pairs
DECISION_FREE = ("reasoning", "plan", "remember")


def minimal_pair(chosen_text: str, rejected_text: str):
    """
    Rewrites a pair so both answers share the same reasoning/plan and differ only in the decision.
    Shared tokens then cancel out of the DPO margin, so learning goes into the action itself
    instead of into wording differences. Both sides use the same canonical JSON as SFT targets.
    Returns None if the chosen answer isn't a usable action.
    """
    chosen = clean_action(parse_llm_json(chosen_text))
    if not chosen.get("action"):
        return None
    rejected_raw = parse_llm_json(rejected_text)
    if rejected_raw.get("action"):
        rejected = clean_action(rejected_raw)
        for k in DECISION_FREE:
            if k in chosen:
                rejected[k] = chosen[k]
            else:
                rejected.pop(k, None)
        # Keep key order identical so the shared prefix really is shared.
        ordered = {k: rejected[k] for k in chosen if k in rejected}
        ordered.update({k: v for k, v in rejected.items() if k not in ordered})
        rejected_out = json.dumps(ordered, ensure_ascii=False)
    else:
        rejected_out = rejected_text.strip()[:1500]  # unusable output: unlearn the broken format itself
    chosen_out = json.dumps(chosen, ensure_ascii=False)
    if chosen_out == rejected_out:
        return None
    return chosen_out, rejected_out


# ---------------------------------------------------------------- mining the student's own mistakes
def mine_pairs(model, tokenizer, mine_path: Path, limit: int, max_len: int):
    rows = [json.loads(l) for l in mine_path.read_text().splitlines() if l.strip()]
    random.Random(7).shuffle(rows)
    pairs, checked = [], 0
    for row in rows[:limit]:
        msgs = row["messages"]
        prompt_msgs, teacher_text = msgs[:-1], msgs[-1]["content"]
        teacher = parse_llm_json(teacher_text)
        if not teacher.get("action"):
            continue
        prompt = tokenizer.apply_chat_template(prompt_msgs, add_generation_prompt=True, tokenize=False)
        out = generate(model, tokenizer, prompt=prompt, max_tokens=300, verbose=False)
        checked += 1
        student = parse_llm_json(out)
        if student.get("action"):
            s_clean, t_clean = clean_action(student), clean_action(teacher)
            same = {k: v for k, v in s_clean.items() if k not in ("reasoning", "plan", "remember")} == \
                   {k: v for k, v in t_clean.items() if k not in ("reasoning", "plan", "remember")}
            if same:
                continue
            rejected = json.dumps(s_clean, ensure_ascii=False)
        else:
            rejected = out.strip()[:1500] or "{}"  # unusable output is a mistake worth unlearning too
        pairs.append({"prompt": prompt_msgs, "chosen": teacher_text, "rejected": rejected, "source": "student_mistake"})
    log(f"Mined {len(pairs)} mistakes from {checked} practice prompts")
    return pairs


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--init-adapter")
    ap.add_argument("--pairs", help="jsonl of {prompt, chosen, rejected}")
    ap.add_argument("--mine", help="jsonl of good chat examples to mine the student's mistakes from")
    ap.add_argument("--mine-limit", type=int, default=200)
    ap.add_argument("--adapter-path", required=True)
    ap.add_argument("--iters", type=int, default=0, help="0 = 1.5 passes over the pairs")
    ap.add_argument("--learning-rate", type=float, default=1e-5)
    ap.add_argument("--beta", type=float, default=0.1)
    ap.add_argument("--sft-weight", type=float, default=1.0)
    ap.add_argument("--max-seq-length", type=int, default=3072)
    ap.add_argument("--val-fraction", type=float, default=0.15)
    args = ap.parse_args()

    out_dir = Path(args.adapter_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    model, tokenizer = load(args.model)
    model.freeze()
    if args.init_adapter:
        load_adapters(model, args.init_adapter)
        config = json.loads((Path(args.init_adapter) / "adapter_config.json").read_text())
    else:
        linear_to_lora_layers(model, 8, DEFAULT_LORA)
        config = {"fine_tune_type": "lora", "num_layers": 8, "lora_parameters": DEFAULT_LORA, "model": args.model}

    # 1. Pairs: provided ones + the student's own mistakes.
    pairs = []
    if args.pairs and Path(args.pairs).exists():
        pairs += [json.loads(l) for l in Path(args.pairs).read_text().splitlines() if l.strip()]
    if args.mine and Path(args.mine).exists():
        model.eval()
        pairs += mine_pairs(model, tokenizer, Path(args.mine), args.mine_limit, args.max_seq_length)
    encoded = []
    for p in pairs:
        mp = minimal_pair(p["chosen"], p["rejected"])
        if not mp:
            continue
        c = encode(tokenizer, p["prompt"], mp[0], args.max_seq_length)
        r = encode(tokenizer, p["prompt"], mp[1], args.max_seq_length)
        if c and r:
            encoded.append({"c": c, "r": r, "source": p.get("source", "?")})
    if len(encoded) < 4:
        log(f"Not enough usable pairs ({len(encoded)}); nothing to learn yet.")
        raise SystemExit(2)
    random.Random(11).shuffle(encoded)
    n_val = max(1, int(len(encoded) * args.val_fraction))
    val, train = encoded[:n_val], encoded[n_val:]
    sources = {}
    for e in encoded:
        sources[e["source"]] = sources.get(e["source"], 0) + 1
    log(f"Pairs: {len(train)} train, {len(val)} validation, by source {json.dumps(sources)}")

    # 2. Reference scores from the starting student (frozen copy = these numbers).
    model.eval()
    for e in encoded:
        rc, _ = answer_logprob(model, *e["c"])
        rr, _ = answer_logprob(model, *e["r"])
        mx.eval(rc, rr)
        e["ref_c"], e["ref_r"] = rc.item(), rr.item()

    def margins(items):
        out = []
        for e in items:
            pc, _ = answer_logprob(model, *e["c"])
            pr, _ = answer_logprob(model, *e["r"])
            mx.eval(pc, pr)
            out.append(args.beta * ((pc.item() - e["ref_c"]) - (pr.item() - e["ref_r"])))
        return out

    def raw_pref_acc(items):
        """Share of pairs where the model already likes the chosen answer more (length-normalised)."""
        good = 0
        for e in items:
            pc, nc = answer_logprob(model, *e["c"])
            pr, nr = answer_logprob(model, *e["r"])
            mx.eval(pc, pr)
            good += (pc.item() / nc) > (pr.item() / nr)
        return good / max(1, len(items))

    log(f"Before: Val pref acc {raw_pref_acc(val):.3f}")

    # 3. DPO training.
    model.train()
    grad_checkpoint(model.layers[0])
    opt = optim.Adam(learning_rate=args.learning_rate)

    def loss_fn(model, c_tok, c_start, r_tok, r_start, ref_c, ref_r):
        pc, nc = answer_logprob(model, c_tok, c_start)
        pr, _ = answer_logprob(model, r_tok, r_start)
        margin = args.beta * ((pc - ref_c) - (pr - ref_r))
        dpo = mx.logaddexp(mx.array(0.0), -margin)  # = -log(sigmoid(margin))
        return dpo + args.sft_weight * (-pc / nc), margin

    loss_and_grad = nn.value_and_grad(model, loss_fn)
    best_val, best_weights, worse_in_a_row, stopped_early = float("inf"), None, 0, False
    iters = args.iters or max(20, int(1.5 * len(train)))
    order, run_loss, run_acc, t0 = [], [], [], time.time()
    for it in range(1, iters + 1):
        if not order:
            order = list(range(len(train)))
            random.shuffle(order)
        e = train[order.pop()]
        (loss, margin), grads = loss_and_grad(model, e["c"][0], e["c"][1], e["r"][0], e["r"][1],
                                              mx.array(e["ref_c"]), mx.array(e["ref_r"]))
        opt.update(model, grads)
        mx.eval(model.parameters(), opt.state, loss, margin)
        run_loss.append(loss.item())
        run_acc.append(1.0 if margin.item() > 0 else 0.0)
        if it % 5 == 0 or it == iters:
            log(f"Iter {it}: Train loss {sum(run_loss) / len(run_loss):.3f}, Pref acc {sum(run_acc) / len(run_acc):.2f}, "
                f"It/sec {it / (time.time() - t0):.2f}, Peak mem {mx.get_peak_memory() / 1e9:.3f} GB")
            run_loss, run_acc = [], []
        if it % max(5, iters // 8) == 0 or it == iters:
            model.eval()
            m = margins(val)
            val_loss = sum(float(mx.logaddexp(mx.array(0.0), mx.array(-x)).item()) for x in m) / len(m)
            log(f"Iter {it}: Val loss {val_loss:.3f}, Val pref acc {sum(x > 0 for x in m) / len(m):.3f}")
            # Keep the best checkpoint; stop once held-out loss rises twice in a row (memorizing).
            if val_loss < best_val - 1e-4:
                best_val, worse_in_a_row = val_loss, 0
                best_weights = {k: mx.array(v) for k, v in tree_flatten(model.trainable_parameters())}
                mx.eval(best_weights)
            else:
                worse_in_a_row += 1
                if worse_in_a_row >= 2:
                    log(f"Stopping early at step {it}: held-out loss stopped improving (best {best_val:.3f})")
                    stopped_early = True
                    break
            model.train()

    # 4. Save the best checkpoint as a normal adapter (mlx_lm.server and the evaluator load it like any other).
    if best_weights is not None:
        model.load_weights(list(best_weights.items()), strict=False)
    model.eval()
    log(f"After: Val pref acc {raw_pref_acc(val):.3f}")
    mx.save_safetensors(str(out_dir / "adapters.safetensors"), dict(tree_flatten(model.trainable_parameters())))
    config.update({"adapter_path": str(out_dir), "preference_training": {
        "method": "dpo", "beta": args.beta, "sft_weight": args.sft_weight, "iters": iters,
        "learning_rate": args.learning_rate, "pairs": len(encoded), "sources": sources,
        "stopped_early": stopped_early, "best_val_loss": None if best_val == float("inf") else round(best_val, 4)}})
    (out_dir / "adapter_config.json").write_text(json.dumps(config, indent=2))
    log("Saved adapter")


if __name__ == "__main__":
    main()
