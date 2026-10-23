import json
import os
import re
from typing import Dict, Any, List, Optional

class EvidenceBoard:
    """
    Structured working memory and scratchpad for the autonomous research agent.
    Maintains verified telemetry, tech stack findings, pricing signals,
    and sources across multi-hop iterations.
    Enables compact context summaries to protect local 8k-16k LLM context windows.
    """

    def __init__(self, objective: str):
        self.objective = objective
        self.target_entity: Optional[str] = None
        self.target_domain: Optional[str] = None
        self.verified_financials: Dict[str, Any] = {}
        self.tech_stack: List[Dict[str, str]] = []
        self.pricing_signals: Dict[str, Any] = {}
        self.competitors: List[str] = []
        self.contradictions: List[str] = []
        self.sources: List[Dict[str, str]] = []
        self.unresolved_questions: List[str] = []
        self.community_sentiment: Optional[str] = None
        self.github_telemetry: Optional[Dict[str, Any]] = None
        self.dns_infrastructure: Optional[Dict[str, Any]] = None
        self.operational_surfaces: Optional[Dict[str, Any]] = None
        self.subdomain_recon: Optional[Dict[str, Any]] = None
        self.talent_telemetry: Optional[Dict[str, Any]] = None
        self.package_telemetry: Optional[Dict[str, Any]] = None
        self.sec_filings: Optional[Dict[str, Any]] = None
        self.ip_intel: Optional[Dict[str, Any]] = None
        self.tags: List[str] = []
        self.browser_telemetry: List[Dict[str, Any]] = []
        self.computer_telemetry: List[Dict[str, Any]] = []
        self.fs_telemetry: List[Dict[str, Any]] = []
        self.visual_telemetry: List[Dict[str, Any]] = []
        self.doc_telemetry: List[Dict[str, Any]] = []
        self.creator_telemetry: Dict[str, Any] = {}
        self.media_telemetry: List[Dict[str, Any]] = []
        self.live_page_content: Optional[str] = None
        self.page_headings: List[str] = []
        self.page_elements: List[str] = []
        self.tool_audit_trail: List[Dict[str, Any]] = []

    def update_from_tool(self, name: str, args: Dict[str, Any], result: Dict[str, Any]):
        if not hasattr(self, "tool_audit_trail"):
            self.tool_audit_trail = []
        self.tool_audit_trail.append({
            "tool": name,
            "args": args,
            "result": result
        })

        if not isinstance(result, dict):
            return

        # Entity / domain discovery
        if "entity" in result and not self.target_entity:
            self.target_entity = result["entity"]
        if "domain" in result and not self.target_domain:
            self.target_domain = result["domain"]

        # 1. From TrustMRR / Verified Financials
        if "verified_financials" in result and result["verified_financials"]:
            vf = result["verified_financials"]
            if isinstance(vf, dict) and not vf.get("not_found"):
                self.verified_financials.update(vf)
                if vf.get("name") and not self.target_entity:
                    self.target_entity = vf["name"]

        if name == "trustmrr_get_startup" and "name" in result:
            f = result.get("formatted", {})
            self.verified_financials.update({
                "name": result.get("name"),
                "slug": result.get("slug"),
                "verified_mrr": f.get("mrr"),
                "last_30d_revenue": f.get("last30Days"),
                "total_revenue": f.get("totalRevenue"),
                "asking_price": f.get("askingPrice"),
                "arr_multiple": f.get("multiple"),
                "growth_30d": f.get("growth30d"),
                "active_subscriptions": result.get("activeSubscriptions"),
                "paying_customers": result.get("payingCustomers"),
                "category": result.get("category"),
                "funding": result.get("startupInsights", {}).get("fundingStatus")
            })
            if result.get("website"):
                self.target_domain = result["website"]

        # 2. From Tech Stack Auditor
        if "technologies" in result:
            self.tech_stack = result.get("technologies", [])
            if "pricing_signals" in result:
                self.pricing_signals.update(result["pricing_signals"])
            if "domain" in result and not self.target_domain:
                self.target_domain = result["domain"]

        if "tech_stack_audit" in result and result["tech_stack_audit"]:
            ta = result["tech_stack_audit"]
            if isinstance(ta, dict):
                if "technologies" in ta:
                    self.tech_stack = ta.get("technologies", [])
                if "pricing_signals" in ta:
                    self.pricing_signals.update(ta.get("pricing_signals", {}))

        # 3. From Community Sentiment (Hacker News + Forums)
        if name == "mine_community_sentiment":
            self.community_sentiment = result.get("sentiment_summary")
            if not self.target_entity and result.get("target"):
                self.target_entity = result["target"]
            for story in result.get("hacker_news_stories", []):
                self._add_source({"title": f"HN: {story.get('title')}", "url": story.get("hn_discussion"), "snippet": f"{story.get('points')} pts, {story.get('comments')} comments"})
            for forum in result.get("forum_discussions", []):
                self._add_source(forum)

        # 4. From GitHub Repo Auditor
        if name == "audit_github_repo" and result.get("success"):
            self.github_telemetry = result
            if not self.target_entity and result.get("name"):
                self.target_entity = result["name"]
            if not self.target_domain and result.get("repo_url"):
                self.target_domain = result["repo_url"]
            for topic in result.get("topics", [])[:5]:
                if topic not in self.tags:
                    self.tags.append(topic)
            gh_badge = {"name": f"GitHub: {result.get('stars', 0)}★ ({result.get('language')})", "category": "Open Source"}
            if not any(t.get("name") == gh_badge["name"] for t in self.tech_stack):
                self.tech_stack.append(gh_badge)

        # 5. From DNS & Infrastructure Inspector
        if name == "inspect_domain_dns_health":
            self.dns_infrastructure = result
            if not self.target_domain and result.get("domain"):
                self.target_domain = result["domain"]
            if not self.target_entity and result.get("domain"):
                self.target_entity = result["domain"].split(".")[0].capitalize()
            if result.get("email_provider") and result["email_provider"] not in ("Unknown / Self-hosted", "No MX Records Found"):
                email_badge = {"name": result["email_provider"], "category": "Email & Auth"}
                if not any(t.get("name") == email_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(email_badge)
            for v in result.get("saas_verifications", []):
                v_badge = {"name": v, "category": "Enterprise Auth"}
                if not any(t.get("name") == v_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(v_badge)

        # 6. From Site Surface Explorer
        if name == "explore_site_surface":
            self.operational_surfaces = result.get("operational_signals")
            if not self.target_domain and result.get("domain"):
                self.target_domain = result["domain"]

        # 7. From Shadow Subdomain Recon
        if name == "recon_shadow_subdomains":
            self.subdomain_recon = result
            if not self.target_domain and result.get("domain"):
                self.target_domain = result["domain"]
            if not self.target_entity and result.get("domain"):
                self.target_entity = result["domain"].split(".")[0].capitalize()
            signals = result.get("architecture_signals", {})
            if signals.get("has_enterprise_subdomains"):
                if not self.operational_surfaces:
                    self.operational_surfaces = {}
                self.operational_surfaces["has_enterprise_tier"] = True
            if signals.get("has_realtime_websockets"):
                ws_badge = {"name": "Realtime WebSockets", "category": "Transport"}
                if not any(t.get("name") == ws_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(ws_badge)
            if signals.get("is_microservice_architecture"):
                ms_badge = {"name": "Microservices Architecture", "category": "Architecture"}
                if not any(t.get("name") == ms_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(ms_badge)
            if signals.get("has_ai_or_mcp_endpoints"):
                mcp_badge = {"name": "MCP / Agentic Endpoints", "category": "AI Protocols"}
                if not any(t.get("name") == mcp_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(mcp_badge)

        # 8. From Talent & Headcount Telemetry
        if name in ("audit_talent_and_hiring", "scan_live_job_firehose", "scan_batch_job_firehose"):
            self.talent_telemetry = result
            if not self.target_entity and result.get("target"):
                self.target_entity = result["target"]
            for tech in result.get("internal_tech_signals", []):
                tech_badge = {"name": f"Hiring: {tech}", "category": "Internal Tech"}
                if not any(t.get("name") == tech_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(tech_badge)
            reqs = result.get("requisitions") or result.get("jobs") or []
            if reqs:
                self._add_source({
                    "title": f"Live Job Requisitions: {result.get('target', 'Career Portal')}",
                    "url": result.get("careers_url") or "https://www.workatastartup.com",
                    "snippet": f"Discovered {len(reqs)} active roles ({', '.join(r.get('title', '') for r in reqs[:3])})"
                })

        # 9. From Package Registry Ecosystem
        if name == "audit_package_ecosystem":
            self.package_telemetry = result
            if not self.target_entity and result.get("target"):
                self.target_entity = result["target"]
            downloads = result.get("total_monthly_downloads", 0)
            if downloads > 0:
                d_badge = {"name": f"Packages: {result.get('ecosystem_summary')}", "category": "Developer Adoption"}
                if not any(t.get("name") == d_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(d_badge)
            for pkg in result.get("npm_packages", []):
                p_badge = {"name": f"NPM: {pkg.get('name')}", "category": "SDK & Packages"}
                if not any(t.get("name") == p_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(p_badge)

        # 10. From SEC EDGAR Form D Diligence
        if name == "audit_sec_filings":
            self.sec_filings = result
            if not self.target_entity and result.get("legal_entity_name"):
                self.target_entity = result["legal_entity_name"]
            if result.get("is_sec_registered"):
                sec_badge = {
                    "name": f"SEC: {result.get('total_capital_sold_formatted')} Sold ({result.get('jurisdiction')})",
                    "category": "Corporate Diligence"
                }
                if not any(t.get("name") == sec_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(sec_badge)
                if result.get("jurisdiction") and result["jurisdiction"] not in self.tags:
                    self.tags.append(result["jurisdiction"])

        # 11. From Intellectual Property & Patent Audit
        if name == "audit_ip_landscape":
            self.ip_intel = result
            if not self.target_entity and result.get("audited_name"):
                self.target_entity = result["audited_name"]
            if result.get("has_ip_moat"):
                ip_badge = {
                    "name": f"IP Moat: {result.get('ip_summary')}",
                    "category": "Intellectual Property"
                }
                if not any(t.get("name") == ip_badge["name"] for t in self.tech_stack):
                    self.tech_stack.append(ip_badge)
                if result.get("total_patents", 0) > 0 and "patents" not in self.tags:
                    self.tags.append("patents")
                if result.get("total_trademarks", 0) > 0 and "registered-trademark" not in self.tags:
                    self.tags.append("registered-trademark")

        # 12. From Knowledge Graph Self-Update
        if name == "update_knowledge_graph_entity":
            if result.get("tags"):
                self.tags = result.get("tags", [])
            if result.get("linked_competitors"):
                for c in result.get("linked_competitors", []):
                    if c not in self.competitors:
                        self.competitors.append(c)

        # 8. From OmniSearch / You.com / Tavily AI
        if "answer" in result and result.get("answer"):
            self._add_source({
                "title": f"Tavily AI Synthesized Answer: {args.get('query', self.objective)}",
                "url": "https://tavily.com",
                "snippet": str(result.get("answer"))[:700]
            })

        if "top_ranked_intelligence" in result:
            for item in result.get("top_ranked_intelligence", []):
                self._add_source(item)

        if "results" in result:
            for item in result.get("results", []):
                self._add_source(item)

        # 9. Contradictions
        if "contradictions_detected" in result:
            for c in result.get("contradictions_detected", []):
                if c not in self.contradictions:
                    self.contradictions.append(c)

        # 13. From Autonomous Browser Sandbox
        if name.startswith("browser_"):
            url = result.get("url") or args.get("url") or self.target_domain or ""
            title = result.get("title") or ""
            if url:
                self.target_domain = url
            if title and title not in ("about:blank", "Empty Tab", "Untitled"):
                if not self.target_entity or self.target_entity == self.objective:
                    self.target_entity = title

            record = {
                "tool": name,
                "url": url,
                "title": title,
                "action": args
            }
            if result.get("text_preview"):
                self.live_page_content = str(result["text_preview"])
                record["text_preview"] = str(result["text_preview"])[:800]
            if result.get("text"):
                self.live_page_content = str(result["text"])
                record["text_preview"] = str(result["text"])[:800]
            if result.get("markdown"):
                self.live_page_content = str(result["markdown"])
                record["text_preview"] = str(result["markdown"])[:800]
            if result.get("headings"):
                raw_headings = result["headings"]
                extracted_headings = []
                for h in raw_headings:
                    if isinstance(h, dict):
                        txt = str(h.get("text") or "").strip()
                    else:
                        txt = str(h).strip()
                    if txt:
                        extracted_headings.append(txt)
                self.page_headings = extracted_headings
                record["headings"] = extracted_headings[:15]
            if result.get("elements"):
                raw_elements = result["elements"]
                extracted_elements = []
                for el in raw_elements:
                    if isinstance(el, dict):
                        txt = str(el.get("text") or el.get("name") or el.get("id") or "").strip()
                    else:
                        txt = str(el).strip()
                    if txt:
                        extracted_elements.append(txt)
                self.page_elements = extracted_elements
                record["elements"] = extracted_elements[:15]
            if result.get("meta_description"):
                record["meta_description"] = str(result["meta_description"])
            if result.get("extracted_data"):
                record["extracted_data"] = result["extracted_data"]

            self.browser_telemetry.append(record)

            if url and (title or result.get("text_preview") or result.get("text")):
                self._add_source({
                    "title": f"Live Browser: {title or url}",
                    "url": url,
                    "snippet": (result.get("text_preview") or result.get("text") or "")[:350]
                })

        # 14. From Background Computer Control & Telemetry
        if name.startswith("computer_"):
            self.computer_telemetry.append({
                "tool": name,
                "args": args,
                "result": result
            })
            if name == "computer_run_command":
                cmd = args.get("command", "")
                out = (result.get("stdout") or result.get("stderr") or "")[:300]
                self._add_source({
                    "title": f"Shell Command: {cmd}",
                    "url": f"computer://{cmd}",
                    "snippet": f"Exit code {result.get('exit_code')}: {out}"
                })
            elif name == "computer_find_files":
                found_names = [f.get("name", "") for f in result.get("results", [])[:5]]
                self._add_source({
                    "title": f"Spotlight Search: {args.get('query')}",
                    "url": f"spotlight://{args.get('query')}",
                    "snippet": f"Found {result.get('count', 0)} files: {', '.join(found_names)}"
                })
            elif name == "computer_inspect_port":
                p_label = "In Use" if result.get("in_use") else "Free"
                self._add_source({
                    "title": f"Port Inspection :{args.get('port')}",
                    "url": f"port://{args.get('port')}",
                    "snippet": f"Port {args.get('port')} is {p_label} ({len(result.get('processes', []))} processes)"
                })

        # 15. From Controlled Workspace Filesystem
        if name.startswith("fs_"):
            self.fs_telemetry.append({
                "tool": name,
                "args": args,
                "result": result
            })
            if name == "fs_list_workspace_files":
                fnames = [f.get("name", "") for f in result.get("files", [])[:6]]
                self._add_source({
                    "title": f"Workspace Files ({result.get('subpath') or 'root'})",
                    "url": f"workspace://{result.get('subpath') or 'root'}",
                    "snippet": f"Listed {result.get('total_files', 0)} files: {', '.join(fnames)}"
                })
            elif name == "fs_read_workspace_file":
                self._add_source({
                    "title": f"Workspace File: {result.get('file_path')}",
                    "url": f"workspace://{result.get('file_path')}",
                    "snippet": f"Read {result.get('total_lines', 0)} lines: {(result.get('content') or '')[:300]}"
                })
            elif name == "fs_search_workspace":
                self._add_source({
                    "title": f"Workspace Search: {args.get('query')}",
                    "url": f"workspace://search?q={args.get('query')}",
                    "snippet": f"Found {result.get('total_matches', 0)} matches across {len(result.get('matches', []))} files"
                })

        # 16. From Creator Discovery & Attention Arbitrage
        if name in ("creator_discover_candidates", "creator_predict_attention"):
            self.creator_telemetry = result
            if result.get("niche") and not self.target_entity:
                self.target_entity = f"Creators in {result['niche']}"
            for c in result.get("creators", []):
                self._add_source({
                    "title": f"Creator: {c.get('handle')} ({c.get('platform')}) - {c.get('followers_formatted')} followers",
                    "url": c.get("profile_url") or f"https://www.{c.get('platform')}.com/{c.get('handle')}",
                    "snippet": f"Expected views: {c.get('expected_views_formatted')}, CPM: {c.get('predicted_cpm_formatted')}, Arbitrage: {c.get('arbitrage_multiple_formatted')}, Contact: {c.get('contact_email')}"
                })

        # 17. From YouTube & Media Studio Tools
        if name.startswith("media_") or "media_type" in result or "relative_url" in result or "output_path" in result:
            self.media_telemetry.append(result)
            if result.get("output_path"):
                self._add_source({
                    "title": f"Media Asset: {result.get('filename') or result.get('name') or name}",
                    "url": result.get("relative_url") or f"file://{result.get('output_path')}",
                    "snippet": f"Saved to {result.get('output_path')} ({result.get('size_bytes', 0)} bytes). Markdown: {result.get('markdown', '')}"
                })
            elif result.get("title") and not self.target_entity:
                self.target_entity = result["title"]
            if result.get("webpage_url"):
                self._add_source({
                    "title": f"Media: {result.get('title')}",
                    "url": result.get("webpage_url"),
                    "snippet": f"{result.get('media_type', 'media').capitalize()} file saved to {result.get('file_path')} ({result.get('file_size_mb')} MB)"
                })

    def _add_source(self, item: Dict[str, Any]):
        if not isinstance(item, dict):
            return
        url = item.get("url", "")
        if not url:
            return
        if not any(s.get("url") == url for s in self.sources):
            self.sources.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", "")[:350]
            })

    def get_stats(self) -> Dict[str, int]:
        return {
            "metrics_count": len([k for k, v in self.verified_financials.items() if v is not None]),
            "tech_count": len(self.tech_stack),
            "sources_count": len(self.sources),
            "contradictions_count": len(self.contradictions),
            "browser_events_count": len(self.browser_telemetry),
            "computer_events_count": len(self.computer_telemetry),
            "fs_events_count": len(self.fs_telemetry),
            "visual_count": len(self.visual_telemetry),
            "doc_count": len(self.doc_telemetry)
        }

    def to_compact_summary(self) -> str:
        """
        Creates a high-density, context-efficient summary (~150-250 tokens).
        This is injected into intermediate agent turns so the LLM remembers all
        gathered facts without keeping raw multi-kilobyte JSON payloads in memory.
        """
        lines = [f"### [Working Memory: Evidence Board for '{self.target_entity or self.objective}']"]

        if self.verified_financials:
            vf = self.verified_financials
            metrics_str = f"MRR: {vf.get('verified_mrr', 'N/A')}, 30d Rev: {vf.get('last_30d_revenue', 'N/A')}, Total: {vf.get('total_revenue', 'N/A')}, Multiple: {vf.get('arr_multiple', 'N/A')}, Active Subs: {vf.get('active_subscriptions', 'N/A')}"
            lines.append(f"* Verified Financials: {metrics_str}")

        if self.tech_stack:
            tech_names = [t.get("name") for t in self.tech_stack if isinstance(t, dict)]
            lines.append(f"* Audited Tech Stack ({len(tech_names)}): {', '.join(tech_names[:10])}")

        if self.pricing_signals:
            ps = self.pricing_signals
            prices = ps.get("detected_price_points", [])
            lines.append(f"* Pricing Signals: Free Tier: {ps.get('has_free_tier')}, Annual Discount: {ps.get('has_annual_discount')}, Detected Prices: {', '.join(prices[:6])}")

        if self.community_sentiment:
            lines.append(f"* Community Sentiment: {self.community_sentiment}")

        if self.github_telemetry:
            gh = self.github_telemetry
            lines.append(f"* Open Source Telemetry: {gh.get('stars', 0)} Stars, {gh.get('forks', 0)} Forks, Lang: {gh.get('language')}, License: {gh.get('license')}, Health: {gh.get('health_rating')}")

        if self.dns_infrastructure:
            dns = self.dns_infrastructure
            lines.append(f"* Infrastructure Posture: Mail: {dns.get('email_provider')}, SPF: {dns.get('spf_configured')}, DMARC: {dns.get('dmarc_configured')} ({dns.get('dmarc_policy')}), Score: {dns.get('enterprise_email_security_score')}/100")

        if self.operational_surfaces:
            ops = self.operational_surfaces
            signals = [k.replace("has_", "").replace("_", " ") for k, v in ops.items() if v]
            if signals:
                lines.append(f"* Operational Surfaces Detected: {', '.join(signals)}")

        if self.subdomain_recon:
            sub = self.subdomain_recon
            notables = sub.get("notable_discoveries", [])
            notable_str = f" ({'; '.join(notables[:2])})" if notables else ""
            lines.append(f"* Shadow Infrastructure ({sub.get('total_subdomains_discovered', 0)} subdomains): {sub.get('scale_verdict')}{notable_str}")

        if self.talent_telemetry:
            tal = self.talent_telemetry
            open_roles = tal.get("total_open_roles", 0)
            if open_roles > 0:
                dept_strs = [f"{k.split(' ')[0]}: {v.get('count')}" for k, v in tal.get("department_breakdown", {}).items() if v.get("count", 0) > 0]
                tech_str = f", Tech: {', '.join(tal.get('internal_tech_signals', [])[:4])}" if tal.get("internal_tech_signals") else ""
                lines.append(f"* Talent & Hiring ({open_roles} open roles on {tal.get('ats_provider')}): {tal.get('hiring_velocity_verdict')} ({', '.join(dept_strs)}){tech_str}")
            else:
                lines.append(f"* Talent & Hiring: 0 public requisitions ({tal.get('burn_rate_signal')})")

        if self.package_telemetry:
            pkg = self.package_telemetry
            lines.append(f"* Package Ecosystem: {pkg.get('ecosystem_summary')} ({pkg.get('adoption_scale_verdict')})")

        if self.sec_filings:
            sec = self.sec_filings
            if sec.get("is_sec_registered"):
                lines.append(f"* SEC Regulatory Filings ({sec.get('legal_entity_name')}, CIK {sec.get('cik')}, {sec.get('jurisdiction')}): {sec.get('total_capital_sold_formatted')} sold | {sec.get('governance_summary')} | Verdict: {sec.get('diligence_verdict')}")
            else:
                lines.append(f"* SEC Regulatory Filings: {sec.get('diligence_verdict')}")

        if self.ip_intel:
            ip = self.ip_intel
            lines.append(f"* Intellectual Property & Moat ({ip.get('ip_defensibility_verdict')}): {ip.get('ip_summary')} | Status: {ip.get('brand_protection_status')}")

        if self.creator_telemetry:
            ct = self.creator_telemetry
            cm = ct.get("campaign_metrics", {})
            creators = ct.get("creators", [])
            handles = [f"{c.get('handle')} ({c.get('predicted_cpm_formatted')}, {c.get('arbitrage_multiple_formatted')} arb)" for c in creators[:5]]
            lines.append(f"* Creator Attention Arbitrage ({len(creators)} shortlisted): Blended CPM: ${cm.get('blended_predicted_cpm')}, Portfolio Arbitrage: {cm.get('portfolio_arbitrage_multiple')}, Expected Views: {cm.get('total_expected_views', 0):,}")
            if handles:
                lines.append(f"  - Top Shortlist: {'; '.join(handles)}")

        if self.tags:
            tag_strs = [str(t) for t in self.tags if str(t).strip()]
            if tag_strs:
                lines.append(f"* Knowledge Graph Tags: {', '.join(tag_strs)}")

        if self.competitors:
            comp_strs = [str(c) for c in self.competitors if str(c).strip()]
            if comp_strs:
                lines.append(f"* Linked Competitors: {', '.join(comp_strs)}")

        if self.contradictions:
            contra_strs = [str(c) for c in self.contradictions if str(c).strip()]
            if contra_strs:
                lines.append(f"* Flagged Contradictions: {'; '.join(contra_strs)}")

        if self.browser_telemetry:
            last_b = self.browser_telemetry[-1]
            lines.append(f"* Autonomous Live Browser Telemetry:")
            lines.append(f"  - Active URL: {last_b.get('url', 'N/A')}")
            if last_b.get('title'):
                lines.append(f"  - Page Title: {last_b.get('title')}")
            if self.page_headings:
                h_strs = [str(h) for h in self.page_headings[:8] if str(h).strip()]
                if h_strs:
                    lines.append(f"  - Key Page Headings: {', '.join(h_strs)}")
            if self.page_elements:
                e_strs = [str(e) for e in self.page_elements[:12] if str(e).strip()]
                if e_strs:
                    lines.append(f"  - Interactive Elements & Nav Menus: {', '.join(e_strs)}")
            if self.live_page_content:
                lines.append(f"  - Page Content Snippet: {str(self.live_page_content)[:800]}")

        if self.computer_telemetry:
            lines.append(f"* Background Computer System & Shell Telemetry:")
            for ct in self.computer_telemetry[-10:]:
                t_name = ct.get("tool", "")
                res = ct.get("result", {}) or {}
                args = ct.get("args", {}) or {}
                if isinstance(res, dict) and t_name != "computer_run_command" and \
                        (res.get("success") is False or (res.get("error") and not res.get("success"))):
                    # A failure, said as one: the lines below describe successes ("Opened …"), and the writer took a
                    # failed open for an opened file.
                    what = args.get("filepath") or args.get("path") or args.get("target") or args.get("src") or args.get("query") or ""
                    lines.append(f"  - {t_name} FAILED{f' ({what})' if what else ''}: {str(res.get('error') or res.get('message') or 'it reported a failure')[:240]}")
                    continue
                if t_name == "computer_run_command":
                    lines.append(f"  - Command `$ {args.get('command')}`: exit {res.get('exit_code')}, output: {str(res.get('stdout', '') or res.get('stderr', ''))[:200]}")
                elif t_name == "computer_directory_tree":
                    st = res.get("stats", {})
                    lines.append(f"  - Directory Tree '{res.get('name', 'folder')}': {st.get('total_dirs', 0)} dirs, {st.get('total_files', 0)} files ({st.get('total_size_formatted', '')}), key files: {', '.join([f.split('/')[-1] for f in st.get('key_files', [])[:4]])}")
                    if res.get("tree"):
                        tree_prev = "\n".join(res["tree"].splitlines()[:15])
                        lines.append(f"    Tree Preview:\n{tree_prev}")
                elif t_name == "computer_read_file":
                    lines.append(f"  - Read File '{res.get('name') or os.path.basename(str(res.get('filepath') or ''))}' "
                                 f"({res.get('lines_returned') or res.get('lines_count', 0)} lines)")
                    if res.get("content"):
                        snippet = "\n".join(res["content"].splitlines()[:10])
                        lines.append(f"    Content Preview:\n{snippet}")
                    if res.get("first_lines"):       # a long file: its start and its end (tools.py), not just the start
                        lines.append("    Start of the file:\n" + "\n".join(res["first_lines"].splitlines()[:8]))
                    if res.get("last_lines"):
                        lines.append(f"    END of the file (the last lines, numbered):\n{res['last_lines']}")
                elif t_name == "computer_edit_file":
                    lines.append(f"  - Surgically Edited '{res.get('name', '')}': line {res.get('line_number', 1)} ({res.get('occurrences_replaced', 1)} replaced, {res.get('lines_delta', 0):+d} lines delta)")
                elif t_name == "computer_write_file":
                    lines.append(f"  - Wrote File '{res.get('name', '')}': {res.get('lines_count', 0)} lines ({res.get('size_bytes', 0)} bytes)")
                elif t_name == "computer_batch_organize":
                    lines.append(f"  - Batch Organized '{args.get('source_dir')}': {res.get('total_files_moved', 0)} files moved into {res.get('breakdown')}")
                elif t_name == "computer_open_item":
                    lines.append(f"  - Opened '{args.get('target')}' with {args.get('app_name', 'default app')}")
                elif t_name == "computer_run_applescript":
                    lines.append(f"  - AppleScript: exit {res.get('exit_code')}, output: {str(res.get('output', ''))[:150]}")
                elif t_name == "computer_hardware_overview":
                    # A Mac's and a PC's fields differ (macos_version/memory_gb vs os/ram_gb; battery shape): say what's there.
                    bat = res.get("battery") or {}
                    pct = bat.get("percentage", bat.get("percent"))
                    plugged = bat.get("state") or ("plugged in" if bat.get("plugged_in") else "on battery" if bat else "")
                    gpus = "; ".join(f"{g.get('name')} ({g.get('memory_gb')} GB{'' if g.get('dedicated') else ', shared'})"
                                     for g in res.get("graphics") or [])
                    parts = [res.get("macos_version") or res.get("os"), res.get("chip"), f"{res.get('cpu_cores')} cores" if res.get("cpu_cores") else "",
                             f"memory {res.get('memory_gb') or res.get('ram_gb')} GB" if (res.get("memory_gb") or res.get("ram_gb")) else "",
                             f"graphics: {gpus}" if gpus else "", f"battery {pct}% ({plugged})" if pct is not None else ""]
                    lines.append("  - Hardware Overview: " + ", ".join(p for p in parts if p))
                elif t_name == "computer_find_files":
                    f_names = [f.get("name", "") for f in res.get("results", [])[:5]]
                    lines.append(f"  - Spotlight Found {res.get('count', 0)} files matching '{args.get('query')}': {', '.join(f_names)}")
                elif t_name == "computer_inspect_port":
                    p_status = "In Use" if res.get("in_use") else "Free"
                    lines.append(f"  - Port {res.get('port')}: {p_status} ({len(res.get('processes', []))} process(es))")
                elif t_name == "computer_manage_file":
                    lines.append(f"  - File Operation '{args.get('action')}': {args.get('src')} ({res.get('note', 'success')})")
                elif t_name == "computer_clipboard":
                    text = res.get("text")
                    lines.append(f"  - System Clipboard: {res.get('chars', 0)} characters"
                                 + (f'; it says: "{str(text)[:1500]}"' if text else " written" if args.get("action") == "write" else ""))
                elif t_name == "computer_start_job":
                    lines.append(f"  - Daemon Job '{res.get('name')}' (PID {res.get('pid')}): {res.get('status')}")
                elif t_name == "computer_inspect_system":
                    mem, disk, net = res.get("memory") or {}, res.get("disk") or {}, res.get("network") or {}
                    procs = "; ".join(f"{p.get('command')} ({p.get('rss_mb')} MB, {p.get('cpu_pct')}% CPU)" for p in (res.get("top_processes") or [])[:8])
                    by_mem = sorted(res.get("top_processes") or [], key=lambda p: -(p.get("rss_mb") or 0))[:5]
                    lines.append(f"  - Host Specs: {res.get('os')} ({res.get('arch')}), RAM: {mem.get('used_gb')}/{mem.get('total_gb')} GB used "
                                 f"({mem.get('free_gb')} GB free), CPU Load: {res.get('cpu_load')}, disk: {disk.get('free_gb')} GB free of {disk.get('total_gb')} GB"
                                 + (f", local IP address: {net.get('local_ip')}" if net.get("local_ip") else "")
                                 + (f" (adapters: {', '.join(a['adapter'] + ' ' + a['ip'] for a in net.get('adapters') or [])})" if net.get("adapters") else "")
                                 + (f"\n    Busiest programs (CPU first): {procs}" if procs else "")
                                 + (f"\n    Most memory: {'; '.join(str(p.get('command')) + ' ' + str(p.get('rss_mb')) + ' MB' for p in by_mem)}" if by_mem else ""))
                elif t_name == "computer_wait_for":
                    state = "condition met" if res.get("met") else f"NOT met: {res.get('error')}"
                    lines.append(f"  - Waited {res.get('waited_sec')} s for {res.get('condition')} ({json.dumps({k: v for k, v in args.items() if k != 'condition'})[:120]}): {state}; {res.get('detail', '')}")
                elif t_name == "computer_check_job":
                    out = "\n".join(res.get("new_output") or [])[-600:]
                    lines.append(f"  - Job '{res.get('name')}' ({res.get('job_id')}): status {res.get('status')}, exit code {res.get('exit_code')}, ran {res.get('running_for_sec')} s"
                                 + (f"\n    Output:\n{out}" if out else ", no new output") + (f"\n    Note: {res['note']}" if res.get("note") else ""))
                elif t_name == "computer_stop_job":
                    lines.append(f"  - Stopped job {res.get('job_id')}: {res.get('status')}")
                elif t_name == "computer_list_jobs":
                    jobs = res.get("jobs") or []
                    lines.append(f"  - Background jobs: " + (", ".join(f"{j.get('name')} ({j.get('id')}, {j.get('status')})" for j in jobs) or "none"))
                else:
                    # Never drop a result just because it has no custom line: the writer would only
                    # see older entries (it reported a finished job as "still running").
                    compact = {k: v for k, v in res.items() if k not in ("screenshot_uri", "raw", "html")} if isinstance(res, dict) else res
                    lines.append(f"  - {t_name}: {json.dumps(compact, default=str)[:400]}")

        # Tools outside the families above (doctor, memory, knowledge graph, misc.): show their results
        # too, so the writer never works from a partial picture.
        families = ("browser_", "computer_", "fs_", "media_", "creator_")
        others = [t for t in getattr(self, "tool_audit_trail", []) if not str(t.get("tool", "")).startswith(families)
                  and isinstance(t.get("result"), dict) and not (t["result"].get("results") or t["result"].get("answer"))]
        if others:
            lines.append("* Other tool results:")
            for t in others[-6:]:
                compact = {k: v for k, v in t["result"].items() if k not in ("raw", "html", "screenshot_uri")}
                if str(t.get("tool", "")).startswith("mcp__"):
                    # A connected app's list (places, messages, events…): all of it the answer can use, not the first two.
                    text = re.sub(r"\s*\n\s*", " ", str(compact.get("content") or json.dumps(compact, default=str)))
                    lines.append(f"  - {t['tool']}: {text[:3000]}")
                else:
                    lines.append(f"  - {t['tool']}: {json.dumps(compact, default=str)[:500]}")

        if hasattr(self, "fs_telemetry") and self.fs_telemetry:
            lines.append(f"* Controlled Workspace Filesystem Telemetry:")
            for ft in self.fs_telemetry[-5:]:
                t_name = ft.get("tool", "")
                res = ft.get("result", {}) or {}
                args = ft.get("args", {}) or {}
                if t_name == "fs_list_workspace_files":
                    lines.append(f"  - Directory Tree ({res.get('total_files', 0)} files in {res.get('subpath') or 'root'}): {', '.join([f.get('name', '') for f in res.get('files', [])[:8]])}")
                elif t_name == "fs_read_workspace_file":
                    preview = str(res.get('content', '') or '')[:300].strip()
                    lines.append(f"  - File Content '{res.get('file_path')}': {res.get('total_lines', 0)} lines, preview: {preview}...")
                elif t_name == "fs_search_workspace":
                    lines.append(f"  - Workspace Search '{args.get('query')}': {res.get('total_matches', 0)} matches found across {len(res.get('matches', []))} files")

        if hasattr(self, "visual_telemetry") and self.visual_telemetry:
            lines.append(f"* Multimodal Visual Assets Ingested: {len(self.visual_telemetry)} image(s) processed for direct scene perception.")

        if hasattr(self, "doc_telemetry") and self.doc_telemetry:
            lines.append(f"* Attached Document Intelligence ({len(self.doc_telemetry)} document(s)):")
            for dt in self.doc_telemetry[:4]:
                lines.append(f"  - Document '{dt.get('name')}': {dt.get('page_count', 1)} pages, {dt.get('size_formatted') or dt.get('size_bytes')}")

        if hasattr(self, "media_telemetry") and self.media_telemetry:
            lines.append(f"* Local Media Studio Assets ({len(self.media_telemetry)} items):")
            for mt in self.media_telemetry[-5:]:
                m_url = mt.get("media_url") or mt.get("relative_url")
                if m_url or "output_path" in mt:
                    lines.append(f"  - [MEDIA ASSET] Filename: {mt.get('filename')}, Preview URL: {m_url}, On-Device Path: {mt.get('output_path')}, Motion: {mt.get('motion', 'none')}")
                else:
                    lines.append(f"  - [{mt.get('media_type', 'media').upper()}] '{mt.get('title')}' ({mt.get('duration')}, {mt.get('file_size_mb')} MB) -> {mt.get('file_path')}")

        if self.sources:
            lines.append(f"* Verified Web Sources ({len(self.sources)}):")
            # Enough of each source for the answer to actually use (5 x 120 chars dropped most facts).
            for s in self.sources[:10]:
                lines.append(f"  - [{s.get('title')}]({s.get('url')}): {s.get('snippet', '')[:400]}")

        return "\n".join(lines)

    def to_markdown_summary(self) -> str:
        """Alias for to_compact_summary to provide markdown representation."""
        return self.to_compact_summary()
