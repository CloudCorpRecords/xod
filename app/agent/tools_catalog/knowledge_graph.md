# Tool Documentation: Knowledge Graph Suite (`query_knowledge_graph` / `update_knowledge_graph_entity`)

## Overview
Interacts with Xod's local, persistent SQLite SaaS Knowledge Graph (`data/xod.db`). Allows the agent to look up previously researched startups, cross-reference historical MRR metrics, link competitors, and self-update due diligence tags.

## When to Use
- `query_knowledge_graph`: Check if a startup, category, or tech stack was already investigated in past sessions.
- `update_knowledge_graph_entity`: Add due diligence tags (e.g. `'plg-moat'`, `'bootstrapped-star'`), link direct competitors, and record takeaways.

## Parameters
- `query` (string, optional, for `query_knowledge_graph`): Search term.
- `slug` (string, required, for `update_knowledge_graph_entity`): Entity slug.
- `tags` / `notes` / `competitor_slugs`: Metadata to store.
