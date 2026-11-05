// ==============================================================
// MAIL WORDS (Phase 3, simplicity redesign): the pure decisions
// behind Family - Messages. No DOM, no fetch: the same file loads
// in the page (window.MailWords) and in the node test harness
// (module.exports), so what is tested is what runs.
//
// What lives here:
//  - telling a machine payload (a hello/capability JSON blob, a
//    phone action) apart from words a person should read, so raw
//    JSON never renders on the surface;
//  - turning a capability set ({"apps": false, "messages": true})
//    into plain sentences: "It can: send messages." / "It cannot:
//    use apps, see your calendar, or read files.";
//  - grouping the inbox by sender, so one conversation list per
//    agent replaces a pile of near-identical threads.
// ==============================================================
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.MailWords = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Capability key -> the plain words for it. Order matters: it is the
  // order the phrases appear in the can/cannot sentences. Keys come from
  // the phone's switch list (services/phone_mcp.py SWITCH_LABELS) plus
  // the generic agent ones; an unknown key is humanised, never shown raw.
  const CAPABILITY_PHRASES = [
    ["messages", "send messages"],
    ["send", "send messages"],
    ["tasks", "run tasks"],
    ["apps", "use apps"],
    ["background_apps", "use apps in the background"],
    ["drive", "use your apps without asking"],
    ["calendar", "see your calendar"],
    ["contacts", "see your contacts"],
    ["files", "read files"],
    ["location", "know your location"],
    ["notifications", "send notifications"],
    ["screen", "see your screen"],
    ["tap", "tap on your screen"],
    ["sms", "send text messages"],
    ["network", "use the network"],
  ];
  const PHRASE = Object.fromEntries(CAPABILITY_PHRASES);

  const phraseFor = (key) => PHRASE[key] || String(key).replace(/_/g, " ");

  // The parsed object when a message's text is a JSON object (a hello /
  // capability payload, a phone action, ...), else null. Plain words a
  // person wrote are never JSON, so this never hides a real message.
  function parseMachinePayload(text) {
    const t = String(text ?? "").trim();
    if (!t.startsWith("{")) return null;
    try {
      const d = JSON.parse(t);
      return d && typeof d === "object" && !Array.isArray(d) ? d : null;
    } catch (e) {
      return null;
    }
  }

  const isMachinePayload = (text) => parseMachinePayload(text) !== null;

  // The capability set inside a payload, when it carries one:
  // {"capabilities": {"apps": false, ...}} -> that object, else null.
  function capabilitySet(payload) {
    if (!payload || typeof payload !== "object") return null;
    const caps = payload.capabilities;
    if (!caps || typeof caps !== "object" || Array.isArray(caps)) return null;
    return caps;
  }

  // {can: [...phrases], cannot: [...phrases]} from a capability set,
  // in the canonical order above (unknown keys last, humanised).
  function capabilitySentences(caps) {
    if (!caps || typeof caps !== "object") return null;
    const can = [], cannot = [];
    const seen = new Set();
    for (const [key] of CAPABILITY_PHRASES) {
      if (!(key in caps) || seen.has(PHRASE[key])) continue;
      seen.add(PHRASE[key]);
      (caps[key] ? can : cannot).push(PHRASE[key]);
    }
    for (const key of Object.keys(caps)) {
      if (key in PHRASE) continue;
      (caps[key] ? can : cannot).push(phraseFor(key));
    }
    return { can, cannot };
  }

  // "a, b, or c" - the list inside the can/cannot sentences.
  function listWords(items) {
    if (!items.length) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return `${items[0]} or ${items[1]}`;
    return `${items.slice(0, -1).join(", ")}, or ${items[items.length - 1]}`;
  }

  function canSentence(caps) {
    const s = capabilitySentences(caps);
    if (!s || !s.can.length) return "";
    return `It can: ${listWords(s.can)}.`;
  }

  function cannotSentence(caps) {
    const s = capabilitySentences(caps);
    if (!s || !s.cannot.length) return "";
    return `It cannot: ${listWords(s.cannot)}.`;
  }

  // What a thread-list preview may show for a message: never raw JSON.
  function plainPreview(text, subject) {
    const payload = parseMachinePayload(text);
    if (payload) {
      if (capabilitySet(payload)) return "Wants to connect and shared what it can do";
      if (payload.phone_action) return "An automatic message between agents";
      return "An automatic message between agents";
    }
    return String(subject || text || "");
  }

  // Group thread rows (agent_mail.threads()) by sender: one group per
  // agent, holding its threads, the newest message across them, and the
  // summed message/unread counts. Groups sort newest first.
  function senderKey(thread, myAddress) {
    const peers = (thread.peers || [thread.last && thread.last.peer] || [])
      .map((p) => String(p || "").toLowerCase())
      .filter((p) => p && p !== String(myAddress || "").toLowerCase());
    return (peers.sort()[0]) || String((thread.last && thread.last.peer) || "");
  }

  function groupThreadsBySender(threads, myAddress) {
    const groups = new Map();
    for (const t of threads || []) {
      const key = senderKey(t, myAddress);
      let g = groups.get(key);
      if (!g) {
        g = { key, peers: [], threads: [], last: t.last, count: 0, unread: 0, last_at: t.last_at || 0 };
        groups.set(key, g);
      }
      g.threads.push(t);
      for (const p of t.peers || []) if (!g.peers.includes(p)) g.peers.push(p);
      g.count += t.count || 0;
      g.unread += t.unread || 0;
      if ((t.last_at || 0) >= (g.last_at || 0)) { g.last_at = t.last_at; g.last = t.last; }
    }
    return [...groups.values()].sort((a, b) => (b.last_at || 0) - (a.last_at || 0));
  }

  return { CAPABILITY_PHRASES, parseMachinePayload, isMachinePayload, capabilitySet,
           capabilitySentences, canSentence, cannotSentence, plainPreview,
           senderKey, groupThreadsBySender };
});
