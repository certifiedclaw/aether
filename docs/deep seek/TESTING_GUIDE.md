
---

## 📄 `docs/TESTING_GUIDE.md`

```markdown
# Aether — Testing Guide

This document explains how to test Aether effectively, covering manual testing (UI/voice) and automated unit tests.

## 1. Automated Test Suites

### `tests/test_core.py`
- **Purpose**: Tests the tool-call parser and dispatcher.
- **Coverage**: JSON extraction from markdown, XML tags, trailing commas, missing parameters.
- **Status**: ✅ **All 6 tests passing**.

### `tests/test_all_critical.py`
- **Purpose**: Stress-tests the most failure-prone components (config, security, memory, LLM retries).
- **Coverage**:
  - Config adapter dotted-key lookup
  - Path ACL (safety)
  - Smart context orphaned tool messages
  - Hybrid memory BM25 incremental update
  - LLM connection retry
  - Extra tools dispatch
  - Security rate limiting & PIN expiry
  - File controller path resolution
  - Evaluator quick check
  - Markdown parser
- **Current Status**: ⚠️ **3 tests failing** (see Section 2 below).

### Running All Tests
```bash
cd C:\Users\igome\Downloads\aether_repo
pytest tests/ -v
To run a specific test:

bash
pytest tests/test_all_critical.py::test_config_adapter_dotted_lookup -v
2. Understanding the 3 Failing Tests
Test	Failure	Implication	Fix Priority
test_config_adapter_dotted_lookup	100 != 50	_MarkConfigAdapter.get() ignores config values with dots (e.g., memory.chat_history_limit). This breaks all memory/RAG settings.	HIGH – affects all users
test_security_rate_limiting	True is False	The rate limit kicks in after 4 attempts instead of 5, or the is_rate_limited logic is flawed.	MEDIUM – security feature
test_pin_consumption_and_expiry	TypeError	Mocking time.time in the test is broken. The fix is to avoid mocking entirely and expire the PIN manually.	LOW – test-only issue
How to Fix Them
Fix #1 (Config Adapter)
File: memory/config_manager.py

Replace:

python
def get(self, key: str, default=None):
    top = key.split(".")[0]
    if top in self._raw:
        return self._raw.get(key, default)
    return self._DEFAULTS.get(key, default)
With:

python
def get(self, key: str, default=None):
    if key in self._raw:
        return self._raw.get(key, default)
    return self._DEFAULTS.get(key, default)
Fix #2 (Security Rate Limiting)
File: security/tokens.py

Ensure _RATE_LIMIT_MAX = 5. In the test, change the loop to run 4 times and assert is_rate_limited() is False. If it still fails, check that _prune_attempts() doesn't remove entries prematurely (it removes entries older than _RATE_LIMIT_WINDOW – 15 min). Since the test runs instantly, no entries should be pruned.

Fix #3 (PIN Expiry Test)
File: tests/test_all_critical.py

Replace the patch('security.tokens.time.time') section with:

python
pin2 = mgr.issue_pin()
mgr._pending[pin2] = time.time() - 1  # Manually expire
assert mgr.consume_pin(pin2) is False
3. Manual Testing (UI + Voice)
A comprehensive manual checklist is available at TESTING.md. Key areas:

Voice interaction: Barge-in, streaming TTS, language switching.

Tool calling: Test each of the 20+ tools (open_app, web_search, etc.).

Memory: Tell Aether your name, restart, ask it to recall.

Dashboard: Pair a phone, send a text command, upload a file.

Quick Smoke Test
text
1. Launch: python main.py
2. Say "What's the weather in London?"
3. Say "Open Notepad"
4. Say "My name is Test"
5. Restart Aether.
6. Say "What is my name?"
7. If it replies "Test", the memory system is working.
4. Writing New Tests
When adding a new feature or fixing a bug, always add a corresponding test.

Test Template
python
# tests/test_my_feature.py
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_my_function():
    from my_module import my_function
    result = my_function("test_input")
    assert result == "expected_output"

def test_my_function_edge_case():
    from my_module import my_function
    result = my_function("")
    assert "error" in result.lower()
Mocking External Calls
Use unittest.mock.patch to mock network requests or subprocess calls:

python
from unittest.mock import patch

def test_ollama_retry():
    with patch('core.llm_client.requests.post') as mock_post:
        mock_post.side_effect = [ConnectionError, MagicMock()]
        # ... assert that retry logic works