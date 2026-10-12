"""actions/desktop_runner.py -- child process that runs approved AI-generated desktop code.

Started by actions.desktop._run_isolated() so the parent can enforce a deadline: exec()
cannot be interrupted in-process, so approved code containing `while True: pass` used to
hang its worker thread for good. Here the parent simply kills this process on timeout.

It reads the code from stdin and RE-VALIDATES it (defence in depth: the allowlist is
enforced in the parent and again here), runs it in the same minimal sandbox, and prints
exactly one tagged JSON result line. Nothing else it prints is trusted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULT_TAG = "\x00AETHER_DESKTOP_RESULT\x00"


def main() -> int:
    code = sys.stdin.read()
    try:
        from actions import desktop as d
        problem = d._validate_generated_code(code)
        if problem:
            result = {"ok": False, "output": f"Blocked: the generated code was not allowed ({problem})."}
        else:
            ok, msg = d._exec_in_process(code)
            result = {"ok": ok, "output": msg}
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "output": f"Execution error: {e}"}
    sys.stdout.write("\n" + RESULT_TAG + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
