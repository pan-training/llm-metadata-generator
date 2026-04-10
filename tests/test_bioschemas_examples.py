"""Tests that every JSON-LD example file in docs/Bioschemas/examples/ is valid
against the master JSON Schema in docs/Bioschemas/bioschemas-training-schema.json.

This test is intentionally lightweight: it checks structural validity (correct
@type, required fields, type constraints) but does NOT perform network requests
or JSON-LD context resolution.  Run with:

    pytest tests/test_bioschemas_examples.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.validators import Draft202012Validator

# Resolve paths relative to this file so the tests work wherever pytest is run.
REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "docs" / "Bioschemas" / "bioschemas-training-schema.json"
EXAMPLES_DIR = REPO_ROOT / "docs" / "Bioschemas" / "examples"


def _load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Parametrised fixtures
# ---------------------------------------------------------------------------


def pytest_collect_file(parent, file_path):  # noqa: ANN001
    """Not used — parametrisation is done via the function below."""


def _example_ids() -> list[str]:
    return [p.name for p in _example_files()]


@pytest.fixture(scope="module")
def schema() -> dict:
    return _load_schema()


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_file_exists() -> None:
    """The master JSON Schema file must be present."""
    assert SCHEMA_PATH.is_file(), f"Schema not found: {SCHEMA_PATH}"


def test_examples_directory_exists() -> None:
    """The examples directory must exist and contain at least one JSON file."""
    assert EXAMPLES_DIR.is_dir(), f"Examples directory not found: {EXAMPLES_DIR}"
    files = _example_files()
    assert files, f"No .json files found in {EXAMPLES_DIR}"


def test_schema_is_valid_draft_2020_12(schema: dict) -> None:
    """The master schema itself must be valid Draft 2020-12."""
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("example_file", _example_files(), ids=_example_ids())
def test_example_is_valid_json(example_file: Path) -> None:
    """Every example file must be parseable as JSON."""
    with example_file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert data is not None, f"{example_file.name}: failed to parse as JSON"


@pytest.mark.parametrize("example_file", _example_files(), ids=_example_ids())
def test_example_validates_against_schema(
    example_file: Path, validator: Draft202012Validator
) -> None:
    """Every example file must validate against bioschemas-training-schema.json.

    The schema expects a JSON array of training resources (TrainingMaterial,
    Course, or CourseInstance items).  Validation errors are formatted as a
    human-readable list to make it easy to trace which property failed.
    """
    with example_file.open(encoding="utf-8") as fh:
        instance = json.load(fh)

    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))

    if errors:
        messages = [
            f"  [{'.'.join(str(p) for p in err.path) or '<root>'}] {err.message}"
            for err in errors
        ]
        raise AssertionError(
            f"{example_file.name} failed validation with "
            f"{len(errors)} error(s):\n" + "\n".join(messages)
        )


@pytest.mark.parametrize("example_file", _example_files(), ids=_example_ids())
def test_example_is_array(example_file: Path) -> None:
    """Every example file must be a JSON array (list of resources)."""
    with example_file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, list), (
        f"{example_file.name}: top-level value must be a JSON array, "
        f"got {type(data).__name__}"
    )
    assert len(data) >= 1, f"{example_file.name}: array must contain at least one item"


@pytest.mark.parametrize("example_file", _example_files(), ids=_example_ids())
def test_example_items_have_required_type(example_file: Path) -> None:
    """Every resource in each example must declare a @type."""
    with example_file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    for i, item in enumerate(data):
        assert "@type" in item, (
            f"{example_file.name}[{i}]: missing required '@type' field"
        )
        assert item["@type"] in {
            "LearningResource",
            "TrainingMaterial",
            "CreativeWork",
            "Course",
            "CourseInstance",
        }, (
            f"{example_file.name}[{i}]: unknown @type '{item['@type']}'; "
            "expected one of: LearningResource, TrainingMaterial, CreativeWork, "
            "Course, CourseInstance"
        )
