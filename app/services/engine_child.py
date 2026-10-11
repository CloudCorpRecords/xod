"""
Runs mlx_lm.server (the built-in engine and the mini "specialist" models), and exits as soon as
the Xod server that started it is gone. Without this, a reload, crash or quit left the model
running and holding gigabytes of memory.

  python -m app.services.engine_child <mlx_lm.server arguments>
"""
import os
import sys
import threading
import time


def _watch_parent(parent: int) -> None:
    while True:
        time.sleep(2)
        if os.getppid() != parent:   # re-parented to launchd: Xod is gone
            os._exit(0)


def main() -> None:
    threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
    from mlx_lm.server import main as serve
    sys.argv = ["mlx_lm.server"] + sys.argv[1:]
    serve()


if __name__ == "__main__":
    main()
