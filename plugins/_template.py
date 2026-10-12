"""
Drop-in Aether plugin template.

Copy this file, rename it without a leading underscore, then fill in PLUGIN
and run(). Aether discovers it from plugins/ and exposes it as a callable tool.
This format is compatible with Mark-LI plugins.
"""

PLUGIN = {
    "name": "my_plugin",
    "description": (
        "One or two sentences the assistant uses to decide when to call this "
        "tool. Be explicit about what user requests should trigger it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "example_arg": {
                "type": "STRING",
                "description": "What this argument means.",
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """
    parameters: arguments extracted by the model.
    player: optional UI object; use player.write_log(...) for status messages.
    session_memory: optional active memory engine.
    """
    example_arg = parameters.get("example_arg", "")
    result = f"Did the thing with {example_arg}."

    if player:
        try:
            player.write_log(f"PLUGIN my_plugin: {result}")
        except Exception:
            pass

    return result
