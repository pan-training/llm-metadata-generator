import os

OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "data/metadata.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

CRON_METADATA_INTERVAL = int(os.environ.get("CRON_METADATA_INTERVAL", 1440))
CRON_ONTOLOGY_INTERVAL = int(os.environ.get("CRON_ONTOLOGY_INTERVAL", 720))
CRON_TOOLS_INTERVAL = int(os.environ.get("CRON_TOOLS_INTERVAL", 168))

# LLM model configuration.
# LLM_MODEL_SMALL: fast/cheap model for classification and routing tasks.
# LLM_MODEL_LARGE: quality model for extraction and review tasks.
# LLM_MODEL_EMBEDDING: embedding model for ontology vector search (TODO #6).
# Defaults match commonly available open-source models on LocalAI-compatible backends.
LLM_MODEL_SMALL = os.environ.get("LLM_MODEL_SMALL", "qwen2.5-coder-7b-instruct")
LLM_MODEL_LARGE = os.environ.get("LLM_MODEL_LARGE", "gemma-3-27b-it")
LLM_MODEL_EMBEDDING = os.environ.get("LLM_MODEL_EMBEDDING", "qwen3-embedding-8b")
