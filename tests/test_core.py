# tests/test_core.py
import pytest
import json
import re
from unittest.mock import MagicMock, patch

# ---- 1. PARSER (extract tool calls from LLM output) ----
def extract_tool_call(llm_output: str) -> dict:
    # Same as before, but with better error handling
    cleaned = re.sub(r'```json\s*', '', llm_output)
    cleaned = re.sub(r'```\s*', '', cleaned)
    # XML-like tags
    xml_match = re.search(r'<tool_call>(.*?)</tool_call>', cleaned, re.DOTALL)
    if xml_match:
        try:
            return json.loads(xml_match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # fallback: find first {...}
    json_match = re.search(r'({.*})', cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            # fix trailing commas
            fixed = re.sub(r',\s*}', '}', json_match.group(1))
            fixed = re.sub(r',\s*]', ']', fixed)
            return json.loads(fixed)
    return {}

# ---- 2. MOCK DISPATCHER ----
class MockToolRegistry:
    def __init__(self):
        self.tools = {
            "browser_open": self.mock_browser,
            "get_weather": self.mock_weather,
        }
    def mock_browser(self, url: str):
        return {"success": True, "data": f"Opened {url}"}
    def mock_weather(self, city: str):
        return {"success": True, "data": f"Weather in {city} is Sunny"}
    def execute(self, tool_name: str, params: dict):
        if tool_name not in self.tools:
            return {"success": False, "error": f"Tool {tool_name} not found"}
        try:
            return self.tools[tool_name](**params)
        except Exception as e:
            return {"success": False, "error": str(e)}

# ---- 3. TESTS ----
def test_parse_sloppy_json():
    raw = """Sure! ```json
    {"name": "browser_open", "params": {"url": "https://github.com"}}
    ```"""
    result = extract_tool_call(raw)
    assert result.get("name") == "browser_open"
    assert result.get("params", {}).get("url") == "https://github.com"

def test_parse_xml():
    raw = """<tool_call>{"name": "get_weather", "params": {"city": "Tokyo"}}</tool_call>"""
    result = extract_tool_call(raw)
    assert result.get("name") == "get_weather"
    assert result.get("params", {}).get("city") == "Tokyo"

def test_parse_trailing_comma():
    raw = """{"name": "browser_open", "params": {"url": "https://google.com", }}"""
    result = extract_tool_call(raw)
    assert result.get("name") == "browser_open"

def test_dispatcher_runs():
    registry = MockToolRegistry()
    tool_call = {"name": "get_weather", "params": {"city": "London"}}
    result = registry.execute(tool_call["name"], tool_call["params"])
    assert result["success"] is True
    assert "London" in result["data"]

def test_dispatcher_handles_missing_params():
    registry = MockToolRegistry()
    tool_call = {"name": "get_weather", "params": {}}
    result = registry.execute(tool_call["name"], tool_call["params"])
    assert result["success"] is False
    assert "error" in result

def test_full_pipeline():
    llm_output = """I found the info. <tool_call>{"name": "browser_open", "params": {"url": "example.com"}}</tool_call>"""
    tool_call = extract_tool_call(llm_output)
    registry = MockToolRegistry()
    result = registry.execute(tool_call["name"], tool_call["params"])
    assert result["success"] is True
    assert "Opened example.com" in result["data"]