"""tools/secrets_cli.py -- keep API keys in the OS credential store instead of a JSON file.

    python tools\\secrets_cli.py status                 where each key resolves from (never shows it)
    python tools\\secrets_cli.py set gemini_api_key     prompt (hidden) and store in the OS keyring
    python tools\\secrets_cli.py migrate                DRY RUN: show what would move
    python tools\\secrets_cli.py migrate --apply        move keys from config/api_keys.json to the keyring
    python tools\\secrets_cli.py delete gemini_api_key  remove from the keyring

Needs the optional package:  python -m pip install keyring

`migrate --apply` is safe by construction: each key is written to the keyring, READ BACK
and compared before its entry in the file is set to "". If anything fails, that key is
left in the file untouched. The file is rewritten atomically (temp file + replace), so a
crash cannot leave it half-written; other settings are preserved (indentation is
normalised to 2 spaces).

The environment (AETHER_GEMINI_API_KEY / GEMINI_API_KEY ...) always wins over both.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import credentials  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_status(args) -> int:
    try:
        cfg = _load(args.config)
    except Exception as e:  # noqa: BLE001
        print(f"(could not read {args.config.name}: {type(e).__name__}; showing env/keyring only)")
        cfg = {}
    print(f"keyring package: {'installed' if credentials.keyring_available() else 'NOT installed (pip install keyring)'}")
    for name in credentials.SECRET_NAMES:
        src = credentials.source_of(name, cfg)
        n = len(credentials.get_secret(name, cfg))
        print(f"  {name:20s} {('set via ' + src + f' ({n} chars)') if src else 'not set'}")
        if src == "env" and (cfg.get(name) or "").strip():
            print(f"  {'':20s} (a copy also sits in {args.config.name})")
    return 0


def cmd_set(args) -> int:
    name = args.name
    if name not in credentials.SECRET_NAMES:
        print(f"unknown secret '{name}'. Choose one of: {', '.join(credentials.SECRET_NAMES)}")
        return 2
    value = getpass.getpass(f"{name} (input hidden): ").strip()
    try:
        credentials.set_secret(name, value)
    except RuntimeError as e:
        print(f"Not stored: {e}")
        return 1
    print(f"Stored {name} in the OS keyring.")
    return 0


def cmd_delete(args) -> int:
    if args.name not in credentials.SECRET_NAMES:
        print(f"unknown secret '{args.name}'. Choose one of: {', '.join(credentials.SECRET_NAMES)}")
        return 2
    print("Removed from the keyring." if credentials.delete_secret(args.name) else "Nothing to remove.")
    return 0


def cmd_migrate(args) -> int:
    path = args.config
    if not credentials.keyring_available():
        print("The 'keyring' package is not installed: python -m pip install keyring")
        return 1
    try:
        cfg = _load(path)
    except Exception as e:  # noqa: BLE001
        print(f"Could not read {path}: {type(e).__name__}. Nothing changed.")
        return 1
    todo = [n for n in credentials.SECRET_NAMES if str(cfg.get(n) or "").strip()]
    if not todo:
        print("No keys stored in the config file; nothing to migrate.")
        return 0
    print(("APPLYING" if args.apply else "DRY RUN (add --apply to do it)") + f" on {path}:")
    moved = 0
    failed = 0
    for name in todo:
        value = str(cfg[name]).strip()
        if not args.apply:
            print(f"  would move {name} ({len(value)} chars) to the keyring and blank it in the file")
            continue
        try:
            credentials.clear_cache()
            existing = credentials._keyring_get(name)
            if existing and existing != value:
                print(f"  {name}: NOT moved; the keyring already holds a DIFFERENT value. "
                      f"Check which is current ('set {name}' to replace it), then blank the file entry yourself.")
                failed += 1
                continue
            if not existing:
                credentials.set_secret(name, value)
            credentials.clear_cache()
            if credentials._keyring_get(name) != value:          # read-back check
                raise RuntimeError("read-back did not match")
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: NOT moved ({e}); left in the file")
            failed += 1
            continue
        cfg[name] = ""
        moved += 1
        print(f"  {name}: stored in the keyring and verified")
    if args.apply and moved:
        _atomic_write(path, cfg)
        print(f"Blanked {moved} key(s) in {path.name}. The app now reads them from the keyring.")
        print("If this file was ever shared or committed, rotate those keys: they were readable there.")
    return 1 if failed else 0       # non-zero when any key could not be moved (it stays in the file)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("set"); p.add_argument("name"); p.set_defaults(fn=cmd_set)
    p = sub.add_parser("delete"); p.add_argument("name"); p.set_defaults(fn=cmd_delete)
    p = sub.add_parser("migrate"); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_migrate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
