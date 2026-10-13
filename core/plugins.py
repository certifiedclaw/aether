"""
plugins.py - Aether plugin system.

Supports both plugin formats:
  Legacy Aether:
    PLUGIN_NAME = "name"
    PLUGIN_TOOLS = {"tool_name": callable}

  Mark-LI:
    PLUGIN = {"name": "tool_name", "description": "...", "parameters": {...}}
    def run(parameters: dict, player=None, session_memory=None) -> str: ...
"""
from __future__ import annotations
import hashlib, hmac, importlib.util, inspect, logging, re, sys
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}


class PluginSystem:
    def __init__(self, config=None) -> None:
        from memory.config_manager import get_mark_config as get_config
        cfg = config or get_config()
        self._plugin_dir = Path(cfg.get("plugins.plugin_dir", "./plugins"))
        self._config = cfg
        self._plugins: dict = {}
        self._tools: dict[str, Callable] = {}
        self._declarations: dict[str, dict] = {}
        self._errors: dict[str, str] = {}

    def load_all(self) -> int:
        self._plugin_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in sorted(self._plugin_dir.glob("*.py")):
            if not f.name.startswith("_") and self._load(f):
                count += 1
        logger.info("Loaded %d plugin(s)", count)
        return count

    def reload(self) -> int:
        """Re-scan plugins/ from scratch: unload everything currently
        loaded, then load_all() again. This is what plugin_watcher.py calls
        on every filesystem change -- it lets a plugin file be edited,
        added, or removed while Aether is running and have the change take
        effect without a restart. Picks up edits to existing plugins (not
        just new/deleted files) since _load() re-execs the module fresh.
        """
        self.unload_all()
        return self.load_all()

    def _load(self, path: Path) -> bool:
        module_name = f"aether_plugin_{path.stem}"
        try:
            source = path.read_bytes()
            expected_hash = self._trusted_hash(path.name)
            actual_hash = hashlib.sha256(source).hexdigest()
            if expected_hash is None or not hmac.compare_digest(actual_hash, expected_hash):
                raise PermissionError(
                    f"plugin is not trusted; approve SHA-256 {actual_hash} in plugins.trusted_hashes")

            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("could not build import spec")
            mod  = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            exec(compile(source, str(path), "exec"), mod.__dict__)

            if hasattr(mod, "PLUGIN"):
                self._load_mark_plugin(mod, path)
            else:
                self._load_legacy_plugin(mod, path)
            return True
        except Exception as e:
            sys.modules.pop(module_name, None)
            self._errors[path.name] = str(e)
            logger.warning("Plugin load failed %s: %s", path.name, e)
            return False

    def _trusted_hash(self, filename: str) -> str | None:
        trusted = self._config.get("plugins.trusted_hashes", {})
        if not isinstance(trusted, dict):
            return None
        digest = trusted.get(filename)
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            return None
        return digest.lower()

    def _load_mark_plugin(self, mod, path: Path) -> None:
        meta = getattr(mod, "PLUGIN", None)
        if not isinstance(meta, dict):
            raise ValueError("PLUGIN must be a dict")

        name = meta.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise ValueError("PLUGIN['name'] must be a valid Python-style identifier")

        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("PLUGIN['description'] is required")

        parameters = meta.get("parameters", _DEFAULT_PARAMS)
        if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
            raise ValueError("PLUGIN['parameters'] must be an OBJECT schema")

        run = getattr(mod, "run", None)
        if not callable(run):
            raise ValueError("Mark-LI plugins must define callable run(parameters, ...)")

        if name in self._tools:
            raise ValueError(f"tool name collision: {name}")

        self._plugins[name] = mod
        self._tools[name] = run
        self._declarations[name] = {
            "name": name,
            "description": description.strip(),
            "parameters": parameters,
        }
        logger.info("Mark-LI plugin: %s (%s)", name, path.name)

    def _load_legacy_plugin(self, mod, path: Path) -> None:
        name = getattr(mod, "PLUGIN_NAME", path.stem)
        tools = getattr(mod, "PLUGIN_TOOLS", {})
        if not isinstance(tools, dict):
            raise ValueError("PLUGIN_TOOLS must be a dict")
        if hasattr(mod, "on_load"):
            mod.on_load(self._config)
        self._plugins[name] = mod
        for tname, fn in tools.items():
            if not callable(fn):
                logger.warning("Plugin %s skipped non-callable tool %s", name, tname)
                continue
            namespaced = f"{name}.{tname}"
            if namespaced in self._tools or tname in self._tools:
                raise ValueError(f"tool name collision: {tname}")
            self._tools[namespaced] = fn
            self._tools.setdefault(tname, fn)
            self._declarations.setdefault(tname, {
                "name": tname,
                "description": f"Tool '{tname}' provided by plugin '{name}'.",
                "parameters": _DEFAULT_PARAMS,
            })
        logger.info("Legacy plugin: %s (%d tools)", name, len(tools))

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool_declarations(self) -> list[dict]:
        """Return Ollama/OpenAI-compatible function declaration sources."""
        return list(self._declarations.values())

    def list_for_ui(self) -> list[dict]:
        rows = []
        for name, mod in self._plugins.items():
            file = getattr(mod, "__file__", "")
            rows.append({
                "name": name,
                "description": self._declarations.get(name, {}).get("description", ""),
                "file": Path(file).name if file else "",
                "valid": True,
                "error": "",
                "enabled": True,
            })
        for file, error in self._errors.items():
            rows.append({
                "name": Path(file).stem,
                "description": "",
                "file": file,
                "valid": False,
                "error": error,
                "enabled": False,
            })
        return rows

    def execute_tool(self, name: str, **kwargs) -> str:
        fn = self._tools.get(name)
        if fn is None:
            return f"Plugin tool not found: {name}"
        try:
            return str(_call_plugin(fn, kwargs, player=kwargs.pop("player", None),
                                    session_memory=kwargs.pop("session_memory", None)) or "Done.")
        except Exception as e:
            return f"Plugin '{name}' error: {e}"

    def unload_all(self) -> None:
        for mod in self._plugins.values():
            if hasattr(mod, "on_unload"):
                try:
                    mod.on_unload()
                except Exception:
                    pass
        self._plugins.clear()
        self._tools.clear()
        self._declarations.clear()
        self._errors.clear()


def _call_plugin(fn: Callable, parameters: dict, player=None, session_memory=None):
    """Call either a Mark-LI run(parameters, ...) or legacy **kwargs tool."""
    sig = inspect.signature(fn)
    params = sig.parameters

    if "parameters" in params:
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        extra = {}
        if accepts_kwargs or "player" in params:
            extra["player"] = player
        if accepts_kwargs or "session_memory" in params:
            extra["session_memory"] = session_memory
        return fn(parameters, **extra)

    return fn(**parameters)
