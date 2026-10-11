from typing import Any, Dict

# Shown in the app under Tools → "What's coming next", and kept in step with docs/ROADMAP.md.
ROADMAP = [
    # ---- next
    {"status": "next", "name": "Faster answers on 16 GB Macs",
     "description": "Fewer model calls per answer and a smaller default model, so a researched answer takes well under a minute."},
    {"status": "next", "name": "Signed, notarized Mac app",
     "description": "No security warning on first open."},
    {"status": "next", "name": "One-click connected apps",
     "description": "Ready-made setups for Gmail, Calendar, Notion and GitHub, instead of pasting a config."},
    {"status": "next", "name": "Spoken replies",
     "description": "Xod answers out loud, with an on-device voice."},
    # ---- later
    {"status": "later", "name": "Shareable routine templates",
     "description": "Import and share routines like \"weekly competitor check\" or \"morning brief\"."},
    {"status": "later", "name": "Approve from your phone",
     "description": "A companion to approve or decline Xod's pending actions when you're away from the Mac."},
    {"status": "later", "name": "More Macs and Linux",
     "description": "Intel Macs (cloud or Ollama models) and Linux."},
    # ---- shipped in 0.1
    {"status": "shipped", "name": "One-download install",
     "description": "Drag Xod to Applications and open it; everything it needs comes inside the app."},
    {"status": "shipped", "name": "Built-in AI engine and setup",
     "description": "Runs a model on your Mac with no other apps; picks one that fits your memory."},
    {"status": "shipped", "name": "Routines",
     "description": "Teach a job once, then repeat it by hand or on a schedule."},
    {"status": "shipped", "name": "Activity and undo",
     "description": "Everything Xod did, with undo for file changes."},
    {"status": "shipped", "name": "Connected apps (MCP)",
     "description": "Use outside tools behind approvals, and use Xod from other AI apps."},
    {"status": "shipped", "name": "Shareable reports",
     "description": "Answers as a polished page and PDF with sources."},
    {"status": "shipped", "name": "Smart model routing",
     "description": "Cloud help for hard steps, with permission, costs shown and a budget."},
    {"status": "shipped", "name": "Add-ons",
     "description": "Startup intel, people finder, website forensics, creators & trends, Media Studio, Model Lab."},
]


def get_project_roadmap() -> Dict[str, Any]:
    return {"title": "What's coming next", "version": "0.1.0", "items": ROADMAP}
