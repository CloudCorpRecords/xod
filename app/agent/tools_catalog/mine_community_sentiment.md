# Tool Documentation: `mine_community_sentiment`

## Overview
Mines real developer sentiment, community critiques, and organic brand perception across Hacker News (Algolia API) and technical forums. Analyzes upvotes, comment sentiment, common complaints, and user praise.

## When to Use
- User asks what people really think of a startup or product.
- Looking for candid developer complaints, pricing backlash, or praise.
- Competitive positioning to understand user friction points.

## When NOT to Use
- Pure financial or technical domain audits where sentiment is not requested.
- Casual greetings.

## Parameters
- `target` (string, required): Startup or library name (e.g. `'v0'`, `'cursor'`, `'supabase'`).
- `query` (string, optional): Specific discussion topic.

## Key Returns
- `sentiment_summary`: Predominantly Positive, Mixed / Critical, or Neutral.
- `hacker_news_stories`: High-ranking discussion threads with point and comment counts.
- `forum_discussions`: Web forum discussions and feedback excerpts.
