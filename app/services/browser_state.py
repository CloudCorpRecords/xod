"""
browser_state: compact DOM state snapshots, before->after diffs, and automatic
form validation error scraping for the browser harness.

After every mutating browser action (click, type, fill_form), computing a compact
delta tells the agent immediately what actually happened — e.g. whether a modal
opened, whether the "Post" / "Submit" button just became enabled, or whether
inline validation errors ("Phone is required", "Please select a country") appeared.
"""
from typing import Any, Dict, List


JS_PAGE_SNAPSHOT = r"""
(() => {
    const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
    const isVis = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 1 && r.height > 1;
    };

    const enabledButtons = [];
    const disabledButtons = [];
    const seenBtn = new Set();
    for (const el of document.querySelectorAll('button, input[type="submit"], [role="button"]')) {
        if (!isVis(el)) continue;
        const label = clean(el.innerText || el.value || el.getAttribute('aria-label') || '').slice(0, 60);
        if (!label || seenBtn.has(label.toLowerCase())) continue;
        seenBtn.add(label.toLowerCase());
        const dis = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
        if (dis) disabledButtons.push(label);
        else enabledButtons.push(label);
    }

    const modals = [];
    for (const m of document.querySelectorAll('[role="dialog"], [role="alertdialog"], dialog[open], [aria-modal="true"]')) {
        if (!isVis(m)) continue;
        const heading = clean(m.getAttribute('aria-label') || (m.querySelector('h1,h2,h3,[role="heading"]') || {}).innerText || 'dialog').slice(0, 80);
        modals.push(heading);
    }

    const errors = [];
    const seenErr = new Set();
    const pushErr = (msg) => {
        const c = clean(msg).slice(0, 140);
        if (c && !seenErr.has(c.toLowerCase())) {
            seenErr.add(c.toLowerCase());
            errors.push(c);
        }
    };

    // 1. Explicit aria-invalid / HTML5 :invalid controls
    for (const inp of document.querySelectorAll('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid')) {
        if (!isVis(inp)) continue;
        const lbl = clean(inp.getAttribute('aria-label') || inp.name || inp.placeholder || inp.id || inp.tagName);
        const descId = inp.getAttribute('aria-errormessage') || inp.getAttribute('aria-describedby') || '';
        let errTxt = '';
        if (descId) {
            for (const id of descId.split(/\s+/)) {
                const node = id && document.getElementById(id);
                if (node && isVis(node)) errTxt += ' ' + clean(node.innerText || node.textContent);
            }
        }
        const vmsg = (inp.validationMessage && inp.matches && inp.matches(':invalid')) ? inp.validationMessage : '';
        const detail = clean(errTxt || vmsg || 'invalid');
        if (inp.getAttribute('aria-invalid') === 'true' || errTxt) {
            pushErr(`${lbl}: ${detail}`);
        }
    }

    // 2. Visible alert / error banners & inline field error messages
    for (const node of document.querySelectorAll('[role="alert"], .error, .error-message, .field-error, .invalid-feedback, [data-error="true"], [class*="error-text"]')) {
        if (!isVis(node)) continue;
        const txt = clean(node.innerText || node.textContent);
        if (txt && txt.length <= 180) pushErr(txt);
    }

    // 3. Compact input/editor state fingerprint
    const fieldStates = [];
    for (const inp of document.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="combobox"]')) {
        if (!isVis(inp)) continue;
        const k = clean(inp.name || inp.id || inp.getAttribute('aria-label') || inp.placeholder || inp.tagName).slice(0, 40);
        const v = inp.type === 'checkbox' || inp.type === 'radio'
            ? String(Boolean(inp.checked))
            : clean(inp.isContentEditable ? (inp.innerText || inp.textContent) : inp.value).slice(0, 60);
        fieldStates.push(`${k}=${v}`);
    }

    return {
        url: window.location.href,
        title: document.title || '',
        modals: modals.slice(0, 8),
        enabled_buttons: enabledButtons.slice(0, 40),
        disabled_buttons: disabledButtons.slice(0, 20),
        validation_errors: errors.slice(0, 15),
        field_count: fieldStates.length,
        fields_sig: fieldStates.slice(0, 40).join('|'),
        text_len: (document.body && document.body.innerText ? document.body.innerText.length : 0),
    };
})()
"""


def diff_page_snapshots(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a compact, high-signal delta between two JS_PAGE_SNAPSHOT dicts."""
    b = before or {}
    a = after or {}
    url_changed = (b.get("url") or "") != (a.get("url") or "")
    title_changed = (b.get("title") or "") != (a.get("title") or "")

    b_modals = list(b.get("modals") or [])
    a_modals = list(a.get("modals") or [])
    modal_opened = [m for m in a_modals if m not in b_modals]
    modal_closed = [m for m in b_modals if m not in a_modals]

    b_en = {x.lower(): x for x in (b.get("enabled_buttons") or [])}
    a_en = {x.lower(): x for x in (a.get("enabled_buttons") or [])}
    b_dis = {x.lower(): x for x in (b.get("disabled_buttons") or [])}
    newly_enabled = [v for k, v in a_en.items() if k in b_dis or k not in b_en]

    b_errs = set(e.lower() for e in (b.get("validation_errors") or []))
    a_errs = list(a.get("validation_errors") or [])
    new_errors = [e for e in a_errs if e.lower() not in b_errs]

    fields_changed = (b.get("fields_sig") or "") != (a.get("fields_sig") or "")
    text_delta = int(a.get("text_len") or 0) - int(b.get("text_len") or 0)

    changed = any([
        url_changed, title_changed, bool(modal_opened), bool(modal_closed),
        bool(newly_enabled), bool(new_errors), fields_changed, abs(text_delta) >= 20,
    ])

    parts: List[str] = []
    if url_changed:
        parts.append(f"URL -> {a.get('url')}")
    if modal_opened:
        parts.append(f"modal opened: {', '.join(modal_opened[:3])}")
    if modal_closed:
        parts.append(f"modal closed: {', '.join(modal_closed[:3])}")
    if newly_enabled:
        parts.append(f"buttons enabled: {', '.join(newly_enabled[:5])}")
    if new_errors:
        parts.append(f"validation errors: {'; '.join(new_errors[:4])}")
    elif a_errs:
        parts.append(f"active errors: {'; '.join(a_errs[:4])}")
    if fields_changed and not parts:
        parts.append("form fields updated")
    if not parts:
        parts.append("no visible DOM delta" if not changed else f"page text delta {text_delta:+d} chars")

    return {
        "changed": changed,
        "url_changed": url_changed,
        "title_changed": title_changed,
        "modal_opened": modal_opened,
        "modal_closed": modal_closed,
        "newly_enabled_buttons": newly_enabled[:10],
        "new_validation_errors": new_errors[:10],
        "validation_errors": a_errs[:10],
        "fields_changed": fields_changed,
        "text_delta": text_delta,
        "summary": "; ".join(parts),
    }
