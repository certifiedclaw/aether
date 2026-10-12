"""
integrations/ — Aether v3 external framework integrations.

All modules are optional. Import guards ensure Aether starts fine
even if none of the packages are installed.

Modules:
  langchain_tools   — LangChain: web search, Wikipedia, summarise, ReAct agent
  oi_shell          — Open Interpreter: natural language computer control
  llama_index_rag   — LlamaIndex: production RAG pipelines
"""

__all__ = ["langchain_tools", "oi_shell", "llama_index_rag"]
