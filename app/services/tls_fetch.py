"""
Fetching a site to audit it, with certificate checks on.

Audit tools (tech_auditor, traffic_intel) look at sites whose HTTPS may be broken, and a broken certificate is
itself worth reporting. So a request checks the certificate first; only when that fails on the certificate does it
fetch again without the check, and the result says so (tls_certificate: "not valid: …") instead of hiding it.

  async with tls_fetch.client(timeout=10, follow_redirects=True) as c:
      res = await c.get(url, headers=...)
  c.tls_note     "valid", "not valid: <reason>", or "" (plain http)
"""
import re
import ssl
from typing import Any

import httpx


def _clean(reason: str) -> str:
    return re.sub(r"\s*\(_ssl\.c:\d+\)", "", reason).strip()


def _cert_problem(e: BaseException) -> str:
    """The certificate reason when this error is a failed certificate check, else ""."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, ssl.SSLCertVerificationError):
            return _clean(getattr(e, "verify_message", "") or str(e))
        text = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower():
            return _clean(text.split("certificate verify failed:")[-1]) or "certificate verify failed"
        e = e.__cause__ or e.__context__
    return ""


class client:
    def __init__(self, **kwargs: Any):
        kwargs.pop("verify", None)
        self._kwargs = kwargs
        self._strict = httpx.AsyncClient(**kwargs)
        self._lax = None
        self.tls_note = ""

    async def __aenter__(self) -> "client":
        await self._strict.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._strict.__aexit__(*exc)
        if self._lax is not None:
            await self._lax.__aexit__(*exc)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            res = await self._strict.get(url, **kwargs)
            self.tls_note = "valid" if str(res.url).startswith("https://") else ""
            return res
        except httpx.ConnectError as e:
            why = _cert_problem(e)
            if not why:
                raise
            if self._lax is None:
                self._lax = httpx.AsyncClient(**self._kwargs, verify=False)   # nosec B501: only after a failed check, and reported
                await self._lax.__aenter__()
            self.tls_note = f"not valid: {why}"
            return await self._lax.get(url, **kwargs)
