"""
pytest configuration for tests/.

stress_test.py is a standalone CLI benchmark script — run directly with
`python tests/stress_test.py [--iterations N] [--skip-llm]`, never through
pytest. Its `test_*(env, iterations)` functions take plain positional
arguments that aren't registered pytest fixtures, so if pytest is allowed
to collect the file, every one of those functions fails at setup with
"fixture 'env' not found" — and by default pytest treats any collection
error as fatal and refuses to run the rest of the suite at all, so
`pytest tests/` was reporting 0 tests run instead of the real suite's
results. Excluding it here keeps stress_test.py's CLI usage completely
unaffected while letting `pytest tests/` collect and run the actual
test suite (test_all_critical.py, test_core.py) cleanly.
"""
collect_ignore = ["stress_test.py"]


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_gemini_cooldowns():
    """The Gemini circuit breaker is module-level state: never let one test's
    recorded failure put another test's Gemini calls on cooldown."""
    try:
        import core.llm_client as lc
    except Exception:  # noqa: BLE001 - tests that don't need llm_client still run
        yield
        return
    lc.reset_gemini_cooldowns()
    yield
    lc.reset_gemini_cooldowns()


@pytest.fixture(autouse=True)
def _reset_risk_state():
    """Task-tier grants/refusals are module-level state: isolate every test."""
    try:
        import core.risk as risk
    except Exception:  # noqa: BLE001
        yield
        return
    risk.reset_state()
    yield
    risk.reset_state()
