import os
import json
import asyncio
import re
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from app.config import settings
from app.services.llm import llm_service, parse_llm_json, JSON_MIN_TOKENS
from app.agent.prompts import PERSONA_PROMPTS
from app.agent.tools import TOOL_DEFINITIONS, execute_tool, select_tools_for_query
from app.agent.evidence import EvidenceBoard
from app.services.scoring import compute_due_diligence_score
from app.services.storage import save_or_update_entity
from app.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger("researcher")

# The user's pick for games: three.js (Xod ships it, so it works offline). A nudge, not a rule.
GAME_TALK_RE = re.compile(r"\b(video ?games?|games?|gaming|game ?dev|game engine|shooter|platformer|fps)\b", re.I)
GAME_NOTE = ("When the talk is about making a video game, lean toward three.js (Xod builds and runs three.js games "
             "itself, offline) unless the user prefers something else; other engines are fine when asked for.")

class _Warmer:
    """Keeps the local model reading ahead: push() the prompt as it grows (question, then each page as it's read)
    and the model server reads it in the background (llm_service.prefill), one request at a time, always the
    newest. settle() stops it and waits for the read in progress, so the real request lands on what's cached."""

    def __init__(self):
        self.latest: Optional[List[Dict[str, Any]]] = None
        self.task: Optional[asyncio.Task] = None
        self.closed = False

    def push(self, msgs: List[Dict[str, Any]]) -> None:
        if self.closed:
            return
        self.latest = msgs
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while self.latest is not None and not self.closed:
            msgs, self.latest = self.latest, None
            if not await llm_service.prefill(msgs):
                self.closed = True             # a cloud model, or the server said no: nothing to gain

    async def settle(self, cap: float = 25.0) -> None:
        self.closed, self.latest = True, None
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), cap)
            except Exception:
                pass


class _Prefetch:
    """A web search (and the reading of its best pages) started the moment a question arrives, while the router
    is still deciding whether it needs the web at all. Cancelled if it doesn't."""

    def __init__(self, query: str, n_read: int, budget: int):
        from app.services import web_reader
        self.query = query
        self.reads: List[asyncio.Future] = []

        async def go():
            found = await web_reader.search(query, 8)
            picks = web_reader.pick_pages(found.get("results") or [], n_read)
            self.reads = [asyncio.ensure_future(web_reader.read_page(h.get("open") or h["url"], query, budget,
                                                                     timeout=web_reader.READ_TIMEOUT)) for h in picks]
            return found, picks, self.reads
        self.task = asyncio.ensure_future(go())

    async def result(self):
        try:
            found, picks, reads = await self.task
        except Exception:
            return None
        return (found, picks, reads) if found.get("results") else None

    def cancel(self) -> None:
        self.task.cancel()
        for r in self.reads:
            r.cancel()


class AgenticResearcher:
    """
    Next-Generation Autonomous Research Director.
    Features:
    1. Dynamic Plan & Hypothesis Tree (Plan-and-Solve)
    2. Structured Evidence Board (Working Memory & Context Compression)
    3. Multi-Hop Autonomous Execution Loop with Gap Detection
    4. Real-time Plan and Evidence SSE event streaming
    """

    def __init__(self, persona: str = "ultimate_researcher"):
        self._planner_example: Optional[Dict[str, Any]] = None   # {"id", "response"} of this turn's plan
        self._carried: Dict[str, Any] = {}   # job_id, output_path, ... from this turn's results (see _fill_placeholders)
        self.persona = persona
        self.system_prompt = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["ultimate_researcher"])

    async def run_research_stream(
        self,
        messages: List[Dict[str, Any]],
        max_hops: int = 4,
        active_workspace_path: Optional[str] = None,
        attached_document: Optional[Dict[str, Any]] = None,
        spoken: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        spoken: the question was asked out loud in a conversation, and the answer will be read
        aloud, so it's short and conversational (details and sources stay on screen).

        Runs the deliberation-first autonomous research loop and yields real-time SSE events.
        1. Deliberation Phase ("Think First, Decide, Then Act"):
           - LLM evaluates conversation history with tool definitions.
           - If conversational / conceptual / greeting: LLM outputs direct answer without tools.
             Stream direct response, do NOT emit plan, do NOT emit scorecard. Done.
           - If research is required: LLM selects initial tool calls.
        2. Plan Formation & Tool Execution:
           - Dynamically formulate research plan reflecting selected tools and query.
           - Emit plan event to UI.
           - Execute initial tool calls and populate EvidenceBoard.
        3. Multi-Hop Gap Detection Loop:
           - Iterate hops until evidence gaps are closed or max_hops reached.
        4. Conditional Scoring & Institutional Synthesis:
           - Emit scorecard ONLY if verified empirical evidence exists for an entity.
           - Auto-index into persistent Knowledge Graph.
           - Stream definitive markdown synthesis report.
        """
        self.spoken = spoken
        # What the person said in this conversation: the safety check compares every action with it (services/sentinel.py).
        from app.services import sentinel, usage as usage_log
        sentinel.begin(messages)
        usage_log.purpose.set("")                 # each turn starts unlabelled; the worker and team label their own calls
        attached_image_urls: List[str] = []
        text_parts: List[str] = []
        user_msg = messages[-1].get("content", "") if messages else "SaaS Market Intelligence"
        if isinstance(user_msg, list):
            for part in user_msg:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url_obj = part.get("image_url", {})
                        img_url = url_obj.get("url") if isinstance(url_obj, dict) else str(url_obj)
                        if img_url:
                            attached_image_urls.append(img_url)
            user_query = " ".join(text_parts).strip() if text_parts else "Analyze this image"
        else:
            user_query = str(user_msg)

        # A chat model that can't see: the picture becomes words before anything else (routing, planning, the answer),
        # from a small vision model (services/vision.py). Planned blind, "who is this invoice from?" went to the browser.
        if attached_image_urls:
            try:
                from app.services import vision
                blind = await vision.needed()
            except Exception:
                blind = False
            if blind:
                yield {"type": "status", "content": "Looking at the image…"}
                described = await vision.as_text(user_query, attached_image_urls)
                if not described:
                    yield {"type": "content", "content": (
                        "Your chat model reads text only, so I can't look at images with it, and there's no vision model on "
                        "this computer yet. Xod downloads a small one by itself when there's room on the disk; or pick a model "
                        "that can see in Tools → Setup & AI model.")}
                    yield {"type": "done"}
                    return
                user_query, attached_image_urls = described, []
                messages = [*messages[:-1], {"role": "user", "content": described}]

        has_image = bool(attached_image_urls)
        has_document = bool(attached_document or "[Attached Document:" in user_query or "[Attached File from Computer:" in user_query)

        evidence_board = EvidenceBoard(objective=user_query)
        if has_image:
            evidence_board.visual_telemetry.append({
                "images_count": len(attached_image_urls),
                "query": user_query
            })
        if has_document:
            doc_name = (attached_document.get("name") if attached_document else None) or "Attached Document"
            evidence_board.doc_telemetry.append(attached_document or {"name": doc_name})

        # "What can't you do?" / "Why can't you see images?": answered from what was just checked on this computer with
        # this model (services/capabilities.py), not from what a model imagines about itself.
        if not has_image and not has_document and self._ABOUT_SELF_RE.search(user_query or ""):
            async for ev in self._answer_about_self(user_query, messages):
                yield ev
            return

        # -------------------------------------------------------------
        # STEP 1: DELIBERATION PHASE ("Think First, Decide, Then Act")
        # -------------------------------------------------------------
        yield {"type": "status", "content": "Thinking…"}

        system_prompt = self.system_prompt
        try:
            from app.services import user_memory
            about = user_memory.core_note()
            if about:
                system_prompt += "\n\n" + about + user_memory.relevant_note(user_query or "")
        except Exception:
            pass
        try:
            from app.services.system_control import system_control_service
            profile = system_control_service.get_computer_profile()
            telem = system_control_service.get_system_telemetry()
            current_cwd = active_workspace_path or profile.get("current_working_directory") or os.getcwd()
            system_prompt += f"""

[PERSISTENT COMPUTER HOST ENVIRONMENT]
- Machine Host: {profile.get('host_os')} ({profile.get('arch')})
- Computer ID: {profile.get('computer_id')}
- Current Working Directory: {current_cwd}
- RAM: {telem.get('memory', {}).get('used_gb')} GB used of {telem.get('memory', {}).get('total_gb')} GB
- CPU 1m Load: {telem.get('cpu_load', {}).get('1m')}

You have direct non-intrusive access to control this computer in the background:
- `computer_run_command`: Run shell commands in the persistent working directory without focus stealing.
- `computer_find_files`: Instantly search across the entire Mac via Spotlight (`mdfind`) for any file or folder.
- `computer_manage_file`: Safely move, copy, make directories, or move files to macOS Trash (recoverable).
- `computer_inspect_port`: Check which process and PID is listening on any port (3000, 8000, etc.).
- `computer_kill_process`: Terminate any stuck process by PID.
- `computer_clipboard`: Read or write the system clipboard (`pbpaste`/`pbcopy`).
- `computer_start_job` / `computer_list_jobs` / `computer_stop_job`: Run and monitor background daemons.
- `computer_wait_for`: Wait until a job finishes or prints something, a file appears, a port opens, or a URL responds.
- `computer_check_job`: See a background job's status and the output since you last looked.
- `computer_inspect_system`: What's running now: the programs using the most CPU and memory, RAM, disk space, CPU load, local IP address.
- `computer_reveal_finder`: Reveal files or directories in Finder.
- `browser_navigate`: Navigate to any URL live via Chrome CDP. Automatically returns top interactive elements with Set-of-Marks numeric tags (`[1]`, `[2]`), detected forms, headings, text snippet, and screenshot. You can act on numeric indices immediately!
- `browser_smart_inspect`: Instant multimodal inspection returning Set-of-Marks tags, forms, headings, text, and screenshot in a single call.
- `browser_dismiss_overlays`: Intelligently dismiss cookie banners, GDPR consent notices, newsletter modals, and blocking overlays.
- `browser_observe`: Scan visible interactive elements with numeric Set-of-Marks IDs (`[1]`, `[2]`, ...).
- `browser_interact`: Deterministically click, type, hover, select, or check elements by numeric Set-of-Marks `index`, CSS `selector`, or `text_target`. Automatically re-indexes if DOM changed.
- `browser_extract_data`: Stagehand-style structured data extraction primitive. Synthesizes clean structured JSON entities (lists, tables, pricing) from the page according to an extraction instruction.
- `browser_fill_form`: Fill multi-field forms in a single batch turn by field label, placeholder, name, id, index, or type, handling text, selects, and checkboxes.
- `browser_press_key`: Send key presses (Enter, Escape, Tab, ArrowDown, etc.) to the page or target element.
- `browser_hover`: Hover over elements by index or selector to trigger dropdowns, tooltips, or hover menus.
- `browser_set_visibility`: Toggle between silent background headless Chrome (`headless=True`) and visible native desktop Chrome on macOS (`headless=False`) so the user can observe live or solve CAPTCHAs.
- `browser_snoop_network`: Capture intercepted background fetch/XHR API responses and JSON payloads directly from SPAs.
- `browser_manage_cookies`: Inspect, set, or clear cookies via Chrome DevTools Protocol.
- `browser_manage_tabs`: List open tabs, open a new tab, switch tabs by ID, or close tabs.
- `browser_wait_for`: Poll until an element selector, body text, URL pattern, or network idle (>1s without API activity) is ready.
- `browser_export_pdf`: Export the active page to a high-resolution PDF document saved to the workspace.
- `browser_upload_file`: Attach local files to an <input type="file"> element via CDP without native OS file dialogues.
- `browser_get_downloads`: Inspect files downloaded into the browser sandbox folder.
- `browser_extract_ax_tree`: Extract the semantic Accessibility (AX) Tree hierarchy for structured reasoning on complex SPAs.
- `browser_snapshot`: Capture a live visual screenshot of the current browser page, optionally with Set-of-Marks overlays (`overlay_som=True`) or full-page (`full_page=True`).
- `scan_live_job_firehose`: Scan verified live requisitions across Ashby, Greenhouse, Lever, and job boards.
- `media_download_youtube`: Downloads YouTube videos (MP4) or extracts audio songs (MP3 at 192kbps) directly into macOS ~/Downloads folder. Call this whenever the user asks to get, download, or rip a song, music, or video.

[EMBODIED WEB ACTION & APPLICATION PRINCIPLES]
You have FULL embodied agency with Chrome CDP access (port 9228) and host computer controls.
STRICLY FORBIDDEN: NEVER output canned AI refusals ("As an AI, I cannot physically submit job applications on your behalf", "I cannot click buttons", or "I am only a ghostwriter").
When asked to apply for jobs or interact with portals, navigate directly via `browser_navigate`, auto-dismiss popups via `browser_dismiss_overlays`, inspect elements, search local resume files with `computer_find_files`, and map form inputs with `browser_fill_form`.
When asked to download or get a song or video from YouTube, call `media_download_youtube` with format_type='audio' (for songs/music) or 'video' (for videos).

When the user asks in natural language to perform computer actions or browse the web, immediately translate their intent into the appropriate computer or browser tool. Explain findings clearly and format code/output cleanly.
"""
        except Exception:
            pass
        from app import platform_info
        if platform_info.WINDOWS:
            system_prompt = platform_info.localize(system_prompt) + "\n\n" + platform_info.SHELL_NOTE

        if active_workspace_path:
            system_prompt += f"""

[ACTIVE CONTROLLED WORKSPACE SCOPE]
The user is working inside and has focused the AI on this local workspace directory:
Target Folder: {active_workspace_path}

You have dedicated filesystem tools for this folder:
- fs_list_workspace_files: Explore directory structure, subfolders, and files in this folder.
- fs_read_workspace_file: Read source code, documentation (README), configs (package.json, pyproject.toml, requirements.txt), or notes.
- fs_write_workspace_file: Create, edit, or write code, dossiers, architecture docs, or scripts into this folder.
- fs_search_workspace: Search for keywords, class names, or symbols across all files in this folder.

When the user asks to inspect, audit, explain, write, or refactor code/files in this project, use these tools autonomously to inspect the directory and produce accurate, real-world findings.
"""

        # Auto-Recall relevant long-term memories from persistent vector vault
        try:
            from app.services.memory_service import memory_service
            from app.agent.prompts import format_memory_context

            query_for_mem = user_query.strip()
            if query_for_mem and len(query_for_mem) >= 3 and not self._is_conversational_greeting(query_for_mem):
                recalled_memories = await memory_service.search_memories_async(query=query_for_mem, limit=3)
                if recalled_memories:
                    mem_block = format_memory_context(recalled_memories)
                    if mem_block:
                        system_prompt += f"\n\n{mem_block}\n"
                        yield {
                            "type": "evidence_update",
                            "key": "long_term_memories",
                            "data": {
                                "recalled_count": len(recalled_memories),
                                "memories": [
                                    {
                                        "id": m["id"],
                                        "title": m["title"],
                                        "category": m["category"],
                                        "score": round(m.get("relevance_score", 0), 2)
                                    }
                                    for m in recalled_memories
                                ]
                            }
                        }
        except Exception as e:
            logger.debug(f"Memory auto-recall skipped: {e}")

        # Maintain true multi-turn conversation thread for native Thinking in Tools
        conversation_thread: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages
        ]

        # "Find me FDE jobs", "only remote ones", "apply to 2 and 5": real openings from companies' own
        # job boards, and applications as tasks that stop for an OK before submitting.
        if not has_image and not has_document:
            from app.agent import jobs as job_lane
            jd = job_lane.route(user_query, messages)
            if jd:
                async for ev in job_lane.stream(user_query, messages, jd, execute_tool, spoken=spoken):
                    yield ev
                return

        # Clear-cut web tasks and "submit it" / "continue" replies skip planning entirely.
        early_mission = self._mission_request(user_query, {})
        if early_mission:
            async for ev in self._stream_browser_mission(**early_mission):
                yield ev
            return

        # "Remember that my dog is called Biscuit": what Xod knows about you picks the fact up on its own (user_memory,
        # in the background); the answer is just an OK. The planner had nothing to plan and wrote an echo with "Sources: None".
        from app.services import user_memory as _um
        rem = _um.REMEMBER_RE.match(user_query or "")
        if rem and not has_image and not has_document and "?" not in user_query:
            what = rem.group(1).strip().rstrip(".!")
            what = re.sub(r"\bmy\b", "your", re.sub(r"\bI am\b|\bI'm\b", "you're", re.sub(r"\bI\b", "you", what)))
            yield {"type": "content", "content": f"Got it: I'll remember that {what}."}
            yield {"type": "done"}
            return

        # "Check yourself" / "something's broken": the doctor answers, no planner, no free-written report.
        # (The planner reached for general system stats and the writer invented problems that weren't there.)
        if self._SELF_CHECK_RE.search(user_query):
            async for ev in self._stream_self_check(user_query):
                yield ev
            return

        # "Change your own code so …": straight to the self-edit gate. Left to the planner, Gemma
        # answered from its training ("I can't modify my own code") without trying the tool.
        if self._SELF_EDIT_RE.search(user_query):
            async for ev in self._stream_self_edit(user_query):
                yield ev
            return

        # "Install jq", "set up a Node project and run it", "continue": jobs on the computer run in one loop that sees
        # every result, gets hints from what just failed, and keeps going until a separate check agrees it works
        # (agent/worker.py). As a fixed plan, every step ran blind and a failed install ended in "you can run …".
        # "New goal: find a flat under $2k by November" / "turn on the morning brief at 7:30" (services/goals.py)
        from app.services import goals
        goal_turn = goals.route(user_query) if not has_image and not has_document else None
        if goal_turn:
            async for ev in goals.stream_chat(goal_turn):
                yield ev
            return

        # A server's "add this to your MCP config" pasted into the chat: read it, try it, connect it (mcp_import).
        from app.services import mcp_import
        if not has_image and not has_document and mcp_import.looks_like(user_query):
            async for ev in mcp_import.stream_chat(user_query):
                yield ev
            return

        if not has_image and not has_document:
            from app.agent import worker
            job = worker.route(user_query, messages)
            if job:
                async for ev in worker.stream(user_query, messages, job, execute_tool, spoken=spoken):
                    yield ev
                return

        # "Make me a game", "build a timer", then "make it harder": the builder writes real files, runs
        # them in headless Chrome, fixes what breaks, and opens the result in the preview pane.
        if not has_image:
            from app.agent import builder
            decision = builder.route(user_query, messages, active_workspace_path)
            if decision:
                async for ev in builder.stream(user_query, messages, active_workspace_path, execute_tool, decision, spoken=spoken):
                    yield ev
                return

        # "Move my dentist appointment to Friday", "check off milk", "launch Spotify": a small
        # tool-calling loop over the connected apps (find the item, then change it; changes wait for an OK).
        from app.agent import operator as app_operator
        op = app_operator.route(user_query, has_image or has_document, messages)
        if op:
            async for ev in app_operator.stream(user_query, messages, op, execute_tool, spoken=spoken):
                yield ev
            return

        # Memory, before deciding anything: questions about the past search Xod's memory of earlier chats and work
        # (shown as a step, like a tool), and questions about its own work get the live picture of its tasks.
        # What they mean: a follow-up that leans on the conversation ("and the other one?", "how much is it") is
        # rewritten as a standalone request first (services/turn_quality.py), so routing, memory, search and the
        # planner all work from it. Past corrections on similar questions come along as lessons.
        from app.services import turn_quality
        self._understood = ""
        if not has_image and not has_document and turn_quality.needs_understanding(user_query, messages[:-1]):
            yield {"type": "status", "content": "Working out what you mean…"}
            self._understood = await turn_quality.understand(user_query, messages[:-1])
            if self._understood:
                yield {"type": "understood", "content": self._understood}
        try:
            self._lessons = await asyncio.to_thread(turn_quality.lessons_note, self._understood or user_query)
        except Exception:
            self._lessons = ""

        self._memory_context = ""
        if not has_image:
            async for ev in self._recall_for(self._understood or user_query, messages):
                yield ev

        # Most questions either need no tools or need a web search or two. One small routing call
        # (its prompt stays cached by the model server) decides which, and streams the answer itself
        # when no search is needed: one or two model calls instead of a full plan, which on a local
        # model saves ~25 s. Anything about files, apps, sites to operate or media goes to the planner.
        if self._fast_lane_ok(user_query, has_image, has_document, active_workspace_path):
            declined = False
            async for ev in self._stream_fast_lane(user_query, messages, evidence_board):
                if ev.get("type") == "_fast_declined":
                    declined = True
                    break
                yield ev
            if not declined:
                return

        # -------------------------------------------------------------
        # STEP 1: NEURAL DELIBERATION & FIRST-PRINCIPLES DECISION
        # -------------------------------------------------------------
        yield {"type": "status", "content": "Planning…"}

        prior_messages = messages[:-1] if len(messages) > 1 else []
        deliberation = None
        try:
            from app.services.agent_clis import context_note
            computer_note = await asyncio.to_thread(context_note, user_query)
        except Exception:
            computer_note = ""
        async for ev in self.deliberate_and_plan(
            query=user_query + (f"\n\n(What they mean, in full: {self._understood})" if getattr(self, "_understood", "") else "")
                  + (f"\n\n{computer_note}" if computer_note else ""),
            conversation_history=prior_messages,
            active_workspace_path=active_workspace_path,
            has_image=has_image,
            has_document=has_document,
            attached_document=attached_document,
            attached_image_urls=attached_image_urls
        ):
            if ev.get("type") == "deliberation_complete":
                deliberation = ev.get("deliberation")
            else:
                yield ev

        if not deliberation:
            deliberation = {
                "thought": "Directly evaluating user request and deploying intelligence research.",
                "decision": "execute_plan",
                "direct_response": "",
                "plan_title": f"Investigation: {user_query[:40]}",
                "plan_steps": [
                    {
                        "id": 1,
                        "title": f"Execute Deep Web & Intelligence Search on '{user_query[:40]}'",
                        "tools": ["web_search"],
                        "tool_args": [{"query": user_query}],
                        "status": "pending"
                    }
                ],
                "task_focus": "research",
                "is_swarm": False
            }

        # Multi-step web tasks (apply, sign up, fill a form...) run as one closed browser loop
        # instead of a fixed plan, so the agent can react to what each page shows.
        mission = self._mission_request(user_query, deliberation)
        if mission:
            async for ev in self._stream_browser_mission(**mission):
                yield ev
            return

        # If LLM decides direct conversational answer is sufficient (greetings, explanations, chitchat)
        if deliberation.get("decision") == "direct_answer":
            direct_reply = deliberation.get("direct_response", "").strip()
            if direct_reply:
                yield {"type": "content", "content": direct_reply}
            else:
                async for chunk in llm_service.stream_chat_completion(
                    messages=conversation_thread,
                    temperature=0.3
                ):
                    yield chunk
            yield {"type": "done"}
            return

        # -------------------------------------------------------------
        # STEP 2: REGISTER DELIBERATED EXECUTION PLAN
        # -------------------------------------------------------------
        plan_title = deliberation.get("plan_title") or f"Execution Plan: {user_query[:40]}"
        raw_steps = deliberation.get("plan_steps") or []
        task_focus = deliberation.get("task_focus", "general")
        is_swarm_task = bool(deliberation.get("is_swarm"))

        plan_steps = []
        for idx, s in enumerate(raw_steps, 1):
            tools = [self._canonicalize_tool_name(t) for t in s.get("tools", [])]
            plan_steps.append({
                "id": idx,
                "title": s.get("title", f"Milestone {idx}"),
                "tools": tools,
                "tool_args": s.get("tool_args", [{}]),
                "is_swarm": s.get("is_swarm", is_swarm_task),
                "is_visual": s.get("is_visual", has_image and idx == 1),
                "is_document": s.get("is_document", has_document and idx == 1),
                "status": "pending"
            })

        if not plan_steps:
            primary_search = "web_search"
            plan_steps.append({
                "id": 1,
                "title": f"Execute Deep Intelligence Investigation on '{user_query[:40]}'",
                "tools": [primary_search],
                "tool_args": [{"query": user_query}],
                "status": "pending"
            })

        # Append final synthesis milestone
        plan_steps.append({
            "id": len(plan_steps) + 1,
            "title": f"Synthesize Findings & Action Report ({task_focus.replace('_', ' ').title()})",
            "tools": [],
            "tool_args": [],
            "status": "pending"
        })

        # Emit plan event immediately to UI
        yield {
            "type": "plan",
            "title": plan_title,
            "steps": plan_steps
        }

        # -------------------------------------------------------------
        # STEP 3: SEQUENTIAL EXECUTION OF PLAN MILESTONES
        # -------------------------------------------------------------
        for step in plan_steps[:-1]:
            step["status"] = "in_progress"
            yield {"type": "plan_step", "step_id": step["id"], "status": "in_progress"}
            yield {"type": "status", "content": f"• {step['title']}…"}

            if step.get("is_swarm"):
                from app.agent.swarm import SwarmCoordinator
                swarm_coord = SwarmCoordinator(active_workspace_path=active_workspace_path)
                subagent_specs = swarm_coord.compile_jit_swarm(user_query)
                yield {
                    "type": "status",
                    "content": f"⚡ Xod Conductor: Spawning JIT Sub-Agent Swarm ({len(subagent_specs)} specialists)..."
                }
                async for ev in swarm_coord.run_swarm_stream(subagent_specs):
                    ev_type = ev.get("type")
                    if ev_type == "subagent_tool_result":
                        fn_name = ev.get("name")
                        fn_res = ev.get("data")
                        evidence_board.update_from_tool(fn_name, {}, fn_res)
                        yield {
                            "type": "evidence_update",
                            "stats": evidence_board.get_stats()
                        }
                    yield ev
            elif step.get("is_visual"):
                yield {"type": "status", "content": "Looking at the image…"}
                yield {
                    "type": "evidence_update",
                    "stats": evidence_board.get_stats()
                }
            elif step.get("is_document"):
                doc_name = (attached_document.get("name") if attached_document else None) or "Attached Document"
                yield {"type": "status", "content": f"Reading {doc_name}…"}
                yield {
                    "type": "evidence_update",
                    "stats": evidence_board.get_stats()
                }
            elif step.get("is_memory_synthesis") or step.get("is_conversational_followup"):
                yield {"type": "status", "content": "Remembering what we found earlier…"}
                yield {
                    "type": "evidence_update",
                    "stats": evidence_board.get_stats()
                }
            else:
                tools = step.get("tools", [])
                tool_args_list = step.get("tool_args", [])
                executed_calls = []
                executed_results = []

                for idx, fn_name in enumerate(tools):
                    args = tool_args_list[idx] if idx < len(tool_args_list) else {}
                    args = self._fill_placeholders(fn_name, args)   # show the real values in the UI too
                    call_id = f"call_{step['id']}_{idx}"

                    yield {
                        "type": "tool_call",
                        "name": fn_name,
                        "args": args,
                        "id": call_id
                    }

                    result, shown = None, False
                    origin = {"kind": "planner", **self._planner_example} if self._planner_example else None
                    async for ev in self._run_tool_healing(fn_name, args, call_id, user_query, active_workspace_path, origin):
                        if ev["type"] == "_final":
                            fn_name, args, result, shown = ev["tool"], ev["args"], ev["result"], ev["shown"]
                        else:
                            yield ev

                    evidence_board.update_from_tool(fn_name, args, result)
                    summary = self._format_summary(result)

                    if not shown:
                        yield {
                            "type": "tool_result",
                            "name": fn_name,
                            "summary": summary,
                            "data": result,
                            "id": call_id
                        }

                    yield {
                        "type": "evidence_update",
                        "stats": evidence_board.get_stats()
                    }

                    executed_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": json.dumps(args)
                        }
                    })
                    executed_results.append(result)

                if executed_calls:
                    conversation_thread.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": executed_calls
                    })
                    for tc_item, res_item in zip(executed_calls, executed_results):
                        clean_payload = self._clean_tool_payload(tc_item["function"]["name"], res_item)
                        conversation_thread.append({
                            "role": "tool",
                            "tool_call_id": tc_item["id"],
                            "name": tc_item["function"]["name"],
                            "content": json.dumps(clean_payload)
                        })

            step["status"] = "completed"
            yield {"type": "plan_step", "step_id": step["id"], "status": "completed"}

        # -------------------------------------------------------------
        # STEP 3.5: DYNAMIC ReAct REFLECTION & ADAPTIVE TOOL LOOP
        # -------------------------------------------------------------
        # If the task requires dynamic problem solving and is not a pure media generation,
        # inspect live observations, detect gaps/errors, and allow the agent to deliberate,
        # self-correct, and invoke follow-up tools dynamically.
        is_media_download_or_gen = any(
            any(t in ("media_generate_image", "media_generate_video", "media_download_youtube") for t in s.get("tools", []))
            for s in plan_steps
        )

        if not is_swarm_task and not is_media_download_or_gen and max_hops > 1:
            react_hop = 1
            max_react_hops = min(max_hops, 3)
            available_candidate_tools = select_tools_for_query(
                query=user_query,
                persona=self.persona,
                active_workspace_path=active_workspace_path,
                max_tools=16
            )

            from app.agent.healing import fingerprint
            seen_calls = {fingerprint(t, (st.get("tool_args") or [{}])[i] if i < len(st.get("tool_args") or []) else {})
                          for st in plan_steps for i, t in enumerate(st.get("tools", []))}
            while react_hop <= max_react_hops:
                stats = evidence_board.get_stats()
                audit_trail = getattr(evidence_board, "tool_audit_trail", [])
                has_observable_errors = False
                for item in audit_trail[-3:]:
                    res_val = item.get("result", {})
                    if isinstance(res_val, dict) and (res_val.get("success") is False or res_val.get("error")):
                        has_observable_errors = True
                        break

                # Every planned step worked and returned something: skip the extra "anything missing?"
                # model call (~15 s on a local model). It runs when something failed or came back empty.
                def _empty(r):
                    if not isinstance(r, dict):
                        return not r
                    if "results" in r:
                        hits = r.get("results") or []
                        return not hits or all((h or {}).get("title") == "Search Error" for h in hits if isinstance(h, dict))
                    return False
                if react_hop == 1 and not has_observable_errors and audit_trail and \
                        not any(_empty(t.get("result")) for t in audit_trail):
                    break

                # Prompt LLM to deliberate on whether more actions/self-corrections are required
                eval_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are Xod's ReAct Deliberator & Gap Analyzer.\n"
                            "Review the user query, recent tool observations, and determine if an additional corrective tool call is needed.\n"
                            "If the user query is already fully answered, return JSON: {\"action\": \"conclude\", \"reasoning\": \"Goal achieved.\"}\n"
                            "If a follow-up action or error recovery is needed, return JSON:\n"
                            "{\n"
                            "  \"action\": \"tool_call\",\n"
                            "  \"tool_name\": \"<one of available tools>\",\n"
                            "  \"tool_args\": {},\n"
                            "  \"reasoning\": \"<1 sentence explanation of why this action resolves the remaining gap>\"\n"
                            "}"
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"USER QUERY: {user_query}\n\n"
                            f"EVIDENCE SUMMARY:\n{evidence_board.to_markdown_summary()[:2000]}\n\n"
                            f"RECENT TOOL AUDIT:\n{json.dumps(audit_trail[-4:], indent=2)[:1500]}\n\n"
                            f"AVAILABLE TOOLS:\n{[t['function']['name'] for t in available_candidate_tools]}"
                        )
                    }
                ]

                try:
                    eval_reply = await llm_service.chat_completion(
                        messages=eval_messages,
                        temperature=0.1,
                        max_tokens=JSON_MIN_TOKENS,
                        task="react_step",   # follow-up decisions are training data too (see repair_learning)
                        think=False,         # a small yes/no-and-which-tool decision
                    )
                    decision = parse_llm_json(eval_reply.get("content", "")) or {"action": "conclude"}
                except Exception as eval_err:
                    logger.debug(f"ReAct reflection evaluation error: {eval_err}")
                    decision, eval_reply = {"action": "conclude"}, {}

                if decision.get("action") == "tool_call" and decision.get("tool_name"):
                    fn_name = decision["tool_name"]
                    fn_args = decision.get("tool_args", {})
                    # Same call again won't give a different answer: stop instead of looping.
                    fp = fingerprint(fn_name, fn_args)
                    if fp in seen_calls:
                        break
                    seen_calls.add(fp)
                    react_call_id = f"react_hop_{react_hop}"
                    reason = decision.get("reasoning", "Adaptive follow-up action.")

                    yield {"type": "status", "content": f"Checking something else: {reason}"}
                    yield {
                        "type": "tool_call",
                        "name": fn_name,
                        "args": fn_args,
                        "id": react_call_id
                    }

                    res, shown = None, False
                    origin = {"kind": "react", "id": eval_reply.get("_example_id"), "response": eval_reply.get("content", "")}
                    async for ev in self._run_tool_healing(fn_name, fn_args, react_call_id, user_query, active_workspace_path, origin):
                        if ev["type"] == "_final":
                            fn_name, fn_args, res, shown = ev["tool"], ev["args"], ev["result"], ev["shown"]
                        else:
                            yield ev

                    evidence_board.update_from_tool(fn_name, fn_args, res)
                    summary = self._format_summary(res)

                    if not shown:
                        yield {
                            "type": "tool_result",
                            "name": fn_name,
                            "summary": summary,
                            "data": res,
                            "id": react_call_id
                        }
                    yield {
                        "type": "evidence_update",
                        "stats": evidence_board.get_stats()
                    }

                    clean_payload = self._clean_tool_payload(fn_name, res)
                    conversation_thread.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": react_call_id,
                            "type": "function",
                            "function": {"name": fn_name, "arguments": json.dumps(fn_args)}
                        }]
                    })
                    conversation_thread.append({
                        "role": "tool",
                        "tool_call_id": react_call_id,
                        "name": fn_name,
                        "content": json.dumps(clean_payload)
                    })

                    react_hop += 1
                else:
                    # Concluded; no further tool calls necessary
                    break

        # Ensure all intermediate steps marked completed
        for s in plan_steps[:-1]:
            if s["status"] != "completed":
                s["status"] = "completed"
                yield {"type": "plan_step", "step_id": s["id"], "status": "completed"}

        # -------------------------------------------------------------
        # STEP 4: CONDITIONAL DUE DILIGENCE SCORECARD & SYNTHESIS
        # -------------------------------------------------------------
        final_step = plan_steps[-1]
        final_step["status"] = "in_progress"
        yield {"type": "plan_step", "step_id": final_step["id"], "status": "in_progress"}
        # Neural task classification derived from deliberator's task_focus & actual evidence
        is_creator_task = (
            task_focus in ("creator", "influencer") or
            bool((evidence_board.creator_telemetry or {}).get("shortlisted_candidates"))
        )
        is_media_task = (
            task_focus in ("media", "video", "image", "music") or
            bool(evidence_board.media_telemetry)
        )
        is_job_action_task = (
            not is_creator_task and not is_media_task and (
                task_focus in ("job_action", "career", "jobs") or
                any(t in ("browser_fill_form", "scan_live_job_firehose") for s in plan_steps for t in s.get("tools", []))
            )
        )
        is_browser_task = not is_creator_task and not is_job_action_task and not is_media_task and (
            task_focus == "browser" or bool(evidence_board.browser_telemetry)
        )
        is_computer_task = not is_creator_task and not is_job_action_task and not is_media_task and (
            task_focus == "computer" or bool(evidence_board.computer_telemetry)
        )
        is_file_organization_task = (
            task_focus == "workspace" or
            bool(evidence_board.fs_telemetry) or
            any(t in ("fs_create_folder", "fs_organize_files", "fs_move_file", "computer_batch_organize") for s in plan_steps for t in s.get("tools", []))
        )
        is_followup_memory_task = any(s.get("is_memory_synthesis") for s in plan_steps)

        if is_swarm_task:
            yield {"type": "status", "content": "Putting the team's findings together…"}
        elif is_media_task:
            yield {"type": "status", "content": "Writing up the download…"}
        elif is_creator_task:
            yield {"type": "status", "content": "Writing up the creators…"}
        elif is_job_action_task:
            yield {"type": "status", "content": "Writing up what I did on the site…"}
        elif is_file_organization_task:
            yield {"type": "status", "content": "Writing up the file changes…"}
        elif is_followup_memory_task:
            yield {"type": "status", "content": "Writing the answer…"}
        elif has_image:
            yield {"type": "status", "content": "Describing the image…"}
        elif has_document:
            yield {"type": "status", "content": "Writing up the document…"}
        elif is_browser_task:
            yield {"type": "status", "content": "Writing up the website…"}
        elif is_computer_task:
            yield {"type": "status", "content": "Writing up what I ran…"}
        else:
            yield {"type": "status", "content": "Writing the answer…"}

        stats = evidence_board.get_stats()
        # Only compute and emit a formal M&A scorecard if verified financial/business metrics were uncovered
        has_real_financials = (
            stats["metrics_count"] > 0 or
            bool(evidence_board.verified_financials) or
            bool(evidence_board.sec_filings) or
            self.persona in ("acquisition_hunter", "competitor_teardown")
        )

        entity_name = (
            evidence_board.target_entity
            or evidence_board.verified_financials.get("name")
            or (evidence_board.target_domain.split(".")[0].capitalize() if evidence_board.target_domain else None)
        )
        scorecard = None

        if has_real_financials and entity_name and not is_browser_task and not is_swarm_task and not has_image and not is_job_action_task:
            scorecard = compute_due_diligence_score(evidence_board)
            try:
                save_or_update_entity({
                    "slug": evidence_board.verified_financials.get("slug") or entity_name.lower().replace(" ", "-"),
                    "name": entity_name,
                    "category": evidence_board.verified_financials.get("category"),
                    "website": evidence_board.target_domain,
                    "verified_mrr": evidence_board.verified_financials.get("verified_mrr"),
                    "last_30d_revenue": evidence_board.verified_financials.get("last_30d_revenue"),
                    "total_revenue": evidence_board.verified_financials.get("total_revenue"),
                    "arr_multiple": evidence_board.verified_financials.get("arr_multiple"),
                    "growth_30d": evidence_board.verified_financials.get("growth_30d"),
                    "active_subscriptions": evidence_board.verified_financials.get("active_subscriptions"),
                    "tech_stack": evidence_board.tech_stack,
                    "pricing_signals": evidence_board.pricing_signals,
                    "tags": evidence_board.tags or ([evidence_board.verified_financials.get("category")] if evidence_board.verified_financials.get("category") else []),
                    "due_diligence_score": scorecard.get("overall_score"),
                    "dossier_summary": f"Audited {entity_name} with score {scorecard.get('overall_score')}/100 ({scorecard.get('rating')})."
                })
            except Exception as e:
                logger.warning(f"Could not auto-index entity to Knowledge Graph: {e}")

            yield {
                "type": "scorecard",
                "scorecard": scorecard,
                "entity": entity_name
            }

        evidence_summary = evidence_board.to_compact_summary()
        scorecard_clause = (
            f"Quantitative Health Score: {scorecard.get('overall_score')}/100 ({scorecard.get('rating')}).\n\n"
            if scorecard else ""
        )

        if has_image:
            task_instruction = f"""
Directly and thoroughly answer the user's visual request: "{user_query}"
You have full multimodal vision capabilities and are looking directly at the uploaded image.
Provide an articulate, structured visual walkthrough:
1. **Scene Overview & Primary Subjects**: Who or what is depicted in the foreground, background, and center. Describe people, clothing, posture, vehicles, street/environment, trees, houses, architecture.
2. **Key Visual Details & Elements**: Identify specific items (e.g. skateboard, logos, text on clothing, cars, street elements), lighting, weather, time of day, and colors.
3. **Atmosphere & Context**: Setting, vibe, activity taking place, and perspective.
4. **Direct Answer & Takeaways**: Provide a clear, definitive response directly answering "{user_query}".

Format your response in articulate Markdown with bold highlights and structured bullet points.
"""
        elif has_document:
            doc_meta_str = ""
            if attached_document:
                doc_meta_str = f"Document: {attached_document.get('name')} ({attached_document.get('size_formatted', '')}, {attached_document.get('page_count', 1)} pages).\n\n"
            task_instruction = f"""
Directly and thoroughly analyze the attached document addressing: "{user_query}"
{doc_meta_str}Provide a high-density, professional executive report:
1. **Document Overview & Metadata**: Title, document type, scope, and key topics covered.
2. **Core Findings & Structural Breakdown**: Key sections, data points, metrics, and arguments.
3. **Critical Analysis & Takeaways**: Significant observations, strengths, risks, or gaps.
4. **Actionable Recommendations & Next Steps**: Pragmatic, high-leverage guidance based on the document.

Format your response in articulate Markdown with bold highlights and structured bullet points.
"""
        elif is_swarm_task:
            task_instruction = f"""
Directly address the user's objective: "{user_query}"
You are Xod, the master conductor. Your specialized JIT sub-agent swarm has completed concurrent investigations across their respective pruned domains.
Using the verified Evidence Board observations below, deliver an articulate, cohesive, high-level institutional master report:
1. **Executive Synthesis & Unified Verdict**: Core takeaways combining all investigated domains.
2. **Specialist Domain Findings**:
   - Provide concrete observations, metrics, or configs uncovered by each specialist domain.
   - Quote real ports, processes, code structures, or web elements.
3. **Strategic Recommendations & Next Actions**:
   - Clear, pragmatic next steps for the user.

Format your response in articulate Markdown with bold highlights, tables or structured bullet points.
"""
        elif is_creator_task:
            task_instruction = f"""
Directly address the user's objective: "{user_query}"
You have scanned and scored creator candidates using quantitative Attention Prediction and CPM Arbitrage models.
Using the verified Evidence Board and Creator Telemetry below, deliver an institutional Attention Arbitrage & Creator Roster Dossier:

1. **Executive Arbitrage Overview**:
   - Total creators analyzed vs shortlisted candidates.
   - Blended Portfolio Predicted CPM vs Industry Baseline ($15–$25 CPM).
   - Expected View Floor ($V_{{50}}$) aggregate reach and total predicted creator budget.
   - Overall Portfolio Arbitrage Multiple.

2. **Ranked Creator Arbitrage Roster**:
   - Provide a clean Markdown table ranking the creators by Arbitrage Multiple / Efficiency.
   - Table columns: `Creator / Handle` | `Platform` | `Est. Followers` | `Median Views (V50)` | `AVR (Velocity)` | `Est. Fair Fee` | `Predicted CPM` | `Arbitrage Multiple` | `Contact / Gateways`
   - Include direct handles, follower counts, view floors, calculated fees, predicted CPMs, and contact emails/links where discovered.

3. **High-Conversion Creative Hooks & Angle Blueprints**:
   - For the top shortlisted creators, detail the tailored narrative hooks, content format recommendations, and angles designed for algorithmic breakout.

4. **Outreach & Deployment Protocol**:
   - Step-by-step guidance on contacting the creators, fee negotiation ranges, and whitelisting / spark ad amplification strategies.

Format your response in articulate Markdown with bold highlights, tables, and structured sections.
"""
        elif is_job_action_task:
            portal_label = "Y Combinator Work at a Startup" if any(k in user_query.lower() for k in ["work at a startup", "workatastartup", "yc", "y combinator"]) else "Target Career Portal"
            task_instruction = f"""
Directly and proactively address the user's career application objective: "{user_query}"
You are Xod, an embodied autonomous agent with direct web and host execution capabilities.
Base every statement on the tool results and evidence above. If a step did not happen or a page did not load, say so plainly; never claim navigation, form filling, or file discovery that the tool results don't show, and never invent job listings.

CRITICAL POLICY - HIGH EMBODIED AGENCY & ZERO CANNED REFUSALS:
- STRICTLY FORBIDDEN: NEVER state canned AI refusals ("As an AI, I cannot physically submit job applications on your behalf", "I cannot click buttons", "I cannot interact with dynamic web forms", or "I am only a ghostwriter"). You DO possess browser automation, form filling (`browser_fill_form`), element interaction (`browser_interact`), file uploading (`browser_upload_file`), and computer file access.
- Deliver a complete, high-agency Embodied Application & Execution Dossier:

1. **Autonomous Navigation & Live Portal Status**:
   - Report the live URL navigated to, page load status, and verified active requisitions/roles.
2. **Discovered Open Positions & Requisition Table**:
   - Present a structured Markdown table of open roles discovered on the portal: `Position Title` | `Company / Team` | `Location / Remote` | `Apply Status / Form Link`.
3. **Automated Candidate Profile & Form Field Mapping**:
   - Detail the automated form fields identified on the application page (e.g. Full Name, Email, LinkedIn, GitHub, Portfolio URL, Years of Experience, Short Bio).
   - Show how the candidate profile maps directly to each input field.
4. **Candidate Resume Discovery on Host**:
   - Report any local resume or CV files discovered on the Mac (from Spotlight `computer_find_files`), file paths, and readiness for automatic attachment via `browser_upload_file`.
5. **Action Execution Summary & Next Steps**:
   - Provide concrete next actions: automated draft preparation, one-click submission confirmation, or automated batch applying across selected target startups.

Format your response in articulate Markdown with bold highlights, tables, and clear actionable steps.
"""
        elif is_browser_task:
            target_site_label = evidence_board.target_domain or entity_name or "the target website"
            task_instruction = f"""
Directly and thoroughly answer the user's request: "{user_query}"
You used the browser on {target_site_label}. Describe ONLY what the tool results and evidence above actually show.
If the page did not load, was blank, or showed a verification/bot check, say that plainly in the first sentence and stop there; never describe a site from memory or general knowledge.
Where the evidence supports it, cover:
1. **Live First Impressions & Hero Offers**: Describe the key promotions, banners, hero products, and value propositions visible right on the page.
2. **Layout & Navigation Architecture**: Walk through the navigation structure, search bar accessibility, department menus, and service hubs.
3. **User Experience & Friction Assessment**: Critique the visual hierarchy, clutter vs clarity, responsiveness, and shopping flow.
4. **Key Takeaways & Strategic Verdict**: What works well, what feels outdated, and your overall verdict.

Format your response in articulate Markdown with bold highlights and bullet points.
"""
        elif is_computer_task:
            # Only sections for tools that actually ran: given the full list, the model filled in
            # every one (an invented "M2 Max, 32 GB, battery 88%" when no telemetry tool had run).
            ran = {item.get("tool") for item in getattr(evidence_board, "tool_audit_trail", [])}
            sections = [
                ({"computer_directory_tree", "computer_list_directory", "fs_list_workspace_files", "computer_find_files"},
                 "**Files & folders found**: the paths and items the tools returned."),
                ({"computer_read_file", "computer_edit_file", "computer_write_file", "fs_read_workspace_file", "fs_write_workspace_file"},
                 "**Files read or changed**: exact paths and what was in them or what changed."),
                ({"computer_batch_organize", "fs_organize_files", "computer_manage_file", "fs_move_file"},
                 "**Files organized or moved**: what moved where."),
                ({"computer_run_command", "computer_run_applescript", "computer_start_job", "computer_check_job", "computer_wait_for"},
                 "**Commands & background jobs**: the command, exit code, how long it took, and its actual output."),
                ({"computer_inspect_system", "computer_hardware_overview", "computer_inspect_port"},
                 "**System readings**: only the numbers the tools returned."),
            ]
            wanted = [text for tools, text in sections if ran & tools]
            task_instruction = f"""
Directly answer the user's computer request: "{user_query}"
Lead with the answer itself (the result, the output, what changed), in a sentence or two.
""" + ("Then, only the parts of this the person needs (no headings for them; the full log is under \"How I got this\"):\n"
       + "\n".join(f"- {w}" for w in wanted) if wanted else "") + """
Show a command and its output only when the person asked to run something or the output is the answer. Never paste
tool output as JSON ({"success": true, …}) or list every command that ran.
Only report what the tool results above show. Never add hardware specs, memory, battery, ports, or
file contents that no tool returned, and don't add sections for things that weren't done.
"""
        elif is_file_organization_task:
            task_instruction = f"""
Directly address the user's file/folder manipulation request: "{user_query}"
Present the actions taken, created folders, and organized files in clean Markdown:
- Detail the created folders and their locations.
- List all files moved or organized into each folder in a structured table or checklist.
- Provide practical instructions for finding and managing the files in the workspace.
"""
        elif is_followup_memory_task:
            task_instruction = f"""
Directly address the user's follow-up request: "{user_query}"
Rely seamlessly on our previous conversation history and established findings.
- If the user requested a table, format the data in clean, readable Markdown tables.
- If the user asked for comparison, elaboration, or clarification, provide an articulate, direct answer without re-explaining unneeded background.
"""
        elif is_media_task:
            task_instruction = f"""
Directly present the completed Media Studio asset creation for: "{user_query}"
You are Xod, an articulate multimedia creative director.
Deliver an engaging asset delivery walkthrough:
1. **Asset Preview & Delivery**:
   - For images or videos, embed the media preview: `![Asset Preview](/media/<filename>)` using the relative URL from the tool output.
   - Quote the full on-device path: `Full On-Device Path: <path>`.
   - Describe the asset in detail: scene composition, subjects (e.g. snowman, snowfall, tree), movement/animation dynamics (e.g. drifting snowflakes, playful bounce/wobble), colors, and atmosphere.
2. **Technical Specifications**:
   - Dimensions, format (e.g. MP4 H.264 video or JPEG image), duration/fps if video, file size, and on-device rendering engine.
3. **Host Computer & Finder Control**:
   - Inform the user they can click "Open in Finder" directly on the preview card, or tell you to open the folder or file in Finder anytime.

Format your response in articulate Markdown with bold highlights. Do not emit any tool call tags.
"""
        elif scorecard_clause:
            task_instruction = f"""
Synthesize a research brief on: "{user_query}".
{scorecard_clause}Use markdown tables for numbers, bold the key findings, and cover the tech stack and any security or infrastructure takeaways the evidence supports.
Cite sources inline as markdown links [name](url) using URLs from the Evidence Board.
"""
        else:
            task_instruction = f"""
Answer: "{user_query}"
- Lead with the direct answer in one to three sentences, then the supporting detail. Match the length to the question: a simple question gets a short answer, and stay under 250 words unless the user asked for detail.
- Use a table only when comparing several things; use headings only if the answer is long.
- Only include sections that fit the question (no "architecture", "security" or "dossier" framing unless it's about that).
- Cite facts you took from pages inline as markdown links [name](url), and end with a short "Sources" list of those links. General know-how needs no source.
- When the person needs steps (how to set something up, fix something), give them, from what you know if the pages didn't have them. Ask one short question only when the steps really differ (which operating system), and still give the most likely steps first.
"""

        synthesis_content = f"""The empirical research investigation has completed. Here is the verified Evidence Board containing all live observations:

{evidence_summary}

CRITICAL INSTRUCTIONS:
- Tool execution phase is CLOSED. DO NOT emit any tool calls, function calls, `<|tool_call|>`, or `call:...` code blocks.
- Speak directly to the user in articulate, professional Markdown.
- Facts about the person, their computer, accounts, files and anything live (prices, news, jobs, a page's contents) must come
  from the Evidence Board above; if something wasn't checked, don't state it. General know-how (how to set up SSH, what a
  command does) can come from what you know.
- Talk like a person, not a log: never mention the "evidence board", "evidence", "tool results" or "the results", and never
  quote raw tool output or JSON. Say what you did ("I searched…", "I looked at your repo…").
{task_instruction}"""

        system_instruction_text = (
            "You are Xod, an articulate, expert AI assistant. Provide your complete, detailed analysis, walkthrough, and thoughts directly to the user in fluent Markdown. Do NOT output any tool calls or function tags."
        )
        # A chat model that can't see gets the picture in words, from a small vision model (services/vision.py).
        seen_as_text = None
        if has_image:
            try:
                from app.services import vision
                if await vision.needed():
                    seen_as_text = await vision.as_text(user_query, attached_image_urls)
            except Exception as e:
                logger.debug(f"vision: {e}")
        if seen_as_text:
            system_instruction_text = ("You are Xod. The user attached an image; you can't see it yourself, so a vision model described it "
                                       "below. Answer from that description in Markdown, and say so plainly if a detail the user asks "
                                       "about isn't in it. Do not emit any tool calls.")
            synthesis_user_content = f"{seen_as_text}\n\n{task_instruction}\n\n{synthesis_content}"
        elif has_image:
            system_instruction_text = "You are Xod, an articulate multimodal AI assistant with sharp visual perception. You can see the user's uploaded image in full fidelity. Directly and thoroughly examine the image to describe its subjects, composition, details, and environment, answering the user's request in structured Markdown. Do not emit any tool calls."
            multimodal_user_parts = [
                {"type": "text", "text": f"{user_query}\n\n{task_instruction}\n\n{synthesis_content}"}
            ]
            for img_url in attached_image_urls:
                multimodal_user_parts.append({"type": "image_url", "image_url": {"url": img_url}})
            synthesis_user_content = multimodal_user_parts
        elif has_document:
            system_instruction_text = "You are Xod, an institutional document intelligence and analysis assistant. Analyze the user's attached document in complete detail. Synthesize an executive report with key findings, data points, structural breakdown, and recommendations in fluent Markdown. Do not emit any tool calls."
            synthesis_user_content = f"{user_query}\n\n{task_instruction}\n\n{synthesis_content}"
        elif is_media_task:
            system_instruction_text = "You are Xod, an articulate multimedia creative director. Present the generated or edited media asset directly to the user with the embedded markdown preview, visual description, and file path. Do not output any tool calls."
            synthesis_user_content = f"{user_query}\n\n{task_instruction}\n\n{synthesis_content}"
        elif is_job_action_task:
            system_instruction_text = "You are Xod, an embodied autonomous AI agent with direct browser automation and computer capabilities. You proactively navigate job portals, inspect application forms, map candidate data, locate resumes on the host machine, and prepare live submissions. NEVER give canned AI refusals ('As an AI, I cannot physically...'). Present an articulate, high-agency execution dossier with live findings, open roles table, form field mapping, and next actions. Do NOT output any raw tool call tags."
            synthesis_user_content = f"{user_query}\n\n{synthesis_content}"
        elif is_file_organization_task:
            system_instruction_text = ("You are Xod, the user's AI on their Mac. Report what you actually did to their files, in plain Markdown. "
                                       "Name ONLY files and folders that a tool result above shows were really created, written, moved or "
                                       "trashed, and say plainly if something wasn't done. Never describe files you didn't write, never "
                                       "present a plan as finished work, and don't call yourself a 'system' or list your limitations.")
            synthesis_user_content = f"{user_query}\n\n{task_instruction}\n\n{synthesis_content}"
        elif is_followup_memory_task:
            system_instruction_text = "You are Xod, an intelligent assistant with seamless conversational memory. You remember prior turns, prior facts, and prior research findings. Address the user's follow-up request directly using your memory of the previous discussion. Do not output any tool call tags."
            synthesis_user_content = f"{user_query}\n\n{task_instruction}\n\n{synthesis_content}"
        else:
            synthesis_user_content = f"{user_query}\n\n{synthesis_content}"

        try:
            from app.services import soul
            note = soul.prompt_note()
            if note:
                system_instruction_text += " " + note
        except Exception:
            pass
        if getattr(self, "_memory_context", ""):
            system_instruction_text += "\n\n" + self._memory_context
        if getattr(self, "_lessons", ""):
            system_instruction_text += "\n\n" + self._lessons
        if GAME_TALK_RE.search(user_query):
            system_instruction_text += " " + GAME_NOTE
        if spoken:
            system_instruction_text += (" This reply will also be read aloud: begin with one or two plain, spoken sentences "
                                        "that answer directly, then put any lists, tables or details after a blank line.")
        synthesis_messages = self._format_conversation_history_for_synthesis(
            messages=messages,
            current_user_content=synthesis_user_content,
            system_instruction=system_instruction_text
        )

        accumulated_text = ""
        synthesis_tool_call_detected = False

        try:
            # The research is done by now: writing it up needs no thinking phase, which on a local
            # model was ~80% of the wait. Depth on request (or for attached documents).
            deep = bool(has_document or self._DEPTH_RE.search(user_query or ""))
            async for chunk in llm_service.stream_chat_completion(
                messages=synthesis_messages,
                temperature=0.3,
                think=None if deep else False,
            ):
                if chunk.get("type") == "content":
                    tok = chunk.get("content", "")
                    accumulated_text += tok
                    # If model leaked a tool call tag during synthesis, hide it from live stream
                    if "<|tool_call" in accumulated_text or "<tool_call" in accumulated_text or ("call:" in accumulated_text and len(accumulated_text.strip()) < 80):
                        synthesis_tool_call_detected = True
                        continue
                    yield chunk
                else:
                    yield chunk
        except Exception as e:
            logger.error(f"Final streaming error: {e}")
            yield {"type": "error", "content": f"Synthesis error: {str(e)}"}

        # Robust Recovery: If model only emitted a tool call syntax and produced no real text
        cleaned_prose = re.sub(r'(?:<\|?tool_call\|?>)?\s*(?:call:)?([a-zA-Z0-9_]+)\s*\{.*?\}\s*(?:<\|?/?tool_call\|?>)?', '', accumulated_text, flags=re.DOTALL).strip()
        if (synthesis_tool_call_detected or not accumulated_text.strip()) and len(cleaned_prose) < 50:
            if is_media_task and evidence_board.media_telemetry:
                last_m = evidence_board.media_telemetry[-1]
                m_filename = last_m.get("filename") or "asset"
                m_url = last_m.get("media_url") or last_m.get("relative_url") or f"/media/{m_filename}"
                m_path = last_m.get("output_path") or str(DATA_DIR / "media_studio" / m_filename)
                is_vid = any(m_filename.endswith(ext) for ext in [".mp4", ".webm", ".mov"])
                kind = "Video" if is_vid else "Visual"
                dur = f"- **Duration**: {last_m.get('duration_sec', 4.0)}s\n" if is_vid else ""
                fallback_msg = f"""I have completed and rendered your {kind.lower()} asset on your machine:

### 🎬 {kind} Asset Delivery

![{kind} Preview]({m_url})

**Full On-Device Path:**
`{m_path}`

---

### ⚙️ Technical Specifications
- **Format**: {"MP4 (H.264)" if is_vid else "JPEG (RGB)"}
{dur}- **File Size**: {last_m.get('size_bytes', 0):,} bytes
- **Engine**: Local Media Studio On-Device Core

---

### 🚀 Host System Control
You can click **Open in Finder** above to view the file directly in macOS Finder, or ask me to open any folder on your Mac at any time!
"""
                yield {"type": "content", "content": fallback_msg}
                accumulated_text = fallback_msg
            else:
                logger.info("Model leaked tool call during synthesis. Executing robust natural-language recovery stream...")
            if seen_as_text:
                recovery_messages = self._format_conversation_history_for_synthesis(
                    messages=messages, current_user_content=seen_as_text,
                    system_instruction="You are Xod. A vision model described the user's image below; answer from it. Do not call any tools.")
            elif has_image:
                recov_parts = [
                    {"type": "text", "text": f"{user_query}\n\nPlease share your detailed visual walkthrough of what you see in the image and your thoughts."}
                ]
                for img_url in attached_image_urls:
                    recov_parts.append({"type": "image_url", "image_url": {"url": img_url}})
                recovery_messages = self._format_conversation_history_for_synthesis(
                    messages=messages,
                    current_user_content=recov_parts,
                    system_instruction="You are Xod, a thoughtful multimodal AI assistant. Describe everything you see in the image in rich detail. Do not call any tools."
                )
            else:
                recovery_text = f"{user_query}\n\nThe empirical investigation has completed. Here are the observations:\n\n{evidence_summary}\n\nPlease share your detailed walkthrough and thoughts."
                recovery_messages = self._format_conversation_history_for_synthesis(
                    messages=messages,
                    current_user_content=recovery_text,
                    system_instruction="You are Xod, a thoughtful AI assistant. Speak directly to the user in fluent Markdown. Do not call any tools."
                )
            try:
                async for chunk in llm_service.stream_chat_completion(
                    messages=recovery_messages,
                    temperature=0.4
                ):
                    if chunk.get("type") == "content":
                        cleaned = re.sub(r'<\|?/?tool_call\|?>.*', '', chunk["content"])
                        if cleaned:
                            yield {"type": "content", "content": cleaned}
                    else:
                        yield chunk
            except Exception as e:
                logger.error(f"Recovery synthesis failed: {e}")

        for ev in self._source_link_events(evidence_board, accumulated_text):
            yield ev

        final_step["status"] = "completed"
        yield {"type": "plan_step", "step_id": final_step["id"], "status": "completed"}
        yield {"type": "done"}

    @staticmethod
    def _source_link_events(evidence_board: EvidenceBoard, answer: str) -> List[Dict[str, Any]]:
        """Small local models often name sources without linking them. Add the real links from what
        the searches returned, so every answer can be checked (and Share report lists them)."""
        if "](http" in answer:
            return []
        links = []
        for src in getattr(evidence_board, "sources", []) or []:
            url, title = str(src.get("url") or ""), str(src.get("title") or "").strip()
            if url.startswith("http") and "tavily.com" not in url and url not in [u for _, u in links]:
                links.append((title[:90] or url, url))
        if links:
            return [{"type": "content", "content": "\n\n**Source links**\n" + "\n".join(
                f"{i}. [{t.replace('[', '(').replace(']', ')')}]({u})" for i, (t, u) in enumerate(links[:6], 1))}]
        web_search = {"web_search", "tavily_search", "you_search", "you_news_search", "omni_deep_search"}   # not memory_search, file search…
        if any((t.get("tool") or "") in web_search for t in getattr(evidence_board, "tool_audit_trail", [])):
            # Searched, found nothing usable: don't let a from-memory answer pass as researched.
            return [{"type": "content", "content": "\n\n> ⚠️ Web search didn't return anything usable this time, so this answer "
                     "comes from the model's own knowledge and may be out of date. Try again, or add a search key in Settings."}]
        return []

    # ------------------------------------------------------------------
    # The fast lane: route, maybe search, answer
    # ------------------------------------------------------------------
    # Things the planner's tools are for: the Mac, files, apps, sites to operate, media, connected
    # accounts, routines and reports. "my notion" goes to the planner; "Notion vs Obsidian" doesn't.
    _FAST_EXCLUDE_RE = re.compile(
        r"https?://|\bwww\.|"
        r"\b(files?|folders?|director(y|ies)|desktop|downloads folder|my downloads|documents folder|finder|terminal|shell|command line|"
        r"script|run (this|the|a|my)|install|uninstall|ports?\b|process(es)?\b|kill|clipboard|screenshot|screen ?shot|"
        r"open (the |my )?(app|file|folder|website|site|page|link)|launch|my mac|this mac|my computer|my laptop|"
        r"(this|my) (pc|computer|machine|laptop|desktop computer)('?s)?|ip address|disk space|free space|(using|uses|use) (the most |a lot of |so much )?(memory|ram|cpu)|"
        r"organi[sz]e|clean ?up|tidy|rename|delete|trash|move (the|my|these|this)|edit (the|my|this)|"
        r"repo(sitory)?|workspace|codebase|my project|this project|"
        r"browser|log ?in|sign ?(up|in)|apply (for|to)|fill (out|in)|submit|book (a|me|the)|order|buy|add to cart|check ?out|"
        r"download|youtube|mp3|mp4|video|image|picture|photo|draw|generate|render|"
        r"(my|in|on|to|from|the) (calendar|inbox|email|mail|notes|reminders|notion|linear|github|slack)|"
        r"remind me|schedule|every (day|morning|evening|week|month|monday|tuesday|wednesday|thursday|friday)|routine|"
        r"remember (this|that)|forget|report|pdf|share (this|it)|"
        r"tech ?stack|built with|funding|founders?|mrr|arr|acquisition|acquire|for sale|deal flow|due diligence|"
        r"competitors?|teardown|who works at|email (address|of)|xod|"
        r"send (a |an |the )?(message|dm|email|text|note)|message (the|my|him|her|them)|reply to|"
        r"jobs? (on|at|in|near|for)|job (listings?|openings?|board)|hiring|resume|cv|"
        # This Mac's everyday abilities: your messages, where you are, places near you, your home
        r"texts?|texted|imessages?|my messages|(any|new) messages|messaged|text (me|him|her|them|back)|"
        r"near (?!(the |a )?(future|term|end|zero|perfect|miss|death|record|impossible|enough)\b)\w+|nearby|closest|nearest|where am i|my location|"
        r"lights?|lamps?|thermostat|(un)?lock (the|my)|garage|scene|contacts?|phone number|my calendar|reminders?)\b", re.I)

    # Something to do on this computer (its files, folders, apps or command line), not something to look up.
    _LOCAL_ACTION_RE = re.compile(
        r"\b(create|make|write|save|rename|delete|remove|move|copy|open|run|execute|tidy|organi[sz]e|clean ?up|unzip|zip|"
        r"list|show me|find|read|what'?s (in|on)|how (big|many|much)|biggest|largest)\b.{0,80}\b(files?|folders?|director(y|ies)|desktop|documents|downloads|pictures|"
        r"\.\w{2,4}\b|commands?|terminal|powershell|script)\b"
        # ...or about this computer itself: what's running, its memory, disk, network ("which programs use the most memory on this PC?")
        r"|\b(?:this|my|the) (?:pc|computer|laptop|machine|mac|desktop computer)(?:'?s)?\b.{0,60}\b(?:memory|ram|cpu|processor|processes|programs|apps|"
        r"running|disk|drive|storage|space|battery|ip|network|wi-?fi|uptime|graphics|gpu|ports?)\b"
        r"|\b(?:memory|ram|cpu|processes|programs|apps|running|disk|drive|storage|space|battery|ip address|network|wi-?fi|uptime|graphics|gpu|ports?)\b"
        r".{0,60}\b(?:on|in|of) (?:this|my) (?:pc|computer|laptop|machine|mac)\b", re.I | re.S)
    # Asking about the web, the world or the news: these keep their web tools even when a file is mentioned.
    _WEB_WORDS_RE = re.compile(r"\b(web|online|internet|google|search (for|the)|look ?up|latest|news|price|weather|website|"
                               r"site|url|https?|research|compare|reviews?|today'?s|current)\b", re.I)

    ROUTER_PROMPT = (
        "You are Xod, a helpful assistant on the user's Mac. Decide how to answer the user's latest message, "
        "and reply in exactly one of these three ways.\n\n"
        "1. SEARCH when the answer depends on facts that change or that you may not know exactly: news, anything "
        "'latest', 'current' or 'right now', prices and plans, comparing products or services, reviews and "
        "recommendations, people, companies, sports, weather, events and dates. When in doubt, search. "
        "Reply with one line only:\nSEARCH: <search query> | <optional second search query>\n\n"
        "2. TOOLS when it needs the user's files, apps or accounts, operating a website, or making images or video. "
        "Reply with the one word:\nTOOLS\n\n"
        "3. Otherwise answer it yourself right away: directly and briefly (a few sentences), in Markdown. This "
        "includes questions about your past chats and your own work when the message comes with notes from your "
        "memory or your task list: answer from those notes (say when things happened), don't search the web.\n\n"
        "Examples:\n"
        "User: Compare the pricing of Figma and Sketch\nSEARCH: Figma pricing plans | Sketch pricing plans\n"
        "User: who won the Lakers game last night\nSEARCH: Lakers game result last night\n"
        "User: tidy up my downloads folder\nTOOLS\n"
        "User: explain recursion simply\nRecursion is when a function solves a problem by calling itself on a smaller "
        "piece of it, until the piece is small enough to answer directly.")

    # Clearly about facts that change: search without asking the model (small models answer these
    # from memory). Only for self-contained questions; "how much is it?" needs the conversation.
    _FRESH_RE = re.compile(
        r"\b(latest|newest|news|headlines|today'?s?|tonight|this (week|month|year|weekend)|right now|currently|current|"
        r"recent(ly)?|upcoming|price[sd]?|pricing|costs?|how much (is|are|does|do)|cheapest|deals?|"
        r"best\b.{0,60}\b(to buy|for|under|right now|in 20\d\d)|top \d+|compare|comparison|vs\.?|versus|reviews?|"
        r"who won|score[sd]?|results?|standings|weather|forecast|rain|snow|temperature|tomorrow|yesterday|last night|stock|share price|release date|"
        r"when (is|does|will)|open(ing)? hours|20(2[5-9]|3\d))\b", re.I)
    _REFERS_BACK_RE = re.compile(r"\b(it|that|this|those|these|them|they|he|she|the (first|second|last|other) one)\b", re.I)
    # An answer that says it changed something on the computer (only tools can): "I have added the line to todo.txt".
    _CLAIMS_DONE_RE = re.compile(
        r"^\W*(?:(?:ok(?:ay)?|done|sure|great|alright|all set)\W+)?(?:i\s*(?:'ve|have|just)?|i've|it's been|it has been|this has been)\s+"
        r"(?:successfully\s+|now\s+)?(?:added|appended|created|made|deleted|removed|moved|saved|renamed|updated|written|wrote|copied|"
        r"trashed|sent|scheduled|set up|installed|uninstalled|opened|closed|changed|edited|inserted|organi[sz]ed|zipped|downloaded|put)\b"
        r".{0,120}?\b(?:files?|folders?|line|lines|desktop|documents|downloads|calendar|reminders?|email|inbox|todo|list|"
        r"\.[a-z0-9]{2,4}\b|recycle bin|trash|clipboard|it|them)\b"
        r"|^\W*(?:the|your)\s+(?:[\w.`'\"\-]+\s+){0,4}?(?:files?|folders?|line|lines|todo|list|reminders?|email|event)\b.{0,60}?"
        r"(?:\b(?:has|have) been\s+(?:successfully\s+)?(?:added|appended|created|deleted|removed|moved|saved|renamed|updated|written|copied|"
        r"trashed|sent|scheduled|changed|edited|organi[sz]ed|zipped)\b|\bnow (?:contains|has|says|reads|includes|lists)\b|\bis now\b)", re.I | re.S)
    # "Now add a second line to it": a change to something from earlier in the chat. Only tools can do that.
    _CHANGE_IT_RE = re.compile(
        r"^\s*(?:(?:now|also|and|then|ok(?:ay)?|please|can you|could you)[\s,]+)*(?:add|append|insert|delete|remove|rename|move|put|"
        r"change|edit|update|replace|undo|copy|paste|save|fix|clear|empty|sort)\b.{0,50}?\b(?:it|that|this|them|those|these|the (?:file|folder|"
        r"line|list|text|note|document|item|entry|row|column))\b", re.I | re.S)

    ROUTER_PROMPT_SPOKEN = None   # set below: the same prompt, answering out loud

    _PAST_RE = re.compile(
        r"\b(remember (when|that|the|what|how)|last time|the other day|earlier|previously|yesterday|last (week|month|night|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d+ (days?|weeks?|months?) ago|a (week|month) ago|"
        r"(we|you|i) (talked|chatted|discussed|spoke|said|told|asked|decided|found|built|made|did|wrote|looked|researched|"
        r"worked|planned|picked|chose|compared)|you (found|said|told|made|built|recommended|suggested|wrote|sent|gave)|"
        r"that (article|link|report|list|game|app|site|video|recipe|idea|thing|place|plan|email|doc|company|product|job) "
        r"(you|we|i)|what was (that|the)|did (we|you|i) ever|have (we|you|i) (ever|already)|in (january|february|march|april|"
        r"may|june|july|august|september|october|november|december)|our (last|previous) (chat|conversation))\b", re.I)

    async def _recall_for(self, query: str, messages: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        """Sets self._memory_context for this turn (past episodes, Xod's own work, its notebook) and shows the lookup."""
        from app.services.recall import when_label as recall_when
        from app.services import work_state
        blocks = []
        if work_state.WORK_RE.search(query or ""):
            try:
                blocks.append(await asyncio.to_thread(work_state.note))
            except Exception as e:
                logger.debug(f"work state: {e}")
        if work_state.OWN_RE.search(query or "") or work_state.WORK_RE.search(query or ""):
            try:
                from app.services import curiosity
                own = await asyncio.to_thread(curiosity.recent_note)
                if own:
                    blocks.append(own)
            except Exception as e:
                logger.debug(f"notebook: {e}")
        try:
            from app.services import user_memory
            use_past = user_memory.load()["settings"].get("recall", True)
        except Exception:
            use_past = True
        if use_past and self._PAST_RE.search(query or ""):
            from app.services import recall
            yield {"type": "status", "content": "Looking through your past chats…"}
            yield {"type": "tool_call", "name": "memory_search", "args": {"query": query}, "id": "mem_recall"}
            try:
                found = await recall.search(query, k=5)
            except Exception as e:
                logger.debug(f"recall: {e}")
                found = {"results": []}
            res = found.get("results") or []
            note = recall.note_for(found)
            if note:
                blocks.append(note)
            where = f" from {found['window']}" if found.get("window") and found.get("in_window") else ""
            summary = f"Found {len(res)} in your past chats and tasks{where}" if res else "Nothing in your past chats about that"
            yield {"type": "tool_result", "name": "memory_search", "id": "mem_recall", "summary": summary,
                   "data": {"memories": [{"when": r["when"], "title": r["title"], "kind": r["kind_label"], "session_id": r["session_id"]}
                                         for r in res]}}
        # Its own notebook: something it read up on by itself because it wasn't sure (services/curiosity.py).
        try:
            from app.services import curiosity
            known = await asyncio.wait_for(curiosity.knowledge(query, k=2), 4)
        except Exception as e:
            logger.debug(f"notebook: {e}")
            known = []
        if known:
            yield {"type": "tool_call", "name": "notebook_search", "args": {"query": query}, "id": "notebook"}
            yield {"type": "tool_result", "name": "notebook_search", "id": "notebook",
                   "summary": f"Read up on this {recall_when(known[0]['ts'])}: {known[0]['question'][:80]}",
                   "data": {"notes": [{"question": n["question"], "answer": n["answer"], "sources": n["sources"]} for n in known]}}
            blocks.append(curiosity.knowledge_note(known))
        self._memory_context = "\n\n".join(b for b in blocks if b)

    def _fast_lane_ok(self, query: str, has_image: bool, has_document: bool, workspace: Optional[str]) -> bool:
        if has_image or has_document or workspace or self.persona != "ultimate_researcher":
            return False
        if os.environ.get("XOD_FAST_LANE", "1") == "0":
            return False
        q = (query or "").strip()
        if len(q) < 40 and self._CONFIRM_RE.search(q):
            return False          # "go ahead", "yes": answering something the planner asked
        if self._CHANGE_IT_RE.search(q):
            return False          # "now add a line to it": a change, which needs tools
        from app.agent.tools import _wants_cli_agent
        if _wants_cli_agent(q):
            return False          # coding agents: the router answered "how's the claude job?" from nothing (evals)
        return 0 < len(q) <= 600 and not self._FAST_EXCLUDE_RE.search(q)

    @staticmethod
    def _today() -> str:
        return time.strftime("%A, %B %d, %Y")

    @classmethod
    def router_messages(cls, query: str, history: List[Dict[str, Any]], spoken: bool = False) -> List[Dict[str, Any]]:
        turns: List[Dict[str, str]] = []
        for m in history[-4:]:
            r, c = m.get("role"), m.get("content")
            if r in ("user", "assistant") and isinstance(c, str) and c.strip():
                if turns and turns[-1]["role"] == r:
                    turns[-1]["content"] += "\n\n" + c[:600]
                else:
                    turns.append({"role": r, "content": c[:600]})
        if turns and turns[-1]["role"] == "user":
            turns.pop()
        # The date is part of the system prompt: it changes once a day, so the prompt stays cached.
        prompt = cls.ROUTER_PROMPT_SPOKEN if spoken else cls.ROUTER_PROMPT
        from app.services import soul
        note = soul.prompt_note()
        if GAME_TALK_RE.search(query):
            note = (note + "\n" if note else "") + GAME_NOTE
        try:
            from app.services import user_memory
            extra = user_memory.relevant_note(query)
        except Exception:
            extra = ""
        return [{"role": "system", "content": f"{prompt}\n\nToday is {cls._today()}." + (f"\n{note}" if note else "")}, *turns,
                {"role": "user", "content": query + extra}]

    async def _stream_fast_lane(self, query: str, messages: List[Dict[str, Any]],
                                evidence_board: EvidenceBoard) -> AsyncGenerator[Dict[str, Any], None]:
        """Yields chat events, or a single {"type": "_fast_declined"} (before anything visible)
        when the planner should take this one."""
        head, mode, line, answer, held, shown = "", None, "", "", "", False
        has_history = any(m.get("role") == "assistant" for m in messages[:-1])
        # A follow-up rewritten as a standalone request stands on its own: it can be searched directly.
        full = getattr(self, "_understood", "") or query
        stands_alone = bool(getattr(self, "_understood", "")) or not (has_history and self._REFERS_BACK_RE.search(query))
        if self._FRESH_RE.search(full) and stands_alone \
                and not getattr(self, "_memory_context", ""):    # "what did we talk about yesterday" is memory, not news
            async for ev in self._fast_search_and_answer(full, [full], messages, evidence_board):
                yield ev
            return
        # A head start: a question that stands on its own gets searched (and its best pages opened) right away, while
        # the router decides whether it needs the web at all. Thrown away if it doesn't.
        prefetch = self._maybe_prefetch(full, has_history and not stands_alone)
        router_msgs = self.router_messages(query, messages[:-1], spoken=getattr(self, "spoken", False))
        if getattr(self, "_understood", ""):
            router_msgs[-1]["content"] += f"\n\n(What they mean, in full: {self._understood})"
        if getattr(self, "_memory_context", ""):
            router_msgs[-1]["content"] += "\n\n" + self._memory_context
        if getattr(self, "_lessons", ""):
            router_msgs[-1]["content"] += "\n\n" + self._lessons
        stream = llm_service.stream_chat_completion(
            messages=router_msgs,
            temperature=0.2, max_tokens=300 if getattr(self, "spoken", False) else 900,
            think=False, task="router")
        try:
            async for ch in stream:
                kind = ch.get("type")
                if kind == "error":
                    yield {"type": "_fast_declined"}
                    return
                if kind != "content":
                    continue
                tok = ch.get("content", "")
                if mode is None:
                    head += tok
                    norm = re.sub(r"^[\s*`#>_]+", "", head)
                    if len(norm) < 6 and "\n" not in norm.strip():
                        continue
                    up = norm.upper()
                    if up.startswith("SEARCH"):
                        mode, line = "search", norm
                    elif up.startswith("TOOLS") or norm.startswith(("{", "[")):
                        mode = "tools"
                        break
                    else:
                        mode, answer = "answer", head.lstrip()
                        if prefetch is not None:
                            prefetch.cancel()      # answering from what it knows: no web needed
                        held = answer
                        shown = False
                    continue
                if mode == "search":
                    line += tok
                    if "\n" in line.strip():
                        break          # the queries are all we need; stop the model here
                else:
                    answer += tok
                    held += tok
                    # The opening waits until it can be checked: "I've added the line to your todo.txt" with no tool
                    # run is a made-up action. Then the planner does it for real (nothing has been shown yet).
                    if not shown:
                        if len(answer) < 90:
                            continue
                        if self._CLAIMS_DONE_RE.search(answer[:200]):
                            mode = "tools"
                            break
                        shown = True
                    # The last few characters wait a moment: small models sometimes end an answer with the word
                    # "TOOLS" (the router's other choice), which must not reach the page.
                    if len(held) > 8:
                        yield {"type": "content", "content": held[:-8]}
                        held = held[-8:]
        finally:
            await stream.aclose()
            if prefetch is not None and mode != "search" and not (mode is None and re.sub(r"^[\s*`#>_]+", "", head).upper().startswith("SEARCH")):
                prefetch.cancel()

        if mode is None:      # a very short reply
            norm = re.sub(r"^[\s*`#>_]+", "", head).strip()
            if not norm or norm.upper().startswith("TOOLS"):
                yield {"type": "_fast_declined"}
                return
            if norm.upper().startswith("SEARCH"):
                mode, line = "search", norm
            else:
                yield {"type": "content", "content": norm}
                mode = "answer"
        if mode in ("tools", "search"):
            # The stream was stopped as soon as the decision was clear, so the server never recorded it: record the
            # decision here (training data for the router, labelled later by how the turn went).
            try:
                from app.services import dataset_recorder
                dataset_recorder.capture(task="router", messages=router_msgs, response="TOOLS" if mode == "tools" else line.strip().split("\n")[0],
                                         model=llm_service.get_endpoint_info().get("model"), provider=llm_service.get_endpoint_info().get("provider"))
            except Exception:
                pass
        if mode == "tools":
            yield {"type": "_fast_declined"}
            return
        if mode == "answer" and self._CLAIMS_DONE_RE.search(answer[:200]) and not shown:
            mode = "tools"         # a short made-up "done": do it for real
            yield {"type": "_fast_declined"}
            return
        if mode == "answer":
            rest = re.sub(r"\s*\bTOOLS\s*$", "", held)
            if rest:
                yield {"type": "content", "content": rest}
            yield {"type": "done"}
            return

        body = line.split(":", 1)[1] if ":" in line.split("\n")[0] else line[6:]
        queries = [q.strip(" \"'`*") for q in body.strip().split("\n")[0].split("|")]
        queries = [q for q in queries if len(q) > 2][:2] or [full]
        async for ev in self._fast_search_and_answer(full, queries, messages, evidence_board, prefetch=prefetch):
            yield ev

    _NO_PREFETCH_RE = re.compile(
        r"^\s*(hi|hey|hello|yo|thanks|thank you|ok(ay)?|cool|nice|great|good (morning|night|evening))\b|"
        r"\b(write|draft|compose|translate|rewrite|summari[sz]e|poem|story|joke|riddle|code|function|script|regex|"
        r"build|make me|create|draw|generate|fix|debug|refactor|remind me|open|launch|my (files?|folder|code|project|"
        r"calendar|notes|email|mail|downloads|desktop))\b", re.I)

    def _maybe_prefetch(self, query: str, has_history: bool) -> Optional[_Prefetch]:
        """Start searching now if this is a self-contained question that probably needs the web."""
        if has_history and self._REFERS_BACK_RE.search(query):
            return None                    # "what about the other one?" needs the conversation to search well
        if len(query.split()) < 3 or len(query) > 300 or self._NO_PREFETCH_RE.search(query):
            return None
        try:
            deep = bool(self._DEPTH_RE.search(query))
            return _Prefetch(query, 4 if deep else 3, 1800 if deep else 800)
        except Exception:
            return None

    async def _fast_search_and_answer(self, query: str, queries: List[str], messages: List[Dict[str, Any]],
                                      evidence_board: EvidenceBoard, prefetch: Optional["_Prefetch"] = None
                                      ) -> AsyncGenerator[Dict[str, Any], None]:
        """Search in Xod's browser, read the best pages, answer from them with citations. Made to feel quick:
        the search may already be under way (prefetch, started while the router was deciding); pages are read in
        parallel and stragglers aren't waited for; and the model reads the question and each page as it arrives
        (prefill), so when the last page is in it only has that one left to read before it starts writing."""
        from app.services import web_reader
        tool = "web_search"
        if re.search(r"\b(latest|newest|news|recent(ly)?|this (week|month)|who won|current(ly)?|right now|upcoming|last night|yesterday)\b", query, re.I):
            stamp = time.strftime("%B %Y")
            queries = [q if re.search(r"\b20\d\d\b", q) else f"{q} {stamp}" for q in queries]
        deep = bool(self._DEPTH_RE.search(query))
        spoken = getattr(self, "spoken", False)
        if spoken:
            system = (
                "You are Xod, talking with the user out loud. Answer their question from the search results in one to "
                "three short spoken sentences: lead with the answer, in plain conversational words. No lists, headings, "
                "markdown, links or citation numbers. If the results don't "
                "answer it, say so in a few words.\n"
                f"Today is {self._today()}.")
        else:
            system = (
                "You are Xod, a helpful research assistant. Answer the user's question using the search results they give you.\n"
                "- Start with the direct answer in one or two sentences.\n"
                + ("- Then cover the details thoroughly, with short headings if it helps.\n" if deep else
                   "- Then add a few short bullet points only if they help. Keep the whole answer under 120 words.\n")
                + "- Anything current or specific (prices, news, a product, a person, a page) comes only from the results; if "
                "they don't have it, say so in one line. How-to and general know-how can come from what you know: when the "
                "person needs steps, give them, and never answer \"the results don't cover that\" to something you can explain.\n"
                "- Cite facts you took from the results by their numbers, like [1] or [2][3]; general know-how needs none.\n"
                "- Never mention 'search results', 'the results' or what they cover, today's date or these instructions (say what "
                "you found, or just answer), and don't add a sources list.\n"
                f"Today is {self._today()}.")
        try:
            from app.services import user_memory
            about = user_memory.core_note()
            if about:
                system += "\n" + about
        except Exception:
            pass
        head = f"{query}\n\nSearch results:\n"

        def prompt(blocks: List[str]) -> List[Dict[str, Any]]:
            return self._format_conversation_history_for_synthesis(
                messages=messages, current_user_content=head + ("\n\n".join(blocks) if blocks else "(nothing came back)"),
                system_instruction=system, max_history_turns=4, max_turn_chars=1200)

        warmer = _Warmer()
        warmer.push(prompt([]))           # the instructions, the conversation and the question, while it searches
        page_budget = 1800 if deep else 800          # every 100 characters is ~0.2 s before the first word
        n_read = 4 if deep else 3

        # 1. Search, like a person would: a search engine's results page in Xod's own browser (no API key).
        ids = [f"fast_{i}" for i in range(len(queries))]
        found: List[Dict[str, Any]] = []
        futures = None
        if prefetch is not None:
            yield {"type": "status", "content": "Searching the web in Xod's browser…"}
            yield {"type": "tool_call", "name": tool, "args": {"query": prefetch.query}, "id": ids[0]}
            ids, queries = ids[:1], [prefetch.query]
            got = await prefetch.result()
            if got:
                found, futures = [got[0]], got[2]
                picks = got[1]
        if not found:
            if prefetch is None:
                yield {"type": "status", "content": "Searching the web in Xod's browser…"}
                for cid, q in zip(ids, queries):
                    yield {"type": "tool_call", "name": tool, "args": {"query": q}, "id": cid}

            async def find(q: str) -> Dict[str, Any]:
                try:
                    return await web_reader.search(q, 8)
                except Exception as e:
                    return {"results": [], "error": str(e)}
            found = list(await asyncio.gather(*(find(q) for q in queries)))
            picks = None

        merged, seen = [], set()
        for rank in range(8):
            for f in found:
                rs = f.get("results") or []
                if rank < len(rs) and rs[rank]["url"] not in seen:
                    seen.add(rs[rank]["url"])
                    merged.append(rs[rank])
        if picks is None:
            picks = web_reader.pick_pages(merged, n_read)

        # 2. Read the best few in background tabs, in parallel; each page goes to the model as soon as it's in.
        blocks: List[str] = []
        meta: List[Tuple[str, str]] = []

        def page_block(h: Dict[str, Any]) -> str:
            body = re.sub(r"!\[[^\]]*\]\([^)]*\)|\S+\.(?:png|jpe?g|gif|webp|svg)\b|^#+\s*", " ", str(h.get("content") or ""), flags=re.M)
            body = re.sub(r"[ \t]+", " ", body).strip()[:page_budget]
            return (f"[{len(blocks) + 1}] {str(h.get('title') or '')[:100]} ({h['url']})"
                    f"{' · ' + h['published'] if h.get('published') else ''} — page read:\n{body}")

        if picks:
            names = ", ".join(re.sub(r"^www\.", "", h.get("host") or "") for h in picks)
            yield {"type": "status", "content": f"Reading {names}…"}
            urls = [h.get("open") or h["url"] for h in picks]
            done_n = 0
            async for i, pg in web_reader.read_stream(urls, query, page_budget, futures=futures):
                if not pg.get("ok"):
                    continue
                h = picks[i]
                h["content"] = pg["content"]
                if str(pg.get("url", "")).startswith("http"):
                    h["url"] = pg["url"]
                if pg.get("published"):
                    h["published"] = pg["published"][:10]
                blocks.append(page_block(h))
                meta.append((str(h.get("title") or h["url"]).strip()[:90], h["url"]))
                warmer.push(prompt(blocks))
                done_n += 1
                left = len(picks) - done_n
                yield {"type": "status", "content": f"Read {re.sub(r'^www[.]', '', h.get('host') or '')}" + (f" · {left} more…" if left else "")}
        for h in merged:
            h.pop("open", None)
        if not merged:
            # The browser couldn't search (Chrome missing, every engine blocked): a configured API or plain HTTP.
            for q in queries:
                try:
                    r = await execute_tool("tavily_search" if settings.tavily_api_key else "you_search", {"query": q})
                except Exception as e:
                    r = {"error": str(e)}
                merged += [x for x in (r or {}).get("results") or [] if isinstance(x, dict)]
        engine = next((f.get("engine") for f in found if f.get("engine")), "")
        results = [{"results": [h for h in merged if h in (f.get("results") or [])] or (merged if i == 0 else []),
                    "provider": "browser", "engine": engine,
                    "pages_read": sum(1 for h in picks if h.get("content"))} for i, f in enumerate(found or [{}])]
        for cid, q, r in zip(ids, queries, results):
            evidence_board.update_from_tool(tool, {"query": q}, r)
            yield {"type": "tool_result", "name": tool, "summary": self._format_summary(r), "data": r, "id": cid}

        # 3. The other results as short snippets, after the pages (so everything read so far stays a prefix).
        for h in merged:
            if len(blocks) >= (7 if deep else 5):     # the model reads every word: more results is a slower answer
                break
            url = str(h.get("url") or "")
            if h.get("content") or not url.startswith("http") or h.get("title") == "Search Error":
                continue
            text = re.sub(r"\s+", " ", str(h.get("snippet") or h.get("content") or "")).strip()[:240]
            blocks.append(f"[{len(blocks) + 1}] {str(h.get('title') or '')[:100]} ({url}) — search snippet:\n{text}")
            meta.append((str(h.get("title") or url).strip()[:90], url))

        # ---- answer
        max_out = 300 if spoken else (1800 if deep else 600)
        msgs = prompt(blocks)
        yield {"type": "status", "content": "Writing the answer…"}
        await warmer.settle()
        written = ""
        async for ch in llm_service.stream_chat_completion(messages=msgs, temperature=0.3,
                                                           max_tokens=max_out, think=False):
            if ch.get("type") == "content":
                written += ch.get("content", "")
            yield ch
        # Number for number, the results it actually cited (the model only sees the numbers).
        cited = []
        for grp in re.findall(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", written):
            for n in re.split(r"[,;]", grp):
                n = int(n)
                if 1 <= n <= len(meta) and n not in cited:
                    cited.append(n)
        if cited:
            yield {"type": "content", "content": "\n\n**Sources**\n" + "\n".join(
                f"[{n}] [{meta[n - 1][0].replace('[', '(').replace(']', ')')}]({meta[n - 1][1]})  " for n in sorted(cited))}
        else:
            for ev in self._source_link_events(evidence_board, written):
                yield ev
        yield {"type": "done"}

    # ------------------------------------------------------------------
    # Browser missions: multi-step web tasks run as one closed loop
    # ------------------------------------------------------------------
    _WEB_ACTION_RE = re.compile(
        r"\b(apply|applying|sign ?up|register|fill (?:out|in)|submit|book|reserve|order|check ?out|"
        r"log ?in|enroll|subscribe|unsubscribe|rsvp|post (?:a|an|my)|create an account|"
        r"buy|purchase|add .{1,40} to (?:my |the )?(?:cart|bag|basket|wishlist)|compare|check (?:the )?(?:price|prices|availability|status|stock)|"
        r"find|look up|look for|search|track|download|message|send (?:a |an )?(?:message|dm|email|note)|reply to|"
        r"cancel (?:my )?(?:subscription|order|account)|go to|open|navigate to|browse)\b",
        re.I,
    )
    # Browser missions need a concrete site; general research questions stay with web search.
    _WEB_CONTEXT_RE = re.compile(
        r"https?://|localhost(?::\d+)?|\b[a-z0-9-]+\.(?:com|io|ai|co|org|net|dev|app|shop|store)\b|"
        r"\b(website|site|online|form|portal|my account|cart|"
        r"indeed|linkedin|glassdoor|wellfound|ziprecruiter|workatastartup|amazon|ebay|etsy|walmart|target|bestbuy|"
        r"reddit|twitter|x\.com|facebook|instagram|youtube|github|airbnb|booking|expedia|craigslist|zillow|opentable)\b",
        re.I,
    )
    _DEPTH_RE = re.compile(r"\b(deep[- ]?dive|in[- ]depth|thorough(ly)?|think (it )?(through|carefully|hard)|step by step|detailed analysis)\b", re.I)
    _QUESTION_RE = re.compile(r"^\s*(how|what|why|when|should|is|are|does|do i|explain|tell me)\b", re.I)
    _CONFIRM_RE = re.compile(r"\b(submit it|send it|go ahead|yes|yep|approve[d]?|confirm(ed)?|do it|ship it)\b", re.I)
    _SELF_CHECK_RE = re.compile(
        r"(check|diagnose|heal|fix|test) (yourself|itself|xod)|self[- ]?(heal|check|diagnos)|health ?check|"
        r"(something|anything)('?s| is)? (seems? )?(broken|wrong)|what('?s| is) (wrong|broken)( with (you|xod|yourself|the app|this app))?\s*[?.!]*$|"
        r"(is|are) (you|xod) (broken|working|ok)|keeps? failing|"
        r"why (isn'?t|is not|aren'?t) (it|you|xod|this) working", re.I)
    _SELF_EDIT_RE = re.compile(
        r"\b(change|edit|modify|update|fix|patch|rewrite|improve)\s+(your|xod'?s)\s+(own\s+)?(source\s+)?code\b|"
        r"\b(edit|change|modify|patch|upgrade|reprogram)\s+(yourself|itself)\b|\bself[- ]?edit\b", re.I)
    _CONTINUE_RE = re.compile(r"\b(continue|keep going|resume|try again|retry|carry on|done|cleared|finished|added|fixed)\b", re.I)

    @staticmethod
    def _normalize_tool_args(args_list: List[Any], n_tools: int) -> List[Dict[str, Any]]:
        """Gemma sometimes writes one call's arguments as name/value items:
        [{"name": "command", "val": "ls"}, {"name": "cwd", "val": "~"}]  ->  [{"command": "ls", "cwd": "~"}]."""
        def is_pair(d):
            return isinstance(d, dict) and "name" in d and len(d) == 2 and any(k in d for k in ("val", "value"))
        if args_list and all(is_pair(d) for d in args_list) and (n_tools <= 1 or len(args_list) != n_tools):
            merged = {d["name"]: d.get("val", d.get("value")) for d in args_list}
            return [merged] + [{} for _ in range(max(0, n_tools - 1))]
        return [d if isinstance(d, dict) else {} for d in args_list]

    # Values a plan step can't know in advance (they come from earlier steps' results).
    _PLACEHOLDER_RE = re.compile(r"replace[_ ]?with|from[_ ]step|<[^>]+>|\{\{.*\}\}|^\$\w+$|previous[_ ]step|placeholder", re.I)
    _CARRY_KEYS = ("job_id", "pid", "output_path", "path", "filepath", "url", "port", "session_id", "tab_id")

    _ABOUT_SELF_RE = re.compile(
        r"\b(what (can|can't|cannot|can not) you (do|see|use|access|connect to)\s*(\?|$|\.|for me\b|here\b|now\b|right now|on (this|my)\b|and\b)|"
        r"what tools (do you have|can you use|are (there|available))|which tools (do you have|can you use|are (there|available))|"
        r"what('s| is) (not |un)?(available|working|supported)( (for you|on this|here|right now))?\s*(\?|$)|"
        r"what (doesn't|does not|won't|isn't|is not) (work|available|supported)|"
        r"why (can'?t|cannot|don't|won't|didn't) you (see|look at|read (images|pictures)|use (tools|the browser|apps)|hear|browse|connect|access|call tools)|"
        r"are you able to (see|use|read|browse|hear|connect)|can you (see|look at|read) (images|pictures|photos|screenshots)|"
        r"(your|xod's) (limits|limitations|capabilities|abilities)|what (are|is) (you|xod) capable of)", re.I)

    async def _answer_about_self(self, question: str, messages: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services import capabilities
        yield {"type": "status", "content": "Checking what works on this computer…"}
        rep = await capabilities.report()
        facts = capabilities.as_text(rep)
        yield {"type": "tool_call", "name": "xod_capabilities", "args": {}, "id": "caps"}
        yield {"type": "tool_result", "name": "xod_capabilities", "id": "caps",
               "summary": f"{rep['counts']['ok']} working, {rep['counts']['limited']} limited, {rep['counts']['off']} not available",
               "data": {"items": [{k: i[k] for k in ("name", "status", "why")} for i in rep["items"]]}}
        prompt = [{"role": "system", "content": (
            "You are Xod. Answer the person's question about what you can and can't do, using ONLY the report below, which was "
            "checked just now on their computer with their model. Be specific: name what doesn't work or is limited, say why in "
            "plain words, and what would fix it. If they asked about one thing, answer about that thing. Short: a few sentences "
            f"or a short list. Don't mention the report itself.\n\n{facts}")},
                  {"role": "user", "content": question}]
        said = ""
        try:
            async for ch in llm_service.stream_chat_completion(messages=prompt, temperature=0.2, max_tokens=700, think=False, task="about_self"):
                if ch.get("type") == "content" and ch.get("content"):
                    said += ch["content"]
                    yield {"type": "content", "content": ch["content"]}
                elif ch.get("type") == "error" and not said:
                    break
        except Exception:
            pass
        if not said.strip():     # the model isn't answering (one of the things that may be off): the facts themselves
            yield {"type": "content", "content": facts.replace("WHAT XOD CAN DO RIGHT NOW (checked just now on this computer):\n", "")}
        yield {"type": "done"}

    def _remember_values(self, result: Any) -> None:
        if isinstance(result, dict):
            for k in self._CARRY_KEYS:
                if result.get(k) not in (None, "", []):
                    self._carried[k] = result[k]
            # What this turn's searches turned up (files and folders), for _which_one.
            found = [r.get("name") or os.path.basename(str(r.get("path") or "")) for r in (result.get("results") or [])
                     if isinstance(r, dict)]
            if found:
                self._carried["_found"] = found

    def _which_one(self, fn_name: str, args: Dict[str, Any], user_query: str) -> Optional[Dict[str, Any]]:
        """"Delete the receipt" when the search found receipt1.pdf and receipt2.pdf: ask which one rather than pick.
        Only for deleting, and only when the person didn't name the file (its name isn't in what they said)."""
        if fn_name != "computer_manage_file" or str(args.get("action", "")).lower() not in ("trash", "delete", "remove"):
            return None
        src = str(args.get("src") or args.get("path") or "").rstrip("\\/")
        target = os.path.basename(src)
        q0 = user_query or ""
        # "Delete the receipt in …\Receipts" trashed the whole Receipts folder: the folder they named is where the
        # thing is, not the thing. Look inside it instead.
        if target and os.path.isdir(os.path.expanduser(src)) and not re.search(r"\b(folder|directory|dir)\b", q0.split(target)[0], re.I) \
                and re.search(rf"\b(in|inside|from|within)\s+(?:the\s+|my\s+)?[^\n]*?{re.escape(target)}\b", q0, re.I):
            try:
                inside = sorted(os.listdir(os.path.expanduser(src)))
            except OSError:
                inside = []
            stems = {re.sub(r"(es|s)$", "", w) for w in re.findall(r"[a-z0-9]{3,}", q0.split(" in ")[0].lower())
                     if w not in {"delete", "remove", "trash", "the", "please", "file", "get", "rid", "throw", "away"}}
            alike = [n for n in inside if any(s and s in n.lower() for s in stems)] or inside
            return {"success": False, "skipped": True, "needs_choice": len(alike) > 1, "candidates": alike[:10],
                    "error": f"Not deleted: {target} is the folder the person said it's in, not the thing to delete. "
                             + (f"Inside it: {', '.join(alike[:10])}. Ask them which one (or whether all of them)."
                                if len(alike) > 1 else f"Delete {alike[0]} inside it instead." if alike else "It's empty.")}
        found = self._carried.get("_found") or []
        if len(found) < 2:
            # No search first ("delete the receipt in …\Receipts" went straight to receipt1.pdf): what's next to it.
            try:
                parent = os.path.dirname(os.path.expanduser(src))
                found = sorted(os.listdir(parent))[:200] if parent and os.path.isdir(parent) else found
            except OSError:
                pass
        q = (user_query or "").lower()
        if not target or len(found) < 2 or re.search(r"\b(all|every|both|each|them|these|those)\b", q):
            return None
        # Named exactly ("delete receipt2.pdf", "delete the Receipts folder"): no question.
        if re.search(rf"\b{re.escape(target.lower())}\b", q) or (os.path.splitext(target)[0].lower() != target.lower()
                                                                  and re.search(rf"\b{re.escape(os.path.splitext(target)[0].lower())}\b", q)):
            return None
        # What they called it ("the receipt") against everything the search found (receipt1.pdf, receipt2.pdf, the Receipts folder…).
        words = {w for w in re.findall(r"[a-z0-9]{3,}", q) if w not in {"delete", "remove", "trash", "the", "my", "file", "folder", "please",
                                                                         "recycle", "bin", "from", "and", "get", "rid", "throw", "away"}}
        stems = {re.sub(r"(es|s)$", "", w) for w in words}
        alike = [n for n in found if any(s and s in n.lower() for s in stems)]
        if len(alike) < 2:
            return None
        return {"success": False, "skipped": True, "needs_choice": True, "candidates": alike[:10],
                "error": f"Not deleted: several files match ({', '.join(alike[:10])}) and the person didn't say which. "
                         "Ask them which one (or whether all of them) before deleting anything."}

    def _fill_placeholders(self, fn_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fills arguments that point at an earlier step's result (e.g. "REPLACE_WITH_JOB_ID_FROM_STEP_1"),
        and a missing job_id for job tools, from what earlier steps in this turn returned."""
        out = dict(args or {})
        for k, v in list(out.items()):
            if isinstance(v, str) and self._PLACEHOLDER_RE.search(v) and k in self._carried:
                out[k] = self._carried[k]
        if fn_name in ("computer_check_job", "computer_stop_job", "computer_wait_for") and not out.get("job_id") \
                and "job_id" in self._carried and (fn_name != "computer_wait_for" or not any(out.get(x) for x in ("port", "url", "path", "pid"))):
            out["job_id"] = self._carried["job_id"]
        return out

    async def _exec_tool_stream(self, fn_name: str, args: Dict[str, Any], call_id: str,
                                active_workspace_path: Optional[str]):
        """Runs one tool. Shell commands stream their output; everything else returns at once.
        Yields tool_stream events, then {"type": "_result", "result": ...}."""
        if fn_name == "computer_wait_for":
            from app.services.job_watch import wait_for_stream
            result: Dict[str, Any] = {}
            async for ev in wait_for_stream(args):
                if ev["type"] == "progress":
                    yield {"type": "status", "content": ev["text"]}   # also keeps a task card's progress fresh
                else:
                    result = ev["result"]
            yield {"type": "_result", "result": result}
            return
        if fn_name == "computer_run_command":
            from app.services.system_control import system_control_service
            cmd_str = args.get("command", "")
            out_accum, err_accum = [], []
            exit_code, status, duration = 0, "completed", 0.0
            async for chunk in system_control_service.run_command_stream(
                    command=cmd_str, cwd=args.get("cwd") or active_workspace_path or None,
                    timeout_sec=args.get("timeout_sec", 45)):
                if chunk.get("type") in ("stdout", "stderr"):
                    txt = chunk.get("text", "")
                    (out_accum if chunk["type"] == "stdout" else err_accum).append(txt)
                    yield {"type": "tool_stream", "name": fn_name, "id": call_id, "stream_type": chunk["type"], "text": txt}
                elif chunk.get("type") == "done":
                    exit_code = chunk.get("exit_code", 0)
                    status = chunk.get("status", "completed")
                    duration = chunk.get("duration_sec", 0.0)
            result = {"command": cmd_str, "cwd": system_control_service.current_working_directory, "status": status,
                      "exit_code": exit_code, "stdout": "".join(out_accum), "stderr": "".join(err_accum),
                      "duration_sec": duration}
        else:
            try:
                result = await execute_tool(fn_name, args, active_workspace_path=active_workspace_path)
            except Exception as e:
                result = {"success": False, "error": str(e)}
        yield {"type": "_result", "result": result}

    async def _run_tool_healing(self, fn_name: str, args: Dict[str, Any], call_id: str, user_query: str,
                                active_workspace_path: Optional[str], origin: Optional[Dict[str, Any]] = None):
        """Runs a tool, checks the result for real, and repairs failures (see agent/healing.py).
        Yields UI events, then {"type": "_final", "tool", "args", "result"} with whichever call worked."""
        from app.agent.healing import Repairer, check_result
        from app.agent.tools import CHANGE_TOOLS, only_asks
        args = self._fill_placeholders(fn_name, args)
        result: Dict[str, Any] = {}
        if fn_name in CHANGE_TOOLS and only_asks(user_query):
            # A question never changes the person's files, whatever the plan says.
            result = {"success": False, "skipped": True, "error": "Not run: the person only asked a question, so nothing gets changed. "
                      "Answer it from what was read (to see the end of a file, read it with tail=<number of lines>)."}
            yield {"type": "_final", "tool": fn_name, "args": args, "result": result, "shown": False}
            return
        which = self._which_one(fn_name, args, user_query)
        if which:
            yield {"type": "_final", "tool": fn_name, "args": args, "result": which, "shown": False}
            return
        async for ev in self._exec_tool_stream(fn_name, args, call_id, active_workspace_path):
            if ev["type"] == "_result":
                result = ev["result"]
            else:
                yield ev
        if check_result(fn_name, args, result)["ok"]:
            self._remember_values(result)
            yield {"type": "_final", "tool": fn_name, "args": args, "result": result, "shown": False}
            return
        # Show the failed attempt, then repair.
        yield {"type": "tool_result", "name": fn_name, "summary": self._format_summary(result), "data": result, "id": call_id}

        async def run(name: str, a: Dict[str, Any]) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            async for e in self._exec_tool_stream(name, a, call_id, active_workspace_path):
                if e["type"] == "_result":
                    out = e["result"]
            return out

        candidates = select_tools_for_query(query=user_query, persona=self.persona,
                                            active_workspace_path=active_workspace_path, max_tools=16)
        repairer = Repairer(run, user_query, TOOL_DEFINITIONS, candidates)
        async for ev in repairer.repair(fn_name, args, result, call_id):
            if ev["type"] == "_repair_done":
                # The decision that made the failed call learns from it (label, and a pair if the fix is clear).
                try:
                    from app.services.repair_learning import record_outcome
                    record_outcome(origin, fn_name, args, ev["tool"], ev["args"], ev["ok"], ev.get("kind") or "unknown",
                                   canon=self._canonicalize_tool_name)
                except Exception as e:
                    logger.debug(f"repair learning skipped: {e}")
                if ev["ok"]:
                    self._remember_values(ev["result"])
                yield {"type": "_final", "tool": ev["tool"], "args": ev["args"], "result": ev["result"], "shown": True}
            else:
                yield ev

    async def _stream_self_edit(self, user_query: str) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services import self_edit
        ed = self_edit.editor_model()
        yield {"type": "plan", "title": "Changing my own code", "steps": [
            {"id": 1, "title": f"Write the change ({ed['model']}) and test it in a copy", "tools": ["xod_self_edit"], "status": "in_progress"},
            {"id": 2, "title": "Report what happened", "tools": [], "status": "pending"},
        ]}
        yield {"type": "plan_step", "step_id": 1, "status": "in_progress"}
        yield {"type": "status", "content": f"🛠 Code changes by {ed['model']}: {ed['tier']} ({ed['why']})"}
        args = {"goal": user_query}
        yield {"type": "tool_call", "name": "xod_self_edit", "args": args, "id": "self_edit_1"}
        result = await execute_tool("xod_self_edit", args)
        yield {"type": "tool_result", "name": "xod_self_edit", "id": "self_edit_1", "data": result,
               "summary": result.get("summary") or result.get("error", "")[:120]}
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "in_progress"}
        if result.get("success"):
            lines = [f"**{result['summary']}**", "",
                     f"- Written by {result['model']} ({result['tier']}), {result['changed_lines']} lines in {', '.join(f'`{f}`' for f in result['files'])}",
                     f"- Passed in a test copy: {', '.join(result['checks_passed'])}",
                     f"- {result['next']}"]
            if result["status"] == "proposed":
                lines.append("\nOpen **Tools → Health → Self-editing** to see the exact diff and approve or reject it.")
        else:
            lines = [f"I didn't change any code. {result.get('error', '')}"]
        yield {"type": "content", "content": "\n".join(lines)}
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        yield {"type": "done"}

    async def _stream_self_check(self, user_query: str) -> AsyncGenerator[Dict[str, Any], None]:
        wants_fix = bool(re.search(r"\b(fix|repair|heal|sort (it|this) out|make it work)\b", user_query, re.I))
        yield {"type": "plan", "title": "Checking myself", "steps": [
            {"id": 1, "title": "Run Xod's health checks" + (" and fix what I can" if wants_fix else ""),
             "tools": ["xod_doctor"], "status": "in_progress"},
            {"id": 2, "title": "Report exactly what I found", "tools": [], "status": "pending"},
        ]}
        yield {"type": "plan_step", "step_id": 1, "status": "in_progress"}
        args = {"fix": wants_fix}
        yield {"type": "tool_call", "name": "xod_doctor", "args": args, "id": "doctor_1"}
        result = await execute_tool("xod_doctor", args)
        yield {"type": "tool_result", "name": "xod_doctor", "id": "doctor_1", "data": result,
               "summary": f"Health: {result.get('overall', '?')}"}
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "in_progress"}
        yield {"type": "content", "content": self._format_doctor_report(result, wants_fix)}
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        yield {"type": "done"}

    @staticmethod
    def _format_doctor_report(result: Dict[str, Any], wants_fix: bool) -> str:
        """Written straight from the doctor's report: every line is something a check actually measured."""
        checks = result.get("checks") or []
        fixed = [f for f in result.get("applied_fixes") or [] if f.get("ok")]
        failed_fixes = [f for f in result.get("applied_fixes") or [] if not f.get("ok")]
        problems = [c for c in checks if c["level"] == "fail"]
        notes = [c for c in checks if c["level"] == "warn"]
        lines = []
        if not problems and not notes and not fixed:
            lines.append("**Everything I can check is working.**")
        elif not problems and fixed:
            lines.append("**I found a problem and fixed it.**")
        elif problems:
            lines.append(f"**{len(problems)} thing{'s' if len(problems) > 1 else ''} need{'' if len(problems) > 1 else 's'} attention.**")
        else:
            lines.append("**Working, with something worth knowing.**")
        if fixed:
            lines.append("\n**Fixed:**")
            lines += [f"- {f['check']}: {f['detail']}" for f in fixed]
        if failed_fixes:
            lines.append("\n**Tried but couldn't fix:**")
            lines += [f"- {f['check']}: {f['detail']}" for f in failed_fixes]
        if problems:
            lines.append("\n**Needs attention:**")
            lines += [f"- **{c['label']}**: {c['detail']}" + (f" (I can do this: *{c['fix_label']}*. Say \"fix it\" or use Tools → Health.)"
                                                              if c.get("fix_label") and not wants_fix else "") for c in problems]
        if notes:
            lines.append("\n**Worth knowing:**")
            lines += [f"- **{c['label']}**: {c['detail']}" for c in notes]
        ok = [c for c in checks if c["level"] == "ok"]
        if ok:
            lines.append("\n**Working:** " + "; ".join(f"{c['label']} ({c['detail']})" for c in ok))
        recent = result.get("recent_failures") or []
        if recent:
            lines.append("\n**Recent tool errors:** " + "; ".join(
                f"{r['tool']} ({r['kind'].replace('_', ' ')})" for r in recent[-5:]))
        return "\n".join(lines)

    def _mission_request(self, user_query: str, deliberation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Decides whether this turn should run (or resume) a browser mission."""
        from app.services.browser_sandbox import browser_sandbox_service as sandbox
        query = (user_query or "").strip()

        pending = sandbox.pending_submit
        if pending and time.time() - pending.get("created_at", 0) > 3600:
            sandbox.pending_submit = pending = None
        if pending and self._CONFIRM_RE.search(query) and len(query) < 120:
            return {
                "goal": f"{pending['goal']}\nThe user approved pressing \"{pending['label']}\" on this page. Press it now, "
                        "then keep going with the goal (any later submit will ask the user again). "
                        "Finish once the goal is done and report what the page confirms.",
                "start_url": "current",
                "allow_submit": True,
                "max_steps": 25,
                "resuming": True,
            }

        last = sandbox.latest_mission or {}
        if last.get("status") in ("blocked", "incomplete", "needs_input", "needs_confirmation", "failed") and self._CONTINUE_RE.search(query) and len(query) < 300:
            return {
                "goal": f"{last.get('goal')}\nUser's latest message: {query}",
                "start_url": "current",
                "allow_submit": False,
                "max_steps": 25,
                "resuming": True,
            }

        planned = [
            (t, (s.get("tool_args") or [{}])[i] if i < len(s.get("tool_args") or []) else {})
            for s in deliberation.get("plan_steps", []) for i, t in enumerate(s.get("tools", []))
        ]
        for tool, args in planned:
            if tool == "browser_run_mission" and isinstance(args, dict):
                return {"goal": args.get("goal") or query, "start_url": args.get("start_url"),
                        "allow_submit": False, "max_steps": args.get("max_steps") or 25, "resuming": False}

        uses_browser = any(t.startswith("browser_") for t, _ in planned) or deliberation.get("task_focus") in ("browser", "job_action")
        if (self._WEB_ACTION_RE.search(query) and not self._QUESTION_RE.match(query)
                and (self._WEB_CONTEXT_RE.search(query) or uses_browser)):
            return {"goal": query, "start_url": None, "allow_submit": False, "max_steps": 25, "resuming": False}
        return None

    async def _stream_browser_mission(
        self, goal: str, start_url: Optional[str], allow_submit: bool, max_steps: int, resuming: bool
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.browser_sandbox import browser_sandbox_service as sandbox

        if not resuming or allow_submit:
            sandbox.pending_submit = None

        title = "Sending it" if allow_submit else ("Picking up where I left off" if resuming else "Doing it in the browser")
        yield {"type": "plan", "title": title, "steps": [
            {"id": 1, "title": "Work through the task step by step", "tools": ["browser_run_mission"], "status": "in_progress"},
            {"id": 2, "title": "Report what actually happened", "tools": [], "status": "pending"},
        ]}
        yield {"type": "plan_step", "step_id": 1, "status": "in_progress"}
        call_args = {"goal": goal, "start_url": start_url, "max_steps": max_steps}
        yield {"type": "tool_call", "name": "browser_run_mission", "args": call_args, "id": "mission_1"}

        task = asyncio.create_task(sandbox.run_browser_mission(
            goal=goal, start_url=start_url, max_steps=max_steps, allow_submit=allow_submit
        ))
        mission_id = None
        seen = 0
        shown_now = ""
        while not task.done():
            await asyncio.sleep(0.5)
            rec = sandbox.latest_mission or {}
            if mission_id is None and rec.get("goal") == goal:
                mission_id = rec.get("mission_id")
            if mission_id and rec.get("mission_id") == mission_id:
                # What it's doing right now (a long careful step otherwise looks like nothing is happening).
                now = rec.get("now") or ""
                if now and now != shown_now:
                    yield {"type": "status", "content": now[:200]}
                shown_now = now
                steps = rec.get("steps", [])
                for st in steps[seen:]:
                    icon = {"success": "✓", "failed": "✗", "waiting": "⏸"}.get(st.get("status"), "•")
                    line = f"{icon} Step {st['step_num']}: {st.get('reasoning') or st.get('action')}"
                    yield {"type": "status", "content": line[:220]}
                    yield {"type": "reasoning", "content": line + "\n"}
                seen = len(steps)

        try:
            result = task.result()
        except Exception as e:
            logger.error(f"Browser mission crashed: {e}")
            result = {"status": "failed", "final_answer": f"The browser ran into an error: {e}. Nothing was submitted.", "steps": []}

        yield {"type": "tool_result", "name": "browser_run_mission",
               "summary": f"{result.get('status', 'done')} after {len(result.get('steps', []))} steps",
               "data": {k: v for k, v in result.items() if k != "steps"}, "id": "mission_1"}
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "in_progress"}
        yield {"type": "content", "content": self._format_mission_report(result)}
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        yield {"type": "done"}

    def _format_mission_report(self, result: Dict[str, Any]) -> str:
        """Plain account of the mission, built only from what happened in the browser."""
        from app.services.browser_sandbox import load_user_profile, PROFILE_PATH
        status = result.get("status", "failed")
        heading = {
            "completed": "### ✅ Done",
            "needs_confirmation": "### ✋ Ready to send, waiting for your OK",
            "needs_input": "### 💬 I need something from you",
            "blocked": "### 🧱 Stopped at a verification check",
            "incomplete": "### ⏳ Not finished yet",
            "failed": "### ⚠️ That didn't work",
        }.get(status, "### Browser task")
        parts = [heading, (result.get("final_answer") or "").strip()]

        if status == "needs_input" and not load_user_profile():
            parts.append(
                f"_Tip: put your details (name, email, phone, links, resume path) in `{PROFILE_PATH}` "
                "so I can fill forms without asking each time._"
            )

        notes = result.get("notes") or []
        if notes:
            parts.append("<details><summary>What I found along the way</summary>\n\n" + "\n".join(f"- {n}" for n in notes) + "\n\n</details>")

        steps = [s for s in result.get("steps", []) if s.get("action") not in ("finish",)]
        if steps:
            lines = []
            for s in steps[-15:]:
                mark = {"success": "✓", "failed": "✗", "waiting": "⏸"}.get(s.get("status"), "•")
                lines.append(f"{s['step_num']}. {mark} {(s.get('reasoning') or s.get('action') or '').strip()[:160]}")
            more = f" (last 15 of {len(steps)})" if len(steps) > 15 else ""
            parts.append(f"<details><summary>What I did{more}</summary>\n\n" + "\n".join(lines) + "\n\n</details>")
        return "\n\n".join(p for p in parts if p)

    def _canonicalize_tool_name(self, name: str) -> str:
        """Normalizes model tool name predictions and aliases to exact registered tool functions."""
        name_clean = (name or "").strip().lower()
        alias_map = {
            "google_search": "web_search",
            "search": "web_search",
            "google": "web_search",
            "ddg_search": "web_search",
            "duckduckgo": "web_search",
            "search_web": "web_search",
            "tavily": "tavily_search",
            "scrape": "web_scrape",
            "scrape_url": "web_scrape",
            "bash": "computer_run_command",
            "shell": "computer_run_command",
            "terminal": "computer_run_command",
            "run_command": "computer_run_command",
            "exec_command": "computer_run_command",
            "read_file": "computer_read_file",
            "write_file": "computer_write_file",
            "edit_file": "computer_edit_file",
            "patch_file": "computer_edit_file",
            "find_files": "computer_find_files",
            "spotlight": "computer_find_files",
            "inspect_port": "computer_inspect_port",
            "port_check": "computer_inspect_port",
            "kill_process": "computer_kill_process",
            "hardware_specs": "computer_hardware_overview",
            "hardware_overview": "computer_hardware_overview",
            "battery": "computer_hardware_overview",
            "system_specs": "computer_inspect_system",
            "system_overview": "computer_inspect_system",
            "open_finder": "computer_reveal_finder",
            "reveal_finder": "computer_reveal_finder",
            "navigate": "browser_navigate",
            "open_browser": "browser_navigate",
            "open_url": "browser_navigate",
            "observe_page": "browser_observe",
            "observe": "browser_observe",
            "interact": "browser_interact",
            "fill_form": "browser_fill_form",
            "extract_data": "browser_extract_data",
            "browser_mission": "browser_run_mission",
            "run_mission": "browser_run_mission",
            "restart_browser": "browser_restart",
            "browser_reboot": "browser_restart",
            "unstuck_browser": "browser_unstuck",
            "unstuck": "browser_unstuck",
            "solve_captcha": "browser_handle_captcha",
            "handle_captcha": "browser_handle_captcha",
            "pass_captcha": "browser_handle_captcha",
            "captcha": "browser_handle_captcha",
            "turnstile": "browser_handle_captcha",
            "cloudflare": "browser_handle_captcha",
            "list_files": "fs_list_workspace_files",
            "create_folder": "fs_create_folder",
            "mkdir": "fs_create_folder",
            "move_file": "fs_move_file",
            "generate_image": "media_generate_image",
            "generate_video": "media_generate_video",
            "download_youtube": "media_download_youtube",
            "youtube_download": "media_download_youtube",
            "audit_tech": "audit_tech_stack",
            "tech_stack": "audit_tech_stack",
            "audit_funding": "audit_funding_and_org",
        }
        return alias_map.get(name_clean, name_clean)


    def _format_conversation_history_for_synthesis(
        self,
        messages: List[Dict[str, Any]],
        current_user_content: Any,
        system_instruction: str,
        max_history_turns: int = 8,
        max_turn_chars: int = 2500
    ) -> List[Dict[str, Any]]:
        """
        Constructs a robust, compliant multi-turn chat sequence for final synthesis:
        - Injects system prompt as the first message.
        - Preserves prior user/assistant conversation history so follow-up questions
          retain complete memory of previously discussed facts, tables, and entities.
        - Sanitizes turns to enforce strictly alternating roles (user -> assistant -> user).
        - Compacts lengthy prior assistant outputs to keep local models within context limits.
        - Appends the current turn as the final user message.
        """
        sanitized: List[Dict[str, Any]] = [{"role": "system", "content": system_instruction}]

        if not messages or len(messages) <= 1:
            sanitized.append({"role": "user", "content": current_user_content})
            return sanitized

        prior_history = messages[:-1]
        recent_history = prior_history[-max_history_turns:]

        accumulated_turns: List[Dict[str, str]] = []
        for turn in recent_history:
            role = turn.get("role", "user")
            if role not in ("user", "assistant"):
                continue
            content = turn.get("content", "")
            if isinstance(content, list):
                txts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(txts).strip()
            else:
                content = str(content or "").strip()

            if not content:
                continue

            if role == "assistant" and len(content) > max_turn_chars:
                content = content[:max_turn_chars] + "\n... [prior response summarized for context]"

            if accumulated_turns and accumulated_turns[-1]["role"] == role:
                accumulated_turns[-1]["content"] += f"\n\n{content}"
            else:
                accumulated_turns.append({"role": role, "content": content})

        if accumulated_turns and accumulated_turns[-1]["role"] == "user":
            extra_user = accumulated_turns.pop()
            if isinstance(current_user_content, str):
                current_user_content = f"{extra_user['content']}\n\n{current_user_content}"
            elif isinstance(current_user_content, list):
                current_user_content.insert(0, {"type": "text", "text": extra_user['content']})

        sanitized.extend(accumulated_turns)
        sanitized.append({"role": "user", "content": current_user_content})
        return sanitized

    async def deliberate_and_plan(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        active_workspace_path: Optional[str] = None,
        has_image: bool = False,
        has_document: bool = False,
        attached_document: Optional[Dict[str, Any]] = None,
        attached_image_urls: Optional[List[str]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Pure LLM-driven first-principles deliberation and planning streamed live.
        Replaces hardcoded regex pattern matching with native LLM reasoning.
        Evaluates the user's objective, context, and the tool ecosystem to decide:
        - 'direct_answer': for greetings, explanations, follow-ups, chitchat.
        - 'execute_plan': formulated milestones, tool names, and arguments.
        Streams reasoning thoughts live to UI and yields 'deliberation_complete'.
        """
        from app.services.llm import llm_service

        doc_info = f"Attached Document: {attached_document.get('name', 'document')} ({attached_document.get('size_formatted', '')})" if (has_document and attached_document) else "None"
        workspace_info = f"Controlled Local Workspace Folder: {active_workspace_path}" if active_workspace_path else "No workspace folder focused"

        deliberator_system_prompt = f"""You are Xod, an autonomous AI operating on macOS.
Deliberate on the user objective from first principles and decide whether to answer directly or execute an action plan.

TOOL ECOSYSTEM:
1. Host Computer:
   - computer_hardware_overview: Apple Silicon/Intel chip, CPU cores, memory, graphics card (GPU) and its memory, macOS version, battery percentage.
   - computer_inspect_system: What's running now: the programs using the most CPU and memory, RAM in use, CPU load, free disk space, local IP address.
   - computer_inspect_port: Check process occupying a port. Args: {{"port": 8000}}
   - computer_kill_process: Terminate process by PID. Args: {{"pid": 1234}}
   - computer_run_command: Run terminal shell command in background. Args: {{"command": "..."}}
   - computer_find_files: Spotlight search for files on macOS. Args: {{"query": "pattern"}}
   - computer_directory_tree: Folder hierarchy tree. Args: {{"path": "...", "max_depth": 2}}
   - computer_list_directory: What's in a folder: files and subfolders with sizes and dates. Args: {{"path": "..."}}
   - computer_read_file: Read file lines safely. Args: {{"filepath": "..."}}
   - computer_write_file: Create a new file (or overwrite one) anywhere, e.g. a note on the Desktop. Args: {{"filepath": "...", "content": "..."}}
   - computer_edit_file: Surgically edit file lines. Args: {{"filepath": "...", "target_content": "...", "replacement_content": "..."}}
   - computer_manage_file: Rename or move ("move" with the new path), copy, or delete a file or folder ("trash": to the Trash, recoverable), or make a folder ("mkdir"). Args: {{"action": "move|copy|trash|mkdir", "src": "...", "dst": "..."}}
   - computer_batch_organize: Organize messy files into categorized subfolders. Args: {{"source_dir": "..."}}
   - computer_reveal_finder: Open folder or highlight file in macOS Finder. Args: {{"path": "..."}}
   - computer_open_item: Launch macOS app or file. Args: {{"target": "..."}}
   - computer_run_applescript: Execute AppleScript automation. Args: {{"script": "..."}}
   - computer_clipboard: Read or write system clipboard. Args: {{"action": "read"}}
   - computer_list_jobs: Audit active background daemons.
   - computer_start_job: Start something long (build, install, server) in the background; returns a job_id at once. Args: {{"name": "...", "command": "..."}}
   - computer_wait_for: Wait until it's done/ready instead of guessing. Args: {{"condition": "job_done|job_output|file_exists|port_open|url_ok", "job_id": "...", "text": "...", "port": 3000, "timeout_sec": 300}}
   - computer_check_job: New output and status of a background job. Args: {{"job_id": "..."}}
   - xod_self_edit: Change Xod's OWN code (you CAN do this; it's gated by model strength, tested in a copy, and revertible). Use when a Xod tool itself is buggy or the user asks you to change yourself. Args: {{"goal": "what to change and why, with the exact error"}}
   - xod_doctor: Check and fix Xod's own health (chat model, browser, memory, hung jobs). Args: {{"fix": true}}
   - memory_search: Search your memory of past chats, finished tasks and what you know about the user. Understands time ("last week", "in September"). Args: {{"query": "...", "when": "optional, e.g. yesterday"}}
   - xod_tasks: Your own background tasks and routines: list them, or the full details and result of one. Args: {{"action": "list|get", "task": "title words or id"}}

2. Controlled Workspace ({workspace_info}):
   - fs_list_workspace_files: Explore workspace directory tree.
   - fs_read_workspace_file: Read source code or config in workspace. Args: {{"filepath": "..."}}
   - fs_write_workspace_file: Write code or doc to workspace. Args: {{"filepath": "...", "content": "..."}}
   - fs_search_workspace: Search code/symbols across workspace. Args: {{"query": "..."}}
   - fs_create_folder: Create directory in workspace. Args: {{"folder_path": "..."}}
   - fs_move_file: Move or rename file in workspace. Args: {{"source_path": "...", "destination_path": "..."}}

3. Live Web & Market Intelligence:
   - web_search: Live web search across Google/Bing/DuckDuckGo. Args: {{"query": "..."}}
   - tavily_search: Deep AI-optimized web search. Args: {{"query": "..."}}
   - web_scrape: Fetch & extract target webpage markdown. Args: {{"url": "https://..."}}
   - audit_tech_stack: Audits software frameworks & cloud architecture. Args: {{"target": "..."}}
   - audit_funding_and_org: VC funding, SEC filings, founders. Args: {{"target": "..."}}
   - trustmrr_get_startup: Verified SaaS metrics, ARR, MRR, multiples. Args: {{"slug": "..."}}
   - trustmrr_screen_deals: Screen live vetted acquisition deals. Args: {{"category": "..."}}

4. Autonomous Browser Sandbox:
   - browser_navigate: Navigate to a webpage. Args: {{"url": "https://..."}}
   - browser_observe: Inspect visible interactive elements using Set-of-Marks.
   - browser_interact: Click or type on element. Args: {{"index": 1, "action": "click"}}
   - browser_fill_form: Populate form fields. Args: {{"fields": {{"#selector": "val"}}}}
   - browser_extract_data: Extract structured data per instructions. Args: {{"instruction": "..."}}
   - browser_extract_markdown: Extract full text/markdown of current page.
   - browser_run_mission: Autonomous closed-loop mission on a goal. Args: {{"goal": "...", "start_url": "..."}}
   - browser_handle_captcha: Autonomously detect and solve Cloudflare Turnstile, 'Just a moment...', reCAPTCHA.
   - browser_unstuck: Autonomous self-healing to unfreeze hung pages, abort spinners, close dialogs & backdrops.
   - browser_restart: Force-restart browser, clear dead singleton locks, or purge profile if stuck. Args: {{"clear_profile": false}}

5. Local Media Studio:
   - media_generate_image: Generate image asset. Args: {{"prompt": "..."}}
   - media_generate_video: Generate AI motion video. Args: {{"prompt": "..."}}
   - media_download_youtube: Download YouTube audio or video. Args: {{"url_or_query": "..."}}

CONTEXT:
- Workspace: {workspace_info}
- Image Attached: {has_image}
- Document Attached: {doc_info}

DECISION RULES:
- 'direct_answer': Greetings ('hi', 'hello'), conceptual questions, chitchat, or follow-ups answerable from context without tools.
- 'execute_plan': External actions, web research, browser automation, terminal commands, file reading/editing, system telemetry, media synthesis.

Keep reasoning brief (1-2 sentences). Emit ONLY a JSON object:
```json
{{
  "thought": "1-2 sentences explaining reasoning and why tools are or are not needed",
  "decision": "direct_answer" | "execute_plan",
  "direct_response": "Conversational Markdown answer if direct_answer, else empty string",
  "plan_title": "Concise human-facing title",
  "plan_steps": [
    {{
      "id": 1,
      "title": "Human-readable milestone description",
      "tools": ["tool_name"],
      "tool_args": [{{"arg_name": "val"}}]
    }}
  ],
  "task_focus": "research" | "computer" | "workspace" | "browser" | "media" | "swarm" | "general"
}}
```"""
        # Keep the tool list honest: drop add-on tools that are switched off, mention the report
        # tool and any connected MCP tools.
        try:
            from app.services import packs
            # Something to do with the person's own files or command line, and nothing about the web: leave the web
            # and browser tools out of the list, so a small model can't plan a web search for "create a file".
            local_only = bool(self._LOCAL_ACTION_RE.search(query or "")) and not self._WEB_WORDS_RE.search(query or "") \
                and not active_workspace_path
            web_tool = re.compile(r"(web_|tavily_|you_search|browser_|audit_|trustmrr_)")
            deliberator_system_prompt = "\n".join(
                line for line in deliberator_system_prompt.split("\n")
                if not (local_only and re.match(r"(3\. Live Web|4\. Autonomous Browser)", line))
                and (not (m := re.match(r"\s+- ([a-z_]+):", line))
                     or (packs.tool_allowed(m.group(1)) and not (local_only and web_tool.match(m.group(1))))))
            extra = []
            # Only offer the report tool when a report is wanted, so plain questions get plain answers.
            if re.search(r"\b(report|pdf|share|send this|write[- ]?up)\b", query or "", re.I):
                extra.append("   - xod_make_report: Save a finished answer as a shareable page + PDF with sources. "
                             "Args: {\"title\": \"...\", \"markdown\": \"...\"}")
            from app.agent.tools import _wants_cli_agent
            if _wants_cli_agent(query) and packs.tool_allowed("agent_job"):
                extra += [
                    "5. Coding agents (Claude Code, Muse, Codex … in this computer's terminal; never run them with computer_run_command):",
                    "   - agent_job: Real work by Claude Code or Muse in a project (fix, build, change, refactor, write tests): a Crew job "
                    "Xod follows; what it asks waits for the person. Args: {\"action\": \"start\", \"agent\": \"claude|muse\", \"task\": \"...\", "
                    "\"folder\": \"...\"}; {\"action\": \"status\", \"job\": \"optional name\"}; {\"action\": \"message\", \"job\": \"claude-job-1\", "
                    "\"text\": \"...\"}; {\"action\": \"interrupt|stop\", \"job\": \"...\"}. Two agents at once: one start per job.",
                    "     When the person says to have Claude Code or Muse do something, start the job in this same step, in their "
                    "words. Work that isn't in one project (their GitHub, their PRs across repos, this computer) starts without a "
                    "folder: the agent works from the home folder with Xod's GitHub connection, and says itself what it can't do. "
                    "Don't search for folders or ask which repo first. After start, say it's started and where to watch it (Tools → "
                    "Crew); never call it done until status says done.",
                    "   - agent_ask: A quick question to an agent, answer only, nothing changed (explain, review). Args: {\"agent\": \"claude\", "
                    "\"message\": \"...\", \"folder\": \"optional\"}",
                    "   - agent_terminal: A live terminal session to watch or talk to, or any long-running program (npm run dev). Args: "
                    "{\"action\": \"start\", \"agent\": \"claude\" or \"command\": \"npm run dev\", \"folder\": \"...\", \"visible\": true}",
                    "   - agent_setup: Which agents are installed and signed in, and installing, signing in, updating or checking one. "
                    "Args: {\"action\": \"list\"}; {\"action\": \"next|install|login|update|doctor\", \"agent\": \"codex\"}"]
            from app.services.mcp_client import mcp_hub
            # The connected apps' tools that fit this request (GitHub alone has 46: the first twelve were often the wrong ones)
            mcp_tools = [d["function"] for d in mcp_hub.tools_for_query(query, limit=12)]
            if mcp_tools:
                extra.append("6. Connected apps (MCP; actions wait for the user's approval):")
                extra += [f"   - {t['name']}: {t['description'][:110]}" for t in mcp_tools]
            deliberator_system_prompt = deliberator_system_prompt.replace("\nCONTEXT:", "\n" + "\n".join(extra) + "\n\nCONTEXT:", 1)
        except Exception as e:
            logger.debug(f"planner tool list not adjusted: {e}")

        delib_history = []
        if conversation_history:
            for m in conversation_history[-4:]:
                r = m.get("role")
                c = m.get("content")
                if r in ("user", "assistant") and isinstance(c, str) and c.strip():
                    delib_history.append({"role": r, "content": c[:400]})

        if getattr(self, "_memory_context", ""):
            deliberator_system_prompt += ("\n\n" + self._memory_context +
                                          "\nIf this answers the question, it's a direct answer: don't search again.")
        if getattr(self, "_lessons", ""):
            deliberator_system_prompt += "\n\n" + self._lessons

        try:  # what Xod knows about the person (services/user_memory.py): answer from it instead of looking things up
            from app.services import user_memory
            about = user_memory.core_note()
            if about:
                deliberator_system_prompt += ("\n\n" + about + user_memory.relevant_note(query or "") +
                                              "\nQuestions these facts answer (\"what's my name?\") are direct answers.")
        except Exception:
            pass

        try:  # lessons from earlier repairs on this Mac (see doctor.planner_notes)
            from app.services.doctor import doctor
            notes = doctor.planner_notes()
            if notes:
                deliberator_system_prompt += "\n\nLEARNED ON THIS MAC (from fixing earlier failures):\n" + "\n".join(f"- {n}" for n in notes)
        except Exception:
            pass

        from app import platform_info
        if platform_info.WINDOWS:
            deliberator_system_prompt = platform_info.localize(deliberator_system_prompt) + "\n\n" + platform_info.SHELL_NOTE
        delib_messages = [
            {"role": "system", "content": deliberator_system_prompt},
            *delib_history,
            {"role": "user", "content": f"USER OBJECTIVE: {query}"}
        ]

        raw_accumulated = ""
        emitted_any_reasoning = False
        model_error = ""

        try:
            # Fast first: most plans don't need a thinking phase (it was ~half the planning time).
            # If the fast plan isn't usable, plan again with thinking on.
            modes = (None,) if self._DEPTH_RE.search(query or "") else (False, None)
            parsed = None
            for think_mode in modes:
                raw_accumulated = ""
                async for chunk in llm_service.stream_chat_completion(
                    messages=delib_messages,
                    temperature=0.1,
                    max_tokens=JSON_MIN_TOKENS,
                    task="planner",
                    think=think_mode,
                ):
                    chunk_type = chunk.get("type")
                    if chunk_type == "error":
                        model_error = str(chunk.get("content") or "")[:300]    # said below if no plan comes of it
                        continue
                    if chunk_type == "example_id":
                        self._planner_example = {"id": chunk["id"], "response": raw_accumulated}
                        continue
                    if chunk_type == "reasoning":
                        content = chunk.get("content", "")
                        if content:
                            emitted_any_reasoning = True
                            yield {"type": "reasoning", "content": content}
                    elif chunk_type == "content":
                        tok = chunk.get("content", "")
                        had_brace = "{" in raw_accumulated
                        raw_accumulated += tok
                        # Some models write prose before the JSON. Show it as a thought once the JSON
                        # starts, unless it's only a code fence like ```json.
                        if not had_brace and "{" in raw_accumulated:
                            prose = re.sub(r"```\w*", "", raw_accumulated.split("{", 1)[0]).strip()
                            if len(prose) >= 20:
                                yield {"type": "reasoning", "content": prose + "\n"}
                                emitted_any_reasoning = True
                parsed = parse_llm_json(raw_accumulated)
                if parsed and (parsed.get("plan_steps") or parsed.get("steps") or parsed.get("direct_response")
                               or parsed.get("decision") in ("direct_response", "respond", "answer")):
                    break
                if think_mode is False:
                    yield {"type": "status", "content": "Thinking it through more carefully…"}
            if not parsed:
                raise ValueError(f"Planner returned no usable JSON: {raw_accumulated[:200]!r}")

            thought = parsed.get("thought") or ""
            if not emitted_any_reasoning and thought:
                yield {"type": "reasoning", "content": f"{thought}\n"}

            decision = parsed.get("decision", "execute_plan")
            direct_resp = parsed.get("direct_response", "")
            title = parsed.get("plan_title") or parsed.get("title") or f"Execution Plan: {query[:40]}"
            raw_steps = parsed.get("plan_steps") or parsed.get("steps") or []
            task_focus = parsed.get("task_focus") or parsed.get("task_category") or "general"
            is_swarm = bool(parsed.get("is_swarm"))

            clean_steps = []
            for idx, s in enumerate(raw_steps, 1):
                tools_val = s.get("tools", [])
                if isinstance(tools_val, str):
                    tools_val = [tools_val]
                tools = [self._canonicalize_tool_name(t) for t in tools_val if t]

                args_val = s.get("tool_args", [{}])
                if isinstance(args_val, dict):
                    args_val = [args_val]
                elif not isinstance(args_val, list):
                    args_val = [{}]
                args_val = self._normalize_tool_args(args_val, len(tools))

                clean_steps.append({
                    "id": idx,
                    "title": s.get("title", f"Milestone {idx}"),
                    "tools": tools,
                    "tool_args": args_val,
                    "is_swarm": s.get("is_swarm", is_swarm),
                    "is_visual": s.get("is_visual", has_image and idx == 1),
                    "is_document": s.get("is_document", has_document and idx == 1),
                    "status": "pending"
                })

            # A cut-off plan can end with a half-written step that has no tools.
            if any(s["tools"] for s in clean_steps):
                clean_steps = [s for s in clean_steps if s["tools"]]
                for idx, s in enumerate(clean_steps, 1):
                    s["id"] = idx

            yield {
                "type": "deliberation_complete",
                "deliberation": {
                    "thought": thought,
                    "decision": decision,
                    "direct_response": direct_resp,
                    "plan_title": title,
                    "plan_steps": clean_steps,
                    "task_focus": task_focus,
                    "is_swarm": is_swarm
                }
            }

        except Exception as e:
            logger.error(f"Deliberation error: {e}" + (f" (the model said: {model_error})" if model_error else ""))
            if model_error:
                # The model itself failed (not running, not installed...): a web search would still need it
                # to write the answer, so say what's wrong now instead of failing again a minute later.
                yield {"type": "deliberation_complete", "deliberation": {
                    "thought": "", "decision": "direct_answer", "plan_title": "", "plan_steps": [], "task_focus": "general",
                    "is_swarm": False, "direct_response": model_error}}
                return
            if self._LOCAL_ACTION_RE.search(query or ""):
                # Something to do on this computer: searching the web for it would only describe how to do it.
                why = f" The model replied with an error: {model_error}" if model_error else " The model didn't give me a usable plan."
                yield {"type": "deliberation_complete", "deliberation": {
                    "thought": "", "decision": "direct_answer", "plan_title": "", "plan_steps": [], "task_focus": "general",
                    "is_swarm": False,
                    "direct_response": (f"I couldn't work out the steps for that just now, so I haven't done anything.{why} "
                                        "Please ask again; if it keeps happening, a bigger model (Settings) usually plans these better.")}}
                return
            yield {
                "type": "deliberation_complete",
                "deliberation": {
                    "thought": "Directly evaluating user request and deploying intelligence research.",
                    "decision": "execute_plan",
                    "direct_response": "",
                    "plan_title": f"Investigation: {query[:40]}",
                    "plan_steps": [
                        {
                            "id": 1,
                            "title": f"Execute Deep Web & Intelligence Search on '{query[:40]}'",
                            "tools": ["web_search"],
                            "tool_args": [{"query": query}],
                            "status": "pending"
                        }
                    ],
                    "task_focus": "research",
                    "is_swarm": False
                }
            }


    def _format_summary(self, result: Dict[str, Any]) -> str:
        if not isinstance(result, dict):
            return "Completed"
        if result.get("error"):          # a job's record carries "error": None when nothing went wrong
            return f"Error: {result.get('message') or result.get('error')}"
        if result.get("agent") in ("claude", "muse") and "worktree" in result and "status" in result:
            who = "Claude Code" if result["agent"] == "claude" else "Muse"
            return f"{who} is on it ({result.get('name')})" if result["status"] in ("starting", "working", "waiting") \
                else f"{who}: {result.get('name')} is {result['status']}"
        if "occurrences_replaced" in result:
            return f"Surgically Updated {result.get('name')}: line {result.get('line_number')} ({result.get('occurrences_replaced')} chunk replaced, delta {result.get('lines_delta', 0):+d} lines)"
        elif "tree" in result and "stats" in result and isinstance(result.get("stats"), dict):
            st = result.get("stats", {})
            return f"Directory Tree {result.get('name')}: {st.get('total_dirs', 0)} dirs, {st.get('total_files', 0)} files ({st.get('total_size_formatted', '')})"
        elif "total_files_moved" in result:
            return f"Batch Organized: {result.get('total_files_moved', 0)} files into {len(result.get('breakdown', {}))} categories"
        elif "chip" in result and "macos_version" in result:
            return f"Hardware: {result.get('chip')}, {result.get('cpu_cores')} cores, {result.get('macos_version')}"
        elif "sliced_range" in result:
            return f"Read File {result.get('name')}: lines {result.get('sliced_range')} ({result.get('lines_returned')} lines)"
        elif "script" in result and "output" in result:
            return f"AppleScript executed: exit {result.get('exit_code', 0)}"
        elif "total_files" in result:
            return f"Workspace Files: Listed {result.get('total_files', 0)} files in {result.get('subpath') or 'root'}"
        elif ("lines" in result or "line_count" in result) and ("path" in result or "name" in result):
            fname = result.get("name") or result.get("path")
            lines_cnt = result.get("lines") or result.get("line_count", 0)
            return f"Read Workspace File {fname}: {lines_cnt} lines ({result.get('size_formatted') or result.get('size_bytes', 0)})"
        elif "total_matches" in result and "query" in result:
            return f"Workspace Search '{result.get('query')}': {result.get('total_matches')} match(es)"
        elif "port" in result:
            p_status = "In Use" if result.get("in_use") else "Available"
            return f"Port {result.get('port')}: {p_status} ({len(result.get('processes', []))} process(es))"
        elif "ports" in result:
            return f"Discovered {len(result.get('ports', []))} active listening ports"
        elif "jobs" in result:
            return f"Background Jobs: {len(result.get('jobs', []))} active"
        elif "chars" in result:
            return f"System Clipboard: {result.get('chars', 0)} characters ingested"
        elif "total_candidates" in result:
            return f"Indexed {result.get('total_candidates', 0)} interactive elements (Set-of-Marks)"
        elif "extracted_data" in result:
            count = len(result.get("extracted_data", [])) if isinstance(result.get("extracted_data"), list) else 1
            return f"Extracted {count} structured entities from browser viewport"
        elif "fields_filled" in result:
            return f"Filled {result.get('fields_filled', 0)} form input(s) via CDP"
        elif "requisitions" in result or ("jobs" in result and "company" in result):
            count = len(result.get("requisitions", result.get("jobs", [])))
            return f"Scanned ATS Firehose: {count} verified live job requisition(s)"
        elif "filename" in result and "pdf_path" in result:
            return f"Exported page to PDF: {result.get('filename')}"
        elif "tree" in result:
            return f"Extracted Accessibility Tree ({len(result.get('tree', []))} nodes)"
        elif "stdout" in result and "exit_code" in result:
            return f"Ran command (exit {result.get('exit_code')}, {result.get('duration_sec', 0)}s)"
        elif "cpu_load" in result and "memory" in result:
            return f"Telemetry: {result.get('os')} ({result.get('arch')}), RAM {result.get('memory', {}).get('used_gb')}/{result.get('memory', {}).get('total_gb')}GB"
        elif "media_type" in result and "file_path" in result:
            m_type = result.get('media_type', 'media').capitalize()
            return f"Downloaded {m_type}: '{result.get('title')}' ({result.get('file_size_mb', 0)} MB) -> {result.get('file_name')}"
        elif result.get("action") == "generate_ai_image" or ("filename" in result and "relative_url" in result):
            return f"Synthesized Visual Asset: {result.get('filename')} ({result.get('width', 1024)}x{result.get('height', 1024)}) -> {result.get('relative_url')}"
        elif result.get("action") == "splice_videos":
            return f"Spliced {result.get('input_clips_count', 0)} Video Clips -> {result.get('filename')} ({result.get('duration_sec', 0)}s)"
        elif result.get("action") == "extract_frame":
            return f"Extracted {result.get('frame_type')} Frame -> {result.get('filename')}"
        elif result.get("action") in ("generate_ai_video", "create_video_from_frame"):
            return f"Synthesized Motion Video ({result.get('motion')}) -> {result.get('filename')} ({result.get('duration_sec', 0)}s)"
        elif result.get("action") in ("open_folder", "reveal_file") or ("revealed_path" in result or "opened_folder" in result):
            tgt = result.get("target") or result.get("revealed_path") or result.get("opened_folder") or "item"
            return f"Opened in macOS Finder: {Path(tgt).name} ({tgt})"
        elif "technologies" in result:
            return f"Audited {result.get('domain', 'target')}: {result.get('total_technologies_detected', 0)} tech badges detected"
        elif "results" in result and result.get("provider") == "browser":
            eng = {"google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo"}.get(result.get("engine") or "", "the web")
            read = result.get("pages_read") or sum(1 for r in result.get("results", []) if isinstance(r, dict) and r.get("content"))
            return f"Searched {eng} in Xod's browser: {len(result.get('results', []))} results" + (f", read {read} page{'s' * (read != 1)}" if read else "")
        elif "results" in result:
            prov = "Tavily" if result.get("provider") == "tavily" else ("You.com" if result.get("provider") == "you.com" else "web")
            ans_note = f" (Answer: '{result.get('answer')[:45]}...')" if result.get("answer") else ""
            return f"Found {len(result.get('results', []))} {prov} results{ans_note}"
        elif "count" in result and "query" in result and "root" in result:
            return f"Spotlight Search '{result.get('query')}': Discovered {result.get('count', 0)} file(s)"
        elif "diagnoses" in result and "remediations" in result:
            return f"System AI Review: {result.get('status', 'OK')} ({len(result.get('diagnoses', []))} diagnosis, {len(result.get('remediations', []))} remediations)"
        elif "pid" in result and ("killed" in result or "success" in result):
            return f"Process PID {result.get('pid')}: Terminated successfully"
        elif "content" in result and "filepath" in result:
            return f"Read {result.get('name') or 'the file'} ({result.get('lines_count', 0)} lines)"
        elif "content" in result:
            return f"Extracted webpage content ({result.get('length') or len(str(result.get('content') or ''))} chars)"
        elif "url" in result and "title" in result:
            return f"Navigated to {result.get('title') or result.get('url')}"
        elif "shortlisted_candidates" in result or "total_discovered" in result:
            blended_cpm = result.get("blended_predicted_cpm", 0.0)
            return f"Discovered & Scored {len(result.get('shortlisted_candidates', []))} creators (Blended Predicted CPM: ${blended_cpm:.2f})"
        elif "predicted_cpm" in result and "handle" in result:
            return f"Modelled {result.get('handle')}: Predicted CPM ${result.get('predicted_cpm', 0.0):.2f} ({result.get('arbitrage_multiple', 1.0):.2f}x arbitrage)"
        return "Done"

    def _clean_tool_payload(self, tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Creates a high-density, context-efficient summary of tool results
        tailored for Gemma 4's 8k context window to ensure lightning-fast thinking.
        """
        if not isinstance(result, dict):
            return {"result": str(result)[:600]}

        cleaned = {}
        for k, v in result.items():
            if k in ("raw_html", "html", "dom", "raw_response", "body", "headers"):
                continue
            if isinstance(v, list) and len(v) > 15:
                cleaned[k] = v[:15]
                cleaned[f"{k}_total_count"] = len(v)
            elif isinstance(v, str) and k == "content" and tool_name.startswith("mcp__") and len(v) > 800:
                # A connected app's answer is a list (places, messages, events…): without its padding, and enough of it
                # that the answer has more than the first two items.
                v = re.sub(r"\n\s*", " ", v)
                cleaned[k] = v[:3000] + ("... [truncated for brevity]" if len(v) > 3000 else "")
            elif isinstance(v, str) and len(v) > 800:
                cleaned[k] = v[:800] + "... [truncated for brevity]"
            else:
                cleaned[k] = v
        return cleaned


AgenticResearcher.ROUTER_PROMPT_SPOKEN = AgenticResearcher.ROUTER_PROMPT.replace(
    "3. Otherwise answer it yourself right away: directly and briefly (a few sentences), in Markdown.",
    "3. Otherwise answer it yourself right away, out loud: one to three short, natural spoken sentences, "
    "with no lists, headings or markdown.")
assert AgenticResearcher.ROUTER_PROMPT_SPOKEN != AgenticResearcher.ROUTER_PROMPT
# On Windows: "the user's PC", File Explorer, PowerShell (app/platform_info.py). Unchanged on a Mac.
from app import platform_info as _plat  # noqa: E402
AgenticResearcher.ROUTER_PROMPT = _plat.localize(AgenticResearcher.ROUTER_PROMPT)
AgenticResearcher.ROUTER_PROMPT_SPOKEN = _plat.localize(AgenticResearcher.ROUTER_PROMPT_SPOKEN)
