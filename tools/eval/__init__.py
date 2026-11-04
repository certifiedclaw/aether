"""tools/eval - run external eval harnesses against Aether's OpenAI-compatible API (roadmap #101).

promptfoo, inspect-ai and lm-evaluation-harness run as SEPARATE processes (their own venv / npx),
so nothing here adds a dependency to Aether's main environment. They reach Aether only through
POST /v1/chat/completions (roadmap #41): text in, text out, no tools, no memory, no files.
"""
