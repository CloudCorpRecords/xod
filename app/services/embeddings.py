"""
Text embeddings from a small model on this computer: nomic-embed-text v1.5, from LM Studio or Ollama.

It's the same model in both (Ollama calls it "nomic-embed-text"), so vectors from either compare with each other
and with ones already stored: memory, recall and the companion's notebook search by meaning whichever app
you use. Never a cloud model. If neither has it, callers fall back to their own hashed vectors.
"""
import logging
import time
from typing import List, Optional

import numpy as np

logger = logging.getLogger("xod.embeddings")

LM_STUDIO = ("http://127.0.0.1:1234/v1/embeddings", "text-embedding-nomic-embed-text-v1.5")
OLLAMA_MODEL = "nomic-embed-text"

_down_until = {}           # url -> time: skip a server that just failed (every answer looks up memories)
_preferred: Optional[str] = None
_warm = set()


def _endpoints():
    try:
        from app.services.ollama_hub import root
        ollama = (f"{root()}/v1/embeddings", OLLAMA_MODEL)
    except Exception:
        ollama = ("http://localhost:11434/v1/embeddings", OLLAMA_MODEL)
    eps = [LM_STUDIO, ollama]
    return sorted(eps, key=lambda e: e[0] != _preferred)       # the one that worked last time first


async def nomic(texts: List[str], timeout: float = 3.0) -> Optional[List[np.ndarray]]:
    """Unit vectors for texts (with nomic's "search_query: "/"search_document: " prefixes already on), or None."""
    global _preferred
    import httpx
    for url, model in _endpoints():
        if time.time() < _down_until.get(url, 0):
            continue
        try:
            # The first request may load the model (several seconds).
            async with httpx.AsyncClient(timeout=timeout if url in _warm else max(timeout, 40.0)) as c:
                res = await c.post(url, json={"model": model, "input": [t[:2000] for t in texts]})
            if res.status_code == 200:
                rows = sorted(res.json().get("data", []), key=lambda d: d.get("index", 0))
                if len(rows) == len(texts):
                    vecs = []
                    for r in rows:
                        v = np.array(r["embedding"], dtype=np.float32)
                        n = np.linalg.norm(v)
                        vecs.append(v / n if n else v)
                    _preferred = url
                    _warm.add(url)
                    return vecs
            _down_until[url] = time.time() + 120
        except Exception as e:
            logger.debug(f"embeddings from {url} unavailable ({e})")
            _down_until[url] = time.time() + 120
    return None
