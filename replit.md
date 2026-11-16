# Xod development on Replit

Preserve the existing Python/FastAPI and Electron desktop structure. This is
a local-first, open-source assistant. Muse auto-approval is a core feature,
not an optional integration to remove during cleanup. Users bring their own
provider keys or use local models.

## Preview

Run the configured **Xod web preview** workflow. It uses:

```
XOD_LIGHTWEIGHT=1 XOD_MUSE_APP_DRY_RUN=1 XOD_NO_UPDATE_CHECK=1 python -m uvicorn app.main:app --host 0.0.0.0 --port 5000
```

The preview is a development interface, not a production hosting configuration.
It cannot control the user's local Mac/Windows desktop. Lightweight mode skips
optional background services; dry run disables desktop approval clicks.
Dependencies are in requirements.txt. Do not use run.sh here: it creates a venv
and binds to loopback for desktop development.

## Approval checks

```
python tests/smoke/run_muse_app_test.py
python tests/partner/run_approval_reliability_test.py
python tests/partner/run_partner_delegation_test.py
python tests/partner/run_partner_ax_test.py
```

Tests use isolated temporary state and fake desktop trees. The real
CoreFoundation permission-prompt check is explicitly skipped off macOS.

## Model testing

The requested live test model is gpt-6.1-terra through Replit-managed OpenAI
access. No available OpenAI integration was returned by discovery in this
workspace; no live-model result has been obtained. Do not invent credentials,
silently substitute models, or write credentials into project files.
The existing BYOK and local-provider implementation has not been replaced.
