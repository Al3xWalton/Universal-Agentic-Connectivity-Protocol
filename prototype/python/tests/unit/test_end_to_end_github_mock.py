"""Mock-based end-to-end test for the GitHub pipeline.

Parallel to test_end_to_end_mock.py (Google), test_end_to_end_slack_mock.py
(Slack), and test_end_to_end_aws_mock.py (AWS). Loads each GitHub
.uacp artifact, mocks the GitHub API, dispatches through the full
stack (spec loader → api_key_header auth → dispatch client → JSON
decoding → link-header pagination), and asserts request shape
(Authorization: Bearer <token>) and pagination behavior across
multiple pages chained via Link headers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.api_key import APIKeyHeaderConfig, APIKeyHeaderMethod
from uacp_prototype.dispatch.client import DispatchClient, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "github"
GET_FILE = EXAMPLES_DIR / "repos-get.uacp"
LIST_FILE = EXAMPLES_DIR / "repos-list-for-user.uacp"


def _client(artifact: Any, *, sleep: Any = lambda _s: None) -> DispatchClient:
    return DispatchClient(
        artifact,
        auth_method=APIKeyHeaderMethod(
            config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
        ),
        credential_resolver=lambda: {"key": "ghp_MOCK_TOKEN"},
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# repos.get
# ---------------------------------------------------------------------------


@respx.mock
def test_repos_get_success_end_to_end_mock() -> None:
    artifact = load(GET_FILE)
    assert artifact.authentication.method == "api_key_header"

    route = respx.get("https://api.github.com/repos/octocat/hello-world").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1296269,
                "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
                "name": "hello-world",
                "full_name": "octocat/hello-world",
                "private": False,
                "html_url": "https://github.com/octocat/hello-world",
                "description": "This your first repo!",
                "fork": False,
                "default_branch": "master",
                "stargazers_count": 80,
            },
            headers={"Content-Type": "application/json"},
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "repos_get",
            path_params={"owner": "octocat", "repo": "hello-world"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    assert result.body["full_name"] == "octocat/hello-world"
    assert result.body["stargazers_count"] == 80

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer ghp_MOCK_TOKEN"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"] == "uacp-prototype/0.1"


@respx.mock
def test_repos_get_404_returns_dispatch_error() -> None:
    from uacp_prototype.dispatch.client import DispatchError

    artifact = load(GET_FILE)
    respx.get("https://api.github.com/repos/octocat/no-such-repo").mock(
        return_value=httpx.Response(
            404,
            json={
                "message": "Not Found",
                "documentation_url": "https://docs.github.com/rest/repos/repos#get-a-repository",
                "status": "404",
            },
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "repos_get",
            path_params={"owner": "octocat", "repo": "no-such-repo"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError)
    assert result.status == 404
    assert result.code == "not_found"


# ---------------------------------------------------------------------------
# repos.listForUser — link-header pagination
# ---------------------------------------------------------------------------


@respx.mock
def test_repos_list_for_user_paginated_end_to_end_mock() -> None:
    """Three-page walk: page 1 → page 2 → page 3 (no rel=next on page 3
    terminates the loop). Validates RFC 8288 parsing including
    multi-rel headers (next + last + first + prev), case-insensitive
    rel matching, and the dispatcher's loop termination when rel=next
    is absent."""
    artifact = load(LIST_FILE)
    op = artifact.operations[0]
    assert op.pagination.pattern == "link_header"

    base = "https://api.github.com/users/octocat/repos"

    # Page 1: rel="next" → page 2, rel="last" → page 3
    page1_link = (
        f'<{base}?page=2&per_page=2>; rel="next", '
        f'<{base}?page=3&per_page=2>; rel="last"'
    )
    page1_body = [
        {"id": 1, "name": "repo-one", "full_name": "octocat/repo-one"},
        {"id": 2, "name": "repo-two", "full_name": "octocat/repo-two"},
    ]

    # Page 2: rel="next" → page 3, rel="prev" → page 1, rel="first" → page 1, rel="last" → page 3
    page2_link = (
        f'<{base}?page=1&per_page=2>; rel="prev", '
        f'<{base}?page=3&per_page=2>; rel="next", '
        f'<{base}?page=1&per_page=2>; rel="first", '
        f'<{base}?page=3&per_page=2>; rel="last"'
    )
    page2_body = [
        {"id": 3, "name": "repo-three", "full_name": "octocat/repo-three"},
        {"id": 4, "name": "repo-four", "full_name": "octocat/repo-four"},
    ]

    # Page 3: rel="prev" → page 2, rel="first" → page 1; NO rel="next"
    page3_link = (
        f'<{base}?page=2&per_page=2>; rel="prev", '
        f'<{base}?page=1&per_page=2>; rel="first"'
    )
    page3_body = [
        {"id": 5, "name": "repo-five", "full_name": "octocat/repo-five"},
    ]

    # Order matters with respx: more-specific routes first.
    respx.get(base, params={"page": "3", "per_page": "2"}).mock(
        return_value=httpx.Response(200, json=page3_body, headers={"Link": page3_link})
    )
    respx.get(base, params={"page": "2", "per_page": "2"}).mock(
        return_value=httpx.Response(200, json=page2_body, headers={"Link": page2_link})
    )
    respx.get(base, params={"per_page": "2"}).mock(
        return_value=httpx.Response(200, json=page1_body, headers={"Link": page1_link})
    )

    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "repos_list_for_user",
                path_params={"username": "octocat"},
                query={"per_page": 2},
            )
        )
    finally:
        client.close()

    assert len(pages) == 3
    assert all(isinstance(p, DispatchSuccess) for p in pages)
    all_repos = []
    for p in pages:
        all_repos.extend(p.body)
    assert [r["name"] for r in all_repos] == [
        "repo-one", "repo-two", "repo-three", "repo-four", "repo-five"
    ]


@respx.mock
def test_repos_list_for_user_single_page_end_to_end_mock() -> None:
    """No Link header → single-page result, loop terminates after page 1."""
    artifact = load(LIST_FILE)
    base = "https://api.github.com/users/octocat/repos"
    respx.get(base).mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "name": "only", "full_name": "octocat/only"}],
            # No Link header
        )
    )
    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "repos_list_for_user",
                path_params={"username": "octocat"},
            )
        )
    finally:
        client.close()
    assert len(pages) == 1
    assert isinstance(pages[0], DispatchSuccess)
    assert pages[0].body[0]["name"] == "only"


@respx.mock
def test_repos_list_for_user_link_with_prev_and_last_no_next_terminates() -> None:
    """Final page declares rel="prev" + rel="last" but NOT rel="next" —
    dispatcher MUST terminate the loop."""
    artifact = load(LIST_FILE)
    base = "https://api.github.com/users/octocat/repos"
    respx.get(base).mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "name": "single"}],
            headers={
                "Link": (
                    f'<{base}?page=2>; rel="prev", '
                    f'<{base}?page=1>; rel="first"'
                )
            },
        )
    )
    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "repos_list_for_user",
                path_params={"username": "octocat"},
            )
        )
    finally:
        client.close()
    assert len(pages) == 1


# ---------------------------------------------------------------------------
# Both .uacp files load
# ---------------------------------------------------------------------------


def test_github_examples_load_clean() -> None:
    for name in ("repos-get.uacp", "repos-list-for-user.uacp"):
        path = EXAMPLES_DIR / name
        artifact = load(path)
        assert artifact.authentication.method == "api_key_header"
        assert len(artifact.operations) == 1
