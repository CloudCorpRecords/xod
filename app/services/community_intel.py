import logging
import httpx
from typing import Dict, Any, List, Optional
from app.services.you_search import you_client

logger = logging.getLogger("community_intel")

class CommunityIntelService:
    """
    Mines developer and user sentiment from Hacker News (Algolia API)
    and social/forum discussions (Reddit, ProductHunt via You.com).
    """

    def __init__(self):
        self.hn_api_url = "https://hn.algolia.com/api/v1/search"

    async def mine(self, target: str, query: Optional[str] = None) -> Dict[str, Any]:
        """
        Gathers community sentiment and developer reactions for a given startup or technology.
        """
        search_term = (query or target).strip()
        results: Dict[str, Any] = {
            "target": target,
            "search_term": search_term,
            "hacker_news_stories": [],
            "forum_discussions": [],
            "sentiment_summary": "Neutral",
            "key_themes": [],
            "total_community_signals": 0
        }

        # 1. Query Hacker News Algolia REST API (Free, zero-auth, high reliability)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    self.hn_api_url,
                    params={
                        "query": search_term,
                        "tags": "story",
                        "hitsPerPage": 5
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", [])
                    for h in hits:
                        title = h.get("title", "")
                        points = h.get("points") or 0
                        comments = h.get("num_comments") or 0
                        story_url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                        hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                        created = h.get("created_at", "")[:10]

                        results["hacker_news_stories"].append({
                            "title": title,
                            "points": points,
                            "comments": comments,
                            "date": created,
                            "url": story_url,
                            "hn_discussion": hn_url
                        })
        except Exception as e:
            logger.warning(f"HN Algolia search failed for '{search_term}': {e}")

        # 2. Query You.com for targeted Reddit / forum discussions
        try:
            forum_query = f'"{target}" reviews feedback complaints OR pricing Reddit'
            you_res = await you_client.search(query=forum_query, count=3)
            for res in you_res.get("results", []):
                results["forum_discussions"].append({
                    "title": res.get("title", ""),
                    "snippet": res.get("snippet", "")[:300],
                    "url": res.get("url", "")
                })
        except Exception as e:
            logger.warning(f"Forum sentiment search failed for '{target}': {e}")

        # 3. Analyze sentiment signals
        hn_count = len(results["hacker_news_stories"])
        forum_count = len(results["forum_discussions"])
        results["total_community_signals"] = hn_count + forum_count

        # Extract themes and calculate basic sentiment heuristic
        all_text = " ".join([
            s.get("title", "") for s in results["hacker_news_stories"]
        ] + [
            f.get("title", "") + " " + f.get("snippet", "") for f in results["forum_discussions"]
        ]).lower()

        positive_words = ["love", "great", "best", "fast", "awesome", "solid", "recommend", "growth", "raised", "success"]
        negative_words = ["expensive", "bug", "crash", "scam", "slow", "churn", "alternatives", "problem", "hate", "issue", "pricing increase"]

        pos_count = sum(all_text.count(w) for w in positive_words)
        neg_count = sum(all_text.count(w) for w in negative_words)

        if pos_count > neg_count * 1.5 and pos_count >= 2:
            results["sentiment_summary"] = "Positive (Strong Community Goodwill)"
        elif neg_count > pos_count * 1.5 and neg_count >= 2:
            results["sentiment_summary"] = "Critical (User Friction / Pricing Complaints)"
        elif pos_count > 0 or neg_count > 0:
            results["sentiment_summary"] = "Mixed (Active Debate / Product Feedback)"
        else:
            results["sentiment_summary"] = "Neutral (Moderate Discussion Volume)"

        # Highlight key themes detected
        themes = []
        if "pricing" in all_text or "expensive" in all_text or "cost" in all_text:
            themes.append("Pricing & Value Scrutiny")
        if "alternative" in all_text or "competitor" in all_text or "vs" in all_text:
            themes.append("Competitor Comparison Active")
        if "show hn" in all_text or "launch" in all_text:
            themes.append("Developer Community Launch Tracked")
        if "ai" in all_text:
            themes.append("AI Capabilities Highlighted")
        results["key_themes"] = themes

        return results

community_intel_service = CommunityIntelService()
