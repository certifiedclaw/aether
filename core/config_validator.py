"""
core/config_validator.py — Aether Configuration Validator

Called at startup before any subsystem loads.
Catches missing keys, bad paths, invalid engine names, and unreachable URLs
so you get a clear error message instead of a cryptic crash later.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    level:   str   # "error" | "warning" | "info"
    key:     str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self)   -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add(self, level: str, key: str, msg: str) -> None:
        self.issues.append(ValidationIssue(level, key, msg))

    def format(self) -> str:
        if not self.issues:
            return "✅ Configuration valid."
        lines = ["Configuration validation:"]
        for i in self.issues:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(i.level, "?")
            lines.append(f"  {icon}  [{i.key}]  {i.message}")
        return "\n".join(lines)


# Validators

_VALID_STT   = {"whisper", "vosk", "faster_whisper"}
_VALID_TTS   = {"edgetts", "kokoro", "elevenlabs", "edge_tts", "pyttsx3"}
_VALID_SAFE  = {"ask", "off", "auto"}


def _check_ollama_url(result: ValidationResult, cfg) -> None:
    # config uses "ollama_url" or "llm_url" — check both; "llm.ollama_url" is legacy
    url = (cfg.get("ollama_url", "")
           or cfg.get("llm_url", "")
           or cfg.get("llm.ollama_url", ""))
    if not url:
        result.add("error", "llm.ollama_url", "Missing — Ollama URL not set")
        return
    if not url.startswith("http"):
        result.add("error", "llm.ollama_url", f"Must start with http:// — got: {url}")
    try:
        import requests
        requests.get(f"{url}/api/tags", timeout=3)
    except Exception as e:
        result.add("warning", "llm.ollama_url",
            f"Could not reach Ollama at {url} — is it running? ({e})")


def _check_model(result: ValidationResult, cfg) -> None:
    model = cfg.get("llm.model", cfg.get("llm_model", ""))
    if not model:
        result.add("error", "llm.model",
            "No model configured — set llm_model in config/api_keys.json")
    elif " " in model:
        result.add("error", "llm.model",
            f"Model name contains spaces: '{model}' — use format like qwen3:8b")


def _check_stt(result: ValidationResult, cfg) -> None:
    engine = cfg.get("stt_engine", "whisper").lower().replace("-", "_")
    if engine not in _VALID_STT:
        result.add("warning", "stt_engine",
            f"Unknown STT engine '{engine}' — valid: {sorted(_VALID_STT)}")


def _check_tts(result: ValidationResult, cfg) -> None:
    engine = cfg.get("tts_engine", "edgetts").lower().replace("-", "_")
    normalized = {"edge_tts": "edgetts"}.get(engine, engine)
    if normalized not in _VALID_TTS:
        result.add("warning", "tts_engine",
            f"Unknown TTS engine '{engine}' — valid: {sorted(_VALID_TTS)}")


def _check_paths(result: ValidationResult, cfg) -> None:
    """Check that configured file paths are accessible."""
    path_keys = {
        "memory.db_path":   False,   # file — parent must exist or be creatable
        "rag.index_path":   False,
        "logging.log_dir":  False,
    }
    for key, must_exist in path_keys.items():
        val = cfg.get(key, "")
        if not val:
            continue
        p = Path(os.path.expanduser(val))
        if must_exist and not p.exists():
            result.add("error", key, f"Path does not exist: {p}")
        else:
            # Try creating parent
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                result.add("warning", key, f"Cannot create directory: {e}")


def _check_oi_safe_mode(result: ValidationResult, cfg) -> None:
    mode = cfg.get("oi.safe_mode", "ask")
    if mode not in _VALID_SAFE:
        result.add("warning", "oi.safe_mode",
            f"Unknown safe_mode '{mode}' — valid: ask | off | auto")


def _check_mcp(result: ValidationResult, cfg) -> None:
    if not cfg.get("mcp.enabled", False):
        return
    servers = cfg.get("mcp.servers", [])
    if not servers:
        result.add("info", "mcp.enabled",
            "MCP enabled but no servers configured in mcp.servers")
    for i, srv in enumerate(servers):
        if not srv.get("name"):
            result.add("error", f"mcp.servers[{i}]", "Missing 'name' field")
        if not srv.get("command"):
            result.add("error", f"mcp.servers[{i}]", "Missing 'command' field")


def _check_memory(result: ValidationResult, cfg) -> None:
    limit = cfg.get("memory.chat_history_limit", 100)
    try:
        if int(limit) < 10:
            result.add("warning", "memory.chat_history_limit",
                f"Value {limit} is very low — may cause poor recall")
    except (ValueError, TypeError):
        result.add("error", "memory.chat_history_limit",
            f"Must be an integer — got: {limit!r}")

    threshold = cfg.get("memory.relevance_threshold", 0.35)
    try:
        t = float(threshold)
        if not 0.0 <= t <= 1.0:
            result.add("warning", "memory.relevance_threshold",
                f"Should be 0.0–1.0 — got: {t}")
    except (ValueError, TypeError):
        result.add("error", "memory.relevance_threshold",
            f"Must be a float — got: {threshold!r}")


# Public API

def validate_config(config=None) -> ValidationResult:
    """
    Validate the full configuration.
    Call this at startup before loading any subsystem.
    """
    from memory.config_manager import get_mark_config
    cfg = config or get_mark_config()
    result = ValidationResult()

    _check_ollama_url(result, cfg)
    _check_model(result, cfg)
    _check_stt(result, cfg)
    _check_tts(result, cfg)
    _check_paths(result, cfg)
    _check_oi_safe_mode(result, cfg)
    _check_mcp(result, cfg)
    _check_memory(result, cfg)

    if result.ok and not result.warnings:
        result.add("info", "all", "All checks passed ✓")

    return result


if __name__ == "__main__":
    r = validate_config()
    print(r.format())
    if not r.ok:
        import sys
        sys.exit(1)
