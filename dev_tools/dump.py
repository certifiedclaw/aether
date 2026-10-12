"""
dump.py — MARK XL Codebase Exporter
Generates a single text file containing all relevant source files.
Perfect for sharing with AI assistants or for code reviews.
"""

import os
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────
# Folders to skip entirely (to avoid bloating the dump)
IGNORE_LIST = {
    '.git', '__pycache__', '.venv', 'venv', 'node_modules',
    'data', 'logs', '.pytest_cache', '.mypy_cache', '.ruff_cache'
}

# File extensions to include
ALLOWED_EXTENSIONS = {'.py', '.md', '.json', '.txt', '.yaml', '.toml', '.ini'}

# Optional: only include files from these subdirectories
# Set to None to include everything (except ignored folders)
INCLUDE_SUBDIRS = None  # e.g., ['core', 'actions', 'agent', 'memory', 'integrations']

# Output file name
OUTPUT_FILE = 'code_dump.txt'


def dump_codebase(root_dir: str, output_file: str) -> None:
    """
    Walk the project directory and write all matching files into a single text file.
    """
    root = Path(root_dir).resolve()
    if not root.exists():
        print(f"❌ Root directory not found: {root}")
        return

    print(f"📂 Scanning: {root}")
    file_count = 0

    with open(output_file, 'w', encoding='utf-8') as out:
        # Iterate over all files recursively
        for path in root.rglob('*'):
            # Skip ignored directories (by checking path parts)
            if any(part in IGNORE_LIST for part in path.parts):
                continue

            # Restrict to specific subdirs if configured
            if INCLUDE_SUBDIRS is not None:
                rel = path.relative_to(root)
                if not any(str(rel).startswith(d) for d in INCLUDE_SUBDIRS):
                    continue

            # Only process files with allowed extensions
            if path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
                relative_path = path.relative_to(root)
                out.write(f"\n\n{'='*80}\n")
                out.write(f"FILE: {relative_path}\n")
                out.write(f"{'='*80}\n\n")
                try:
                    content = path.read_text(encoding='utf-8')
                    out.write(content)
                    file_count += 1
                except UnicodeDecodeError:
                    out.write(f"[SKIPPED: binary or non-UTF-8 file]")
                except Exception as e:
                    out.write(f"[COULD NOT READ FILE: {e}]")

    print(f"✅ Done! Exported {file_count} file(s) to: {output_file}")


if __name__ == "__main__":
    # Anchored on this file's location (repo_root/dev_tools/dump.py) rather
    # than the current working directory, so `python dump.py` still dumps
    # the whole project correctly even when run from inside dev_tools/
    # itself instead of the repo root.
    _repo_root = Path(__file__).resolve().parent.parent
    dump_codebase(str(_repo_root), str(_repo_root / OUTPUT_FILE))
    print("\n💡 Tip: If the file is too large, edit INCLUDE_SUBDIRS at the top to export only specific folders.")