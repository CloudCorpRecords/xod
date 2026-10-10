# Tool Documentation: `recon_shadow_subdomains`

## Overview
Mines Certificate Transparency (CT) logs (Certspotter) and passive DNS telemetry (HackerTarget) to uncover unlisted subdomains, internal staging environments, private APIs, real-time WebSocket clusters, and AI/agent protocol endpoints without active port scanning.

## When to Use
- User asks about architectural scale, microservices, backend topology, or unlisted endpoints.
- Discovering shadow environments (e.g. `staging.`, `dev.`, `internal.`, `auth.`, `billing.`).
- Checking for real-time WebSocket infrastructure (`ws.`, `realtime.`, `socket.`) or AI protocol infrastructure (`mcp.`, `agent.`, `ai.`).

## When NOT to Use
- When no domain name is available or for company names without a known URL.
- Casual greetings or general discussions.

## Parameters
- `domain` (string, required): Root domain of the startup to inspect (e.g. `'postiz.com'`, `'stan.store'`, `'cal.com'`).

## Key Returns
- `total_subdomains_discovered` (int): Number of unique discovered subdomains.
- `scale_verdict` (str): Microservices Enterprise, Distributed Multi-Service, Moderate Service Footprint, or Monolith.
- `architecture_signals` (dict): Boolean flags for `has_realtime_websockets`, `has_ai_or_mcp_endpoints`, `is_microservice_architecture`, `has_internal_staging`.
- `categorized_subdomains` (dict): Subdomains grouped by role (api, auth, realtime, internal, docs).
