"""LLM client factory and task-to-model mapping.

Model defaults are read from environment variables (LLM_MODEL_SMALL,
LLM_MODEL_LARGE, LLM_MODEL_EMBEDDING) so the same code works against any
OpenAI-compatible backend (OpenAI, LocalAI, Ollama, …).

TODO (issue #7): look up per-task model overrides from the model_assignments
  DB table instead of using the three-tier env-var approach.
"""

import os

from flask import current_app
from openai import OpenAI

# Task categories: each task maps to one of three tiers.
_SMALL_TASKS = {"content_relevance", "link_decision", "model_selection"}
_EMBEDDING_TASKS = {"ontology_embedding"}
# All other tasks (content_summary, json_ld_review, tool_discovery, …) use the large model.


def get_llm_client(task: str = "default") -> OpenAI:
    """Return an OpenAI-compatible client configured from current app config."""
    api_base: str = current_app.config.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    api_key: str = current_app.config.get("OPENAI_API_KEY", "")
    return OpenAI(base_url=api_base, api_key=api_key)


def get_model_for_task(task: str = "default") -> str:
    """Return the preferred model name for the given task.

    Reads from environment variables so no Flask application context is
    required — agents can call this at any time, even in background threads.

    Task tiers:
      small  – LLM_MODEL_SMALL (default: qwen2.5-coder-7b-instruct)
               Fast, cheap: content_relevance, link_decision, model_selection.
      large  – LLM_MODEL_LARGE (default: gemma-3-27b-it)
               Quality: content_summary, json_ld_review, tool_discovery, default.
      embed  – LLM_MODEL_EMBEDDING (default: qwen3-embedding-8b)
               Embedding: ontology_embedding.
    """
    small = os.environ.get("LLM_MODEL_SMALL", "qwen2.5-coder-7b-instruct")
    large = os.environ.get("LLM_MODEL_LARGE", "gemma-3-27b-it")
    embedding = os.environ.get("LLM_MODEL_EMBEDDING", "qwen3-embedding-8b")

    if task in _SMALL_TASKS:
        return small
    if task in _EMBEDDING_TASKS:
        return embedding
    return large
