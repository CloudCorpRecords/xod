"""
recipe_store: "Learn Once, Replay Fast" skill/recipe compilation for browser_run
and desktop_run workflows.

When Xod succeeds at a recurring multi-step workflow (posting on LinkedIn,
filling a Greenhouse application, exporting from a native app), compile_recipe()
saves the verified steps with {{param}} placeholders under DATA_DIR/recipes/.
Next time, replay_recipe() runs deterministically in seconds — and if a site's
UI changed on step N, returns exact failure metadata so the agent can fall back
to the LLM on step N instead of restarting from scratch.
"""
import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _dir() -> Path:
    from app.paths import DATA_DIR
    d = DATA_DIR / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "recipe").lower()).strip("-")
    return s[:64] or "recipe"


def _substitute(obj: Any, values: Dict[str, Any]) -> Any:
    if isinstance(obj, str):
        def repl(m):
            key = m.group(1).strip()
            return str(values[key]) if key in values else m.group(0)
        return re.sub(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", repl, obj)
    if isinstance(obj, list):
        return [_substitute(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, values) for k, v in obj.items()}
    return obj


def _extract_params(obj: Any) -> List[str]:
    found = set()
    def walk(x):
        if isinstance(x, str):
            for m in re.finditer(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", x):
                found.add(m.group(1).strip())
        elif isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
    walk(obj)
    return sorted(found)


def compile_recipe(
    name: str,
    surface: str,
    steps: List[Dict[str, Any]],
    *,
    domain: str = "",
    tags: Optional[List[str]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Save a parameterized browser or desktop recipe."""
    if surface not in ("browser", "desktop"):
        raise ValueError(f"recipe surface must be 'browser' or 'desktop', got {surface!r}")
    slug = _slug(name)
    now = time.time()
    rec = {
        "slug": slug,
        "name": str(name).strip(),
        "surface": surface,
        "domain": str(domain or "").lower().strip(),
        "tags": [str(t).lower().strip() for t in (tags or []) if str(t).strip()],
        "description": str(description or "").strip(),
        "params": _extract_params(steps),
        "steps": copy.deepcopy(steps),
        "created_at": now,
        "updated_at": now,
        "stats": {"runs": 0, "successes": 0, "last_failed_step": None},
    }
    (_dir() / f"{slug}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def load_recipe(name_or_slug: str) -> Optional[Dict[str, Any]]:
    p = _dir() / f"{_slug(name_or_slug)}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_recipes(surface: Optional[str] = None, domain: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    dom = str(domain or "").lower().strip()
    for p in sorted(_dir().glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if surface and rec.get("surface") != surface:
            continue
        if dom and dom not in (rec.get("domain") or "").lower():
            continue
        out.append(rec)
    return out


def find_recipe(query: str, domain: str = "") -> Optional[Dict[str, Any]]:
    """Find best matching saved recipe by exact slug, domain, or keyword overlap."""
    exact = load_recipe(query)
    if exact:
        return exact
    q_words = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
    dom = str(domain or "").lower().strip()
    best, best_score = None, 0
    for rec in list_recipes():
        score = 0
        if dom and rec.get("domain") and dom in rec["domain"]:
            score += 4
        hay = set(re.findall(r"[a-z0-9]+", f"{rec.get('name','')} {rec.get('description','')} {' '.join(rec.get('tags') or [])}".lower()))
        score += len(q_words & hay)
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= 2 else None


def render_recipe(recipe: Dict[str, Any], values: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    vals = values or {}
    missing = [p for p in (recipe.get("params") or []) if p not in vals]
    if missing:
        raise ValueError(f"missing recipe parameters: {', '.join(missing)}")
    return _substitute(recipe.get("steps") or [], vals)


async def replay_recipe(
    name_or_slug: str,
    values: Optional[Dict[str, Any]] = None,
    *,
    runner: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Replay a compiled recipe deterministically and update its run statistics."""
    rec = load_recipe(name_or_slug)
    if not rec:
        return {"success": False, "error": f"recipe '{name_or_slug}' not found"}
    try:
        concrete_steps = render_recipe(rec, values)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if runner is None:
        if rec["surface"] == "browser":
            from app.services import browser_run
            res = await browser_run.run_steps(concrete_steps)
        else:
            from app.services import desktop_run
            res = await desktop_run.run_desktop_steps(concrete_steps)
    else:
        res = await runner(concrete_steps)

    stats = rec.setdefault("stats", {"runs": 0, "successes": 0, "last_failed_step": None})
    stats["runs"] = int(stats.get("runs") or 0) + 1
    if res.get("success"):
        stats["successes"] = int(stats.get("successes") or 0) + 1
        stats["last_failed_step"] = None
    else:
        stats["last_failed_step"] = res.get("failed_step")
    rec["updated_at"] = time.time()
    (_dir() / f"{rec['slug']}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return {**res, "recipe": rec["slug"], "surface": rec["surface"], "stats": stats}
