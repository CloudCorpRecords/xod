"""
One memory store for everything Xod remembers (1A/2A/3A, 2026-10-23).

Before this, memory lived in three places that never talked to each other: facts in about_you.json
(services/user_memory.py), long-term memories as embedding blobs in xod.db (services/memory_service.py),
and episodes in recall.db (services/recall.py). A fact told in chat could be "remembered" in one and
invisible to the others — which is how Xod could say "noted" and then ask again.

Now there is one interface, `MemoryStore`, and one record shape for all three kinds:
  kind="fact"     things the person said about themselves (was: about_you.json)
  kind="memory"   long-term notes/learnings (was: xod.db `memories`)
  kind="episode"  things that happened, with a date (was: recall.db `episodes`)

Backends (local first):
  - "chroma" (default): Chroma embedded on this machine (DATA_DIR/chroma). The same code points at a
    bigger Chroma server when XOD_CHROMA_URL is set (e.g. http://192.168.1.10:8000) — replacing local
    with something bigger is a setting, not a rewrite. Other databases can join behind this interface.
  - "sqlite": built-in fallback in DATA_DIR/memory.db (FTS5 + hashed vectors). Used automatically if
    chromadb isn't installed or can't start, so memory never breaks the app; stats() says why.

Embeddings are computed by callers when they can (services/embeddings.py, local nomic) and otherwise a
deterministic hashed vector is used, exactly like the old stores did. Migration (`migrate()`) imports
the three legacy sources by id, keeps every source file/table untouched, and is safe to run twice.
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import numpy as np

from app.paths import DATA_DIR

logger = logging.getLogger("xod.memory_store")

DIM = 768
COLLECTION = "xod_memory"
KINDS = ("fact", "memory", "episode")


def hash_vector(text: str, dim: int = DIM) -> np.ndarray:
    """Deterministic zero-dependency vector (same scheme the old SQLite stores used)."""
    vec = np.zeros(dim, dtype=np.float32)
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\s]", " ", (text or "").lower())
    for token in cleaned.split():
        h = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16)
        vec[h % dim] += 1.5 * (1.0 if h & 1 else -1.0)
        if len(token) >= 3:
            for i in range(len(token) - 2):
                g = int(hashlib.md5(token[i:i + 3].encode()).hexdigest()[:8], 16)
                vec[g % dim] += 0.5 * (1.0 if g & 1 else -1.0)
    n = np.linalg.norm(vec)
    return vec / n if n else vec


# Passwords, keys and card numbers belong in the Keychain vault (services/credential_vault.py,
# services/keychain.py) — never in memory, whichever backend is configured. Same rule the fact
# store already follows (user_memory.SECRET_RE), plus sentinel's token shapes (services/sentinel.py).
_TOKEN_RES = None


def _secret_like(text: str) -> bool:
    global _TOKEN_RES
    if _TOKEN_RES is None:
        import re as _re
        _TOKEN_RES = [_re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
                      _re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
                      _re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}"),
                      _re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
                      _re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
                      _re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}", _re.I)]
    try:
        from app.services.user_memory import SECRET_RE
        if SECRET_RE.search(text):
            return True
    except Exception:
        pass
    return any(rx.search(text) for rx in _TOKEN_RES)


def _norm(rec: Dict[str, Any], embedding: Any = None) -> Dict[str, Any]:
    now = time.time()
    text = str(rec.get("text") or rec.get("content") or "")
    if _secret_like(f"{rec.get('title') or ''} {text}"):
        raise ValueError("looks like a password, key or card number — those live in the Keychain vault, not memory")
    tags = rec.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    out = {
        "id": str(rec.get("id") or f"{rec.get('kind') or 'memory'}:{uuid.uuid4().hex[:12]}"),
        "kind": rec.get("kind") if rec.get("kind") in KINDS else "memory",
        "title": str(rec.get("title") or text[:80]),
        "text": text,
        "category": str(rec.get("category") or "general"),
        "tags": [str(t) for t in tags],
        "importance": float(rec.get("importance") or 3.0),
        "source": str(rec.get("source") or ""),
        "session_id": str(rec.get("session_id") or ""),
        "ts": float(rec.get("ts") or rec.get("created_at") or now),
        "created_at": float(rec.get("created_at") or rec.get("ts") or now),
        "updated_at": float(rec.get("updated_at") or now),
    }
    emb = embedding if embedding is not None else rec.get("embedding")
    if emb is None:
        emb = hash_vector(f"{out['title']} {out['text']} {' '.join(out['tags'])}")
        out["emb_model"] = "local_semantic_hash_768"
    else:
        out["emb_model"] = str(rec.get("emb_model") or rec.get("embedding_model") or "provided")
    out["embedding"] = np.asarray(emb, dtype=np.float32).ravel()
    return out


class MemoryStore:
    """The one interface every memory backend implements. Records are plain dicts (see _norm)."""
    backend = "base"

    def upsert(self, rec: Dict[str, Any], embedding: Any = None) -> Dict[str, Any]:
        raise NotImplementedError

    def get(self, rid: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def delete(self, rid: str) -> bool:
        raise NotImplementedError

    def search(self, query: str, query_embedding: Any = None, kinds: Optional[List[str]] = None,
               category: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list(self, kinds: Optional[List[str]] = None, category: Optional[str] = None,
             limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def stats(self) -> Dict[str, Any]:
        raise NotImplementedError


def _public(r: Dict[str, Any], score: Optional[float] = None) -> Dict[str, Any]:
    out = {k: r[k] for k in ("id", "kind", "title", "text", "category", "tags", "importance",
                             "source", "session_id", "ts", "created_at", "updated_at")}
    if score is not None:
        out["score"] = round(float(score), 4)
    return out


def _row(row) -> Dict[str, Any]:
    emb = row["embedding"]
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "text": row["text"],
            "category": row["category"], "tags": [t for t in (row["tags"] or "").split(",") if t],
            "importance": row["importance"] or 3.0, "source": row["source"] or "",
            "session_id": row["session_id"] or "", "ts": row["ts"] or 0.0,
            "created_at": row["created_at"] or 0.0, "updated_at": row["updated_at"] or 0.0,
            "embedding": np.frombuffer(emb, dtype=np.float32) if emb else None}


class ChromaMemoryStore(MemoryStore):
    backend = "chroma"

    def __init__(self, url: str = "", path=None):
        import chromadb
        from chromadb.config import Settings
        if url:
            u = urlparse(url)
            self._client = chromadb.HttpClient(host=u.hostname or "127.0.0.1", port=u.port or 8000,
                                               ssl=u.scheme == "https",
                                               settings=Settings(anonymized_telemetry=False))
            self.where = url
        else:
            pth = Path(path or (DATA_DIR / "chroma"))
            pth.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(pth),
                                                     settings=Settings(anonymized_telemetry=False))
            self.where = str(pth)
        self._col = self._client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    @staticmethod
    def _meta(r):
        return {"kind": r["kind"], "title": r["title"][:500], "category": r["category"],
                "tags": ",".join(r["tags"]), "importance": r["importance"], "source": r["source"],
                "session_id": r["session_id"], "ts": r["ts"], "created_at": r["created_at"],
                "updated_at": r["updated_at"], "emb_model": r["emb_model"]}

    @staticmethod
    def _from(rid, doc, meta, score=None):
        r = {"id": rid, "kind": meta.get("kind", "memory"), "title": meta.get("title", ""),
             "text": doc or "", "category": meta.get("category", "general"),
             "tags": [t for t in (meta.get("tags") or "").split(",") if t],
             "importance": meta.get("importance", 3.0), "source": meta.get("source", ""),
             "session_id": meta.get("session_id", ""), "ts": meta.get("ts", 0.0),
             "created_at": meta.get("created_at", 0.0), "updated_at": meta.get("updated_at", 0.0)}
        return _public(r, score)

    def upsert(self, rec, embedding=None):
        r = _norm(rec, embedding)
        self._col.upsert(ids=[r["id"]], documents=[r["text"]], metadatas=[self._meta(r)],
                         embeddings=[r["embedding"].tolist()])
        return _public(r)

    def get(self, rid):
        got = self._col.get(ids=[rid], include=["documents", "metadatas"])
        if not got["ids"]:
            return None
        return self._from(got["ids"][0], got["documents"][0], got["metadatas"][0])

    def delete(self, rid):
        if not self.get(rid):
            return False
        self._col.delete(ids=[rid])
        return True

    def search(self, query, query_embedding=None, kinds=None, category=None, limit=5):
        qv = np.asarray(query_embedding, dtype=np.float32).ravel() if query_embedding is not None \
            else hash_vector(query)
        where = {}
        if kinds and len(kinds) == 1:
            where = {"kind": kinds[0]}
        elif kinds:
            where = {"kind": {"$in": list(kinds)}}
        if category:
            where = {"$and": [where, {"category": category}]} if where else {"category": category}
        res = self._col.query(query_embeddings=[qv.tolist()], n_results=max(limit, 1),
                              where=where or None, include=["documents", "metadatas", "distances"])
        return [self._from(rid, doc, meta, score=max(0.0, 1.0 - float(dist)))
                for rid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                                res["metadatas"][0], res["distances"][0])]

    def list(self, kinds=None, category=None, limit=50):
        got = self._col.get(include=["documents", "metadatas"], limit=10000)
        out = [self._from(i, d, m) for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]
        if kinds:
            out = [r for r in out if r["kind"] in kinds]
        if category:
            out = [r for r in out if r["category"] == category]
        out.sort(key=lambda r: -r["updated_at"])
        return out[:limit]

    def stats(self):
        kinds = {}
        for r in self.list(limit=10000):
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        return {"backend": self.backend, "total": self._col.count(), "kinds": kinds, "path": self.where}


# --------------------------------------------------------------------------------------------- factory

_cache: Dict[str, MemoryStore] = {}
fallback_reason = ""


def _chroma_url() -> str:
    return os.environ.get("XOD_CHROMA_URL", "").strip()


def get_store(force: str = "") -> MemoryStore:
    """The configured store. Default: embedded Chroma locally, or the Chroma server at XOD_CHROMA_URL.
    XOD_MEMORY_BACKEND=sqlite (or force="sqlite") picks the built-in store; any Chroma failure falls
    back to it and is reported in stats()["fallback_reason"] instead of breaking memory."""
    global fallback_reason
    backend = (force or os.environ.get("XOD_MEMORY_BACKEND") or "chroma").lower().strip()
    url = _chroma_url() if backend == "chroma" else ""
    key = f"{backend}|{url}|{DATA_DIR}"
    if key in _cache:
        return _cache[key]
    if backend == "chroma":
        try:
            store: MemoryStore = ChromaMemoryStore(url=url)
        except Exception as e:
            fallback_reason = f"chroma unavailable ({e}); using built-in sqlite store"
            logger.warning(f"memory_store: {fallback_reason}")
            store = SqliteMemoryStore()
    else:
        store = SqliteMemoryStore()
    _cache[key] = store
    return store


def stats() -> Dict[str, Any]:
    s = get_store().stats()
    if fallback_reason:
        s["fallback_reason"] = fallback_reason
    s["chroma_url"] = _chroma_url() or None
    return s


# --------------------------------------------------------------------------------------------- migration

def _blob_vec(blob) -> Optional[np.ndarray]:
    if not blob:
        return None
    try:
        v = np.frombuffer(blob, dtype=np.float32)
        return v if v.size else None
    except Exception:
        return None


def migrate(store: Optional[MemoryStore] = None) -> Dict[str, int]:
    """Imports about_you.json facts, xod.db memories and recall.db episodes into the unified store.
    Sources are read-only here (nothing is deleted); ids are stable, so re-running imports nothing new."""
    store = store or get_store()
    counts = {"facts": 0, "memories": 0, "episodes": 0, "skipped": 0}

    counts["refused"] = 0

    def put(rec, embedding=None):
        if store.get(rec["id"]):
            counts["skipped"] += 1
            return False
        try:
            store.upsert(rec, embedding=embedding)
        except ValueError:
            counts["refused"] += 1   # secret-like legacy record: stays out of the unified store
            return False
        return True

    p = DATA_DIR / "about_you.json"
    if p.exists():
        try:
            for f in json.loads(p.read_text()).get("facts", []):
                if put({"id": f"fact:{f['id']}", "kind": "fact", "title": f["text"][:80],
                        "text": f["text"], "category": f.get("topic") or "other",
                        "source": f.get("source") or "", "ts": f.get("added") or 0,
                        "created_at": f.get("added") or 0, "updated_at": f.get("updated") or 0,
                        "importance": 5.0 if f.get("pinned") else 3.0}):
                    counts["facts"] += 1
        except Exception as e:
            logger.warning(f"memory_store migrate facts: {e}")

    db = DATA_DIR / "xod.db"
    if db.exists():
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True); c.row_factory = sqlite3.Row
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
                for r in c.execute("SELECT * FROM memories"):
                    rid = r["id"] if str(r["id"]).startswith("mem_") else f"memory:{r['id']}"
                    if put({"id": rid, "kind": "memory", "title": r["title"], "text": r["content"],
                            "category": r["category"], "tags": r["tags"],
                            "importance": r["importance"], "session_id": r["source_session_id"] or "",
                            "ts": r["created_at"], "created_at": r["created_at"],
                            "updated_at": r["updated_at"], "emb_model": r["embedding_model"]},
                           embedding=_blob_vec(r["embedding"])):
                        counts["memories"] += 1
            c.close()
        except Exception as e:
            logger.warning(f"memory_store migrate memories: {e}")

    db = DATA_DIR / "recall.db"
    if db.exists():
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True); c.row_factory = sqlite3.Row
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='episodes'").fetchone():
                for r in c.execute("SELECT * FROM episodes"):
                    if put({"id": f"episode:{r['id']}", "kind": "episode", "title": r["title"],
                            "text": r["text"], "category": r["kind"] or "episode",
                            "session_id": r["session_id"] or "", "ts": r["ts"],
                            "created_at": r["ts"], "updated_at": r["ts"],
                            "emb_model": r["emb_model"]}, embedding=_blob_vec(r["embedding"])):
                        counts["episodes"] += 1
            c.close()
        except Exception as e:
            logger.warning(f"memory_store migrate episodes: {e}")

    counts["total"] = counts["facts"] + counts["memories"] + counts["episodes"]
    return counts


class SqliteMemoryStore(MemoryStore):
    backend = "sqlite"

    def __init__(self, path=None):
        self.path = Path(path or (DATA_DIR / "memory.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = self._c()
        c.execute("""CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT, title TEXT,
                     text TEXT, category TEXT, tags TEXT, importance REAL, source TEXT, session_id TEXT,
                     ts REAL, created_at REAL, updated_at REAL, embedding BLOB, emb_model TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS records_kind ON records(kind, ts)")
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(id UNINDEXED, title, text)")
        except sqlite3.OperationalError:
            pass
        c.commit(); c.close()

    def _c(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def upsert(self, rec, embedding=None):
        r = _norm(rec, embedding)
        c = self._c()
        c.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (r["id"], r["kind"], r["title"], r["text"], r["category"], ",".join(r["tags"]),
                   r["importance"], r["source"], r["session_id"], r["ts"], r["created_at"],
                   r["updated_at"], r["embedding"].tobytes(), r["emb_model"]))
        try:
            c.execute("DELETE FROM records_fts WHERE id = ?", (r["id"],))
            c.execute("INSERT INTO records_fts (id, title, text) VALUES (?,?,?)",
                      (r["id"], r["title"], r["text"]))
        except sqlite3.OperationalError:
            pass
        c.commit(); c.close()
        return _public(r)

    def get(self, rid):
        c = self._c()
        row = c.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        c.close()
        return _public(_row(row)) if row else None

    def delete(self, rid):
        c = self._c()
        n = c.execute("DELETE FROM records WHERE id = ?", (rid,)).rowcount
        try:
            c.execute("DELETE FROM records_fts WHERE id = ?", (rid,))
        except sqlite3.OperationalError:
            pass
        c.commit(); c.close()
        return n > 0

    def search(self, query, query_embedding=None, kinds=None, category=None, limit=5):
        qv = np.asarray(query_embedding, dtype=np.float32).ravel() if query_embedding is not None \
            else hash_vector(query)
        c = self._c()
        sql, args, where = "SELECT * FROM records", [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})"); args += list(kinds)
        if category:
            where.append("category = ?"); args.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = c.execute(sql, args).fetchall()
        fts = set()
        try:
            toks = [t for t in re.sub(r"[^a-zA-Z0-9_\s]", " ", query or "").split() if len(t) > 1]
            if toks:
                fts = {r[0] for r in c.execute(
                    "SELECT id FROM records_fts WHERE records_fts MATCH ? LIMIT 30",
                    (" ".join(f'"{t}"*' for t in toks),)).fetchall()}
        except sqlite3.OperationalError:
            pass
        c.close()
        qn = np.linalg.norm(qv)
        scored = []
        for row in rows:
            r = _row(row)
            cos = 0.0
            if r["embedding"] is not None and qn:
                cn = np.linalg.norm(r["embedding"])
                if cn:
                    cos = float(np.dot(qv, r["embedding"]) / (qn * cn))
            score = cos * 0.65 + (0.35 if r["id"] in fts else 0.0) + (r["importance"] / 5.0) * 0.1
            if score >= 0.15:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [_public(r, s) for s, r in scored[:limit]]

    def list(self, kinds=None, category=None, limit=50):
        c = self._c()
        sql, args, where = "SELECT * FROM records", [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})"); args += list(kinds)
        if category:
            where.append("category = ?"); args.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = c.execute(sql + " ORDER BY updated_at DESC LIMIT ?", args + [limit]).fetchall()
        c.close()
        return [_public(_row(r)) for r in rows]

    def stats(self):
        c = self._c()
        total = c.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        kinds = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind")}
        c.close()
        return {"backend": self.backend, "total": total, "kinds": kinds, "path": str(self.path)}
