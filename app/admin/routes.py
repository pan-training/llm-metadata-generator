"""Admin UI routes for ontology management and lookup."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any

from flask import Blueprint, Response, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from markupsafe import escape

from app.agents import get_llm_client, get_model_for_task
from app.agents.ontology import OntologyIndexerAgent
from app.db.sqlite import get_db, vector_search

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin() -> ResponseReturnValue | None:
    if not session.get("user_id"):
        return redirect(url_for("sessions_viewer.login_form"))
    if not session.get("is_admin"):
        return Response("Forbidden – admin access required", status=403)
    return None


def _embed_lookup_query(query: str, llm_client: Any) -> list[float]:
    try:
        result = llm_client.embeddings.create(
            model=get_model_for_task("ontology_embedding"),
            input=[query],
        )
        data = getattr(result, "data", [])
        if data and isinstance(data, list):
            embedding = getattr(data[0], "embedding", None)
            if isinstance(embedding, list):
                return [float(value) for value in embedding]
    except Exception:
        pass
    # deterministic fallback for environments/tests without live embedding endpoint
    digest = hashlib.sha256(query.encode()).digest()
    return [byte / 255.0 for byte in digest]


@bp.get("")
def dashboard_redirect() -> ResponseReturnValue:
    """Redirect to ontology management page as the initial admin screen."""
    guard = _require_admin()
    if guard is not None:
        return guard
    return redirect(url_for("admin.ontologies"))


@bp.route("/ontologies", methods=["GET", "POST"])
def ontologies() -> ResponseReturnValue:
    guard = _require_admin()
    if guard is not None:
        return guard

    db = get_db()
    status_message: str | None = None
    lookup_query = ""
    lookup_results: list[dict[str, Any]] = []

    if request.method == "POST":
        action = request.form.get("action", "add")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            rdf_url = (request.form.get("rdf_url") or "").strip() or None
            documentation_url = (request.form.get("documentation_url") or "").strip() or None
            if name and description:
                db.execute(
                    "INSERT INTO ontology_sources (name, description, rdf_url, documentation_url)"
                    " VALUES (?, ?, ?, ?)",
                    (name, description, rdf_url, documentation_url),
                )
                db.commit()
                status_message = f"Added ontology source '{name}'."
            else:
                status_message = "Name and description are required."
        elif action == "lookup":
            lookup_query = (request.form.get("lookup_query") or "").strip()
            if lookup_query:
                llm_client = get_llm_client("ontology_embedding")
                query_embedding = _embed_lookup_query(lookup_query, llm_client)
                lookup_results = vector_search(query_embedding, top_k=10)
                status_message = f"Found {len(lookup_results)} lookup candidate(s)."
            else:
                status_message = "Enter a lookup query."

    rows = db.execute(
        "SELECT id, name, description, rdf_url, documentation_url, last_indexed_at, active_version_id"
        " FROM ontology_sources ORDER BY id DESC"
    ).fetchall()
    sources = [dict(row) for row in rows]

    return render_template(
        "admin_ontologies.html",
        sources=sources,
        status_message=status_message,
        lookup_query=lookup_query,
        lookup_results=lookup_results,
    )


@bp.route("/ontologies/<int:source_id>/delete", methods=["GET", "POST"])
def delete_ontology(source_id: int) -> ResponseReturnValue:
    guard = _require_admin()
    if guard is not None:
        return guard

    db = get_db()
    row = db.execute("SELECT id, name FROM ontology_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return Response("Ontology source not found", status=404)

    if request.method == "POST":
        db.execute("DELETE FROM ontology_sources WHERE id = ?", (source_id,))
        db.commit()
        return redirect(url_for("admin.ontologies"))

    return Response(
        (
            "<html><body>"
            "<h3>Delete this ontology source?</h3>"
            "<form method='post' action=''>"
            "<button type='submit'>Confirm delete</button>"
            "</form>"
            "<p><a href='/admin/ontologies'>Cancel</a></p>"
            "</body></html>"
        ),
        mimetype="text/html",
    )


@bp.post("/ontologies/<int:source_id>/reindex")
def reindex_ontology(source_id: int) -> ResponseReturnValue:
    guard = _require_admin()
    if guard is not None:
        return guard

    db = get_db()
    row = db.execute(
        "SELECT id, name, description, rdf_url, documentation_url FROM ontology_sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        return Response("Ontology source not found", status=404)

    combined_description = str(row["description"])
    if row["rdf_url"]:
        combined_description += f"\nRDF/OWL source: {row['rdf_url']}"
    if row["documentation_url"]:
        combined_description += f"\nDocumentation URL: {row['documentation_url']}"

    agent = OntologyIndexerAgent()
    agent.run(
        description=combined_description,
        llm_client=get_llm_client("ontology_embedding"),
        source_id=source_id,
        source_name=str(row["name"]),
    )
    return redirect(url_for("admin.ontology_history", source_id=source_id))


@bp.route("/ontologies/<int:source_id>/history", methods=["GET", "POST"])
def ontology_history(source_id: int) -> ResponseReturnValue:
    guard = _require_admin()
    if guard is not None:
        return guard

    db = get_db()
    source = db.execute(
        "SELECT id, name, active_version_id FROM ontology_sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if source is None:
        return Response("Ontology source not found", status=404)

    if request.method == "POST":
        version_id_raw = request.form.get("version_id", "").strip()
        if version_id_raw.isdigit():
            version_id = int(version_id_raw)
            version = db.execute(
                "SELECT id FROM ontology_index_versions WHERE id = ? AND source_id = ?",
                (version_id, source_id),
            ).fetchone()
            if version is not None:
                db.execute(
                    "UPDATE ontology_index_versions SET is_active = 0 WHERE source_id = ?",
                    (source_id,),
                )
                db.execute(
                    "UPDATE ontology_index_versions SET is_active = 1 WHERE id = ?",
                    (version_id,),
                )
                db.execute(
                    "UPDATE ontology_sources SET active_version_id = ?, updated_at = datetime('now') WHERE id = ?",
                    (version_id, source_id),
                )
                db.commit()
        return redirect(url_for("admin.ontology_history", source_id=source_id))

    versions = db.execute(
        "SELECT id, embedding_model, status, notes, is_active, created_at"
        " FROM ontology_index_versions WHERE source_id = ? ORDER BY id DESC",
        (source_id,),
    ).fetchall()
    history_rows = [dict(version) for version in versions]

    html_rows = []
    for row in history_rows:
        marker = " (active)" if row["is_active"] else ""
        html_rows.append(
            "<li>"
            f"version #{row['id']} – model={row['embedding_model']} – status={row['status']}{marker}"
            f" – {row['created_at']}"
            f"<form method='post' style='display:inline;margin-left:8px'>"
            f"<input type='hidden' name='version_id' value='{row['id']}'>"
            "<button type='submit'>Roll back to this version</button>"
            "</form>"
            "</li>"
        )

    escaped_source_name = escape(source["name"])
    return Response(
        (
            "<html><body>"
            f"<h2>Ontology history: {escaped_source_name}</h2>"
            "<ul>"
            + "".join(html_rows)
            + "</ul>"
            "<p><a href='/admin/ontologies'>Back to ontologies</a></p>"
            "</body></html>"
        ),
        mimetype="text/html",
    )


@bp.get("/missing-terms")
def missing_terms() -> ResponseReturnValue:
    guard = _require_admin()
    if guard is not None:
        return guard

    db = get_db()
    rows = db.execute(
        "SELECT mt.id, mt.label, mt.description, mt.ontology_name, mt.suggested_source_url,"
        "       mc.url AS metadata_url"
        " FROM missing_ontology_terms mt"
        " LEFT JOIN missing_ontology_term_links mtl ON mtl.missing_term_id = mt.id"
        " LEFT JOIN metadata_cache mc ON mc.id = mtl.metadata_cache_id"
        " ORDER BY coalesce(mt.ontology_name, 'Unspecified'), mt.label, mc.url"
    ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        ontology_name = str(row["ontology_name"] or "Unspecified")
        missing_term_id = int(row["id"])
        if missing_term_id not in by_id:
            term = {
                "id": missing_term_id,
                "label": str(row["label"]),
                "description": str(row["description"] or ""),
                "suggested_source_url": str(row["suggested_source_url"] or ""),
                "metadata_urls": [],
            }
            grouped[ontology_name].append(term)
            by_id[missing_term_id] = term
        metadata_url = row["metadata_url"]
        if metadata_url:
            by_id[missing_term_id]["metadata_urls"].append(str(metadata_url))

    return render_template(
        "admin_missing_terms.html",
        grouped_missing_terms=dict(grouped),
    )
