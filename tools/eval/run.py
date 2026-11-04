"""tools/eval/run.py - run promptfoo / inspect-ai / lm-evaluation-harness against Aether's API.

    set AETHER_API_TOKEN=<api_server_token>          (PowerShell: $env:AETHER_API_TOKEN="...")
    python -m tools.eval.run check
    python -m tools.eval.run promptfoo --label baseline
    python -m tools.eval.run inspect   --label baseline --limit 10
    python -m tools.eval.run lm-eval   --tasks gsm8k --limit 20

Each harness is a separate program that you install on its own (nothing is added to Aether's
environment): `npx` for promptfoo (version pinned below), `pip install inspect-ai openai` for
inspect, `pip install "lm_eval[api]"` for lm-eval. Put them in a venv of their own and point at it
with --bin. Aether must have api_server_enabled + api_server_token set (docs/SERVER.md).

Over this API the model gets text only: no tools, no memory, and Aether ignores temperature and
max_tokens. So results measure the configured model, not the whole assistant; the whole-assistant
suite stays tools/eval_harness.py. The token travels in the environment, never on a command line.
Self-signed HTTPS: pass the certificate with --ca-bundle; there is no "skip verification" switch.
Telemetry is switched off for every harness that has a switch.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval import cases as eval_cases  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TOKEN_ENV = "AETHER_API_TOKEN"
PROMPTFOO_VERSION = "0.125.1"          # pinned: npx would otherwise run whatever is "latest"
OUT_ROOT = ROOT / "logs" / "eval" / "harness"
INSTALL_HINT = {
    "promptfoo": "needs Node.js (npx).",
    "inspect": "pip install inspect-ai openai   (in a venv of its own; then --bin <venv>/Scripts/inspect)",
    "lm-eval": 'pip install "lm_eval[api]"   (in a venv of its own; then --bin <venv>/Scripts/lm_eval)',
}


class EvalSetupError(RuntimeError):
    pass


# ── pure helpers (unit-tested) ───────────────────────────────────────────────
def resolve_token(environ: dict | None = None, name: str = TOKEN_ENV) -> str:
    tok = (environ if environ is not None else os.environ).get(name, "").strip()
    if len(tok) < 24:
        raise EvalSetupError(f"set {name} to Aether's api_server_token (24+ characters); "
                             "it is read from the environment only")
    return tok


def api_root(base_url: str) -> str:
    """http://host:8000 (or .../v1) -> http://host:8000/v1"""
    base = base_url.strip().rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def tls_env(ca_bundle: str | None) -> dict[str, str]:
    if not ca_bundle:
        return {}
    p = str(Path(ca_bundle).expanduser())
    return {"SSL_CERT_FILE": p, "REQUESTS_CA_BUNDLE": p, "NODE_EXTRA_CA_CERTS": p}


def build_promptfoo_command(config_path: Path, out_json: Path, limit: int | None = None,
                            version: str = PROMPTFOO_VERSION, bin_: str | None = None) -> list[str]:
    argv = [bin_ or "npx", *([] if bin_ else ["--yes", f"promptfoo@{version}"]),
            "eval", "-c", str(config_path), "-o", str(out_json), "--no-cache", "--max-concurrency", "1"]
    if limit:
        argv += ["--filter-first-n", str(limit)]
    return argv


def build_inspect_command(model: str, log_dir: Path, limit: int | None = None,
                          bin_: str | None = None) -> list[str]:
    # relative to cwd (= repo root, see _run): inspect rejects absolute task paths
    argv = [bin_ or "inspect", "eval", "tools/eval/inspect_task.py",
            "--model", f"openai-api/aether/{model}", "--log-dir", str(log_dir), "--max-connections", "1"]
    if limit:
        argv += ["--limit", str(limit)]
    return argv


def build_lm_eval_command(model: str, base_url: str, tasks: str, out_dir: Path,
                          limit: int | None = None, bin_: str | None = None,
                          include_path: str | None = None) -> list[str]:
    if not tasks.strip():
        raise EvalSetupError("lm-eval needs --tasks (generation tasks only, e.g. gsm8k; "
                             "loglikelihood tasks cannot run over a chat API)")
    margs = (f"model={model},base_url={api_root(base_url)}/chat/completions,"
             "num_concurrent=1,max_retries=2,tokenized_requests=False")
    argv = [bin_ or "lm_eval", "--model", "local-chat-completions", "--model_args", margs,
            "--apply_chat_template", "--tasks", tasks, "--output_path", str(out_dir)]
    if limit:
        argv += ["--limit", str(limit)]
    if include_path:
        argv += ["--include_path", str(include_path)]
    return argv


def harness_env(tool: str, base_url: str, token: str, ca_bundle: str | None) -> dict[str, str]:
    env = {"HF_HUB_DISABLE_TELEMETRY": "1", **tls_env(ca_bundle)}
    if tool == "promptfoo":
        env.update({TOKEN_ENV: token, "PROMPTFOO_DISABLE_TELEMETRY": "1", "PROMPTFOO_DISABLE_UPDATE": "1"})
    elif tool == "inspect":
        env.update({"AETHER_BASE_URL": api_root(base_url), "AETHER_API_KEY": token})
    elif tool == "lm-eval":
        env["OPENAI_API_KEY"] = token
    return env


# ── talking to Aether ────────────────────────────────────────────────────────
def _ctx(ca_bundle: str | None):
    return ssl.create_default_context(cafile=str(Path(ca_bundle).expanduser())) if ca_bundle else None


def list_models(base_url: str, token: str, ca_bundle: str | None = None, timeout: float = 15) -> list[str]:
    req = urllib.request.Request(api_root(base_url) + "/models", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(ca_bundle)) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        hint = {401: "wrong token", 404: "API server is off (api_server_enabled / api_server_token)",
                429: "locked out after bad tokens; wait 10 minutes"}.get(e.code, "")
        raise EvalSetupError(f"Aether answered HTTP {e.code}" + (f": {hint}" if hint else "")) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EvalSetupError(f"cannot reach {api_root(base_url)}: {e}") from e
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


# ── commands ─────────────────────────────────────────────────────────────────
def _setup(args) -> tuple[str, str]:
    token = resolve_token()
    model = args.model or (list_models(args.base_url, token, args.ca_bundle) or [""])[0]
    if not model:
        raise EvalSetupError("Aether lists no model; pass --model")
    return token, model


def _out_dir(args, tool: str) -> Path:
    d = OUT_ROOT / f"{args.label or time.strftime('%Y%m%d-%H%M%S')}-{tool}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(argv: list[str], env_extra: dict[str, str], tool: str) -> int:
    exe = argv[0]
    if shutil.which(exe) is None and not Path(exe).exists():
        print(f"{exe!r} not found - {tool}: {INSTALL_HINT[tool]}", file=sys.stderr)
        return 127
    print("$ " + " ".join(argv))
    return subprocess.run(argv, env={**os.environ, **env_extra}, cwd=str(ROOT)).returncode


def cmd_check(args) -> int:
    token = resolve_token()
    models = list_models(args.base_url, token, args.ca_bundle)
    n = len(eval_cases.load_api_cases())
    print(f"OK: {api_root(args.base_url)} lists model(s): {', '.join(models) or '(none)'}")
    print(f"{n} suite cases run over this API (memory-dependent ones excluded).")
    return 0


def cmd_promptfoo(args) -> int:
    token, model = _setup(args)
    out = _out_dir(args, "promptfoo")
    cfg = out / "promptfooconfig.json"
    cfg.write_text(json.dumps(eval_cases.to_promptfoo_config(
        eval_cases.load_api_cases(), model, args.base_url, TOKEN_ENV), indent=2), encoding="utf-8")
    argv = build_promptfoo_command(cfg, out / "results.json", args.limit, args.promptfoo_version, args.bin)
    rc = _run(argv, harness_env("promptfoo", args.base_url, token, args.ca_bundle), "promptfoo")
    print(f"results: {out}")
    return rc


def cmd_inspect(args) -> int:
    token, model = _setup(args)
    out = _out_dir(args, "inspect")
    rc = _run(build_inspect_command(model, out, args.limit, args.bin),
              harness_env("inspect", args.base_url, token, args.ca_bundle), "inspect")
    print(f"logs: {out}   (view with: inspect view --log-dir {out})")
    return rc


def cmd_lm_eval(args) -> int:
    token, model = _setup(args)
    out = _out_dir(args, "lm-eval")
    argv = build_lm_eval_command(model, args.base_url, args.tasks, out, args.limit, args.bin,
                                 args.include_path)
    rc = _run(argv, harness_env("lm-eval", args.base_url, token, args.ca_bundle), "lm-eval")
    print(f"results: {out}")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.eval.run", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, tool=True):
        p.add_argument("--base-url", default=os.environ.get("AETHER_API_URL", DEFAULT_BASE_URL))
        p.add_argument("--ca-bundle", help="certificate file for a self-signed https Aether")
        if tool:
            p.add_argument("--model", help="model id (default: first from /v1/models)")
            p.add_argument("--label", help="name for the results folder under logs/eval/harness/")
            p.add_argument("--limit", type=int, help="run only the first N cases")
            p.add_argument("--bin", help="path to the harness executable (e.g. inside its venv)")

    common(sub.add_parser("check", help="test the connection and token"), tool=False)
    common(sub.add_parser("promptfoo", help="Aether's core suite through promptfoo"))
    common(sub.add_parser("inspect", help="Aether's core suite through inspect-ai"))
    sub.choices["promptfoo"].add_argument("--promptfoo-version", default=PROMPTFOO_VERSION)
    lm = sub.add_parser("lm-eval", help="standard benchmarks through lm-evaluation-harness")
    common(lm)
    lm.add_argument("--tasks", required=True, help="comma-separated lm-eval generation tasks")
    lm.add_argument("--include-path", help="folder with your own lm-eval task YAMLs")
    args = ap.parse_args(argv)
    try:
        return {"check": cmd_check, "promptfoo": cmd_promptfoo, "inspect": cmd_inspect,
                "lm-eval": cmd_lm_eval}[args.cmd](args)
    except EvalSetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
