# exercises/polished-docs/tests/test_docs.py
# L12 — The published documentation: metadata, tags, docstrings, examples and
# documented response codes, read from /openapi.json as a client would.

import pytest

from app.main import app
from tests.helpers import call, json_of


@pytest.fixture
def spec(client):
    return json_of(call(client, "GET", "/openapi.json", expect=200))


def test_api_metadata(spec):
    info = spec["info"]
    assert info["title"] == "Tasks and Inventory API"
    assert info["version"] == "1.2.0"
    assert "**tasks**" in info["description"] and "## " in info["description"]  # markdown
    assert set(info["contact"]) == {"name", "email"}
    assert info["license"]["name"] == "MIT"


def test_tags_are_described_and_used(spec):
    tags = {tag["name"]: tag["description"] for tag in spec["tags"]}
    assert set(tags) == {"tasks", "items", "meta"}
    assert all(tags.values())
    used = {tag for path in spec["paths"].values() for op in path.values() for tag in op["tags"]}
    assert used == set(tags)


def test_every_operation_has_a_summary_and_description(spec):
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            assert op.get("summary"), (method, path)
            assert op.get("description"), (method, path)


def test_item_endpoints_document_their_responses(spec):
    paths = spec["paths"]
    assert sorted(paths["/items"]) == ["get", "post"]
    assert sorted(paths["/items/{item_id}"]) == ["get"]
    assert {"201", "409", "422", "429"} <= set(paths["/items"]["post"]["responses"])
    assert {"200", "404", "422", "429"} <= set(paths["/items/{item_id}"]["get"]["responses"])
    list_params = {p["name"] for p in paths["/items"]["get"]["parameters"]}
    assert list_params == {"name", "category"}


def test_task_endpoints_document_their_responses(spec):
    ops = spec["paths"]["/tasks/{task_id}"]
    assert {"404", "409", "422"} <= set(ops["patch"]["responses"])
    assert {"409", "422"} <= set(spec["paths"]["/tasks"]["post"]["responses"])


def test_error_responses_use_the_envelope_schema(spec):
    ref = spec["paths"]["/items/{item_id}"]["get"]["responses"]["404"]["content"]["application/json"]["schema"]["$ref"]
    envelope = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert set(envelope["required"]) == {"error", "detail", "status_code"}


@pytest.mark.parametrize("schema", ["ItemCreate", "ItemResponse", "TaskCreate", "TaskResponse", "ErrorResponse"])
def test_schemas_carry_examples(spec, schema):
    assert spec["components"]["schemas"][schema]["example"]


def test_item_create_example_is_itself_a_valid_request(client, spec):
    """The Swagger 'Try it out' example must actually work."""
    example = spec["components"]["schemas"]["ItemCreate"]["example"]
    body = json_of(call(client, "POST", "/items", json=example, expect=201))
    assert {k: body[k] for k in example} == example


def test_item_schema_documents_field_rules(spec):
    props = spec["components"]["schemas"]["ItemCreate"]["properties"]
    assert (props["name"]["minLength"], props["name"]["maxLength"]) == (1, 100)
    assert props["description"]["maxLength"] == 500
    assert props["price"]["exclusiveMinimum"] == 0
    assert props["category"]["maxLength"] == 50
    assert all(p.get("description") for p in props.values())


def test_docs_pages_are_served(client):
    assert "swagger" in call(client, "GET", "/docs", expect=200).text.lower()
    assert "redoc" in call(client, "GET", "/redoc", expect=200).text.lower()
    assert app.openapi_tags[0]["name"] == "tasks"
