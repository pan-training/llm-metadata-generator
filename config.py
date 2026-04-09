import os

OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "data/metadata.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

CRON_METADATA_INTERVAL = int(os.environ.get("CRON_METADATA_INTERVAL", 1440))
CRON_ONTOLOGY_INTERVAL = int(os.environ.get("CRON_ONTOLOGY_INTERVAL", 720))
CRON_TOOLS_INTERVAL = int(os.environ.get("CRON_TOOLS_INTERVAL", 168))
