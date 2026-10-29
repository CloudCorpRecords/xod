import os
import json
import time
import uuid
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("storage")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = DATA_DIR / "xod.db"

def get_db_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes SQLite tables for persistent settings, chat sessions, and messages."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at REAL
    )
    """)

    # 2. Chat Sessions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        persona TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """)

    # 3. Messages Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        reasoning TEXT,
        tool_events TEXT,
        created_at REAL NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """)

    # 4. Bookmarks Table (Saved startups & deals)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookmarks (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT,
        mrr TEXT,
        asking_price TEXT,
        notes TEXT,
        saved_at REAL NOT NULL
    )
    """)

    # 5. Persistent SaaS Knowledge Graph Entities Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS entities (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT,
        website TEXT,
        verified_mrr TEXT,
        last_30d_revenue TEXT,
        total_revenue TEXT,
        arr_multiple TEXT,
        growth_30d TEXT,
        active_subscriptions INTEGER,
        tech_stack TEXT,
        pricing_signals TEXT,
        due_diligence_score INTEGER,
        dossier_summary TEXT,
        research_count INTEGER DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """)

    # 6. Knowledge Graph Relationships Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS entity_relationships (
        id TEXT PRIMARY KEY,
        source_slug TEXT NOT NULL,
        target_slug TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        notes TEXT,
        created_at REAL NOT NULL,
        FOREIGN KEY(source_slug) REFERENCES entities(slug) ON DELETE CASCADE
    )
    """)

    # Ensure tags column exists for enriched knowledge graph
    try:
        cursor.execute("ALTER TABLE entities ADD COLUMN tags TEXT")
    except sqlite3.OperationalError:
        pass

    # Where a chat was started: desktop, or phone (the paired phone app, through phone_api)
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'desktop'")
    except sqlite3.OperationalError:
        pass

    # 7. Persistent Long-Term Vector Memories Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT,
        source_session_id TEXT,
        embedding BLOB,
        embedding_model TEXT,
        embedding_dim INTEGER,
        importance REAL DEFAULT 3.0,
        access_count INTEGER DEFAULT 0,
        last_accessed_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """)

    # 8. Full-Text Search Index (FTS5) for Memories
    try:
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title,
            content,
            tags,
            category,
            content='memories',
            content_rowid='rowid'
        )
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
          INSERT INTO memories_fts(rowid, title, content, tags, category)
          VALUES (new.rowid, new.title, new.content, new.tags, new.category);
        END;
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, category)
          VALUES('delete', old.rowid, old.title, old.content, old.tags, old.category);
        END;
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, category)
          VALUES('delete', old.rowid, old.title, old.content, old.tags, old.category);
          INSERT INTO memories_fts(rowid, title, content, tags, category)
          VALUES (new.rowid, new.title, new.content, new.tags, new.category);
        END;
        """)
    except Exception as fts_err:
        logger.warning("FTS5 virtual table setup skipped: %s", fts_err)

    conn.commit()
    conn.close()
    logger.info("Persistent SQLite database initialized at %s", DB_PATH)

# -------------------------------------------------------------
# SETTINGS STORAGE
# -------------------------------------------------------------
def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["value"] or default
    return default

def set_setting(key: str, value: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, value, time.time()))
    conn.commit()
    conn.close()

def delete_setting(key: str) -> None:
    """Removes a setting and overwrites its bytes on disk (used to scrub old plain-text API keys)."""
    conn = get_db_connection()
    conn.execute("PRAGMA secure_delete = ON")
    cur = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
    if cur.rowcount:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    conn.close()

def get_all_settings() -> Dict[str, str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

# -------------------------------------------------------------
# SESSIONS STORAGE
# -------------------------------------------------------------
def create_session(title: str = "New Research Session", persona: str = "ultimate_researcher",
                   source: str = "desktop") -> Dict[str, Any]:
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    now = time.time()
    source = source if source in ("desktop", "phone") else "desktop"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (id, title, persona, created_at, updated_at, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, title, persona, now, now, source))
    conn.commit()
    conn.close()
    return {
        "id": session_id,
        "title": title,
        "persona": persona,
        "created_at": now,
        "updated_at": now,
        "source": source
    }

def list_sessions() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.title, s.persona, s.created_at, s.updated_at, s.source,
               COUNT(m.id) as message_count
        FROM sessions s
        LEFT JOIN messages m ON s.id = m.session_id
        GROUP BY s.id
        ORDER BY s.updated_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "persona": row["persona"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
            "source": row["source"] or "desktop"
        }
        for row in rows
    ]

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def update_session(session_id: str, title: Optional[str] = None, persona: Optional[str] = None):
    now = time.time()
    conn = get_db_connection()
    cursor = conn.cursor()
    if title and persona:
        cursor.execute("UPDATE sessions SET title = ?, persona = ?, updated_at = ? WHERE id = ?", (title, persona, now, session_id))
    elif title:
        cursor.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, now, session_id))
    elif persona:
        cursor.execute("UPDATE sessions SET persona = ?, updated_at = ? WHERE id = ?", (persona, now, session_id))
    else:
        cursor.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    conn.commit()
    conn.close()

def delete_session(session_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

# -------------------------------------------------------------
# MESSAGES STORAGE
# -------------------------------------------------------------
def add_message(
    session_id: str,
    role: str,
    content: str,
    reasoning: str = "",
    tool_events: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = time.time()
    tool_events_json = json.dumps(tool_events or [])

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO messages (id, session_id, role, content, reasoning, tool_events, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (msg_id, session_id, role, content, reasoning, tool_events_json, now))

    # Touch session updated_at
    cursor.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    conn.commit()
    conn.close()

    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "reasoning": reasoning,
        "tool_events": tool_events or [],
        "created_at": now
    }

def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM messages
        WHERE session_id = ?
        ORDER BY created_at ASC
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        tools = []
        if r["tool_events"]:
            try:
                tools = json.loads(r["tool_events"])
            except Exception:
                tools = []
        result.append({
            "id": r["id"],
            "session_id": r["session_id"],
            "role": r["role"],
            "content": r["content"],
            "reasoning": r["reasoning"] or "",
            "tool_events": tools,
            "created_at": r["created_at"]
        })
    return result

def get_message(message_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM messages
        WHERE id = ?
    """, (message_id,))
    r = cursor.fetchone()
    conn.close()
    if not r:
        return None

    tools = []
    if r["tool_events"]:
        try:
            tools = json.loads(r["tool_events"])
        except Exception:
            tools = []

    return {
        "id": r["id"],
        "session_id": r["session_id"],
        "role": r["role"],
        "content": r["content"],
        "reasoning": r["reasoning"] or "",
        "tool_events": tools,
        "created_at": r["created_at"]
    }

# -------------------------------------------------------------
# SAAS KNOWLEDGE GRAPH & ENTITY STORAGE
# -------------------------------------------------------------
def save_or_update_entity(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upserts an analyzed startup entity into the local persistent Knowledge Graph.
    Increments research count if already indexed.
    """
    slug = (data.get("slug") or data.get("name", "startup")).lower().replace(" ", "-").replace(".", "")
    name = data.get("name") or slug.capitalize()
    now = time.time()

    tech_stack_json = json.dumps(data.get("tech_stack") or [])
    pricing_signals_json = json.dumps(data.get("pricing_signals") or {})
    tags_json = json.dumps(data.get("tags") or [])

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if entity already exists
    cursor.execute("SELECT research_count, created_at FROM entities WHERE slug = ?", (slug,))
    existing = cursor.fetchone()

    if existing:
        research_count = existing["research_count"] + 1
        created_at = existing["created_at"]
        cursor.execute("""
            UPDATE entities SET
                name = COALESCE(?, name),
                category = COALESCE(?, category),
                website = COALESCE(?, website),
                verified_mrr = COALESCE(?, verified_mrr),
                last_30d_revenue = COALESCE(?, last_30d_revenue),
                total_revenue = COALESCE(?, total_revenue),
                arr_multiple = COALESCE(?, arr_multiple),
                growth_30d = COALESCE(?, growth_30d),
                active_subscriptions = COALESCE(?, active_subscriptions),
                tech_stack = ?,
                pricing_signals = ?,
                tags = CASE WHEN ? != '[]' THEN ? ELSE tags END,
                due_diligence_score = COALESCE(?, due_diligence_score),
                dossier_summary = COALESCE(?, dossier_summary),
                research_count = ?,
                updated_at = ?
            WHERE slug = ?
        """, (
            name, data.get("category"), data.get("website"),
            data.get("verified_mrr"), data.get("last_30d_revenue"),
            data.get("total_revenue"), data.get("arr_multiple"),
            data.get("growth_30d"), data.get("active_subscriptions"),
            tech_stack_json, pricing_signals_json,
            tags_json, tags_json,
            data.get("due_diligence_score"), data.get("dossier_summary"),
            research_count, now, slug
        ))
    else:
        research_count = 1
        created_at = now
        cursor.execute("""
            INSERT INTO entities (
                slug, name, category, website, verified_mrr, last_30d_revenue,
                total_revenue, arr_multiple, growth_30d, active_subscriptions,
                tech_stack, pricing_signals, tags, due_diligence_score, dossier_summary,
                research_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, name, data.get("category"), data.get("website"),
            data.get("verified_mrr"), data.get("last_30d_revenue"),
            data.get("total_revenue"), data.get("arr_multiple"),
            data.get("growth_30d"), data.get("active_subscriptions"),
            tech_stack_json, pricing_signals_json, tags_json,
            data.get("due_diligence_score"), data.get("dossier_summary"),
            1, now, now
        ))

    conn.commit()
    conn.close()

    return {
        "slug": slug,
        "name": name,
        "research_count": research_count,
        "updated_at": now
    }

def list_entities(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns list of all saved entities in the local Knowledge Graph."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM entities
        ORDER BY updated_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        tech = []
        pricing = {}
        tags = []
        if r["tech_stack"]:
            try:
                tech = json.loads(r["tech_stack"])
            except Exception:
                pass
        if r["pricing_signals"]:
            try:
                pricing = json.loads(r["pricing_signals"])
            except Exception:
                pass
        if "tags" in r.keys() and r["tags"]:
            try:
                tags = json.loads(r["tags"])
            except Exception:
                pass

        result.append({
            "slug": r["slug"],
            "name": r["name"],
            "category": r["category"],
            "website": r["website"],
            "verified_mrr": r["verified_mrr"],
            "last_30d_revenue": r["last_30d_revenue"],
            "total_revenue": r["total_revenue"],
            "arr_multiple": r["arr_multiple"],
            "growth_30d": r["growth_30d"],
            "active_subscriptions": r["active_subscriptions"],
            "tech_stack": tech,
            "pricing_signals": pricing,
            "tags": tags,
            "due_diligence_score": r["due_diligence_score"],
            "dossier_summary": r["dossier_summary"],
            "research_count": r["research_count"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"]
        })
    return result

def get_entity(slug: str) -> Optional[Dict[str, Any]]:
    """Retrieves full profile of a single entity from Knowledge Graph including relationships."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM entities WHERE slug = ?", (slug.lower(),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    tech = []
    pricing = {}
    tags = []
    if row["tech_stack"]:
        try:
            tech = json.loads(row["tech_stack"])
        except Exception:
            pass
    if row["pricing_signals"]:
        try:
            pricing = json.loads(row["pricing_signals"])
        except Exception:
            pass
    if "tags" in row.keys() and row["tags"]:
        try:
            tags = json.loads(row["tags"])
        except Exception:
            pass

    relationships = get_entity_relationships(slug)

    return {
        "slug": row["slug"],
        "name": row["name"],
        "category": row["category"],
        "website": row["website"],
        "verified_mrr": row["verified_mrr"],
        "last_30d_revenue": row["last_30d_revenue"],
        "total_revenue": row["total_revenue"],
        "arr_multiple": row["arr_multiple"],
        "growth_30d": row["growth_30d"],
        "active_subscriptions": row["active_subscriptions"],
        "tech_stack": tech,
        "pricing_signals": pricing,
        "tags": tags,
        "relationships": relationships,
        "due_diligence_score": row["due_diligence_score"],
        "dossier_summary": row["dossier_summary"],
        "research_count": row["research_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }

def add_or_update_relationship(source_slug: str, target_slug: str, relationship_type: str = "competitor", notes: str = "") -> Dict[str, Any]:
    """Adds a directional or mutual relationship link between two startups in the Knowledge Graph."""
    rel_id = f"rel_{uuid.uuid4().hex[:10]}"
    now = time.time()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO entity_relationships (id, source_slug, target_slug, relationship_type, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (rel_id, source_slug.lower(), target_slug.lower(), relationship_type, notes, now))
    conn.commit()
    conn.close()
    return {"id": rel_id, "source": source_slug, "target": target_slug, "type": relationship_type}

def get_entity_relationships(slug: str) -> List[Dict[str, Any]]:
    """Retrieves all competitor, alternative, or dependency relationships for an entity."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM entity_relationships
        WHERE source_slug = ? OR target_slug = ?
        ORDER BY created_at DESC
    """, (slug.lower(), slug.lower()))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "source_slug": r["source_slug"],
            "target_slug": r["target_slug"],
            "relationship_type": r["relationship_type"],
            "notes": r["notes"],
            "created_at": r["created_at"]
        }
        for r in rows
    ]

def update_entity_enrichment(
    slug: str,
    tags: Optional[List[str]] = None,
    notes: Optional[str] = None,
    competitor_slugs: Optional[List[str]] = None,
    metrics_update: Optional[Dict[str, Any]] = None,
    due_diligence_score: Optional[int] = None
) -> Dict[str, Any]:
    """
    Enables the agent to self-update and enrich an entity profile in the persistent Knowledge Graph:
    adding tags, updating diligence notes, linking competitors, and adjusting metrics.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM entities WHERE slug = ?", (slug.lower(),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"error": f"Entity '{slug}' not found in Knowledge Graph."}

    now = time.time()
    existing_tags = []
    if "tags" in row.keys() and row["tags"]:
        try:
            existing_tags = json.loads(row["tags"])
        except Exception:
            pass

    if tags:
        for t in tags:
            clean_tag = t.strip().lower()
            if clean_tag and clean_tag not in existing_tags:
                existing_tags.append(clean_tag)

    tags_json = json.dumps(existing_tags)
    dossier = notes or row["dossier_summary"]
    score = due_diligence_score if due_diligence_score is not None else row["due_diligence_score"]

    cursor.execute("""
        UPDATE entities
        SET tags = ?, dossier_summary = ?, due_diligence_score = ?, updated_at = ?
        WHERE slug = ?
    """, (tags_json, dossier, score, now, slug.lower()))

    # Update metrics if specified
    if metrics_update:
        if "verified_mrr" in metrics_update:
            cursor.execute("UPDATE entities SET verified_mrr = ? WHERE slug = ?", (str(metrics_update["verified_mrr"]), slug.lower()))
        if "growth_30d" in metrics_update:
            cursor.execute("UPDATE entities SET growth_30d = ? WHERE slug = ?", (str(metrics_update["growth_30d"]), slug.lower()))

    conn.commit()
    conn.close()

    # Link competitors
    linked_competitors = []
    if competitor_slugs:
        for comp in competitor_slugs:
            comp_clean = comp.strip().lower()
            if comp_clean and comp_clean != slug.lower():
                add_or_update_relationship(slug, comp_clean, "competitor", f"Identified as competitor by research agent")
                linked_competitors.append(comp_clean)

    return {
        "success": True,
        "slug": slug,
        "tags": existing_tags,
        "dossier_summary": dossier,
        "due_diligence_score": score,
        "linked_competitors": linked_competitors,
        "updated_at": now
    }

def search_entities(query: str) -> List[Dict[str, Any]]:
    """Searches Knowledge Graph by name, category, or tech stack."""
    q = f"%{query.lower()}%"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM entities
        WHERE lower(name) LIKE ? OR lower(category) LIKE ? OR lower(tech_stack) LIKE ?
        ORDER BY updated_at DESC
        LIMIT 10
    """, (q, q, q))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "slug": r["slug"],
            "name": r["name"],
            "category": r["category"],
            "verified_mrr": r["verified_mrr"],
            "due_diligence_score": r["due_diligence_score"],
            "website": r["website"]
        })
    return result

# Auto-initialize DB when module loads
init_db()
