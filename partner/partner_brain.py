#!/usr/bin/env python3
"""
partner_brain: the Partner's judgment layer (v1.2), sitting ON TOP of the
deterministic watchdog floor, never replacing it.

The floor (probe cadence, failure gate, revival, backoff, the planned and
unplanned cap accounting, HOLD) decides everything it decided before this
module existed. The brain is consulted only at four ambiguity points the
watchdog names (see TRIGGERS), and it answers with exactly one decision
from a strict enum plus a one-line reason. Anything malformed, missing,
or unparseable is a brain malfunction: the floor proceeds exactly as
today and the malfunction is logged. If the mode is off, the key is
missing, or the API is down, the brain is simply never in the way.

Modes (partner_brain_mode in the Partner config, default shadow):
  off    - never consulted.
  shadow - consulted at triggers; the decision and reason are logged with
           a shadow marker; the floor acts exactly as today.
  active - consulted, and the watchdog may apply the bounded effects the
           decision names (defer one restart by one probe cycle, clear a
           cap HOLD, escalate). A hard hourly restart ceiling in
           xod_partner binds no matter what the brain decides.

The brain's API key is the Partner's own: Xod sends one key profile's key
once over the token-authenticated loopback channel (POST /brain/key) and
the Partner stores it in its OWN Keychain item (service
"dev.xod.partner brain"), never in a file, never in the log. The store
goes through the macOS security CLI as a subprocess, behind an injectable
runner seam: the CLI is the stable documented interface for generic
passwords, the Partner stays stdlib-only, and tests can prove the exact
service, account, and update behavior with a fake runner without ever
touching a real Keychain.

The LLM call is an OpenAI-compatible chat completion; the provider base
URL and model are config, so tests point the brain at a stub server.

Design record: the "Partner gets a brain" addendum (2026-10-31) in
XOD-BRAIN-ARCHITECTURE.md.
"""
import json
import subprocess
import urllib.request

DECISIONS = ("wait_and_reprobe", "restart", "release_hold", "escalate")
TRIGGERS = ("wedge_suspicion", "cap_event", "repeated_failure", "post_revival_triage")
MODES = ("off", "shadow", "active")

KEYCHAIN_SERVICE = "dev.xod.partner brain"
KEYCHAIN_ACCOUNT = "brain-api-key"

MAX_EVIDENCE_CHARS = 4000
MAX_REASON_CHARS = 300

_SYSTEM = (
    "You are the judgment layer of a small watchdog that keeps a desktop app "
    "alive. A deterministic floor has already handled everything it can; you "
    "are consulted only at an ambiguity point. Answer with exactly one JSON "
    "object and nothing else: {\"decision\": <one of wait_and_reprobe, "
    "restart, release_hold, escalate>, \"reason\": <one short line>}. "
    "wait_and_reprobe means the evidence suggests waiting one probe cycle "
    "before restarting. restart means restarting now is right. release_hold "
    "means a restart cap hold should be cleared (only sensible at a cap "
    "event). escalate means a human should look; use it when the pattern "
    "looks like something restarts cannot fix. Never invent other fields."
)


# -- decision validation ---------------------------------------------------


def validate_decision(obj):
    """(decision, reason) from a parsed object, or None when the shape is
    wrong in any way: not a dict, a decision outside the enum, or a reason
    that is missing, empty, or not a string."""
    if not isinstance(obj, dict):
        return None
    decision = obj.get("decision")
    reason = obj.get("reason")
    if decision not in DECISIONS:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return decision, " ".join(reason.split())[:MAX_REASON_CHARS]


def parse_decision_text(text):
    """(decision, reason) from a model reply, or None. Tolerates a code
    fence around the JSON; anything else unparseable is a malfunction."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return validate_decision(obj)


# -- the key store -----------------------------------------------------------


def _security_runner(argv, timeout=15):
    """The real runner: one security CLI invocation. Returns
    (returncode, stdout, stderr) as text. The key travels only in argv to
    this one subprocess on save, and only in stdout back on read; neither
    is ever logged by this module."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


class KeychainBrainKeyStore:
    """The brain key in the Partner's own Keychain item, via the security
    CLI. save uses add-generic-password with -U (create or update), so
    re-provisioning replaces the key in place. get answers None when no
    item exists. Any CLI failure is a soft None or False: a broken store
    degrades the brain to absent, never the watchdog to broken."""

    def __init__(self, runner=None, service=KEYCHAIN_SERVICE, account=KEYCHAIN_ACCOUNT):
        self._run = runner or _security_runner
        self.service = service
        self.account = account

    def save(self, key):
        if not key or not isinstance(key, str):
            return False
        try:
            rc, _out, _err = self._run([
                "security", "add-generic-password",
                "-s", self.service, "-a", self.account,
                "-w", key, "-U",
            ])
        except Exception:
            return False
        return rc == 0

    def get(self):
        try:
            rc, out, _err = self._run([
                "security", "find-generic-password",
                "-s", self.service, "-a", self.account, "-w",
            ])
        except Exception:
            return None
        if rc != 0:
            return None
        key = (out or "").strip()
        return key or None


class MemoryBrainKeyStore:
    """The same seam, in memory. Tests only."""

    def __init__(self):
        self.key = None

    def save(self, key):
        if not key or not isinstance(key, str):
            return False
        self.key = key
        return True

    def get(self):
        return self.key


# -- the LLM call ------------------------------------------------------------


def chat_call(base_url, model, key, messages, timeout=20, opener=None):
    """One OpenAI-compatible chat completion. Returns (text, usage) where
    usage is the response's usage dict (or {} when the provider sent
    none). Raises on transport or shape errors; the caller treats any
    raise as a malfunction and lets the floor proceed."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    choices = data.get("choices") or []
    text = choices[0]["message"]["content"]
    usage = data.get("usage") or {}
    return text, usage


# -- the brain -----------------------------------------------------------------


class Brain:
    """Consults the model at the watchdog's triggers and validates the
    answer. Holds no authority of its own: it returns a decision dict and
    the watchdog (in xod_partner.Core) decides what, if anything, the
    decision may change. Every consultation, malfunction included, is
    logged through the Partner's log with trigger, evidence summary,
    decision, reason, mode, and token counts."""

    def __init__(self, cfg, log, key_store, llm_call=None):
        self.cfg = cfg
        self.log = log
        self.store = key_store
        self._llm_call = llm_call or chat_call

    def mode(self):
        mode = str(self.cfg.get("partner_brain_mode") or "shadow").lower()
        return mode if mode in MODES else "shadow"

    def base_url(self):
        return str(self.cfg.get("brain_base_url") or "https://api.openai.com/v1")

    def model(self):
        return str(self.cfg.get("brain_model") or "gpt-6.1-terra")

    def provider(self):
        return str(self.cfg.get("brain_provider") or "openai")

    def has_key(self):
        try:
            return bool(self.store.get())
        except Exception:
            return False

    def provision_key(self, key):
        """Store a key handed over the authenticated provisioning route.
        The value is never logged and never echoed; only the fact of the
        provisioning is recorded."""
        ok = False
        try:
            ok = bool(self.store.save(key))
        except Exception:
            ok = False
        self.log("brain key provisioning: %s (the key itself is never logged)"
                 % ("stored in the Partner Keychain item" if ok else "the store refused it"))
        return ok

    def _messages(self, trigger, evidence):
        ev = json.dumps(evidence, sort_keys=True, default=str)[:MAX_EVIDENCE_CHARS]
        user = "Trigger: %s\nEvidence (JSON): %s" % (trigger, ev)
        return [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user}]

    def consult(self, trigger, evidence):
        """One consultation. Returns a dict {trigger, decision, reason,
        mode, model, provider, prompt_tokens, completion_tokens} on a
        valid decision, or None when the brain is off, unkeyed, the API
        fails, or the answer is malformed. None always means: the floor
        proceeds exactly as today."""
        mode = self.mode()
        if mode == "off":
            return None
        key = None
        try:
            key = self.store.get()
        except Exception:
            key = None
        if not key:
            self.log("brain consult skipped (trigger %s): no brain key provisioned" % trigger)
            return None
        summary = json.dumps(evidence, sort_keys=True, default=str)[:300]
        try:
            text, usage = self._llm_call(self.base_url(), self.model(), key,
                                         self._messages(trigger, evidence),
                                         timeout=int(self.cfg.get("brain_timeout_s") or 20))
        except Exception as exc:
            self.log("brain malfunction (trigger %s, mode %s): the API call failed: %s; "
                     "the floor proceeds" % (trigger, mode, exc))
            return None
        pt = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0
        ct = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0
        parsed = parse_decision_text(text)
        if parsed is None:
            self.log("brain malfunction (trigger %s, mode %s): unparseable decision %r; "
                     "the floor proceeds" % (trigger, mode, str(text)[:160]))
            return None
        decision, reason = parsed
        self.log("brain consult (trigger %s, mode %s%s): decision %s, reason: %s "
                 "[evidence: %s] [tokens: prompt %d, completion %d, model %s]"
                 % (trigger, mode, ", shadow only" if mode == "shadow" else "",
                    decision, reason, summary, pt, ct, self.model()))
        return {"trigger": trigger, "decision": decision, "reason": reason,
                "mode": mode, "model": self.model(), "provider": self.provider(),
                "prompt_tokens": pt, "completion_tokens": ct}
