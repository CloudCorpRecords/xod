import os
import json
import time
import re
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import sqlite3
from app.services.storage import DATA_DIR, get_db_connection

logger = logging.getLogger("project_storage")

PROJECTS_DIR = DATA_DIR / "projects"

def format_relative_time(timestamp: float) -> str:
    """Formats unix timestamp into concise relative time like '4h', '10d', '12m'."""
    if not timestamp:
        return "now"
    diff = max(0, time.time() - timestamp)
    if diff < 60:
        return f"{int(diff)}s"
    elif diff < 3600:
        return f"{int(diff // 60)}m"
    elif diff < 86400:
        return f"{int(diff // 3600)}h"
    else:
        return f"{int(diff // 86400)}d"

def _apps_in(folder) -> List[Dict[str, Any]]:
    """Apps the builder made in this project (they show under it in the sidebar)."""
    try:
        from app.services import previews
        return [{"title": a.get("title") or Path(a["path"]).name, "path": a["path"], "url": a["url"], "root": a["root"]}
                for a in previews.find_apps(str(folder))]
    except Exception:
        return []


class ProjectStorageService:
    """
    Manages physical project folders on the local computer, storing data, dossiers,
    session transcripts, and files for the user with high-performance search
    and holistic observability telemetry (knowledge graphs, metrics, file explorers).
    """

    def __init__(self):
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_sessions_project_column()
        self._ensure_linked_projects_table()
        self._seed_default_projects_if_empty()

    def _ensure_linked_projects_table(self):
        """Ensures the SQLite table for linked computer folders exists."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS linked_projects (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                description TEXT,
                color TEXT,
                icon TEXT,
                tags TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to create linked_projects table: {e}")

    def _ensure_sessions_project_column(self):
        """Ensures the SQLite sessions table has a project_slug column."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(sessions)")
            columns = [r[1] for r in cursor.fetchall()]
            if "project_slug" not in columns:
                cursor.execute("ALTER TABLE sessions ADD COLUMN project_slug TEXT")
                conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to alter sessions table for project_slug: {e}")

    def _seed_default_projects_if_empty(self):
        """Creates one starter project on first run."""
        existing = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
        if existing:
            return

        initial_projects = [
            {
                "slug": "research",
                "name": "Getting started",
                "description": "A first project. Drag chats and files here, or make your own with the folder button.",
                "color": "#6366f1",
                "icon": "sparkles",
                "tags": ["welcome"],
                "sample_files": [
                    {
                        "subfolder": "notes",
                        "filename": "welcome.md",
                        "content": (
                            "# Welcome to Xod\n\n"
                            "- Projects hold chats and saved files. Drag a chat onto a project to file it.\n"
                            "- Click the target icon on a project to focus Xod on it; new chats then land there.\n"
                            "- Research Xod saves (dossiers, notes) shows up inside the project.\n"
                        ),
                    }
                ],
            }
        ]

        now = time.time()
        for idx, p in enumerate(initial_projects):
            p_dir = PROJECTS_DIR / p["slug"]
            p_dir.mkdir(parents=True, exist_ok=True)
            (p_dir / "dossiers").mkdir(exist_ok=True)
            (p_dir / "sessions").mkdir(exist_ok=True)
            (p_dir / "notes").mkdir(exist_ok=True)

            meta = {
                "id": f"proj_{p['slug']}",
                "slug": p["slug"],
                "name": p["name"],
                "description": p["description"],
                "color": p["color"],
                "icon": p["icon"],
                "tags": p["tags"],
                "created_at": now - (idx * 86400 * 2),
                "updated_at": now - (idx * 3600 * 4)
            }
            with open(p_dir / "project.json", "w") as f:
                json.dump(meta, f, indent=2)

            for sf in p.get("sample_files", []):
                file_path = p_dir / sf["subfolder"] / sf["filename"]
                with open(file_path, "w") as f:
                    f.write(sf["content"])

    def list_projects(self) -> List[Dict[str, Any]]:
        """
        Lists all projects on the local filesystem with their files, linked sessions,
        and observability telemetry.
        """
        projects = []
        if not PROJECTS_DIR.exists():
            return []

        # Get session counts per project from SQLite
        session_map: Dict[str, List[Dict[str, Any]]] = {}
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, persona, created_at, updated_at, COALESCE(project_slug, '') as p_slug FROM sessions ORDER BY updated_at DESC")
            for row in cursor.fetchall():
                p_slug = row["p_slug"]
                if p_slug not in session_map:
                    session_map[p_slug] = []
                session_map[p_slug].append({
                    "id": row["id"],
                    "title": row["title"],
                    "persona": row["persona"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "time_ago_str": format_relative_time(row["updated_at"]),
                    "is_active": (time.time() - row["updated_at"]) < 3600 * 24
                })
            conn.close()
        except Exception as e:
            logger.warning(f"Error fetching sessions for projects: {e}")

        for p_path in sorted(PROJECTS_DIR.iterdir()):
            if not p_path.is_dir():
                continue
            slug = p_path.name
            meta_file = p_path / "project.json"
            meta = {}
            if meta_file.exists():
                try:
                    with open(meta_file, "r") as f:
                        meta = json.load(f)
                except Exception:
                    pass

            name = meta.get("name") or slug.replace("_", " ").replace("-", " ").title()
            desc = meta.get("description") or f"Local project repository for {name}."
            color = meta.get("color") or "#6366f1"
            icon = meta.get("icon") or "folder"
            tags = meta.get("tags") or []
            created_at = meta.get("created_at") or p_path.stat().st_ctime
            updated_at = meta.get("updated_at") or p_path.stat().st_mtime

            # Gather physical files inside dossiers, notes, etc.
            files_list = []
            total_bytes = 0
            for sub in ["dossiers", "notes", "sessions"]:
                sub_dir = p_path / sub
                if sub_dir.exists():
                    for f in sub_dir.iterdir():
                        if f.is_file() and not f.name.startswith("."):
                            st = f.stat()
                            total_bytes += st.st_size
                            files_list.append({
                                "filename": f.name,
                                "subfolder": sub,
                                "path": str(f.relative_to(PROJECTS_DIR)),
                                "size_bytes": st.st_size,
                                "size_formatted": f"{st.st_size / 1024:.1f} KB" if st.st_size > 1024 else f"{st.st_size} B",
                                "updated_at": st.st_mtime,
                                "time_ago_str": format_relative_time(st.st_mtime)
                            })

            proj_sessions = session_map.get(slug, [])

            projects.append({
                "slug": slug,
                "name": name,
                "description": desc,
                "color": color,
                "icon": icon,
                "tags": tags,
                "path": str(p_path),
                "is_linked": False,
                "created_at": created_at,
                "updated_at": updated_at,
                "time_ago_str": format_relative_time(updated_at),
                "total_files": len(files_list),
                "total_size_bytes": total_bytes,
                "total_sessions": len(proj_sessions),
                "files": sorted(files_list, key=lambda x: x["updated_at"], reverse=True),
                "sessions": proj_sessions,
                "apps": _apps_in(p_path)
            })

        # Also retrieve linked computer folders from SQLite
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT slug, name, path, description, color, icon, tags, created_at, updated_at FROM linked_projects")
            linked_rows = cursor.fetchall()
            conn.close()

            existing_slugs = {p["slug"] for p in projects}

            for row in linked_rows:
                l_slug = row["slug"]
                if l_slug in existing_slugs:
                    continue

                l_path = Path(row["path"]).resolve()
                if not l_path.exists() or not l_path.is_dir():
                    continue

                meta_file = l_path / "project.json"
                meta = {}
                if meta_file.exists():
                    try:
                        with open(meta_file, "r") as f:
                            meta = json.load(f)
                    except Exception:
                        pass

                raw_tags = row["tags"] or "[]"
                try:
                    parsed_tags = json.loads(raw_tags) if raw_tags.startswith("[") else [raw_tags]
                except Exception:
                    parsed_tags = ["linked-folder"]

                name = meta.get("name") or row["name"] or l_path.name
                desc = meta.get("description") or row["description"] or f"Linked computer folder at {l_path}."
                color = meta.get("color") or row["color"] or "#10b981"
                icon = meta.get("icon") or row["icon"] or "folder"

                # Inspect files in linked folder
                files_list = []
                total_bytes = 0

                try:
                    for item in l_path.iterdir():
                        if item.name.startswith(".") or item.name == "node_modules" or item.name == ".git":
                            continue
                        if item.is_file():
                            st = item.stat()
                            total_bytes += st.st_size
                            files_list.append({
                                "filename": item.name,
                                "subfolder": "root",
                                "path": str(item),
                                "size_bytes": st.st_size,
                                "size_formatted": f"{st.st_size / 1024:.1f} KB" if st.st_size > 1024 else f"{st.st_size} B",
                                "updated_at": st.st_mtime,
                                "time_ago_str": format_relative_time(st.st_mtime)
                            })
                        elif item.is_dir():
                            for sub_item in item.iterdir():
                                if sub_item.is_file() and not sub_item.name.startswith("."):
                                    st = sub_item.stat()
                                    total_bytes += st.st_size
                                    files_list.append({
                                        "filename": sub_item.name,
                                        "subfolder": item.name,
                                        "path": str(sub_item),
                                        "size_bytes": st.st_size,
                                        "size_formatted": f"{st.st_size / 1024:.1f} KB" if st.st_size > 1024 else f"{st.st_size} B",
                                        "updated_at": st.st_mtime,
                                        "time_ago_str": format_relative_time(st.st_mtime)
                                    })
                except Exception as e:
                    logger.debug("Error listing files in linked folder %s: %s", l_path, e)

                proj_sessions = session_map.get(l_slug, [])

                projects.append({
                    "slug": l_slug,
                    "name": name,
                    "description": desc,
                    "color": color,
                    "icon": icon,
                    "tags": parsed_tags,
                    "path": str(l_path),
                    "is_linked": True,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "time_ago_str": format_relative_time(row["updated_at"]),
                    "total_files": len(files_list),
                    "total_size_bytes": total_bytes,
                    "total_sessions": len(proj_sessions),
                    "files": sorted(files_list, key=lambda x: x["updated_at"], reverse=True)[:50],
                    "sessions": proj_sessions,
                    "apps": _apps_in(l_path)
                })

        except Exception as e:
            logger.warning(f"Error reading linked projects: {e}")

        return sorted(projects, key=lambda x: x["updated_at"], reverse=True)

    def get_project_dir(self, slug: str) -> Path:
        """Resolves the physical directory path for a project slug."""
        p_dir = PROJECTS_DIR / slug
        if p_dir.exists():
            return p_dir

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM linked_projects WHERE slug = ?", (slug,))
            row = cursor.fetchone()
            conn.close()
            if row and row["path"]:
                lp = Path(row["path"]).resolve()
                if lp.exists():
                    return lp
        except Exception:
            pass

        return p_dir

    def get_project(self, slug: str) -> Optional[Dict[str, Any]]:
        """Returns details, files, and sessions for a specific project slug."""
        for p in self.list_projects():
            if p["slug"] == slug:
                return p
        return None

    def create_project(self, name: str, slug: Optional[str] = None, description: str = "", color: str = "#6366f1", icon: str = "folder", tags: Optional[List[str]] = None, custom_path: Optional[str] = None) -> Dict[str, Any]:
        """Creates a physical project folder on the computer's hard drive."""
        clean_name = name.strip()
        if not slug:
            clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_name.lower()).strip("_")
            clean_slug = re.sub(r"_+", "_", clean_slug)
            if not clean_slug:
                clean_slug = f"project_{int(time.time())}"
            # Never overwrite an existing project when the user reuses a name
            base_slug, n = clean_slug, 2
            while (PROJECTS_DIR / clean_slug).exists():
                clean_slug = f"{base_slug}_{n}"
                n += 1
        else:
            clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug.lower()).strip("_")

        now = time.time()

        if custom_path and custom_path.strip():
            # Create in custom computer location
            p_dir = Path(os.path.expanduser(custom_path.strip())).resolve()
            p_dir.mkdir(parents=True, exist_ok=True)
            (p_dir / "dossiers").mkdir(exist_ok=True)
            (p_dir / "notes").mkdir(exist_ok=True)

            meta = {
                "id": f"proj_{clean_slug}",
                "slug": clean_slug,
                "name": clean_name,
                "description": description or f"Local research workspace for {clean_name}.",
                "color": color or "#6366f1",
                "icon": icon or "folder",
                "tags": tags or ["custom-computer-folder"],
                "path": str(p_dir),
                "is_linked": True,
                "created_at": now,
                "updated_at": now
            }
            with open(p_dir / "project.json", "w") as f:
                json.dump(meta, f, indent=2)

            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO linked_projects (slug, name, path, description, color, icon, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name, path=excluded.path, description=excluded.description,
                    color=excluded.color, icon=excluded.icon, tags=excluded.tags, updated_at=excluded.updated_at
                """, (clean_slug, clean_name, str(p_dir), meta["description"], color, icon, json.dumps(meta["tags"]), now, now))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to record linked project: {e}")

            return meta

        else:
            p_dir = PROJECTS_DIR / clean_slug
            p_dir.mkdir(parents=True, exist_ok=True)
            (p_dir / "dossiers").mkdir(exist_ok=True)
            (p_dir / "sessions").mkdir(exist_ok=True)
            (p_dir / "notes").mkdir(exist_ok=True)

            meta = {
                "id": f"proj_{clean_slug}",
                "slug": clean_slug,
                "name": clean_name,
                "description": description or f"Local research and diligence workspace for {clean_name}.",
                "color": color or "#6366f1",
                "icon": icon or "folder",
                "tags": tags or ["local-workspace"],
                "path": str(p_dir),
                "is_linked": False,
                "created_at": now,
                "updated_at": now
            }
            with open(p_dir / "project.json", "w") as f:
                json.dump(meta, f, indent=2)

            return meta

    def link_folder(self, folder_path: str, name: Optional[str] = None, description: str = "", color: str = "#6366f1", icon: str = "folder", tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Links any existing directory on the computer as a Xod project."""
        target = Path(os.path.expanduser(folder_path.strip())).resolve()
        if not target.exists() or not target.is_dir():
            raise ValueError(f"Folder '{folder_path}' does not exist on your computer.")

        folder_name = name.strip() if name and name.strip() else target.name
        clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", folder_name.lower()).strip("_")
        if not clean_slug:
            clean_slug = f"project_{int(time.time())}"

        now = time.time()
        meta_file = target / "project.json"
        meta = {}
        if meta_file.exists():
            try:
                with open(meta_file, "r") as f:
                    meta = json.load(f)
            except Exception:
                pass

        meta.update({
            "id": f"proj_{clean_slug}",
            "slug": clean_slug,
            "name": folder_name,
            "description": description or meta.get("description") or f"Linked workspace at {target}.",
            "color": color or meta.get("color") or "#6366f1",
            "icon": icon or meta.get("icon") or "folder",
            "tags": tags or meta.get("tags") or ["computer-folder"],
            "path": str(target),
            "is_linked": True,
            "created_at": meta.get("created_at") or now,
            "updated_at": now
        })

        try:
            with open(meta_file, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO linked_projects (slug, name, path, description, color, icon, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name, path=excluded.path, description=excluded.description,
                color=excluded.color, icon=excluded.icon, tags=excluded.tags, updated_at=excluded.updated_at
            """, (clean_slug, folder_name, str(target), meta["description"], meta["color"], meta["icon"], json.dumps(meta["tags"]), meta["created_at"], now))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to record linked project: {e}")

        return meta

    def unlink_project(self, slug: str) -> Dict[str, Any]:
        """Unlinks a linked computer folder from Xod without touching any files on disk."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM linked_projects WHERE slug = ?", (slug,))
            conn.commit()
            conn.close()
            return {"success": True, "message": f"Unlinked project '{slug}' successfully."}
        except Exception as e:
            return {"error": f"Failed to unlink project: {str(e)}"}

    def save_project_file(self, project_slug: str, filename: str, content: str, subfolder: str = "dossiers") -> Dict[str, Any]:
        """Writes or updates a physical file in the project folder."""
        p_dir = self.get_project_dir(project_slug)
        if not p_dir.exists():
            self.create_project(name=project_slug, slug=project_slug)
            p_dir = self.get_project_dir(project_slug)

        sub_dir = p_dir if subfolder == "root" else p_dir / subfolder
        sub_dir.mkdir(parents=True, exist_ok=True)

        clean_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
        file_path = sub_dir / clean_filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Update project modified time
        meta_file = p_dir / "project.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r") as f:
                    meta = json.load(f)
                meta["updated_at"] = time.time()
                with open(meta_file, "w") as f:
                    json.dump(meta, f, indent=2)
            except Exception:
                pass

        st = file_path.stat()
        return {
            "project_slug": project_slug,
            "subfolder": subfolder,
            "filename": clean_filename,
            "path": str(file_path),
            "size_bytes": st.st_size,
            "updated_at": st.st_mtime
        }

    def read_project_file(self, project_slug: str, subfolder: str, filename: str) -> Dict[str, Any]:
        """Reads a physical file from a project folder."""
        p_dir = self.get_project_dir(project_slug)
        sub_dir = p_dir if subfolder == "root" else p_dir / subfolder
        file_path = sub_dir / filename
        if not file_path.exists():
            # Try searching directly in p_dir
            if (p_dir / filename).exists():
                file_path = p_dir / filename
            else:
                return {"error": f"File '{filename}' not found in project '{project_slug}'."}

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        st = file_path.stat()
        return {
            "project_slug": project_slug,
            "subfolder": subfolder,
            "filename": filename,
            "content": content,
            "size_bytes": st.st_size,
            "updated_at": st.st_mtime,
            "time_ago_str": format_relative_time(st.st_mtime)
        }

    def delete_project_file(self, project_slug: str, subfolder: str, filename: str) -> Dict[str, Any]:
        """Deletes a file from the project directory."""
        p_dir = self.get_project_dir(project_slug)
        sub_dir = p_dir if subfolder == "root" else p_dir / subfolder
        file_path = sub_dir / filename
        if file_path.exists():
            file_path.unlink()
            return {"success": True, "message": f"Deleted {filename} from {project_slug}."}
        return {"error": "File does not exist."}

    def assign_session_to_project(self, session_id: str, project_slug: str) -> bool:
        """Assigns an existing chat session to a project folder."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE sessions SET project_slug = ? WHERE id = ?", (project_slug or None, session_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to assign session {session_id} to project {project_slug}: {e}")
            return False

    def list_unfiled_sessions(self, project_slugs: List[str]) -> List[Dict[str, Any]]:
        """Chats that aren't in any project (no project, or one that no longer exists)."""
        try:
            conn = get_db_connection()
            rows = conn.execute("SELECT id, title, updated_at, COALESCE(project_slug, '') AS p_slug FROM sessions ORDER BY updated_at DESC").fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"Error listing chats: {e}")
            return []
        known = set(project_slugs)
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"], "time_ago_str": format_relative_time(r["updated_at"])}
                for r in rows if r["p_slug"] not in known]

    def move_project_file(self, from_slug: str, subfolder: str, filename: str, to_slug: str) -> Dict[str, Any]:
        """Moves a saved file from one project to another (same subfolder). Never overwrites."""
        if from_slug == to_slug:
            return {"success": True, "message": "Already there."}
        if subfolder not in ("dossiers", "notes", "sessions", "root") or "/" in filename or filename.startswith("."):
            return {"error": "That file can't be moved."}
        src_dir = self.get_project_dir(from_slug)
        src = (src_dir if subfolder == "root" else src_dir / subfolder) / filename
        if not src.is_file():
            return {"error": "File does not exist."}
        dest_root = self.get_project_dir(to_slug)
        if not dest_root.exists():
            return {"error": f"No project called {to_slug}."}
        dest_dir = dest_root if subfolder == "root" else dest_root / subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        if dest.exists():
            return {"error": f"{to_slug} already has a file called {filename}."}
        shutil.move(str(src), str(dest))
        return {"success": True, "message": f"Moved {filename} to {to_slug}."}

    def get_observability_data(self) -> Dict[str, Any]:
        """
        Synthesizes a graph visualization and observability metrics across all projects,
        files, sessions, knowledge graph entities, tech stacks, and founders.
        Much cleaner, interactive, and responsive than Neo4j!
        """
        projects = self.list_projects()
        nodes = []
        edges = []
        seen_nodes = set()

        tech_frequency: Dict[str, int] = {}
        total_files = sum(p["total_files"] for p in projects)
        total_sessions = sum(p["total_sessions"] for p in projects)
        total_disk_bytes = sum(p["total_size_bytes"] for p in projects)

        # 1. Add Project Nodes
        for p in projects:
            p_node_id = f"proj_{p['slug']}"
            if p_node_id not in seen_nodes:
                seen_nodes.add(p_node_id)
                nodes.append({
                    "id": p_node_id,
                    "label": p["name"],
                    "type": "project",
                    "color": p["color"] or "#8b5cf6",
                    "size": 28,
                    "category": "Project Folder",
                    "slug": p["slug"],
                    "description": p["description"],
                    "file_count": p["total_files"],
                    "session_count": p["total_sessions"],
                    "time_ago": p["time_ago_str"]
                })

            # Add File Nodes linked to Project
            for f in p["files"][:8]:  # Limit sample files per project
                f_node_id = f"file_{p['slug']}_{f['filename']}"
                if f_node_id not in seen_nodes:
                    seen_nodes.add(f_node_id)
                    nodes.append({
                        "id": f_node_id,
                        "label": f["filename"],
                        "type": "file",
                        "color": "#f59e0b",
                        "size": 14,
                        "category": "Dossier / File",
                        "project": p["name"],
                        "subfolder": f["subfolder"],
                        "size_str": f["size_formatted"],
                        "time_ago": f["time_ago_str"]
                    })
                    edges.append({
                        "source": p_node_id,
                        "target": f_node_id,
                        "label": "stores",
                        "color": "rgba(245, 158, 11, 0.4)"
                    })

        # 2. Add SaaS Knowledge Graph Entities & connect to the research project
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT slug, name, category, website, verified_mrr, last_30d_revenue, tech_stack, tags, due_diligence_score FROM entities")
            entities = cursor.fetchall()

            for ent in entities:
                ent_slug = ent["slug"]
                ent_name = ent["name"] or ent_slug
                ent_cat = ent["category"] or "SaaS"
                raw_tech = ent["tech_stack"]
                tech_list = []
                if raw_tech:
                    try:
                        tech_list = json.loads(raw_tech) if raw_tech.startswith("[") else [t.strip() for t in raw_tech.split(",") if t.strip()]
                    except Exception:
                        tech_list = [t.strip() for t in raw_tech.split(",") if t.strip()]

                e_node_id = f"ent_{ent_slug}"
                if e_node_id not in seen_nodes:
                    seen_nodes.add(e_node_id)
                    nodes.append({
                        "id": e_node_id,
                        "label": ent_name,
                        "type": "startup",
                        "color": "#10b981",
                        "size": 22,
                        "category": "Researched Startup",
                        "mrr": ent["verified_mrr"] or "N/A",
                        "category_tag": ent_cat,
                        "slug": ent_slug,
                        "health_score": ent["due_diligence_score"] or 85
                    })

                    # Connect to the research project
                    edges.append({
                        "source": "proj_research",
                        "target": e_node_id,
                        "label": "researched",
                        "color": "rgba(16, 185, 129, 0.4)"
                    })

                # Tech Stacks from tech_list
                for t in tech_list[:6]:
                    t_name = (t.get("name") if isinstance(t, dict) else str(t)).strip()
                    if not t_name or len(t_name) > 40:
                        continue
                    tech_frequency[t_name] = tech_frequency.get(t_name, 0) + 1
                    t_node_id = f"tech_{re.sub(r'[^a-zA-Z0-9]', '_', t_name.lower())}"
                    if t_node_id not in seen_nodes:
                        seen_nodes.add(t_node_id)
                        nodes.append({
                            "id": t_node_id,
                            "label": t_name,
                            "type": "tech",
                            "color": "#06b6d4",
                            "size": 16,
                            "category": "Tech Stack"
                        })
                    edges.append({
                        "source": e_node_id,
                        "target": t_node_id,
                        "label": "uses",
                        "color": "rgba(6, 182, 212, 0.4)"
                    })

            # 3. Inter-startup relationships from entity_relationships table
            cursor.execute("SELECT source_slug, target_slug, relationship_type FROM entity_relationships")
            for rel in cursor.fetchall():
                s_id = f"ent_{rel['source_slug']}"
                t_id = f"ent_{rel['target_slug']}"
                if s_id in seen_nodes and t_id in seen_nodes:
                    edges.append({
                        "source": s_id,
                        "target": t_id,
                        "label": rel["relationship_type"] or "competes_with",
                        "color": "rgba(139, 92, 246, 0.5)"
                    })

            conn.close()
        except Exception as e:
            logger.warning(f"Error building graph from entities: {e}")

        # Top tech breakdown
        sorted_tech = sorted([{"name": k, "count": v} for k, v in tech_frequency.items()], key=lambda x: x["count"], reverse=True)[:10]

        return {
            "summary": {
                "total_projects": len(projects),
                "total_files": total_files,
                "total_sessions": total_sessions,
                "total_disk_bytes": total_disk_bytes,
                "total_disk_formatted": f"{total_disk_bytes / 1024:.1f} KB" if total_disk_bytes < 1024*1024 else f"{total_disk_bytes / (1024*1024):.2f} MB",
                "total_nodes": len(nodes),
                "total_edges": len(edges)
            },
            "top_technologies": sorted_tech,
            "nodes": nodes,
            "edges": edges,
            "projects_list": projects
        }

    def search_all_projects(self, query: str) -> Dict[str, Any]:
        """
        Performs global fast search across all local projects, files, dossiers,
        sessions, and indexed entities on the machine.
        """
        q = query.strip().lower()
        if not q:
            return {"projects": [], "files": [], "sessions": [], "entities": []}

        matched_projects = []
        matched_files = []
        matched_sessions = []
        matched_entities = []

        # 1. Search Project Folders & Files
        for p in self.list_projects():
            if q in p["name"].lower() or q in p["description"].lower() or q in p["slug"].lower() or any(q in t.lower() for t in p["tags"]):
                matched_projects.append({
                    "slug": p["slug"],
                    "name": p["name"],
                    "description": p["description"],
                    "color": p["color"],
                    "time_ago": p["time_ago_str"],
                    "file_count": p["total_files"]
                })

            for f in p["files"]:
                # Check filename
                fn_match = q in f["filename"].lower()
                # Check file content preview
                snippet = ""
                full_path = PROJECTS_DIR / p["slug"] / f["subfolder"] / f["filename"]
                if full_path.exists():
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as content_f:
                            text = content_f.read()
                            idx = text.lower().find(q)
                            if idx != -1:
                                start = max(0, idx - 40)
                                end = min(len(text), idx + 80)
                                snippet = ("..." if start > 0 else "") + text[start:end].strip() + ("..." if end < len(text) else "")
                    except Exception:
                        pass

                if fn_match or snippet:
                    matched_files.append({
                        "project_slug": p["slug"],
                        "project_name": p["name"],
                        "subfolder": f["subfolder"],
                        "filename": f["filename"],
                        "snippet": snippet or "Filename match",
                        "size": f["size_formatted"],
                        "time_ago": f["time_ago_str"]
                    })

        # 2. Search Sessions
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, persona, updated_at, project_slug FROM sessions WHERE lower(title) LIKE ? ORDER BY updated_at DESC LIMIT 10", (f"%{q}%",))
            for r in cursor.fetchall():
                matched_sessions.append({
                    "id": r["id"],
                    "title": r["title"],
                    "persona": r["persona"],
                    "project_slug": r["project_slug"] or "",
                    "time_ago": format_relative_time(r["updated_at"])
                })

            # 3. Search Entities
            cursor.execute("SELECT slug, name, category, verified_mrr FROM entities WHERE lower(name) LIKE ? OR lower(slug) LIKE ? OR lower(category) LIKE ? LIMIT 10", (f"%{q}%", f"%{q}%", f"%{q}%"))
            for r in cursor.fetchall():
                matched_entities.append({
                    "slug": r["slug"],
                    "name": r["name"],
                    "category": r["category"] or "SaaS",
                    "mrr": r["verified_mrr"] or "N/A"
                })

            conn.close()
        except Exception as e:
            logger.warning(f"Database search failed: {e}")

        return {
            "query": query,
            "total_matches": len(matched_projects) + len(matched_files) + len(matched_sessions) + len(matched_entities),
            "projects": matched_projects,
            "files": matched_files,
            "sessions": matched_sessions,
            "entities": matched_entities
        }

project_storage_service = ProjectStorageService()
