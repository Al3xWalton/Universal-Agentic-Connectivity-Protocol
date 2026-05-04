"""Tests for OpenAPI 3.x and Google-discovery ingestion per §3.6."""

from __future__ import annotations

from typing import Any

import pytest

from uacp_prototype.connections.ingest_openapi import (
    from_discovery_doc,
    from_openapi,
)
from uacp_prototype.spec.models import (
    CursorPagination,
    OpenAPISource,
    Operation,
)


# ---------------------------------------------------------------------------
# OpenAPI 3.x ingestion
# ---------------------------------------------------------------------------


def _minimal_openapi_doc() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Example", "version": "1.0"},
        "servers": [{"url": "https://api.example.com/v1"}],
        "paths": {
            "/users/{user_id}": {
                "get": {
                    "operationId": "get_user",
                    "summary": "Get a user.",
                    "tags": ["users", "read"],
                    "parameters": [
                        {
                            "in": "path",
                            "name": "user_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "User found.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        },
                        "4xx": {"description": "Client error."},
                    },
                },
            },
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "summary": "Create a user.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/User"}
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                }
            }
        },
    }


def test_from_openapi_basic_mapping() -> None:
    result = from_openapi(_minimal_openapi_doc())
    assert result.base_url == "https://api.example.com/v1"
    assert "User" in result.definitions
    assert len(result.operations) == 2

    by_id = {op.id: op for op in result.operations}
    assert "get_user" in by_id
    assert "createuser" in by_id  # CamelCase → lowercase via _safe_id


def test_from_openapi_path_parameters_split_correctly() -> None:
    result = from_openapi(_minimal_openapi_doc())
    op = next(op for op in result.operations if op.id == "get_user")
    assert op.request.method == "GET"
    assert op.request.path == "/users/{user_id}"
    assert op.request.path_parameters is not None
    assert "user_id" in op.request.path_parameters["properties"]
    assert "user_id" in op.request.path_parameters["required"]


def test_from_openapi_idempotency_default_get_idempotent() -> None:
    result = from_openapi(_minimal_openapi_doc())
    by_id = {op.id: op for op in result.operations}
    assert by_id["get_user"].idempotency == "idempotent"


def test_from_openapi_idempotency_default_post_unknown() -> None:
    result = from_openapi(_minimal_openapi_doc())
    by_id = {op.id: op for op in result.operations}
    assert by_id["createuser"].idempotency == "unknown"


def test_from_openapi_response_body_ref_rewritten() -> None:
    result = from_openapi(_minimal_openapi_doc())
    op = next(op for op in result.operations if op.id == "get_user")
    assert "200" in op.response
    body = op.response["200"].body
    assert isinstance(body, dict)
    schema = body["schema"]
    assert schema == {"$ref": "#/definitions/User"}


def test_from_openapi_request_body_ref_rewritten() -> None:
    result = from_openapi(_minimal_openapi_doc())
    op = next(op for op in result.operations if op.id == "createuser")
    assert isinstance(op.request.body, dict)
    schema = op.request.body["schema"]
    assert schema == {"$ref": "#/definitions/User"}


def test_from_openapi_carries_source_provenance() -> None:
    result = from_openapi(_minimal_openapi_doc())
    op = result.operations[0]
    assert isinstance(op.source, OpenAPISource)
    assert op.source.type == "openapi"
    assert op.source.url == "<inline>"
    assert op.source.ingested_at  # RFC 3339-ish


def test_from_openapi_rejects_non_openapi_doc() -> None:
    with pytest.raises(ValueError, match="missing required 'openapi' field"):
        from_openapi({"swagger": "2.0", "paths": {}})


def test_from_openapi_skips_authorization_header_param() -> None:
    """Per §3.6 authentication-bearing header parameters are excluded."""
    doc = _minimal_openapi_doc()
    doc["paths"]["/users/{user_id}"]["get"]["parameters"].append(
        {
            "in": "header",
            "name": "Authorization",
            "schema": {"type": "string"},
        }
    )
    result = from_openapi(doc)
    op = next(op for op in result.operations if op.id == "get_user")
    if op.request.headers is not None:
        assert "Authorization" not in op.request.headers["properties"]


# ---------------------------------------------------------------------------
# Pagination inference
# ---------------------------------------------------------------------------


def test_pagination_inferred_from_cursor_param_and_response() -> None:
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "x", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/messages": {
                "get": {
                    "operationId": "list_messages",
                    "summary": "List.",
                    "parameters": [
                        {"in": "query", "name": "page_token", "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "messages": {
                                                "type": "array",
                                                "items": {"type": "object"},
                                            },
                                            "nextPageToken": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    result = from_openapi(doc)
    op = result.operations[0]
    assert isinstance(op.pagination, CursorPagination)
    assert op.pagination.request_cursor_parameter == "page_token"
    assert op.pagination.response_cursor_path == "$.nextPageToken"


# ---------------------------------------------------------------------------
# Google discovery ingestion
# ---------------------------------------------------------------------------


def _gmail_discovery_fragment() -> dict[str, Any]:
    """A small slice of Gmail's discovery doc: users.messages.send."""
    return {
        "kind": "discovery#restDescription",
        "id": "gmail:v1",
        "name": "gmail",
        "version": "v1",
        "rootUrl": "https://gmail.googleapis.com/",
        "servicePath": "",
        "schemas": {
            "Message": {
                "id": "Message",
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "threadId": {"type": "string"},
                    "raw": {"type": "string", "format": "byte"},
                },
            }
        },
        "resources": {
            "users": {
                "resources": {
                    "messages": {
                        "methods": {
                            "send": {
                                "id": "gmail.users.messages.send",
                                "path": "gmail/v1/users/{userId}/messages/send",
                                "httpMethod": "POST",
                                "description": "Sends the specified message to the recipients in the To, Cc, and Bcc headers. For example payloads, see <a href=\"...\">examples</a>.",
                                "parameters": {
                                    "userId": {
                                        "type": "string",
                                        "required": True,
                                        "default": "me",
                                        "description": "The user's email address. The special value me can be used to indicate the authenticated user.",
                                        "location": "path",
                                    }
                                },
                                "parameterOrder": ["userId"],
                                "request": {"$ref": "Message"},
                                "response": {"$ref": "Message"},
                            }
                        }
                    }
                }
            }
        },
    }


def _calendar_discovery_fragment() -> dict[str, Any]:
    """A small slice of Calendar's discovery doc: events.list."""
    return {
        "kind": "discovery#restDescription",
        "id": "calendar:v3",
        "name": "calendar",
        "version": "v3",
        "rootUrl": "https://www.googleapis.com/",
        "servicePath": "calendar/v3/",
        "schemas": {
            "Events": {
                "id": "Events",
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "Event"}},
                    "nextPageToken": {"type": "string"},
                },
            },
            "Event": {
                "id": "Event",
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
        "resources": {
            "events": {
                "methods": {
                    "list": {
                        "id": "calendar.events.list",
                        "path": "calendars/{calendarId}/events",
                        "httpMethod": "GET",
                        "description": "Returns events on the specified calendar.",
                        "parameters": {
                            "calendarId": {
                                "type": "string",
                                "required": True,
                                "location": "path",
                            },
                            "maxResults": {
                                "type": "integer",
                                "format": "int32",
                                "minimum": 1,
                                "maximum": 2500,
                                "location": "query",
                            },
                            "pageToken": {
                                "type": "string",
                                "location": "query",
                            },
                            "timeMin": {
                                "type": "string",
                                "format": "date-time",
                                "location": "query",
                            },
                        },
                        "parameterOrder": ["calendarId"],
                        "response": {"$ref": "Events"},
                    }
                }
            }
        },
    }


def test_discovery_doc_rejects_non_discovery() -> None:
    with pytest.raises(ValueError, match="not a Google discovery document"):
        from_discovery_doc({"openapi": "3.1.0", "paths": {}})


def test_discovery_gmail_send_round_trip() -> None:
    result = from_discovery_doc(_gmail_discovery_fragment())
    assert result.base_url == "https://gmail.googleapis.com"
    assert "Message" in result.definitions
    assert len(result.operations) == 1
    op = result.operations[0]
    assert op.id == "gmail_users_messages_send"
    assert op.request.method == "POST"
    assert op.request.path == "/gmail/v1/users/{userId}/messages/send"
    assert op.request.path_parameters is not None
    assert "userId" in op.request.path_parameters["properties"]
    assert "userId" in op.request.path_parameters["required"]
    # Body and response refs rewritten to local definitions
    assert isinstance(op.request.body, dict)
    assert op.request.body["schema"] == {"$ref": "#/definitions/Message"}
    assert op.response["200"].body["schema"] == {"$ref": "#/definitions/Message"}
    # Idempotency for POST defaults to unknown per §3.6
    assert op.idempotency == "unknown"
    # Source provenance
    assert isinstance(op.source, OpenAPISource)


def test_discovery_calendar_list_pagination_inferred() -> None:
    result = from_discovery_doc(_calendar_discovery_fragment())
    assert len(result.operations) == 1
    op = result.operations[0]
    assert op.id == "calendar_events_list"
    assert op.request.method == "GET"
    assert op.idempotency == "idempotent"
    # Cursor pagination inferred — pageToken in query, nextPageToken in response
    assert isinstance(op.pagination, CursorPagination)
    assert op.pagination.request_cursor_parameter == "pageToken"
    assert op.pagination.response_cursor_path == "$.nextPageToken"


def test_discovery_default_response_envelope_added() -> None:
    """Google APIs return a `{error: {code, message, status}}` envelope on
    failure; the discovery ingester adds it as the `default` response so
    the dispatch error-envelope handling can extract the message.
    """
    result = from_discovery_doc(_gmail_discovery_fragment())
    op = result.operations[0]
    assert "default" in op.response
    assert isinstance(op.response["default"].body, dict)
    schema = op.response["default"].body["schema"]
    assert "error" in schema["properties"]


def test_discovery_walks_resource_tree() -> None:
    """Methods nested under multiple resource levels (Gmail's
    users.messages.send is two levels deep) are extracted.
    """
    result = from_discovery_doc(_gmail_discovery_fragment())
    assert any(op.id == "gmail_users_messages_send" for op in result.operations)
