"""LLM client factory and task-to-model mapping."""

from flask import current_app
from openai import OpenAI

# Task-to-model default mapping. In future these will be looked up from model_assignments table.
TASK_MODELS: dict[str, str] = {
    "content_relevance": "gpt-4o-mini",
    "content_summary": "gpt-4o",
    "link_decision": "gpt-4o-mini",
    "json_ld_review": "gpt-4o",
    "ontology_embedding": "text-embedding-3-small",
    "tool_discovery": "gpt-4o-mini",
    "model_selection": "gpt-4o-mini",
    "default": "gpt-4o",
}


def get_llm_client(task: str = "default") -> OpenAI:
    """Return an OpenAI-compatible client configured from current app config."""
    api_base: str = current_app.config.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    api_key: str = current_app.config.get("OPENAI_API_KEY", "")
    return OpenAI(base_url=api_base, api_key=api_key)


def get_model_for_task(task: str = "default") -> str:
    """Return the preferred model name for the given task.

    TODO: In future, look up the model_assignments table for per-task overrides.
    """
    return TASK_MODELS.get(task, TASK_MODELS["default"])
