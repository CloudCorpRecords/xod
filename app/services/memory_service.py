import os
import re
import json
import time
import uuid
import math
import struct
import logging
import hashlib
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import httpx

from app.config import settings
from app.services.storage import get_db_connection

logger = logging.getLogger("memory")

DEFAULT_EMBEDDING_DIM = 768

class MemoryService:
    """
    Persistent Long-Term Memory Engine for Xod.
    Provides local SQLite-backed Vector & FTS5 Hybrid Retrieval for:
      - User Preferences & Coding Habits
      - Project Architecture & Tech Stack Blueprints
      - Agent Learnings & Execution Tricks
      - Knowledge Snippets & Research Artifacts
    """

    def __init__(self):
        self.embedding_dim = DEFAULT_EMBEDDING_DIM

    def _fallback_semantic_vector(self, text: str) -> np.ndarray:
        """
        Deterministic, zero-dependency subword & character n-gram semantic vector generator.
        Produces normalized 768-dimensional float32 vectors.
        Ensures high cosine similarity for overlapping linguistic semantics even offline.
        """
        vec = np.zeros(self.embedding_dim, dtype=np.float32)
        if not text:
            return vec

        cleaned = re.sub(r'[^a-zA-Z0-9_\-\s]', ' ', text.lower())
        tokens = cleaned.split()
        
        # Token and subword hash projection
        for token in tokens:
            # Word-level embedding projection
            h_word = int(hashlib.sha256(token.encode('utf-8')).hexdigest()[:8], 16)
            idx = h_word % self.embedding_dim
            sign = 1.0 if (h_word & 1) else -1.0
            vec[idx] += 1.5 * sign

            # Character n-grams (3-grams) for morphological similarity
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    ngram = token[i:i+3]
                    h_gram = int(hashlib.md5(ngram.encode('utf-8')).hexdigest()[:8], 16)
                    g_idx = h_gram % self.embedding_dim
                    g_sign = 1.0 if (h_gram & 1) else -1.0
                    vec[g_idx] += 0.5 * g_sign

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    async def generate_embedding(self, text: str) -> Tuple[np.ndarray, str]:
        """
        Generates dense vector embeddings.
        Priority:
          1. Local LM Studio (/v1/embeddings) with nomic-embed-text or loaded model
          2. OpenAI (/v1/embeddings) if api_key present
          3. Deterministic Semantic Vector Fallback (always works, 0 latency, 0 token cost)
        """
        text_clean = text.strip().replace("\n", " ")[:3000]
        if not text_clean:
            return np.zeros(self.embedding_dim, dtype=np.float32), "empty"

        # 1. nomic-embed-text from LM Studio or Ollama (services/embeddings.py). Every answer looks up memories
        # first, so this must never hold one up: a short timeout, and a server that failed is skipped for a while.
        from app.services import embeddings
        vecs = await embeddings.nomic([text_clean], timeout=1.5)
        if vecs:
            return vecs[0], "lm_studio_nomic_embed"      # the name stored memories already carry (LM Studio or Ollama: same model)

        # 2. Try OpenAI API if key configured
        if settings.openai_api_key and settings.openai_api_key.startswith("sk-"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    res = await client.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                        json={
                            "model": "text-embedding-3-small",
                            "input": text_clean
                        }
                    )
                    if res.status_code == 200:
                        data = res.json()
                        raw_vec = data.get("data", [{}])[0].get("embedding")
                        if raw_vec:
                            v = np.array(raw_vec, dtype=np.float32)
                            norm = np.linalg.norm(v)
                            if norm > 0:
                                v /= norm
                            return v, "openai_text_embedding_3_small"
            except Exception as e:
                logger.debug(f"OpenAI embeddings error: {e}")

        # 3. Robust local semantic projection fallback
        v = self._fallback_semantic_vector(text_clean)
        return v, "local_semantic_hash_768"

    def store_memory(
        self,
        title: str,
        content: str,
        category: str = "agent_learning",
        tags: Optional[List[str]] = None,
        importance: float = 3.0,
        source_session_id: Optional[str] = None,
        vector: Optional[np.ndarray] = None,
        model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stores or surgically updates a long-term memory in SQLite vector storage.
        """
        now = time.time()
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        tag_str = ",".join([t.strip().lower() for t in tags]) if tags else ""

        if vector is None:
            # Synchronous fallback if vector not precomputed
            vector = self._fallback_semantic_vector(f"{title} {content} {tag_str}")
            model_name = "local_semantic_hash_768"

        emb_bytes = vector.astype(np.float32).tobytes()
        dim = len(vector)

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check for near-duplicate title in same category to update rather than duplicate
        cursor.execute("SELECT id FROM memories WHERE title = ? AND category = ?", (title, category))
        existing = cursor.fetchone()

        if existing:
            target_id = existing["id"]
            cursor.execute("""
                UPDATE memories
                SET content = ?, tags = ?, embedding = ?, embedding_model = ?,
                    embedding_dim = ?, importance = ?, updated_at = ?
                WHERE id = ?
            """, (content, tag_str, emb_bytes, model_name or "local", dim, importance, now, target_id))
            mem_id = target_id
        else:
            cursor.execute("""
                INSERT INTO memories (
                    id, category, title, content, tags, source_session_id,
                    embedding, embedding_model, embedding_dim, importance,
                    access_count, last_accessed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """, (
                mem_id, category, title, content, tag_str, source_session_id,
                emb_bytes, model_name or "local", dim, importance,
                now, now, now
            ))

        conn.commit()
        conn.close()

        try:  # unified store (services/memory_store.py): same record, searchable with facts + episodes
            from app.services import memory_store
            memory_store.get_store().upsert(
                {"id": mem_id, "kind": "memory", "title": title, "text": content, "category": category,
                 "tags": tags or [], "importance": importance, "session_id": source_session_id or "",
                 "created_at": now, "updated_at": now}, embedding=vector)
        except Exception as e:
            logger.debug(f"memory_store mirror failed: {e}")

        logger.info(f"Stored long-term memory [{category}] '{title}' (ID: {mem_id})")
        return {
            "id": mem_id,
            "category": category,
            "title": title,
            "content": content,
            "tags": tags or [],
            "importance": importance,
            "model": model_name,
            "updated_at": now
        }

    async def store_memory_async(
        self,
        title: str,
        content: str,
        category: str = "agent_learning",
        tags: Optional[List[str]] = None,
        importance: float = 3.0,
        source_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Async wrapper that computes dense embeddings via provider before storage."""
        embed_text = f"{title} {content} {' '.join(tags or [])}"
        vector, model_name = await self.generate_embedding(embed_text)
        return self.store_memory(
            title=title,
            content=content,
            category=category,
            tags=tags,
            importance=importance,
            source_session_id=source_session_id,
            vector=vector,
            model_name=model_name
        )

    def search_memories(
        self,
        query: str,
        query_vector: Optional[np.ndarray] = None,
        category: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.15
    ) -> List[Dict[str, Any]]:
        """
        Hybrid Vector & Lexical search:
          - Vector Cosine Similarity (Semantic concepts)
          - SQLite FTS5 Match (Exact keywords & identifiers)
          - Importance & Recency weighting
        """
        if not query and query_vector is None:
            return self.list_memories(category=category, limit=limit)

        if query_vector is None:
            query_vector = self._fallback_semantic_vector(query)

        try:  # unified store first; legacy path below is the fallback
            from app.services import memory_store
            hits = memory_store.get_store().search(
                query, query_embedding=query_vector, kinds=["memory"],
                category=category if category and category != "all" else None, limit=limit)
            hits = [h for h in hits if h.get("score", 0) >= min_score]
            if hits:
                return [{"id": h["id"], "category": h["category"], "title": h["title"],
                         "content": h["text"], "tags": h["tags"], "importance": h["importance"],
                         "access_count": 0, "similarity_score": h.get("score", 0),
                         "hybrid_score": h.get("score", 0), "updated_at": h["updated_at"]} for h in hits]
        except Exception as e:
            logger.debug(f"memory_store search failed, using legacy: {e}")

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Fetch keyword matches via FTS5 if available
        fts_scores = {}
        try:
            # Clean query for FTS5 (remove special syntax characters)
            fts_query = re.sub(r'[^a-zA-Z0-9_\s]', ' ', query).strip()
            if fts_query:
                tokens = [f'"{tok}"*' for tok in fts_query.split() if len(tok) > 1]
                if tokens:
                    fts_sql = " ".join(tokens)
                    cursor.execute("""
                        SELECT rowid, rank FROM memories_fts
                        WHERE memories_fts MATCH ?
                        ORDER BY rank
                        LIMIT 30
                    """, (fts_sql,))
                    for row in cursor.fetchall():
                        # FTS5 rank is negative (lower = better match)
                        fts_scores[row[0]] = abs(float(row[1]))
        except Exception as fts_err:
            logger.debug(f"FTS5 match fallback: {fts_err}")

        # 2. Fetch candidate memories
        sql = "SELECT rowid, id, category, title, content, tags, embedding, embedding_dim, importance, access_count, last_accessed_at, created_at, updated_at FROM memories"
        params = []
        if category and category != "all":
            sql += " WHERE category = ?"
            params.append(category)

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()

        if not rows:
            conn.close()
            return []

        scored_results = []
        q_norm = np.linalg.norm(query_vector)

        for row in rows:
            rowid = row[0]
            emb_blob = row["embedding"]
            cos_sim = 0.0

            if emb_blob:
                try:
                    cand_vec = np.frombuffer(emb_blob, dtype=np.float32)
                    c_norm = np.linalg.norm(cand_vec)
                    if q_norm > 0 and c_norm > 0:
                        cos_sim = float(np.dot(query_vector, cand_vec) / (q_norm * c_norm))
                except Exception:
                    cos_sim = 0.0

            # FTS bonus
            fts_bonus = 0.35 if rowid in fts_scores else 0.0
            
            # Importance multiplier (1.0 - 5.0 -> 0.0 - 0.1 bonus)
            importance = row["importance"] or 3.0
            importance_bonus = (importance / 5.0) * 0.1

            # Composite hybrid score
            hybrid_score = (cos_sim * 0.65) + fts_bonus + importance_bonus

            if hybrid_score >= min_score:
                tags_list = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
                scored_results.append({
                    "id": row["id"],
                    "category": row["category"],
                    "title": row["title"],
                    "content": row["content"],
                    "tags": tags_list,
                    "importance": importance,
                    "access_count": row["access_count"] or 0,
                    "similarity_score": round(max(0.0, min(1.0, cos_sim)), 4),
                    "hybrid_score": round(hybrid_score, 4),
                    "updated_at": row["updated_at"]
                })

        # Sort descending by hybrid score (the old made-up seed memories are never recalled)
        scored_results = [r for r in scored_results if r.get("title") not in self.SEED_TITLES]
        scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
        top_results = scored_results[:limit]

        # Update access count for retrieved items
        now = time.time()
        for item in top_results:
            cursor.execute("""
                UPDATE memories
                SET access_count = access_count + 1, last_accessed_at = ?
                WHERE id = ?
            """, (now, item["id"]))
        conn.commit()
        conn.close()

        return top_results

    async def search_memories_async(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.15
    ) -> List[Dict[str, Any]]:
        """Async vector search: resolves dense query vector via model before search."""
        query_vec, _ = await self.generate_embedding(query)
        return self.search_memories(
            query=query,
            query_vector=query_vec,
            category=category,
            limit=limit,
            min_score=min_score
        )

    def list_memories(
        self,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Lists memories ordered by most recently updated."""
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = "SELECT id, category, title, content, tags, importance, access_count, updated_at FROM memories"
        params = []
        if category and category != "all":
            sql += " WHERE category = ?"
            params.append(category)

        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        conn.close()

        results = []
        for r in rows:
            tags_list = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
            results.append({
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "content": r["content"],
                "tags": tags_list,
                "importance": r["importance"],
                "access_count": r["access_count"],
                "updated_at": r["updated_at"]
            })
        return results

    def delete_memory(self, memory_id: str) -> bool:
        """Deletes a memory by ID."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        try:
            from app.services import memory_store
            deleted = memory_store.get_store().delete(memory_id) or deleted
        except Exception:
            pass
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Returns long-term vector memory repository telemetry."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM memories")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT category, COUNT(*) FROM memories GROUP BY category")
        cat_rows = cursor.fetchall()
        breakdown = {r[0]: r[1] for r in cat_rows}

        cursor.execute("SELECT embedding_model, COUNT(*) FROM memories GROUP BY embedding_model")
        model_rows = cursor.fetchall()
        models = {r[0] or "unknown": r[1] for r in model_rows}

        conn.close()

        out = {
            "total_memories": total,
            "category_breakdown": breakdown,
            "models_used": models,
            "engine": "SQLite Vector + FTS5 Hybrid"
        }
        try:
            from app.services import memory_store
            out["store"] = memory_store.stats()
            out["engine"] = f"unified ({out['store']['backend']}) + legacy SQLite"
        except Exception:
            pass
        return out

    async def search_unified_async(
        self,
        query: str,
        kinds=None,
        limit: int = 5
    ):
        """Searches every kind of memory (fact + memory + episode) in the unified store."""
        query_vec, _ = await self.generate_embedding(query)
        from app.services import memory_store
        return memory_store.get_store().search(query, query_embedding=query_vec, kinds=kinds, limit=limit)

    async def auto_index_workspace(self, workspace_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans and indexes the current project repository into persistent vector memory:
          - Reads README.md, package.json, requirements.txt, pyproject.toml
          - Scans top-level architecture and key entry points
          - Memorizes directory structure, framework stack, and development rules
        """
        target_path = Path(workspace_path) if workspace_path else Path(os.getcwd())
        if not target_path.exists():
            return {"success": False, "error": f"Path {target_path} not found"}

        memories_created = []

        # 1. Project Overview & Readme
        readme_candidates = [target_path / "README.md", target_path / "readme.md", target_path / "README.txt"]
        readme_content = ""
        for r in readme_candidates:
            if r.exists():
                try:
                    with open(r, "r", encoding="utf-8", errors="replace") as f:
                        readme_content = f.read(4000)
                    break
                except Exception:
                    pass

        if readme_content:
            res = await self.store_memory_async(
                title=f"{target_path.name}: Project Overview & Architecture",
                content=f"Project root at {target_path}.\n\nREADME Summary:\n{readme_content[:2500]}",
                category="project_architecture",
                tags=["overview", "readme", "architecture", target_path.name.lower()],
                importance=5.0
            )
            memories_created.append(res)

        # 2. Dependency & Stack Detection
        stack_details = []
        req_file = target_path / "requirements.txt"
        if req_file.exists():
            try:
                with open(req_file, "r") as f:
                    deps = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                stack_details.append(f"Python Requirements: {', '.join(deps[:30])}")
            except Exception:
                pass

        pkg_file = target_path / "package.json"
        if pkg_file.exists():
            try:
                with open(pkg_file, "r") as f:
                    pkg_data = json.load(f)
                deps = list(pkg_data.get("dependencies", {}).keys())
                dev_deps = list(pkg_data.get("devDependencies", {}).keys())
                stack_details.append(f"Node.js Dependencies: {', '.join((deps + dev_deps)[:30])}")
            except Exception:
                pass

        if stack_details:
            res = await self.store_memory_async(
                title=f"{target_path.name}: Tech Stack & Dependencies",
                content="\n\n".join(stack_details),
                category="project_architecture",
                tags=["dependencies", "stack", "packages", target_path.name.lower()],
                importance=4.5
            )
            memories_created.append(res)

        # 3. Codebase Layout & Directory Hierarchy
        dir_structure = []
        try:
            for item in sorted(target_path.iterdir()):
                if item.name.startswith(".") or item.name in ("node_modules", ".venv", "__pycache__", "venv"):
                    continue
                if item.is_dir():
                    sub_items = [s.name for s in item.iterdir() if not s.name.startswith(".")] [:8]
                    dir_structure.append(f"📁 {item.name}/: {', '.join(sub_items)}")
                else:
                    dir_structure.append(f"📄 {item.name}")
        except Exception:
            pass

        if dir_structure:
            res = await self.store_memory_async(
                title=f"{target_path.name}: Core Directory Structure",
                content=f"Directory hierarchy for {target_path}:\n" + "\n".join(dir_structure),
                category="project_architecture",
                tags=["structure", "folders", "layout", target_path.name.lower()],
                importance=4.0
            )
            memories_created.append(res)

        return {
            "success": True,
            "workspace": str(target_path),
            "indexed_memories": len(memories_created),
            "memories": memories_created
        }

    SEED_TITLES = ("User Preference: Frontend & Code Aesthetic", "Xod Architecture: Modular Services & FastAPI Backend",
                   "Agent Learning: Terminal Streaming & Process Management")

    def seed_initial_memories_if_empty(self):
        """No longer seeds anything: the three it used to write were made up (one claimed a UI taste the user never
        stated) and were recalled as if they were the user's. What's known about the user lives in user_memory."""
        return
        stats = self.get_stats()
        if stats.get("total_memories", 0) > 0:
            return

        # 1. User Preference: Modern UI & Development Principles
        self.store_memory(
            title="User Preference: Frontend & Code Aesthetic",
            content="The user prefers sleek, modern dark mode interfaces with specular highlights, Tailwind CSS, Lucide icons, responsive split panes, and real-time streaming feedback without blocking delays.",
            category="user_preference",
            tags=["ui", "frontend", "styling", "tailwind", "design"],
            importance=5.0
        )

        # 2. Project Architecture: Xod Core System
        self.store_memory(
            title="Xod Architecture: Modular Services & FastAPI Backend",
            content="Xod is built with FastAPI in app/main.py, modular services in app/services/, dynamic agent loops in app/agent/researcher.py, and pure vanilla JS + Tailwind CSS in app/static/. Database state lives in data/xod.db.",
            category="project_architecture",
            tags=["architecture", "fastapi", "backend", "sqlite", "xod"],
            importance=5.0
        )

        # 3. Agent Learning: Safe Subprocess Execution & Background Ports
        self.store_memory(
            title="Agent Learning: Terminal Streaming & Process Management",
            content="When running long shell commands or development servers, use /api/computer/run-stream to emit real-time stdout/stderr lines. Never let Chrome or Uvicorn hold stale singleton locks or listening sockets.",
            category="agent_learning",
            tags=["terminal", "streaming", "process", "lock", "debugging"],
            importance=4.5
        )

        logger.info("Seeded initial persistent vector memories into SQLite.")

memory_service = MemoryService()
