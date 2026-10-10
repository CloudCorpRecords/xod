# Tool Documentation: `audit_package_ecosystem`

## Overview
Audits ground-truth developer adoption and production consumption across package registries (NPM, PyPI, and Docker Hub). Verifies real monthly SDK installations and container pulls to separate genuine production utilization from vanity GitHub stars.

## When to Use
- Evaluating developer tooling, API infrastructure, SDK libraries, or open-source software.
- Validating whether a developer tool has real-world adoption or just marketing hype.
- Assessing container deployment traction across Docker Hub repositories.

## When NOT to Use
- Pure B2B enterprise software or closed-source consumer apps that do not distribute public SDK packages or containers (e.g. consumer marketplaces).
- Casual greetings or unrelated general queries.

## Parameters
- `target` (string, required): Library or startup name (e.g. `'postiz'`, `'supabase'`, `'fastapi'`, `'calcom'`).
- `package_name` (string, optional): Specific package identifier if known (e.g. `'@supabase/supabase-js'`).

## Key Returns
- `total_monthly_downloads` (int): Combined monthly downloads across NPM and PyPI.
- `total_docker_pulls` (int): Total lifetime container pulls on Docker Hub.
- `adoption_scale_verdict` (str): Tier rating (e.g. Tier 1 Institutional Infrastructure, High-Volume Base).
- `npm_packages` / `pypi_packages` / `docker_repositories` (list): Top matching packages with exact download statistics.
