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
