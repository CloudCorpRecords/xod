# Tool Documentation: `audit_talent_and_hiring`

## Overview
Mines public Applicant Tracking System (ATS) job boards (Ashby, Greenhouse, Lever) and probes career page redirects to evaluate real-time hiring velocity, department ratios (Engineering vs Sales/GTM), internal tech requirements, leadership hires, and headcount burn rate.

## When to Use
- User asks about a startup's headcount growth, open roles, hiring velocity, or burn rate.
- Investigating internal tech stacks that aren't visible on the frontend (e.g. backend languages, databases, Kubernetes mentioned in job descriptions).
- Evaluating whether a company is in expansion mode (heavy engineering & sales hiring) vs cash preservation / hiring freeze.

## When NOT to Use
- Casual greetings or general Q&A.
- Micro-SaaS projects with 1-2 solo founders that do not use formal enterprise ATS job boards.

## Parameters
- `target` (string, required): Target company name (e.g. `'Cursor'`, `'Linear'`, `'Supabase'`, `'Anthropic'`).
- `domain` (string, optional): Website domain to probe career page redirects (e.g. `'cursor.com'`).

## Key Returns
- `total_open_roles` (int): Number of active public job requisitions.
- `ats_provider` (str): ATS platform detected (Ashby, Greenhouse, Lever).
- `department_breakdown` (dict): Roles categorized by department (Engineering & Product, Sales & GTM, Operations).
- `internal_tech_signals` (list): Internal libraries, frameworks, or cloud providers extracted from job specs.
- `hiring_velocity_verdict` (str): Hyper-Growth Velocity, Active Expansion, Steady Maintenance, or Hiring Pause.
- `burn_rate_signal` (str): Estimated headcount cash burn pressure based on hiring volume.
