"""Periodic ontology reindex cron job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask

from app.agents import get_llm_client
from app.agents.ontology import OntologyIndexerAgent
from app.db.sqlite import get_db


def refresh_stale_ontologies(app: Flask) -> None:
    """Refresh ontology indexes older than CRON_ONTOLOGY_INTERVAL hours."""
    with app.app_context():
        db = get_db()
        interval_hours = int(app.config.get("CRON_ONTOLOGY_INTERVAL", 720))
        stale_before = datetime.now(timezone.utc) - timedelta(hours=interval_hours)
        rows = db.execute(
            "SELECT id, name, description, rdf_url, documentation_url, last_indexed_at"
            " FROM ontology_sources"
        ).fetchall()

        agent = OntologyIndexerAgent()
        llm_client = get_llm_client("ontology_embedding")
        for row in rows:
            last_indexed_raw = row["last_indexed_at"]
            needs_refresh = True
            if isinstance(last_indexed_raw, str) and last_indexed_raw:
                try:
                    normalized = last_indexed_raw.replace("Z", "+00:00")
                    last_indexed = datetime.fromisoformat(normalized)
                    if last_indexed.tzinfo is None:
                        last_indexed = last_indexed.replace(tzinfo=timezone.utc)
                    needs_refresh = last_indexed < stale_before
                except ValueError:
                    needs_refresh = True
            if not needs_refresh:
                continue

            description = str(row["description"] or "")
            if row["rdf_url"]:
                description += f"\nRDF/OWL source: {row['rdf_url']}"
            if row["documentation_url"]:
                description += f"\nDocumentation URL: {row['documentation_url']}"
            agent.run(
                description=description,
                llm_client=llm_client,
                source_id=int(row["id"]),
                source_name=str(row["name"]),
            )


def register(scheduler: Any, app: Flask) -> None:
    """Register ontology refresh cron job in APScheduler."""
    scheduler.add_job(
        func=refresh_stale_ontologies,
        trigger="interval",
        hours=int(app.config.get("CRON_ONTOLOGY_INTERVAL", 720)),
        id="cron-ontology-refresh",
        replace_existing=True,
        kwargs={"app": app},
    )
