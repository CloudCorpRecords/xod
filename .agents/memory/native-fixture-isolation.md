---
name: Native fixture isolation
description: Why native agent tests need both HOME and XDG configuration isolation.
---
Native agent tests must isolate XDG_CONFIG_HOME alongside HOME and XOD_DATA_DIR.

**Why:** Replit's inherited XDG configuration can take precedence over a test's
throwaway HOME. A native Crew fixture consequently read the wrong settings and
failed despite its otherwise isolated files.

**How to apply:** Set all three paths to temporary storage before importing
agent/config services. Do not fix such failures by using the developer's real
settings or weakening keyring boundaries.
