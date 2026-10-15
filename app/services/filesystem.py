import os
import sys
import json
import time
import shutil
import base64
import mimetypes
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("filesystem_service")

# Common safe locations (on Windows, wherever Windows really keeps them: OneDrive often moves Desktop and Documents)
from app.platform_info import real_folder
USER_HOME = Path.home()
DOCUMENTS_DIR = Path(real_folder(str(USER_HOME / "Documents")))
DESKTOP_DIR = Path(real_folder(str(USER_HOME / "Desktop")))
DOWNLOADS_DIR = Path(real_folder(str(USER_HOME / "Downloads")))
CURRENT_REPO = Path(__file__).resolve().parent.parent.parent

# Protected system paths that should never be deleted, moved, or overwritten
PROTECTED_PATHS = {
    "/",
    "/System",
    "/Library",
    "/usr",
    "/bin",
    "/sbin",
    "/Applications",
    "/private",
    "/etc",
    "/var",
    str(USER_HOME),  # Do not allow deleting user root home directory itself
}

def format_relative_time(timestamp: float) -> str:
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

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".js", ".jsx", ".ts", ".tsx",
    ".py", ".html", ".css", ".scss", ".yaml", ".yml", ".toml", ".sh",
    ".bash", ".zsh", ".csv", ".tsv", ".xml", ".sql", ".env", ".log",
    ".ini", ".conf", ".cfg", ".rs", ".go", ".c", ".cpp", ".h", ".swift",
    ".java", ".rb", ".php", ".m", ".mm"
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"
}

class FileSystemService:
    """
    Provides safe, high-performance file and folder management across the user's computer.
    Allows browsing, creating, editing, renaming, duplicating, and deleting files,
    along with smart metadata extraction and AI analysis integration.
    """

    def resolve_path(self, raw_path: Optional[str]) -> Path:
        """Resolves raw input path, expanding ~ and converting to absolute resolved path."""
        if not raw_path or not raw_path.strip():
            return USER_HOME
        clean = raw_path.strip()
        if clean.startswith("~"):
            p = USER_HOME / clean[1:].lstrip("/\\")
        else:
            p = Path(clean)
        return p.resolve()

    def get_shortcuts(self) -> List[Dict[str, Any]]:
        """Returns standard Mac computer directories for 1-click navigation."""
        shortcuts = [
            {"id": "home", "name": "Home", "path": str(USER_HOME), "icon": "home"},
            {"id": "documents", "name": "Documents", "path": str(DOCUMENTS_DIR), "icon": "file-text"},
            {"id": "desktop", "name": "Desktop", "path": str(DESKTOP_DIR), "icon": "monitor"},
            {"id": "downloads", "name": "Downloads", "path": str(DOWNLOADS_DIR), "icon": "download"},
            {"id": "xod", "name": "Xod Workspace", "path": str(CURRENT_REPO), "icon": "sparkles"},
            {"id": "projects", "name": "Xod Data Projects", "path": str(DATA_DIR / "projects"), "icon": "folder-kanban"}
        ]
        return [s for s in shortcuts if Path(s["path"]).exists()]

    def list_directory(self, raw_path: Optional[str] = None, show_hidden: bool = False, sort_by: str = "name") -> Dict[str, Any]:
        """
        Lists directory contents with detailed folder and file telemetry, breadcrumbs,
        and write permissions.
        """
        target = self.resolve_path(raw_path)
        if not target.exists():
            # Fallback to Home if path does not exist
            target = USER_HOME

        if not target.is_dir():
            target = target.parent

        # Build breadcrumbs
        parts = []
        curr = target
        while curr != curr.parent:
            parts.insert(0, {"name": curr.name or str(curr), "path": str(curr)})
            curr = curr.parent
        # Add root
        parts.insert(0, {"name": "/" if sys.platform != "win32" else str(curr), "path": str(curr)})

        is_writable = os.access(target, os.W_OK)
        parent_path = str(target.parent) if target.parent != target else None

        folders = []
        files = []

        try:
            with os.scandir(target) as it:
                for entry in it:
                    name = entry.name
                    if not show_hidden and name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        st = entry.stat(follow_symlinks=False)
                        updated_at = st.st_mtime
                        size_bytes = st.st_size

                        if is_dir:
                            # Count items if readable
                            item_count = 0
                            try:
                                with os.scandir(entry.path) as sub_it:
                                    item_count = sum(1 for sub in sub_it if show_hidden or not sub.name.startswith("."))
                            except (PermissionError, OSError):
                                item_count = 0

                            folders.append({
                                "name": name,
                                "path": entry.path,
                                "updated_at": updated_at,
                                "time_ago_str": format_relative_time(updated_at),
                                "item_count": item_count,
                                "is_symlink": entry.is_symlink()
                            })
                        else:
                            ext = Path(name).suffix.lower()
                            is_text = ext in TEXT_EXTENSIONS
                            is_img = ext in IMAGE_EXTENSIONS
                            mime, _ = mimetypes.guess_type(entry.path)

                            files.append({
                                "name": name,
                                "path": entry.path,
                                "extension": ext,
                                "size_bytes": size_bytes,
                                "size_formatted": format_size(size_bytes),
                                "updated_at": updated_at,
                                "time_ago_str": format_relative_time(updated_at),
                                "is_text": is_text,
                                "is_image": is_img,
                                "mime_type": mime or "application/octet-stream"
                            })
                    except (PermissionError, OSError) as e:
                        logger.debug("Failed to read entry %s: %s", name, e)
                        continue
        except PermissionError:
            return {
                "error": f"Permission denied accessing directory: {str(target)}",
                "current_path": str(target),
                "parent_path": parent_path,
                "breadcrumbs": parts,
                "is_writable": False,
                "folders": [],
                "files": [],
                "shortcuts": self.get_shortcuts()
            }

        # Sorting
        if sort_by == "date":
            folders.sort(key=lambda x: x["updated_at"], reverse=True)
            files.sort(key=lambda x: x["updated_at"], reverse=True)
        elif sort_by == "size":
            folders.sort(key=lambda x: x["item_count"], reverse=True)
            files.sort(key=lambda x: x["size_bytes"], reverse=True)
        else: # name
            folders.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())

        return {
            "current_path": str(target),
            "parent_path": parent_path,
            "breadcrumbs": parts,
            "is_writable": is_writable,
            "total_folders": len(folders),
            "total_files": len(files),
            "folders": folders,
            "files": files,
            "shortcuts": self.get_shortcuts()
        }

    def read_file(self, raw_path: str, max_size_mb: float = 10.0) -> Dict[str, Any]:
        """Reads a file with safe size bounds and returns content, tokens, lines, and mime type."""
        target = self.resolve_path(raw_path)
        if not target.exists():
            return {"error": f"File not found: {str(target)}"}
        if target.is_dir():
            return {"error": f"Path is a directory, not a file: {str(target)}"}

        st = target.stat()
        size_bytes = st.st_size
        max_bytes = int(max_size_mb * 1024 * 1024)
        ext = target.suffix.lower()
        mime, _ = mimetypes.guess_type(str(target))
        is_text = ext in TEXT_EXTENSIONS
        is_img = ext in IMAGE_EXTENSIONS

        if size_bytes > max_bytes:
            return {
                "name": target.name,
                "path": str(target),
                "extension": ext,
                "size_bytes": size_bytes,
                "size_formatted": format_size(size_bytes),
                "is_too_large": True,
                "error": f"File is too large ({format_size(size_bytes)} > {max_size_mb} MB limit)."
            }

        if is_img:
            try:
                with open(target, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                mime_str = mime or "image/png"
                return {
                    "name": target.name,
                    "path": str(target),
                    "extension": ext,
                    "size_bytes": size_bytes,
                    "size_formatted": format_size(size_bytes),
                    "updated_at": st.st_mtime,
                    "time_ago_str": format_relative_time(st.st_mtime),
                    "is_image": True,
                    "is_text": False,
                    "mime_type": mime_str,
                    "data_uri": f"data:{mime_str};base64,{b64}"
                }
            except Exception as e:
                return {"error": f"Failed to read image: {str(e)}"}

        # Rich Document Parsing (.pdf, .docx, .csv, etc.)
        if ext in (".pdf", ".docx", ".doc"):
            try:
                from app.services.document_parser import document_parser_service
                doc_res = document_parser_service.parse_file(target)
                if not doc_res.get("error"):
                    doc_res.update({
                        "updated_at": st.st_mtime,
                        "time_ago_str": format_relative_time(st.st_mtime),
                        "is_text": True,
                        "is_image": False,
                        "mime_type": mime or ("application/pdf" if ext == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                    })
                    return doc_res
            except Exception as e:
                logger.warning(f"Document parser error for {target}: {e}")

        # Attempt text read
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            lines = content.count("\n") + 1 if content else 0
            approx_tokens = int(len(content) / 3.8) if content else 0

            return {
                "name": target.name,
                "path": str(target),
                "extension": ext,
                "size_bytes": size_bytes,
                "size_formatted": format_size(size_bytes),
                "updated_at": st.st_mtime,
                "time_ago_str": format_relative_time(st.st_mtime),
                "is_text": True,
                "is_image": False,
                "mime_type": mime or "text/plain",
                "line_count": lines,
                "char_count": len(content),
                "approx_tokens": approx_tokens,
                "content": content
            }
        except Exception as e:
            return {"error": f"Failed to read file: {str(e)}"}

    def write_file(self, raw_path: str, content: str) -> Dict[str, Any]:
        """Saves text content to a computer file."""
        target = self.resolve_path(raw_path)
        if target.is_dir():
            return {"error": "Target path is a directory."}

        # Check protected paths
        if str(target) in PROTECTED_PATHS:
            return {"error": "Modifying this system file is protected for computer safety."}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)

            st = target.stat()
            return {
                "success": True,
                "name": target.name,
                "path": str(target),
                "size_bytes": st.st_size,
                "size_formatted": format_size(st.st_size),
                "updated_at": st.st_mtime,
                "time_ago_str": format_relative_time(st.st_mtime)
            }
        except Exception as e:
            logger.error("Failed to write file %s: %s", target, e)
            return {"error": f"Failed to write file: {str(e)}"}

    def create_file(self, raw_dir: str, filename: str, content: str = "") -> Dict[str, Any]:
        """Creates a new file in the target directory."""
        dir_path = self.resolve_path(raw_dir)
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)

        clean_name = filename.strip()
        if not clean_name:
            return {"error": "Filename cannot be empty."}

        target = dir_path / clean_name
        if target.exists():
            return {"error": f"File '{clean_name}' already exists in this folder."}

        return self.write_file(str(target), content)

    def create_folder(self, raw_parent: str, folder_name: Optional[str] = None) -> Dict[str, Any]:
        """Creates a new folder in the target directory, or creates the directory path."""
        if folder_name and folder_name.strip():
            parent_path = self.resolve_path(raw_parent)
            clean_name = folder_name.strip()
            target = parent_path / clean_name
        else:
            target = self.resolve_path(raw_parent)
            clean_name = target.name

        if str(target) in PROTECTED_PATHS:
            return {"error": "Cannot create folder at protected system path."}

        if target.exists() and target.is_dir():
            return {
                "success": True,
                "already_exists": True,
                "name": clean_name,
                "path": str(target)
            }

        try:
            target.mkdir(parents=True, exist_ok=True)
            return {
                "success": True,
                "name": clean_name,
                "path": str(target)
            }
        except Exception as e:
            return {"error": f"Failed to create folder: {str(e)}"}

    def rename_item(self, raw_source: str, new_name: str) -> Dict[str, Any]:
        """Renames a file or folder safely."""
        source = self.resolve_path(raw_source)
        if not source.exists():
            return {"error": "Source path does not exist."}

        if str(source) in PROTECTED_PATHS:
            return {"error": "Cannot rename protected system path."}
        from app.services.system_control import system_control_service
        why = system_control_service._unsafe_target(str(source), destructive=True)
        if why:
            return {"error": why}

        clean_name = new_name.strip()
        if not clean_name:
            return {"error": "New name cannot be empty."}

        target = source.parent / clean_name
        if target.exists():
            return {"error": f"An item named '{clean_name}' already exists in this directory."}

        try:
            source.rename(target)
            return {
                "success": True,
                "old_path": str(source),
                "new_path": str(target),
                "name": clean_name
            }
        except Exception as e:
            return {"error": f"Failed to rename: {str(e)}"}

    def move_item(self, raw_source: str, raw_destination: str) -> Dict[str, Any]:
        """Moves or renames a file or folder safely into another directory or new path."""
        source = self.resolve_path(raw_source)
        if not source.exists():
            return {"error": f"Source path does not exist: {str(source)}"}

        if str(source) in PROTECTED_PATHS:
            return {"error": "Cannot move protected system path."}

        destination = self.resolve_path(raw_destination)
        if str(destination) in PROTECTED_PATHS:
            return {"error": "Cannot move into protected system path."}
        from app.services.system_control import system_control_service
        why = system_control_service._unsafe_target(str(source), destructive=True) or \
            system_control_service._unsafe_target(str(destination), destructive=False)
        if why:
            return {"error": why}

        try:
            if destination.exists() and destination.is_dir():
                target = destination / source.name
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                target = destination

            shutil.move(str(source), str(target))
            return {
                "success": True,
                "old_path": str(source),
                "new_path": str(target),
                "name": target.name
            }
        except Exception as e:
            return {"error": f"Failed to move item: {str(e)}"}

    def organize_files(self, file_paths: List[str], target_folder: str) -> Dict[str, Any]:
        """Moves multiple files into a specified target directory, creating it if needed."""
        target_dir = self.resolve_path(target_folder)
        if str(target_dir) in PROTECTED_PATHS:
            return {"error": "Cannot organize files into a protected system path."}

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"error": f"Failed to create target directory '{str(target_dir)}': {str(e)}"}

        moved = []
        errors = []

        for p_str in file_paths:
            src = self.resolve_path(p_str)
            if not src.exists():
                errors.append({"path": p_str, "error": "File does not exist"})
                continue
            if str(src) in PROTECTED_PATHS:
                errors.append({"path": p_str, "error": "Protected path cannot be moved"})
                continue
            try:
                dest = target_dir / src.name
                shutil.move(str(src), str(dest))
                moved.append({
                    "original": str(src),
                    "destination": str(dest),
                    "name": dest.name
                })
            except Exception as e:
                errors.append({"path": p_str, "error": str(e)})

        return {
            "success": len(moved) > 0 or len(errors) == 0,
            "target_folder": str(target_dir),
            "moved_count": len(moved),
            "error_count": len(errors),
            "moved_files": moved,
            "errors": errors
        }

    def duplicate_item(self, raw_source: str) -> Dict[str, Any]:
        """Duplicates a file or folder with an incremented copy name."""
        source = self.resolve_path(raw_source)
        if not source.exists():
            return {"error": "Source path does not exist."}

        parent = source.parent
        stem = source.stem
        suffix = source.suffix

        # Find available duplicate name
        idx = 1
        dup_target = parent / f"{stem}_copy{suffix}"
        while dup_target.exists():
            idx += 1
            dup_target = parent / f"{stem}_copy_{idx}{suffix}"

        try:
            if source.is_dir():
                shutil.copytree(source, dup_target)
            else:
                shutil.copy2(source, dup_target)

            return {
                "success": True,
                "original_path": str(source),
                "new_path": str(dup_target),
                "name": dup_target.name
            }
        except Exception as e:
            return {"error": f"Failed to duplicate item: {str(e)}"}

    def delete_item(self, raw_path: str) -> Dict[str, Any]:
        """Moves a file or folder to the Trash (Recycle Bin on Windows), so it can be put back. Never a system
        folder, a whole top folder like the home folder or Desktop, or Xod itself (the same guard as the agent's tools)."""
        target = self.resolve_path(raw_path)
        if not target.exists():
            return {"error": "Path does not exist."}

        target_str = str(target)
        for prot in PROTECTED_PATHS:
            if target_str == prot or target_str.startswith(prot + "/") and prot in ["/System", "/Library", "/usr", "/bin", "/sbin"]:
                return {"error": f"Cannot delete protected system directory: {target_str}"}
        from app.services.system_control import system_control_service
        why = system_control_service._unsafe_target(target_str, destructive=True)
        if why:
            return {"error": why}

        try:
            if sys.platform == "win32":
                from app.services.system_control_win import recycle
                recycle(target_str)
            elif os.path.exists("/usr/bin/trash"):
                import subprocess
                r = subprocess.run(["/usr/bin/trash", target_str], capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    return {"error": f"Failed to move it to the Trash: {r.stderr.strip()[:200]}"}
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"success": True, "path": target_str, "name": target.name, "recoverable": sys.platform == "win32" or os.path.exists("/usr/bin/trash")}
        except Exception as e:
            return {"error": f"Failed to delete item: {str(e)}"}

    def search_filesystem(self, raw_base: str, query: str, max_results: int = 40) -> Dict[str, Any]:
        """Searches files and folders recursively inside a directory."""
        base = self.resolve_path(raw_base)
        if not base.exists() or not base.is_dir():
            base = USER_HOME

        q = query.strip().lower()
        if not q:
            return {"results": [], "query": query, "base_path": str(base)}

        results = []
        try:
            for root, dirs, files in os.walk(base):
                # Filter hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                # Stop if reached max results
                if len(results) >= max_results:
                    break

                # Check dirs
                for d in dirs:
                    if q in d.lower():
                        p = Path(root) / d
                        results.append({
                            "type": "folder",
                            "name": d,
                            "path": str(p),
                            "relative_path": str(p.relative_to(base)),
                            "match_type": "folder_name"
                        })
                        if len(results) >= max_results:
                            break

                # Check files
                for f in files:
                    if f.startswith("."):
                        continue
                    p = Path(root) / f
                    ext = p.suffix.lower()
                    name_matched = q in f.lower()
                    snippet = ""

                    if not name_matched and ext in TEXT_EXTENSIONS and p.stat().st_size < 500 * 1024:
                        # Search content preview
                        try:
                            with open(p, "r", encoding="utf-8", errors="ignore") as cf:
                                txt = cf.read()
                                idx = txt.lower().find(q)
                                if idx != -1:
                                    start = max(0, idx - 40)
                                    end = min(len(txt), idx + 80)
                                    snippet = ("..." if start > 0 else "") + txt[start:end].strip() + ("..." if end < len(txt) else "")
                        except Exception:
                            pass

                    if name_matched or snippet:
                        results.append({
                            "type": "file",
                            "name": f,
                            "path": str(p),
                            "relative_path": str(p.relative_to(base)),
                            "match_type": "content" if snippet and not name_matched else "filename",
                            "snippet": snippet,
                            "size_formatted": format_size(p.stat().st_size)
                        })
                        if len(results) >= max_results:
                            break

        except Exception as e:
            logger.warning("Filesystem search error: %s", e)

        return {
            "query": query,
            "base_path": str(base),
            "total_matches": len(results),
            "results": results
        }

    def search_files(self, raw_base: str, query: str, max_results: int = 40) -> Dict[str, Any]:
        """Searches files and folders recursively inside a directory (alias for search_filesystem)."""
        return self.search_filesystem(raw_base, query, max_results=max_results)

filesystem_service = FileSystemService()
