"""
partner_ax: the Partner's hands. macOS Accessibility through ctypes, so the
Partner (a plain system-python process riding on its own app bundle's
identity) can read the Muse app's approval cards and press the approving
button, exactly as Xod's in-process walk did before v1.

The card rules here are a faithful, frozen port of app/services/muse_app.py
(the pick order, the expiry check, the card-text fields, the card id). Policy
lives in Xod; this module only reads trees and presses what the rules say.
It takes no other AX actions: no typing, no other apps, no other buttons.

Fronting (bringing Muse to the front for a card that only answers there)
is done with two System Events calls (frontmost pid, set frontmost), never
per element: per-element scripting is what made that route take ~25s a scan
in Xod's earlier life. The tree itself is walked with raw AX calls.
"""
import ctypes
import hashlib
import re
import subprocess
import time

SCAN_TIMEOUT = 10.0   # a healthy walk takes well under a second; this guards a stuck tree
FRONT_SETTLE = 0.6    # after bringing Muse forward (or before putting the other app back)
UTF8 = 0x08000100     # kCFStringEncodingUTF8

_AS = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"


# ------------------------------------------------------------------ elements
_cf = None  # the CoreFoundation handle, set by CtypesAx when it loads


def _release(ref):
    if ref and _cf is not None:
        try:
            _cf.CFRelease(ref)
        except Exception:
            pass


class El:
    """One AXUIElementRef, owned (+1). Equality is by ref, like the pyobjc walk."""

    __slots__ = ("ref",)

    def __init__(self, ref):
        self.ref = ref

    def __del__(self):
        _release(self.ref)

    def __eq__(self, other):
        return isinstance(other, El) and self.ref == other.ref

    def __hash__(self):
        return hash(self.ref)


class CtypesAx:
    """The MacAX surface (trusted / create_app / attr / perform / set_attr)
    over raw ApplicationServices calls. Any load failure leaves every method
    answering None or an error code, never raising into callers."""

    def __init__(self):
        global _cf
        self.ok = False
        self._strings = {}
        try:
            cf, asv = ctypes.CDLL(_CF), ctypes.CDLL(_AS)
            vp, cp, ul, lg, bl, it, fl = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong,
                                          ctypes.c_long, ctypes.c_bool, ctypes.c_int, ctypes.c_float)
            for lib, name, restype, argtypes in (
                    (cf, "CFRelease", None, [vp]), (cf, "CFRetain", vp, [vp]),
                    (cf, "CFGetTypeID", ul, [vp]), (cf, "CFStringGetTypeID", ul, []),
                    (cf, "CFArrayGetTypeID", ul, []), (cf, "CFBooleanGetTypeID", ul, []),
                    (cf, "CFNumberGetTypeID", ul, []),
                    (cf, "CFStringCreateWithCString", vp, [vp, cp, ctypes.c_uint32]),
                    (cf, "CFStringGetLength", lg, [vp]),
                    (cf, "CFStringGetMaximumSizeForEncoding", lg, [lg, ctypes.c_uint32]),
                    (cf, "CFStringGetCString", bl, [vp, cp, lg, ctypes.c_uint32]),
                    (cf, "CFStringGetCStringPtr", cp, [vp, ctypes.c_uint32]),
                    (cf, "CFArrayGetCount", lg, [vp]),
                    (cf, "CFArrayGetValueAtIndex", vp, [vp, lg]),
                    (cf, "CFBooleanGetValue", bl, [vp]),
                    (cf, "CFNumberGetValue", bl, [vp, lg, vp]),
                    (asv, "AXIsProcessTrusted", bl, []),
                    (asv, "AXUIElementCreateApplication", vp, [it]),
                    (asv, "AXUIElementCopyAttributeValue", it, [vp, vp, ctypes.POINTER(vp)]),
                    (asv, "AXUIElementPerformAction", it, [vp, vp]),
                    (asv, "AXUIElementSetAttributeValue", it, [vp, vp, vp]),
                    (asv, "AXUIElementSetMessagingTimeout", it, [vp, fl]),
                    (asv, "AXUIElementGetTypeID", ul, [])):
                fn = getattr(lib, name)
                fn.restype, fn.argtypes = restype, argtypes
            self._cf, self._asv = cf, asv
            self._str_t, self._arr_t = cf.CFStringGetTypeID(), cf.CFArrayGetTypeID()
            self._bool_t, self._num_t = cf.CFBooleanGetTypeID(), cf.CFNumberGetTypeID()
            self._el_t = asv.AXUIElementGetTypeID()
            _cf = cf
            self.ok = True
        except Exception:
            self.ok = False

    # -- primitives ---------------------------------------------------
    def _name(self, s):
        ref = self._strings.get(s)
        if ref is None:
            ref = self._cf.CFStringCreateWithCString(None, s.encode(), UTF8)
            self._strings[s] = ref
        return ref

    def trusted(self):
        if not self.ok:
            return None
        try:
            return bool(self._asv.AXIsProcessTrusted())
        except Exception:
            return None

    def create_app(self, pid):
        if not self.ok:
            return None
        ref = self._asv.AXUIElementCreateApplication(int(pid))
        if not ref:
            return None
        try:
            self._asv.AXUIElementSetMessagingTimeout(ref, ctypes.c_float(3.0))
        except Exception:
            pass
        return El(ref)

    def attr(self, el, name):
        if not self.ok or el is None:
            return None
        out = ctypes.c_void_p()
        err = self._asv.AXUIElementCopyAttributeValue(el.ref, self._name(name), ctypes.byref(out))
        if err != 0 or not out.value:
            return None
        return self._convert(out.value)

    def _convert(self, ref):
        """Take ownership of a +1 ref and hand back a plain Python value."""
        cf = self._cf
        tid = cf.CFGetTypeID(ref)
        if tid == self._el_t:
            return El(ref)
        if tid == self._str_t:
            text = self._str_value(ref)
            _release(ref)
            return text
        if tid == self._bool_t:
            value = bool(cf.CFBooleanGetValue(ref))
            _release(ref)
            return value
        if tid == self._num_t:
            buf = ctypes.c_double()
            ok = cf.CFNumberGetValue(ref, 13, ctypes.byref(buf))  # kCFNumberDoubleType
            _release(ref)
            return buf.value if ok else None
        if tid == self._arr_t:
            items = []
            for i in range(cf.CFArrayGetCount(ref)):
                item = cf.CFArrayGetValueAtIndex(ref, i)
                if item:
                    cf.CFRetain(item)
                    items.append(El(item))
            _release(ref)
            return items
        _release(ref)
        return None

    def _str_value(self, ref):
        cf = self._cf
        ptr = cf.CFStringGetCStringPtr(ref, UTF8)
        if ptr:
            return ptr.decode("utf-8", "replace")
        size = cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(ref), UTF8) + 1
        buf = ctypes.create_string_buffer(size)
        if cf.CFStringGetCString(ref, buf, size, UTF8):
            return buf.value.decode("utf-8", "replace")
        return ""

    def perform(self, el, action):
        if not self.ok or el is None:
            return -1
        return int(self._asv.AXUIElementPerformAction(el.ref, self._name(action)))

    def set_attr(self, el, name, value):
        if not self.ok or el is None:
            return -1
        raw = ctypes.c_void_p.in_dll(self._cf, "kCFBooleanTrue" if value else "kCFBooleanFalse")
        return int(self._asv.AXUIElementSetAttributeValue(el.ref, self._name(name), raw))


# ------------------------------------------------------------------ the card rules (frozen port of muse_app)
def pick_button(labels):
    """The approving button to press, or None. Same order as Xod's _pick_button:
    an always-allow naming a recipient or site, then Allow all once, then Send /
    Allow once / Allow. Deny and friends are never picked."""
    clean = [str(l).strip() for l in labels if str(l).strip()]
    for l in clean:
        low = l.lower()
        if low.startswith("always allow") and ("recipient" in low or "site" in low):
            return l
    for l in clean:
        if l.lower() == "allow all once":
            return l
    for want in ("send", "allow once", "allow"):
        for l in clean:
            if l.lower() == want:
                return l
    return None


def card_type(title):
    m = re.match(r"(?i)approval request for\s+(.+)", str(title or "").strip())
    return (m.group(1).strip() if m else "Muse") or "Muse"


_FIELD_NEXT = r"(?=\s+(?:From|To|Cc|Bcc|Subject|Attachments?|Date|Send|Allow|Always|Don\'t send|Deny)\b|$)"


def _field(text, name):
    m = re.search(rf"(?i)\b{name}\s*:?\s*(.+?){_FIELD_NEXT}", text or "")
    return " ".join(m.group(1).split()).strip(" <>") if m else ""


def card_fields(what, texts=None):
    """From/To/Subject/Attachments from the card; same logic as Xod's _card_fields."""
    out = {"from": "", "to": "", "subject": "", "attachments": []}
    for item in texts or []:
        m = re.match(r"(?i)(from|to|subject|attachments?)\s*:?\s*(.*)", str(item).strip())
        if not m:
            continue
        key, val = m.group(1).lower(), " ".join(m.group(2).split())
        if key.startswith("attachment"):
            out["attachments"] = [a.strip() for a in re.split(r"[,;]", val) if a.strip()]
        else:
            out[key] = val
    if not any(out.values()):
        to_m = re.search(r"(?i)\bto\s*:?\s*<?([\w.+\-]+@[\w\-.]+\.\w+)>?", what or "")
        from_m = re.search(r"(?i)\bfrom\s*:?\s*(.+?)(?=\s+to\s*:?\s*<?[\w.+\-]+@|$)", what or "")
        subj_m = re.search(r"(?i)\bsubject\s*:?\s*(.+?)(?=\s+attachments?\b|\s+Hi\b|$)", what or "")
        atts = _field(what, "Attachments") or _field(what, "Attachment")
        names = [a.strip() for a in re.split(r"[,;]", atts) if a.strip()] if atts else \
            re.findall(r"[\w\-]+\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|png|jpe?g|zip)\b", what or "", flags=re.I)
        out = {"from": " ".join(from_m.group(1).split()) if from_m and to_m else "",
               "to": to_m.group(1) if to_m else "",
               "subject": " ".join(subj_m.group(1).split()) if subj_m and to_m else "",
               "attachments": names if to_m else []}
    return out


def card_id(card):
    full = "%s\n%s" % (card.get("type") or card_type(card.get("title") or ""),
                       card.get("what") or card.get("title") or "")
    return "museapp-" + hashlib.sha256(full.encode()).hexdigest()[:16]


def no_button_reason(labels):
    labs = [l for l in labels if l.lower() in ("deny", "don't send", "deny all")]
    others = [l for l in labels if l.lower() not in ("deny", "don't send", "deny all")]
    if others:
        return "no button Xod may press (saw: %s)" % ", ".join(others)
    if labs == ["Deny"]:
        return "only a Deny button"
    if labs:
        return "only negative buttons (%s)" % ", ".join(labs)
    return "no button Xod may press"


def _label(ax, el):
    for name in ("AXTitle", "AXDescription", "AXValue"):
        value = ax.attr(el, name)
        if value:
            return str(value)
    return ""


def _muse_pid():
    r = subprocess.run(["pgrep", "-x", "Muse"], capture_output=True, text=True, timeout=5)
    pids = (r.stdout or "").split()
    return int(pids[0]) if pids else None


def _frontmost_pid():
    try:
        r = subprocess.run(["osascript", "-e",
                            'tell application "System Events" to return unix id of '
                            'first application process whose frontmost is true'],
                           capture_output=True, text=True, timeout=10)
        s = (r.stdout or "").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None


def _activate(pid):
    try:
        subprocess.run(["osascript", "-e",
                        'tell application "System Events" to set frontmost of '
                        'first application process whose unix id is %d to true' % int(pid)],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ------------------------------------------------------------------ the walk
class Walk:
    """One tree walk over the Muse app, against an injected ax surface
    (CtypesAx in production, a fake tree in the tests)."""

    def __init__(self, ax, log=lambda msg: None, pid_of=_muse_pid):
        self.ax, self.log, self.pid_of = ax, log, pid_of

    # -- shared tree machinery (mirrors muse_app._scan) ----------------
    def _cards(self, pid):
        """Every approval card in Muse's tree: (entry, buttons-by-label)."""
        ax = self.ax
        deadline = time.time() + SCAN_TIMEOUT
        found = []

        def children(el):
            return ax.attr(el, "AXChildren") or []

        def card_info(card):
            texts, buttons = [], {}

            def inner(el, depth):
                if depth > 12:
                    return
                role, label = str(ax.attr(el, "AXRole") or ""), _label(ax, el).strip()
                if role == "AXButton" and label:
                    buttons.setdefault(label, el)
                elif label and role == "AXStaticText":
                    texts.append(label)
                for child in children(el):
                    inner(child, depth + 1)

            inner(card, 0)
            return buttons, " ".join(" ".join(texts).split()), texts

        def nearby_button(card):
            el = card
            for _ in range(3):
                parent = ax.attr(el, "AXParent")
                if parent is None:
                    return None, None
                btns = {}

                def hunt(x, d):
                    if x != card and str(ax.attr(x, "AXRole") or "") == "AXGroup" \
                            and "approval request" in _label(ax, x).lower():
                        return
                    if str(ax.attr(x, "AXRole") or "") == "AXButton":
                        lab = _label(ax, x).strip()
                        if lab:
                            btns.setdefault(lab, x)
                    if d < 8:
                        for c in children(x):
                            hunt(c, d + 1)

                hunt(parent, 0)
                chosen = pick_button(list(btns))
                if chosen is not None:
                    return btns[chosen], chosen
                el = parent
            return None, None

        def walk(el, depth):
            if time.time() > deadline:
                raise TimeoutError("Muse app scan timed out after %ds" % SCAN_TIMEOUT)
            if depth > 40:
                return
            if str(ax.attr(el, "AXRole") or "") == "AXGroup" \
                    and "approval request" in _label(ax, el).lower():
                title = _label(ax, el)
                buttons, what, texts = card_info(el)
                chosen = pick_button(list(buttons))
                if chosen is None:
                    btn2, chosen2 = nearby_button(el)
                    if btn2 is not None:
                        buttons[chosen2] = btn2
                        chosen = chosen2
                entry = {"title": title, "what": what, "type": card_type(title),
                         **card_fields(what, texts)}
                entry["buttons"] = list(buttons)
                entry["button"] = chosen
                entry["expired"] = "approval has expired" in what.lower()
                entry["card_id"] = card_id(entry)
                found.append((entry, buttons))
                return
            for child in children(el):
                walk(child, depth + 1)

        app = ax.create_app(pid)
        if app is None:
            return []
        walk(app, 0)
        return found

    # -- fronting -------------------------------------------------------
    def _bring_forward(self, pid):
        prev = _frontmost_pid()
        if prev == pid:
            return None
        app = self.ax.create_app(pid)
        if app is not None:
            for w in self.ax.attr(app, "AXWindows") or []:
                if self.ax.attr(w, "AXMinimized"):
                    self.ax.set_attr(w, "AXMinimized", False)
        _activate(pid)
        return prev

    def _put_back(self, prev):
        if prev:
            _activate(prev)

    # -- the two operations ----------------------------------------------
    def scan(self, front=False):
        """Read-only: the cards showing now and the labels on their buttons.
        Never presses anything."""
        pid = self.pid_of()
        if pid is None:
            return {"running": False, "cards": []}
        prev = self._bring_forward(pid) if front else None
        if front and prev is not None:
            time.sleep(FRONT_SETTLE)
        try:
            cards = [entry for entry, _ in self._cards(pid)]
        finally:
            if front and prev is not None:
                time.sleep(FRONT_SETTLE)
                self._put_back(prev)
        return {"running": True, "cards": cards}

    def press(self, want_id, front=False, away=False):
        """Press the approving button on one card (identified by its card_id),
        re-walking the live tree. Approve-only, exactly the frozen rules; a
        failed or buttonless first attempt may be retried once with Muse in
        front when the caller allows fronting."""
        pid = self.pid_of()
        if pid is None:
            return {"pressed": False, "button": None, "card_id": want_id,
                    "reason": "Muse app not running", "running": False}
        result, needs_front = self._press_attempt(pid, want_id)
        if needs_front and front:
            prev = self._bring_forward(pid)
            if prev is not None:
                time.sleep(FRONT_SETTLE)
            try:
                result, _ = self._press_attempt(pid, want_id)
            finally:
                if prev is not None:
                    time.sleep(FRONT_SETTLE)
                    self._put_back(prev)
        if result["pressed"]:
            self.log("ax press: pressed %r on %s card (away=%s)"
                     % (result["button"], result.get("type") or "Muse", away))
        else:
            self.log("ax press: not pressed (%s) for card %s (away=%s)"
                     % (result["reason"], want_id, away))
        return result

    def _press_attempt(self, pid, want_id):
        for entry, buttons in self._cards(pid):
            if entry["card_id"] != want_id:
                continue
            base = {"button": None, "card_id": want_id, "type": entry["type"]}
            if entry["expired"]:
                return {**base, "pressed": False, "reason": "expired before Xod got to it"}, False
            chosen = entry["button"]
            if chosen is None:
                return {**base, "pressed": False,
                        "reason": no_button_reason(entry["buttons"])}, len(buttons) == 0
            err = self.ax.perform(buttons[chosen], "AXPress")
            if err == 0:
                return {**base, "pressed": True, "button": chosen, "reason": ""}, False
            return {**base, "pressed": False, "button": chosen,
                    "reason": "press failed: AXPress returned %d" % err}, True
        return {"pressed": False, "button": None, "card_id": want_id,
                "reason": "card not showing"}, False
