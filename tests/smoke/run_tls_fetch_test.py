"""
Audit fetches keep certificate checks on (services/tls_fetch.py), with no network:
  - a good HTTPS answer: fetched once, tls_certificate "valid"
  - a failed certificate check: fetched again without the check, and reported as "not valid: <reason>"
  - any other connection error is raised as it is, never retried without the check
  - the two audit tools no longer turn certificate checks off themselves
  .venv/bin/python tests/smoke/run_tls_fetch_test.py
"""
import asyncio
import ssl
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def cert_error(msg):
    inner = ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {msg} (_ssl.c:1010)")
    inner.verify_message = msg
    try:
        raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as e:
        return e


async def main():
    from app.services import tls_fetch
    calls = []
    real = httpx.AsyncClient

    def make(mode):
        def handler(request):
            return httpx.Response(200, text="hi", request=request)

        class Fake(real):
            def __init__(self, **kw):
                strict = kw.get("verify", True) is not False
                calls.append("strict" if strict else "lax")
                super().__init__(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "verify"})
                self._strict_mode = strict

            async def get(self, url, **kw):
                if self._strict_mode and mode == "bad-cert":
                    raise cert_error("certificate has expired")
                if self._strict_mode and mode == "down":
                    raise httpx.ConnectError("connection refused")
                return await super().get(url, **kw)
        return Fake

    print("T1 certificate checks")
    tls_fetch.httpx.AsyncClient = make("ok")
    async with tls_fetch.client(timeout=5) as c:
        r = await c.get("https://good.example")
    check("a good certificate: fetched once, reported valid", r.status_code == 200 and c.tls_note == "valid" and calls == ["strict"], str(calls))
    calls.clear()
    tls_fetch.httpx.AsyncClient = make("bad-cert")
    async with tls_fetch.client(timeout=5) as c:
        r = await c.get("https://expired.example")
    check("a failed certificate: fetched without the check, and says so", r.status_code == 200 and calls == ["strict", "lax"]
          and c.tls_note == "not valid: certificate has expired", f"{calls} {c.tls_note!r}")
    calls.clear()
    tls_fetch.httpx.AsyncClient = make("down")
    try:
        async with tls_fetch.client(timeout=5) as c:
            await c.get("https://down.example")
        raised = False
    except httpx.ConnectError:
        raised = True
    check("another connection error is raised, never retried without the check", raised and calls == ["strict"], str(calls))
    tls_fetch.httpx.AsyncClient = real

    print("T2 the audit tools")
    for f in ("app/services/tech_auditor.py", "app/services/traffic_intel.py"):
        text = (ROOT / f).read_text()
        check(f"{f.split('/')[-1]}: no verify=False of its own, uses tls_fetch", "verify=False" not in text and "tls_fetch.client(" in text)


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
