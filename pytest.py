"""Top-level pytest entry point backed by `app.services.pytest_shim`.

Allows both `import pytest` and `python3 -m pytest` to work seamlessly in Xod's
bundled Python runtime when third-party pytest is not installed.
"""

from app.services.pytest_shim import (  # noqa: F401
    ExceptionInfo,
    Failed,
    MonkeyPatch,
    Skipped,
    approx,
    fail,
    fixture,
    main,
    mark,
    raises,
    skip,
    warns,
)

if __name__ == "__main__":
    raise SystemExit(main())
