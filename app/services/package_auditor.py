import logging
import httpx
import re
import asyncio
from typing import Dict, Any, List, Optional

logger = logging.getLogger("package_auditor")

class PackageAuditorService:
    """
    Audits ground-truth developer adoption and production consumption across
    package registries (NPM, PyPI, and Docker Hub).
    Reveals real SDK installations and container pulls vs vanity GitHub stars.
    """

    def __init__(self):
        self.npm_search_url = "https://registry.npmjs.org/-/v1/search"
        self.npm_downloads_url = "https://api.npmjs.org/downloads/point/last-month"
        self.pypi_stats_url = "https://pypistats.org/api/packages"
        self.pypi_json_url = "https://pypi.org/pypi"
        self.docker_search_url = "https://hub.docker.com/v2/search/repositories"

    async def audit_package_ecosystem(self, target: str, package_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes multi-registry queries across NPM, PyPI, and Docker Hub.
        """
        clean_target = target.strip().lower()
        search_query = package_name or clean_target

        async with httpx.AsyncClient(timeout=7.0, headers={"User-Agent": "XodPackageAuditor/1.0"}) as client:
            npm_task = self._audit_npm(client, search_query)
            pypi_task = self._audit_pypi(client, search_query)
            docker_task = self._audit_docker(client, search_query)

            npm_results, pypi_results, docker_results = await asyncio.gather(
                npm_task, pypi_task, docker_task, return_exceptions=True
            )

        npm_pkgs = npm_results if isinstance(npm_results, list) else []
        pypi_pkgs = pypi_results if isinstance(pypi_results, list) else []
        docker_repos = docker_results if isinstance(docker_results, list) else []

        # Aggregate total monthly downloads and pulls
        npm_monthly_total = sum(p.get("monthly_downloads", 0) for p in npm_pkgs)
        pypi_monthly_total = sum(p.get("monthly_downloads", 0) for p in pypi_pkgs)
        total_monthly_downloads = npm_monthly_total + pypi_monthly_total

        docker_total_pulls = sum(d.get("pull_count", 0) for d in docker_repos)

        # Classify adoption tier
        if total_monthly_downloads >= 1_000_000 or docker_total_pulls >= 10_000_000:
            adoption_verdict = "Institutional Infrastructure (Tier 1 Production Adoption)"
            adoption_color = "emerald"
        elif total_monthly_downloads >= 100_000 or docker_total_pulls >= 1_000_000:
            adoption_verdict = "High-Volume Developer Adoption (Strong Production Base)"
            adoption_color = "brand"
        elif total_monthly_downloads >= 10_000 or docker_total_pulls >= 50_000:
            adoption_verdict = "Active Traction (Established Package Distribution)"
            adoption_color = "blue"
        elif total_monthly_downloads >= 1_000 or docker_total_pulls >= 5_000:
            adoption_verdict = "Early Developer Footprint (Growing Traction)"
            adoption_color = "amber"
        elif total_monthly_downloads > 0 or docker_total_pulls > 0:
            adoption_verdict = "Nascent / Experimental Usage (Early Stage)"
            adoption_color = "zinc"
        else:
            adoption_verdict = "No Public Package Registry Footprint Detected"
            adoption_color = "zinc"

        # Ecosystem summary
        summary_parts = []
        if total_monthly_downloads > 0:
            summary_parts.append(f"{self._format_number(total_monthly_downloads)} monthly package downloads")
        if docker_total_pulls > 0:
            summary_parts.append(f"{self._format_number(docker_total_pulls)} Docker container pulls")

        ecosystem_summary = " & ".join(summary_parts) if summary_parts else "No public SDK or container distribution detected"

        return {
            "target": target,
            "total_monthly_downloads": total_monthly_downloads,
            "total_docker_pulls": docker_total_pulls,
            "adoption_scale_verdict": adoption_verdict,
            "adoption_color": adoption_color,
            "ecosystem_summary": ecosystem_summary,
            "npm_packages": npm_pkgs[:5],
            "pypi_packages": pypi_pkgs[:3],
            "docker_repositories": docker_repos[:3]
        }

    async def _audit_npm(self, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        """Queries NPM registry search and fetches monthly downloads for top matching packages."""
        packages = []
        try:
            r = await client.get(f"{self.npm_search_url}?text={query}&size=4")
            if r.status_code == 200:
                data = r.json()
                for obj in data.get("objects", []):
                    pkg = obj.get("package", {})
                    name = pkg.get("name")
                    if not name:
                        continue

                    # Fetch downloads
                    downloads = 0
                    try:
                        down_r = await client.get(f"{self.npm_downloads_url}/{name}")
                        if down_r.status_code == 200:
                            downloads = down_r.json().get("downloads", 0)
                    except Exception:
                        pass

                    packages.append({
                        "name": name,
                        "version": pkg.get("version"),
                        "description": (pkg.get("description") or "")[:100],
                        "monthly_downloads": downloads,
                        "registry_url": f"https://www.npmjs.com/package/{name}"
                    })
        except Exception as e:
            logger.debug(f"NPM search failed for {query}: {e}")
        return packages

    async def _audit_pypi(self, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        """Queries PyPIStats and PyPI JSON API for Python library adoption."""
        packages = []
        # Test exact package name and kebab-case
        candidates = [query.lower(), query.lower().replace(" ", "-"), query.lower().replace("-", "")]
        seen = set()

        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)

            try:
                # 1. Download stats
                stat_r = await client.get(f"{self.pypi_stats_url}/{cand}/recent")
                if stat_r.status_code == 200:
                    data = stat_r.json().get("data", {})
                    last_month = data.get("last_month", 0)

                    # 2. Package metadata
                    info_r = await client.get(f"{self.pypi_json_url}/{cand}/json")
                    version = ""
                    desc = ""
                    if info_r.status_code == 200:
                        info = info_r.json().get("info", {})
                        version = info.get("version", "")
                        desc = (info.get("summary") or "")[:100]

                    packages.append({
                        "name": cand,
                        "version": version,
                        "description": desc,
                        "monthly_downloads": last_month,
                        "registry_url": f"https://pypi.org/project/{cand}/"
                    })
                    break  # Found exact package
            except Exception as e:
                logger.debug(f"PyPI check failed for {cand}: {e}")
        return packages

    async def _audit_docker(self, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        """Queries Docker Hub repository search for container pulls and stars."""
        repos = []
        try:
            r = await client.get(f"{self.docker_search_url}?query={query}&page_size=3")
            if r.status_code == 200:
                data = r.json()
                for item in data.get("results", []):
                    repos.append({
                        "repository": item.get("repo_name"),
                        "star_count": item.get("star_count", 0),
                        "pull_count": item.get("pull_count", 0),
                        "description": (item.get("short_description") or "")[:100],
                        "hub_url": f"https://hub.docker.com/r/{item.get('repo_name')}"
                    })
        except Exception as e:
            logger.debug(f"Docker Hub search failed for {query}: {e}")
        return repos

    def _format_number(self, num: int) -> str:
        if num >= 1_000_000_000:
            return f"{num / 1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num / 1_000:.1f}k"
        return str(num)

package_auditor_service = PackageAuditorService()
