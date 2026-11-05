# Plugin Security

Aether does not import plugin files by default. A plugin runs with the same
permissions as Aether, so only trust code you have reviewed.

To approve a plugin, calculate the SHA-256 digest of its exact file contents:

```powershell
python -c "import hashlib,pathlib; p=pathlib.Path('plugins/my_tool.py'); print(hashlib.sha256(p.read_bytes()).hexdigest())"
```

Add the filename and digest to the `plugins.trusted_hashes` setting in
`config/api_keys.json`:

```json
"plugins.trusted_hashes": {
  "my_tool.py": "paste-the-reviewed-64-character-sha256-here"
}
```

Restart Aether after changing the trust entry. The plugin is loaded only when
its current bytes match the approved digest. Editing a trusted plugin blocks
it again until the new contents are reviewed and a new digest is approved.
The template file beginning with `_` is never loaded.

Hash approval detects unexpected edits; it is not an execution sandbox. Do not
approve untrusted plugins. Keep `config/api_keys.json` private and out of Git,
archives, and shared bug reports because it may contain credentials.

## Generated-code sandbox (`core/sandbox.py`)

Model-written Python (agent `generated_code` step) runs in a restricted subprocess after you approve the exact code. The approval dialog states the active policy.

Enforced: scratch working directory (deleted afterwards), environment allowlist (API keys and tokens never reach the code), `python -I`, timeout with process-tree kill, memory cap (POSIX `RLIMIT_AS`, Windows job object), CPU-seconds cap (POSIX), output cap, and network and program-launching blocked by default via an audit hook.

Not enforced: filesystem isolation (absolute paths still work), and the audit hook can be bypassed by ctypes or C extensions. The Windows job-object path is untested on Windows as of this change. For real isolation use a container. Settings: `sandbox.*` in `config/config.example.json`.

### Docker backend (`sandbox.backend = "docker"`)

Real filesystem isolation: each run is a throwaway container (`--rm`) with no network unless `sandbox.allow_network`, all capabilities dropped, read-only root, pid/memory/cpu limits, running as your user id on Linux. It sees `/work` (a scratch folder) plus only the folders you list in `sandbox.docker_mounts_ro` / `sandbox.docker_mounts_rw`, mounted at `/mnt/<name>`. The model is told this layout instead of your real paths.

It fails closed: if Docker is missing or its daemon is not running, the code is not run (it never falls back to the weaker process backend), and an unknown `sandbox.backend` value is treated as `docker`. As of this change the container path is unit-tested for command construction and failure handling only; the end-to-end isolation test (`test_real_container_isolation`) runs only where a Docker daemon is reachable and has not been run yet.
