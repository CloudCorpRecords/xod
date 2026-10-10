import logging
import httpx
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("github_auditor")

class GitHubAuditorService:
    """
    Audits public GitHub repositories for open source startups, developer tools,
    and codebase velocity signals (stars, forks, commit activity, health).
    """

    def __init__(self):
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "XOD-SaaS-Intelligence-Researcher/1.0"
        }

    async def audit(self, target: str, repo: Optional[str] = None) -> Dict[str, Any]:
        """
        Audits a GitHub repository by explicit repo slug or company/tool keyword.
        """
        # 1. Determine repo path
        repo_slug = None
        if repo:
            repo_slug = self._extract_slug(repo)
        elif "github.com/" in target:
            repo_slug = self._extract_slug(target)

        async with httpx.AsyncClient(timeout=8.0, headers=self.headers) as client:
            repo_data = None

            # If we have an exact repo slug, fetch it directly
            if repo_slug:
                url = f"https://api.github.com/repos/{repo_slug}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    repo_data = resp.json()
                elif resp.status_code == 404:
                    return {"success": False, "error": f"Repository '{repo_slug}' not found on GitHub."}

            # Otherwise search by target keyword
            if not repo_data:
                clean_target = re.sub(r'https?://(?:www\.)?', '', target).split('/')[0].split('.')[0]
                search_url = "https://api.github.com/search/repositories"
                resp = await client.get(search_url, params={"q": clean_target, "sort": "stars", "order": "desc", "per_page": 1})
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        repo_data = items[0]

            if not repo_data:
                return {
                    "success": False,
                    "target": target,
                    "message": f"No public GitHub repository found matching '{target}'."
                }

            # 2. Extract metrics and activity status
            pushed_at_str = repo_data.get("pushed_at") or ""
            days_since_push = None
            activity_status = "Unknown"

            if pushed_at_str:
                try:
                    pushed_dt = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    days_since_push = (now_dt - pushed_dt).days
                    if days_since_push <= 7:
                        activity_status = "High Velocity (Pushed in last 7 days)"
                    elif days_since_push <= 30:
                        activity_status = "Active Maintenance (Pushed in last 30 days)"
                    elif days_since_push <= 90:
                        activity_status = "Moderate (Pushed in last 90 days)"
                    else:
                        activity_status = f"Stale / Low Activity ({days_since_push} days since push)"
                except Exception:
                    pass

            stars = repo_data.get("stargazers_count", 0)
            forks = repo_data.get("forks_count", 0)
            open_issues = repo_data.get("open_issues_count", 0)

            # Health indicator
            if stars >= 5000 and (days_since_push is not None and days_since_push <= 30):
                health = "Institutional Grade Open Source (Tier 1 Community)"
            elif stars >= 1000:
                health = "Strong Community Traction"
            elif stars >= 100:
                health = "Growing Validation"
            else:
                health = "Early Stage / Niche Repo"

            return {
                "success": True,
                "name": repo_data.get("name"),
                "full_name": repo_data.get("full_name"),
                "repo_url": repo_data.get("html_url"),
                "description": repo_data.get("description"),
                "stars": stars,
                "forks": forks,
                "open_issues": open_issues,
                "language": repo_data.get("language") or "N/A",
                "license": repo_data.get("license", {}).get("spdx_id") if repo_data.get("license") else "None",
                "is_fork": repo_data.get("fork", False),
                "is_archived": repo_data.get("archived", False),
                "days_since_last_push": days_since_push,
                "activity_status": activity_status,
                "health_rating": health,
                "topics": repo_data.get("topics", [])
            }

    def _extract_slug(self, raw: str) -> Optional[str]:
        raw = raw.strip()
        match = re.search(r"github\.com/([^/]+/[^/]+)", raw)
        if match:
            return match.group(1).rstrip("/")
        parts = raw.split("/")
        if len(parts) == 2:
            return raw
        return None

github_auditor_service = GitHubAuditorService()
