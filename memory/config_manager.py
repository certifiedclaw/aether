import json
import sys
from pathlib import Path

from core.data_home import data_path, resolve_data_path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
from core import config_home as _config_home
CONFIG_FILE = _config_home.config_path()
CONFIG_DIR  = CONFIG_FILE.parent


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_config(cfg: dict) -> None:
    ensure_config_dir()
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(cfg)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def load_api_keys() -> dict:
    return _config_home.load_config(CONFIG_FILE)   # never raises; falls back to last good copy


class _MarkConfigAdapter:
    """
    Thin adapter so Aether-borrowed modules (semantic_memory, rag, plugins, etc.)
    can call config.get("dotted.key", default) and config.property_name
    against Aether's JSON config without needing Aether's config.py.
    """
    _DEFAULTS = {
        "memory.db_path":              str(data_path("memory.db")),
        "memory.relevance_threshold":  0.35,
        "memory.chat_history_limit":   100,
        "memory.embedding_model":      "all-MiniLM-L6-v2",
        "memory.window":               12,
        "rag.index_path":              str(data_path("rag_index")),
        "rag.chunk_size":              500,
        "rag.chunk_overlap":           50,
        "rag.top_k":                   5,
        "plugins.plugin_dir":          "./plugins",
        "plugins.trusted_hashes":      {},
        "hotkey.toggle":               "ctrl+alt+j",
        "safety.allowed_paths":        ["~", "."],
        "safety.confirm_on":           ["delete", "shell", "write_file"],
        "llm.ollama_url":              "http://localhost:11434",
        "llm.fast_model":              "qwen2.5:7b",
        "llm.deep_model":              "qwen2.5:7b",
        "llm.vision_model":            "llava:latest",
        "llm.auto_route":              False,
        "llm.route_threshold":         400,
        "llm.temperature":             0.4,
        "llm.num_ctx":                 8192,
        "llm.timeout_seconds":         120,
        "logging.log_dir":             "./logs",
        "browser.cdp_port":            9222,
        "mcp.enabled":                 False,
        "mcp.servers":                 [],
        "osint.max_output_chars":      2000,
        "osint.timeout_seconds":       30,
        "oi.safe_mode":                "ask",
        "oi.auto_run":                 False,
        "llm.embed_model":             "nomic-embed-text",
        "llama_index.default_collection": "aether",
        "llama_index.chroma_dir":      str(data_path("chroma_db")),
        "goals.path":                   str(data_path("goals.json")),
        "langchain.max_research_steps": 4,
    }

    def __init__(self):
        self._raw = load_api_keys()

    def get(self, key: str, default=None):
        # Check raw JSON first (full dotted key, since _raw is flat, not nested)
        if key in self._raw:
            value = self._raw.get(key, default)
        else:
            value = self._DEFAULTS.get(key, default)
        path_names = {
            "memory.db_path": "memory.db",
            "rag.index_path": "rag_index",
            "llama_index.chroma_dir": "chroma_db",
            "goals.path": "goals.json",
        }
        if key in path_names:
            return str(resolve_data_path(value, path_names[key]))
        return value

    # Properties expected by router.py / diagnostics.py
    @property
    def ollama_url(self):
        return self._raw.get("llm_url", self._DEFAULTS["llm.ollama_url"])

    @property
    def fast_model(self):
        return self._raw.get("llm_model", self._DEFAULTS["llm.fast_model"])

    @property
    def deep_model(self):
        return self._raw.get("llm_model", self._DEFAULTS["llm.deep_model"])

    @property
    def vision_model(self):
        return self._DEFAULTS["llm.vision_model"]

    @property
    def cdp_port(self):
        return int(self._DEFAULTS["browser.cdp_port"])

    @property
    def allowed_paths(self):
        import os
        return [os.path.expanduser(p) for p in self._DEFAULTS["safety.allowed_paths"]]

    @property
    def confirm_on(self):
        return self._DEFAULTS["safety.confirm_on"]


_mark_config_instance = None


def get_mark_config() -> _MarkConfigAdapter:
    global _mark_config_instance
    if _mark_config_instance is None:
        _mark_config_instance = _MarkConfigAdapter()
    return _mark_config_instance


def invalidate_config_cache() -> None:
    """Force the next get_mark_config() call to build a fresh
    _MarkConfigAdapter (re-reading from disk via load_api_keys()) instead of
    returning the stale cached singleton.

    Called by main.py's _do_reconfigure() after the user changes settings
    live. Without this, every subsystem that was handed get_mark_config()'s
    result (MemoryEngine, HybridMemory, PluginSystem, etc.) keeps reading
    the config snapshot taken the first time it was ever constructed --
    live settings changes never propagate anywhere until a full restart.
    """
    global _mark_config_instance
    _mark_config_instance = None


def is_configured() -> bool:
    cfg = load_api_keys()
    return (
        bool(cfg.get("os_system")) and
        bool(cfg.get("llm_model")) and
        bool(cfg.get("stt_engine")) and
        bool(cfg.get("tts_engine"))
    )
