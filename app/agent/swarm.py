"""
Xod JIT Sub-Agent Swarm & Model Specialization Runtime
======================================================
Inspired by Amjad Masad's (Replit CEO) JIT Model Specialization:
Instead of a single monolithic agent with an overwhelming action space and bloated context,
Xod (the conductor) dynamically spawns and coordinates specialized micro-agents
with strictly pruned action spaces, concise instructions, and targeted model tiering:
- Local Models (LM Studio / Ollama): Fast, private, zero token cost for host/file/telemetry operations.
- Cloud Models (OpenAI / Gemini / Groq / OpenRouter / Anthropic): High-reasoning cloud swarms when API keys exist.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncGenerator

from app.config import settings
from app.services.llm import llm_service
from app.agent.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger("swarm")

@dataclass
class SubAgentSpec:
    id: str
    name: str
    role: str
    objective: str
    tools: List[str]  # Strictly pruned action space (names of allowed tools)
    tier: str = "auto"  # "local", "cloud", "auto"
    model_override: Optional[str] = None
    icon: str = "bot"
    max_hops: int = 2

@dataclass
class SubAgentResult:
    agent_id: str
    name: str
    role: str
    tier_requested: str
    tier_resolved: str
    model_used: str
    status: str  # "completed", "partial", "error"
    findings: str
    tool_calls_count: int
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    raw_evidence: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: Optional[str] = None


# -------------------------------------------------------------
# BUILT-IN SPECIALIST ARCHETYPES (Pruned Action Spaces)
# -------------------------------------------------------------
SPECIALIST_ARCHETYPES = {
    "code_auditor": {
        "name": "Codebase Auditor",
        "role": "Local Project & Code Structure Specialist",
        "icon": "folder-code",
        "tools": [
            "fs_list_workspace_files",
            "fs_read_workspace_file",
            "fs_search_workspace"
        ],
        "default_tier": "local",
        "description": "Reads source code, directory hierarchies, dependencies, configs, and patterns."
    },
    "system_supervisor": {
        "name": "System Supervisor",
        "role": "Host Telemetry & Process Supervisor",
        "icon": "cpu",
        "tools": [
            "computer_get_ports",
            "computer_inspect_port",
            "computer_inspect_system",
            "computer_review_system",
            "computer_kill_process",
            "computer_list_jobs",
            "computer_find_files"
        ],
        "default_tier": "local",
        "description": "Monitors open ports, background daemons, CPU/RAM telemetry, runaway processes, and machine health."
    },
    "terminal_operator": {
        "name": "Terminal Operator",
        "role": "Shell Command & File Systems Operator",
        "icon": "terminal",
        "tools": [
            "computer_run_command",
            "computer_execute_action",
            "computer_kill_process",
            "fs_write_workspace_file",
            "computer_manage_file",
            "computer_clipboard"
        ],
        "default_tier": "local",
        "description": "Executes non-intrusive background commands, patches files, terminates processes, and bridges clipboard."
    },
    "browser_scout": {
        "name": "Browser Scout",
        "role": "Autonomous Web DOM & Visual Scout",
        "icon": "globe",
        "tools": [
            "browser_observe",
            "browser_interact",
            "browser_extract_data",
            "browser_fill_form",
            "browser_snoop_network",
            "browser_manage_cookies",
            "browser_manage_tabs",
            "browser_wait_for",
            "browser_export_pdf",
            "browser_upload_file",
            "browser_get_downloads",
            "browser_extract_ax_tree",
            "browser_navigate",
            "browser_extract_content",
            "browser_extract_markdown",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_run_mission"
        ],
        "default_tier": "auto",
        "description": "Navigates websites live via isolated Chrome CDP, performs Set-of-Marks visual DOM indexing, interacts deterministically by element indices, extracts structured data records, fills forms, snoops internal network JSON APIs, and captures screenshots."
    },
    "market_intel": {
        "name": "Market Intelligence Specialist",
        "role": "SaaS Financials & M&A Valuation Auditor",
        "icon": "trending-up",
        "tools": [
            "trustmrr_financials",
            "calculate_metrics",
            "sec_edgar",
            "funding_intel"
        ],
        "default_tier": "auto",
        "description": "Extracts verified MRR, ARR multiples, SEC filings, and venture funding rounds."
    },
    "network_recon": {
        "name": "Network Recon Specialist",
        "role": "DNS, Shadow Subdomain & Edge Traffic Auditor",
        "icon": "network",
        "tools": [
            "dns_lookup",
            "subdomain_recon",
            "traffic_intel",
            "surface_recon"
        ],
        "default_tier": "auto",
        "description": "Discovers shadow infrastructure, DNS records, Tranco traffic, and CDN latency."
    },
    "web_researcher": {
        "name": "Web Intelligence Researcher",
        "role": "Open Web Search & Evidence Scraper",
        "icon": "search",
        "tools": [
            "web_search",
            "tavily_search",
            "scrape_page",
            "omni_search"
        ],
        "default_tier": "auto",
        "description": "Searches public news, documentation, reviews, and community sentiment."
    }
}


class SubAgent:
    """
    Lightweight, isolated micro-agent runtime.
    Features:
    - Pruned action space: only receives the 2-4 tools allowed in spec.
    - Minimal prompt overhead: ~200 words vs monolithic 5,000+ words.
    - Tier-targeted execution: routes to local fast model or cloud reasoning model.
    """

    def __init__(self, spec: SubAgentSpec, active_workspace_path: Optional[str] = None):
        self.spec = spec
        self.active_workspace_path = active_workspace_path
        self.endpoint_info = llm_service.resolve_endpoint_for_tier(
            tier=spec.tier,
            model_override=spec.model_override
        )

        # Filter TOOL_DEFINITIONS strictly to allowed action space
        self.allowed_tool_defs = [
            t for t in TOOL_DEFINITIONS
            if t.get("function", {}).get("name") in self.spec.tools
        ]

    def _build_system_prompt(self, host_context: Optional[str] = None) -> str:
        tools_str = ", ".join(self.spec.tools)
        prompt = f"""You are {self.spec.name}, a specialized JIT sub-agent deployed by Xod (the conductor).
Role: {self.spec.role}
Mission Objective: {self.spec.objective}

ACTION SPACE (STRICTLY PRUNED):
You have access ONLY to the following tools: [{tools_str}].
You do NOT have access to any other tools. Do not attempt to call tools outside this list.

BEHAVIORAL DIRECTIVES:
1. Immediately invoke the necessary tool(s) to obtain ground-truth empirical facts for your objective.
2. If tool execution provides data, evaluate it critically.
3. Once all facts are gathered, provide a concise, structured factual summary of your findings (under 300 words).
4. Be rigorous, factual, and quantitative. Do not extrapolate or guess."""

        if self.active_workspace_path:
            prompt += f"\n\nActive Controlled Workspace: {self.active_workspace_path}"
        if host_context:
            prompt += f"\nHost Telemetry Context: {host_context}"

        return prompt

    async def execute(
        self,
        event_queue: asyncio.Queue,
        host_context: Optional[str] = None
    ) -> SubAgentResult:
        start_time = time.time()
        agent_id = self.spec.id
        tier_resolved = self.endpoint_info.get("tier_resolved", "local")
        model_used = self.endpoint_info.get("model", settings.llm_model)

        await event_queue.put({
            "type": "subagent_status",
            "agent_id": agent_id,
            "status": "running",
            "message": f"Starting mission on {self.endpoint_info.get('tier_display', 'Local')}",
            "tier": tier_resolved,
            "model": model_used
        })

        system_prompt = self._build_system_prompt(host_context)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self.spec.objective}
        ]

        tool_history = []
        raw_evidence = {}
        tool_calls_count = 0
        hop = 0
        final_findings = ""

        try:
            while hop < self.spec.max_hops:
                hop += 1
                deliberation_calls = []
                deliberation_content = ""

                # Deliberate using assigned tier/endpoint
                async for ev in llm_service.deliberate_stream(
                    messages=messages,
                    tools=self.allowed_tool_defs,
                    tool_choice="auto",
                    temperature=0.2,
                    endpoint_override=self.endpoint_info
                ):
                    if ev.get("type") == "deliberation_complete":
                        deliberation_calls = ev.get("tool_calls", [])
                        deliberation_content = ev.get("content", "")

                if not deliberation_calls:
                    # Model provided final text
                    final_findings = deliberation_content or "Mission completed with no additional tools required."
                    break

                # Execute pruned tool calls
                executed_calls = []
                executed_results = []

                for tc in deliberation_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    # Security check: ensure tool is in pruned action space
                    if fn_name not in self.spec.tools:
                        logger.warning(f"SubAgent {agent_id} attempted unauthorized tool {fn_name}. Ignored.")
                        continue

                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}

                    call_id = tc.get("id") or f"sub_{agent_id}_{tool_calls_count}"
                    tool_calls_count += 1

                    tool_start = time.time()
                    span_id = f"span_tool_{call_id}"
                    parent_span_id = f"span_sub_{agent_id}"

                    await event_queue.put({
                        "type": "subagent_tool_call",
                        "agent_id": agent_id,
                        "agent_name": self.spec.name,
                        "agent_role": self.spec.role,
                        "name": fn_name,
                        "args": args,
                        "id": call_id,
                        "span_id": span_id,
                        "parent_span_id": parent_span_id,
                        "tier": tier_resolved,
                        "model": model_used,
                        "timestamp": tool_start
                    })

                    res = await execute_tool(fn_name, args, active_workspace_path=self.active_workspace_path)
                    tool_duration_ms = int((time.time() - tool_start) * 1000)
                    summary = self._summarize_result(fn_name, res)

                    executed_calls.append(tc)
                    executed_results.append(res)
                    tool_history.append({
                        "tool": fn_name,
                        "args": args,
                        "summary": summary,
                        "result": res,
                        "duration_ms": tool_duration_ms,
                        "span_id": span_id
                    })
                    raw_evidence[fn_name] = res

                    await event_queue.put({
                        "type": "subagent_tool_result",
                        "agent_id": agent_id,
                        "agent_name": self.spec.name,
                        "agent_role": self.spec.role,
                        "name": fn_name,
                        "summary": summary,
                        "data": res,
                        "id": call_id,
                        "span_id": span_id,
                        "parent_span_id": parent_span_id,
                        "duration_ms": tool_duration_ms,
                        "tier": tier_resolved,
                        "model": model_used,
                        "timestamp": time.time()
                    })

                # Once pruned tools are executed, compile concise factual findings immediately
                if executed_calls:
                    findings_bullets = []
                    for item in tool_history:
                        title = self._format_tool_title(item["tool"])
                        summary = item["summary"]
                        findings_bullets.append(f"- **{title}**: {summary}")
                    final_findings = "\n".join(findings_bullets) if findings_bullets else "Pruned tools executed successfully."
                    break

            if not final_findings:
                final_findings = "Mission completed with verified observations."

            duration_ms = int((time.time() - start_time) * 1000)

            await event_queue.put({
                "type": "subagent_status",
                "agent_id": agent_id,
                "agent_name": self.spec.name,
                "agent_role": self.spec.role,
                "status": "completed",
                "findings": final_findings,
                "duration_ms": duration_ms,
                "tier": tier_resolved,
                "model": model_used,
                "tools_count": tool_calls_count,
                "span_id": f"span_sub_{agent_id}",
                "parent_span_id": "span_conductor_xod"
            })

            return SubAgentResult(
                agent_id=agent_id,
                name=self.spec.name,
                role=self.spec.role,
                tier_requested=self.spec.tier,
                tier_resolved=tier_resolved,
                model_used=model_used,
                status="completed",
                findings=final_findings,
                tool_calls_count=tool_calls_count,
                tool_history=tool_history,
                raw_evidence=raw_evidence,
                duration_ms=duration_ms
            )

        except Exception as e:
            logger.error(f"SubAgent {agent_id} execution error: {e}")
            duration_ms = int((time.time() - start_time) * 1000)
            err_msg = str(e)

            await event_queue.put({
                "type": "subagent_status",
                "agent_id": agent_id,
                "status": "error",
                "message": err_msg,
                "duration_ms": duration_ms
            })

            return SubAgentResult(
                agent_id=agent_id,
                name=self.spec.name,
                role=self.spec.role,
                tier_requested=self.spec.tier,
                tier_resolved=tier_resolved,
                model_used=model_used,
                status="error",
                findings=f"Error executing sub-agent: {err_msg}",
                tool_calls_count=tool_calls_count,
                tool_history=tool_history,
                raw_evidence=raw_evidence,
                duration_ms=duration_ms,
                error=err_msg
            )

    def _format_tool_title(self, name: str) -> str:
        return name.replace("fs_", "").replace("computer_", "").replace("_", " ").title()

    def _summarize_result(self, name: str, res: Any) -> str:
        if isinstance(res, dict):
            if "error" in res:
                return f"Error: {res['error']}"
            if name == "fs_list_workspace_files":
                return f"Listed {res.get('total_files', 0)} files in {res.get('subpath') or 'root'}"
            if name == "fs_read_workspace_file":
                return f"Read {res.get('total_lines', 0)} lines ({res.get('size_bytes', 0)} bytes)"
            if name == "fs_search_workspace":
                return f"Found {res.get('total_matches', 0)} occurrences"
            if name == "computer_inspect_port":
                return f"Port {res.get('port')}: {'In use' if res.get('in_use') else 'Available'}"
            if name == "computer_get_ports":
                return f"Discovered {len(res.get('ports', []))} active listening ports"
            if name == "computer_inspect_system":
                return f"Host {res.get('os')} ({res.get('arch')}), RAM: {res.get('memory', {}).get('used_gb')}GB used ({res.get('memory', {}).get('pressure_pct', 0)}%)"
            if name == "computer_review_system":
                return f"System health {res.get('status', 'audited')}: {len(res.get('remediations', []))} remediations proposed"
            if name == "computer_kill_process":
                return f"Terminated process PID {res.get('pid')}"
            if name == "computer_execute_action":
                return res.get("message") or "Executed remediation action"
            if name == "computer_run_command":
                return f"Exit code {res.get('exit_code')} ({res.get('duration_sec', 0)}s)"
            if name == "browser_navigate":
                return f"Navigated to {res.get('url')} ({res.get('status')})"
            if name == "browser_extract_content":
                return f"Extracted {len(res.get('text', ''))} chars"
            if name == "browser_extract_markdown":
                return f"Extracted structured Markdown ({res.get('total_chars', len(res.get('markdown', '')))} chars)"
            if name == "browser_click":
                return f"Clicked element: {res.get('clicked_element', {}).get('tagName', 'target')}"
            if name == "browser_type":
                return f"Typed {res.get('typed', {}).get('valueLength', 0)} chars into form"
            if name == "browser_scroll":
                return f"Scrolled page {res.get('scroll', {}).get('mode', 'down')}"
            if name == "browser_run_mission":
                return f"Mission {res.get('status', 'done')}: {res.get('total_steps', 0)} steps"
            if name == "browser_snapshot":
                return "Captured browser snapshot"
            if name == "trustmrr_financials":
                return f"MRR: ${res.get('financials', {}).get('verified_mrr', 0):,}"
        return "Executed successfully"


class SwarmCoordinator:
    """
    Orchestrates the JIT Sub-Agent Swarm under Xod's command.
    - Compiles objectives into specialized micro-agent rosters.
    - Executes sub-agents concurrently via asyncio.
    - Feeds findings back into Xod's working memory and UI stream.
    """

    def __init__(self, active_workspace_path: Optional[str] = None):
        self.active_workspace_path = active_workspace_path

    def compile_jit_swarm(self, objective: str) -> List[SubAgentSpec]:
        """
        Dynamically decomposes an objective into a targeted roster of specialized sub-agents.
        Each sub-agent gets a pruned action space and optimal model tier.
        """
        q = objective.lower()
        subagents: List[SubAgentSpec] = []

        # 1. Check for Codebase / Filesystem needs
        needs_code = any(k in q for k in [
            "code", "file", "folder", "directory", "repo", "project", "workspace",
            "read", "readme", "config", "src", "inspect project", "audit repo"
        ])
        if needs_code or self.active_workspace_path:
            arch = SPECIALIST_ARCHETYPES["code_auditor"]
            subagents.append(SubAgentSpec(
                id="sub_code_auditor",
                name=arch["name"],
                role=arch["role"],
                objective=f"Inspect local project structure, files, and configurations related to: '{objective}'",
                tools=arch["tools"],
                tier=arch["default_tier"],
                icon=arch["icon"]
            ))

        # 2. Check for System / Computer / OS needs
        needs_system = any(k in q for k in [
            "port", "cpu", "ram", "memory", "specs", "system", "process", "pid",
            "daemon", "job", "computer", "mac", "machine", "host", "listening"
        ])
        if needs_system:
            arch = SPECIALIST_ARCHETYPES["system_supervisor"]
            subagents.append(SubAgentSpec(
                id="sub_sys_supervisor",
                name=arch["name"],
                role=arch["role"],
                objective=f"Audit host telemetry, listening ports, and background services for: '{objective}'",
                tools=arch["tools"],
                tier=arch["default_tier"],
                icon=arch["icon"]
            ))

        # 3. Check for Browser / Web Navigation needs
        needs_browser = any(k in q for k in [
            "http://", "https://", ".com", ".org", ".io", ".dev", ".ai", "browse",
            "navigate", "website", "web page", "dom", "walkthrough", "taskcato"
        ])
        if needs_browser:
            arch = SPECIALIST_ARCHETYPES["browser_scout"]
            # Extract target url if present
            url_match = re.search(r'https?://[^\s]+', objective)
            target_url = url_match.group(0) if url_match else "the target website"
            subagents.append(SubAgentSpec(
                id="sub_browser_scout",
                name=arch["name"],
                role=arch["role"],
                objective=f"Navigate to {target_url}, extract page DOM architecture, and capture snapshot.",
                tools=arch["tools"],
                tier=arch["default_tier"],
                icon=arch["icon"]
            ))

        # 4. Check for SaaS / Financials / Market Intel needs
        needs_market = any(k in q for k in [
            "mrr", "arr", "revenue", "valuation", "sec", "edgar", "funding", "investor", "acquire", "saas metrics"
        ])
        if needs_market:
            arch = SPECIALIST_ARCHETYPES["market_intel"]
            subagents.append(SubAgentSpec(
                id="sub_market_intel",
                name=arch["name"],
                role=arch["role"],
                objective=f"Audit verified financial metrics, ARR multiples, and filings for: '{objective}'",
                tools=arch["tools"],
                tier=arch["default_tier"],
                icon=arch["icon"]
            ))

        # 5. Check for Network / DNS / Infrastructure Recon needs
        needs_network = any(k in q for k in [
            "dns", "subdomain", "traffic", "tranco", "certificate", "cdn", "latency", "shadow infrastructure"
        ])
        if needs_network:
            arch = SPECIALIST_ARCHETYPES["network_recon"]
            subagents.append(SubAgentSpec(
                id="sub_network_recon",
                name=arch["name"],
                role=arch["role"],
                objective=f"Audit DNS records, subdomains, and digital traffic telemetry for: '{objective}'",
                tools=arch["tools"],
                tier=arch["default_tier"],
                icon=arch["icon"]
            ))

        # Fallback: If no specialist matched, build a versatile dual-agent swarm
        if not subagents:
            # Sub-Agent 1: Local System & Code
            arch_sys = SPECIALIST_ARCHETYPES["system_supervisor"]
            subagents.append(SubAgentSpec(
                id="sub_sys_supervisor",
                name=arch_sys["name"],
                role=arch_sys["role"],
                objective=f"Audit local host metrics and open ports relevant to: '{objective}'",
                tools=arch_sys["tools"],
                tier="local",
                icon=arch_sys["icon"]
            ))

            # Sub-Agent 2: Web Research
            arch_web = SPECIALIST_ARCHETYPES["web_researcher"]
            subagents.append(SubAgentSpec(
                id="sub_web_researcher",
                name=arch_web["name"],
                role=arch_web["role"],
                objective=f"Gather empirical facts and public data regarding: '{objective}'",
                tools=arch_web["tools"],
                tier="auto",
                icon=arch_web["icon"]
            ))

        return subagents

    async def run_swarm_stream(
        self,
        specs: List[SubAgentSpec],
        host_context: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes the sub-agents concurrently, streaming real-time events to the frontend.
        """
        event_queue = asyncio.Queue()
        swarm_start = time.time()

        # Emit swarm initialization plan
        roster = [
            {
                "id": s.id,
                "name": s.name,
                "role": s.role,
                "icon": s.icon,
                "tools": s.tools,
                "tier": s.tier,
                "status": "pending",
                "span_id": f"span_sub_{s.id}",
                "parent_span_id": "span_conductor_xod"
            }
            for s in specs
        ]
        yield {
            "type": "swarm_plan",
            "title": f"JIT Sub-Agent Swarm ({len(specs)} Specialists)",
            "conductor": "Xod",
            "span_id": "span_conductor_xod",
            "subagents": roster
        }

        # Initialize SubAgent worker instances
        workers = [
            SubAgent(spec, active_workspace_path=self.active_workspace_path)
            for spec in specs
        ]

        # Launch concurrent background execution
        async def run_worker(w: SubAgent):
            return await w.execute(event_queue, host_context=host_context)

        worker_tasks = [asyncio.create_task(run_worker(w)) for w in workers]

        # Drain the queue while tasks are running
        while not all(t.done() for t in worker_tasks) or not event_queue.empty():
            try:
                # Wait for next event with a short timeout
                event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                yield event
                event_queue.task_done()
            except asyncio.TimeoutError:
                await asyncio.sleep(0.02)
                continue

        # Collect final results from all worker tasks
        results: List[SubAgentResult] = await asyncio.gather(*worker_tasks)
        total_duration_ms = int((time.time() - swarm_start) * 1000)
        total_tools_count = sum(r.tool_calls_count for r in results)

        # Emit swarm completion event
        yield {
            "type": "swarm_complete",
            "conductor": "Xod",
            "span_id": "span_conductor_xod",
            "duration_ms": total_duration_ms,
            "total_subagents": len(results),
            "total_tools_count": total_tools_count,
            "results": [
                {
                    "id": r.agent_id,
                    "name": r.name,
                    "role": r.role,
                    "tier_resolved": r.tier_resolved,
                    "model_used": r.model_used,
                    "status": r.status,
                    "findings": r.findings,
                    "duration_ms": r.duration_ms,
                    "tools_count": r.tool_calls_count,
                    "span_id": f"span_sub_{r.agent_id}",
                    "parent_span_id": "span_conductor_xod"
                }
                for r in results
            ]
        }
