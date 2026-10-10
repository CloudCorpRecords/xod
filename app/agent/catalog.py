from pathlib import Path
from typing import Dict, List, Optional

CATALOG_DIR = Path(__file__).resolve().parent / "tools_catalog"

def list_documented_tools() -> List[str]:
    """Returns a list of all documented tool names in tools_catalog."""
    if not CATALOG_DIR.exists():
        return []
    return [p.stem for p in CATALOG_DIR.glob("*.md") if p.name != "INDEX.md"]

def get_tool_documentation(tool_name: str) -> Optional[str]:
    """Retrieves full markdown documentation for a given tool."""
    path = CATALOG_DIR / f"{tool_name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None

def get_tools_catalog_summary() -> str:
    """
    Builds a high-density decision matrix and tool documentation summary
    injected into the agent system prompt so it knows when to think, when to act,
    and which tool fits which investigation.
    """
    index_path = CATALOG_DIR / "INDEX.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")

    return "Tools catalog available."
