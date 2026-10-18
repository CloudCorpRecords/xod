"""A name for the user's agent address (the part before the @), from what Xod knows: their first name, as an address."""
import re
import unicodedata


def default_name() -> str:
    try:
        from app.services import user_memory
        for f in user_memory.facts():
            if f.get("key") == "name":
                first = re.sub(r"(?i)^\s*name is\s+", "", f.get("text") or "").strip().split()[0]
                ascii_ = unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode().lower()
                ascii_ = re.sub(r"[^a-z0-9._-]", "", ascii_)
                if 2 <= len(ascii_) <= 32:
                    return ascii_
    except Exception:
        pass
    return ""
