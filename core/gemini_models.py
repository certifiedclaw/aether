"""core/gemini_models.py - which Gemini models can this key actually use?

The settings dropdown used to be a hardcoded list, so retired or key-restricted
models (a live log showed gemini-2.5-pro and gemini-2.5-flash-lite answering 404)
stayed selectable and each one then rested for an hour. Google's OpenAI-compatible
`GET <base>/models` lists what the key can reach, so the dropdown asks it, and
falls back to the built-in list when that can't be reached (offline, bad key).

Pure Python: no Qt. The one network call is `fetch_available`.

    live = fetch_available(api_key)                    # list[str] | None
    choices = merge_choices(static_models, live, current="gemini-2.5-pro")
    # -> [(model_id, label), ...] ready for QComboBox.addItem(label, userData=id)
"""
from __future__ import annotations

import re

from core.llm_client import GEMINI_BASE_URL

# Models in /models that cannot do text chat through the chat-completions API.
_NOT_CHAT = re.compile(
    r"embed|aqa|imagen|veo|tts|image|audio|live|vision|robotics|learnlm|"
    r"computer-use|native|transcribe|gemma|bison|gecko",
    re.IGNORECASE,
)
# Dated snapshots / previews clutter a dropdown: "gemini-2.5-flash-preview-05-20",
# "gemini-2.0-flash-001", "...-latest" aliases. Kept only if nothing else exists
# or the user already has one selected.
_SNAPSHOT = re.compile(
    r"preview|exp(?:erimental)?(?:-|$)|-latest$|-\d{3,4}$|-\d{2}-\d{2}(?:-|$)",
    re.IGNORECASE,
)


def _clean_id(raw: object) -> str:
    """'models/gemini-2.5-pro' -> 'gemini-2.5-pro'; '' when not a usable string."""
    s = str(raw or "").strip()
    if s.lower().startswith("models/"):
        s = s[len("models/"):]
    return s


def parse_model_list(payload: object) -> list[str]:
    """Chat-capable Gemini model ids from a /models response (OpenAI shape
    {"data": [{"id": "models/..."}]}, also tolerating Google's native
    {"models": [{"name": "models/..."}]}). Order preserved, duplicates dropped."""
    items: list = []
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    out: list[str] = []
    for item in items if isinstance(items, list) else []:
        raw = item.get("id") or item.get("name") if isinstance(item, dict) else item
        mid = _clean_id(raw)
        if (mid.lower().startswith("gemini-") and not _NOT_CHAT.search(mid)
                and mid not in out):
            out.append(mid)
    return out


def _version_key(model_id: str) -> tuple:
    """Newest-first sort key: 'gemini-3.8-flash' > 'gemini-2.5-pro' > 'gemini-2.0-flash'."""
    m = re.match(r"gemini-(\d+)(?:\.(\d+))?", model_id)
    major, minor = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return (-major, -minor, model_id)


def pretty_label(model_id: str) -> str:
    """'gemini-2.5-flash-lite' -> 'Gemini 2.5 Flash Lite'."""
    parts = model_id.split("-")
    return " ".join(p.capitalize() if not p[:1].isdigit() else p for p in parts)


def merge_choices(static: list[tuple[str, str]], live: list[str] | None,
                  current: str = "") -> list[tuple[str, str]]:
    """The dropdown rows: [(model_id, label)].

    * `live` is None (couldn't ask) -> the static list unchanged, plus `current`
      if it isn't in it, so a saved custom model is never silently dropped.
    * `live` is a list -> only models the key can use. Known models keep their
      friendly static label and order; other live models follow, newest first,
      without dated snapshots/previews (unless nothing else is left).
    * A saved `current` model that isn't usable stays selectable but is flagged,
      so the user sees *why* their chat is failing instead of it vanishing.
    """
    labels = dict(static)
    current = _clean_id(current)

    if live is None:
        rows = list(static)
    else:
        usable = set(live)
        rows = [(m, label) for m, label in static if m in usable]
        extras = [m for m in live if m not in labels]
        tidy = [m for m in extras if not _SNAPSHOT.search(m)] or extras
        for m in sorted(tidy, key=_version_key):
            rows.append((m, pretty_label(m)))
        if not rows:                       # empty/odd response: don't leave the user with nothing
            rows = list(static)

    if current and current not in {m for m, _ in rows}:
        base = (labels.get(current) or pretty_label(current)).split(" — ")[0]   # name only, no tagline
        note = "not available for your key" if live is not None else "saved"
        rows.insert(0, (current, f"{base} — {note}"))
    return rows


def fetch_available(api_key: str, timeout: float = 6.0, *, base_url: str = GEMINI_BASE_URL,
                    get=None) -> list[str] | None:
    """Ask Google which models `api_key` can use. None on any failure (no key,
    offline, 401/403, odd payload) - callers then keep the static list.
    `get` is requests.get by default; injectable for tests."""
    key = (api_key or "").strip()
    if not key:
        return None
    try:
        if get is None:
            import requests
            get = requests.get
        r = get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        if getattr(r, "status_code", 0) != 200:
            return None
        models = parse_model_list(r.json())
        return models or None
    except Exception:  # noqa: BLE001 - a settings dropdown must never raise
        return None
