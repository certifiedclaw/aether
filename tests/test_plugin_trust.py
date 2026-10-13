import hashlib

from core.plugins import PluginSystem


def test_plugin_import_requires_exact_trusted_hash(tmp_path):
    marker = tmp_path / "imported.txt"
    plugin_path = tmp_path / "sample.py"
    plugin_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
        "PLUGIN_NAME = 'sample'\nPLUGIN_TOOLS = {}\n",
        encoding="utf-8",
    )
    config = {
        "plugins.plugin_dir": str(tmp_path),
        "plugins.trusted_hashes": {},
    }
    system = PluginSystem(config)

    assert system.load_all() == 0
    assert not marker.exists()
    assert "SHA-256" in system.list_for_ui()[0]["error"]

    config["plugins.trusted_hashes"]["sample.py"] = hashlib.sha256(
        plugin_path.read_bytes()).hexdigest()
    assert system.reload() == 1
    assert marker.read_text(encoding="utf-8") == "loaded"

    marker.unlink()
    plugin_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('changed')\n"
        "PLUGIN_NAME = 'sample'\nPLUGIN_TOOLS = {}\n",
        encoding="utf-8",
    )
    assert system.reload() == 0
    assert not marker.exists()
