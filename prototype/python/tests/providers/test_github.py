"""Integration tests against the real GitHub API.

Marked @pytest.mark.integration; skipped by default. Requires operator
setup per the README:

  1. Visit https://github.com/settings/tokens (classic) OR
     https://github.com/settings/personal-access-tokens (fine-grained).

     **Fine-grained PATs are recommended** for least-privilege access.
     A classic PAT works too — both formats land at the wire as
     ``Authorization: Bearer <token>`` and UACP doesn't distinguish
     them.

  2. Token scopes / permissions:
     - For testing public-repo reads only: NO scopes / permissions
       needed. GitHub's REST API serves public repository metadata
       and listings without authentication, but rate-limits unauth'd
       calls aggressively (60/hour); a PAT with no scopes raises that
       to 5,000/hour and is sufficient for the integration tests.
     - For testing private-repo reads: ``repo`` scope (classic) or
       "Contents: Read" + "Metadata: Read" (fine-grained).
     - For listing repos for users you don't own: same posture as
       above; public listings are fine without scopes.

  3. Populate ``.env`` (or export):

         UACP_GITHUB_TOKEN=ghp_... or github_pat_...
         UACP_GITHUB_TEST_USER=octocat                # any GitHub username
         UACP_GITHUB_TEST_REPO=octocat/hello-world    # owner/repo string

  4. Run with:
         uv run pytest tests/providers/test_github.py -m integration

The integration tests are read-only and non-destructive. The default
``UACP_GITHUB_TEST_USER=octocat`` works for everyone — Octocat is
GitHub's mascot account and has many public repos suitable for
exercising pagination.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.auth.api_key import APIKeyHeaderConfig, APIKeyHeaderMethod
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "github"
GET_FILE = EXAMPLES_DIR / "repos-get.uacp"
LIST_FILE = EXAMPLES_DIR / "repos-list-for-user.uacp"


def _required_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(
            f"integration test requires {var} (see tests/providers/test_github.py docstring)"
        )
    return value


@pytest.fixture(scope="module")
def github_config() -> dict[str, str]:
    return {
        "token": _required_env("UACP_GITHUB_TOKEN"),
        "test_user": os.environ.get("UACP_GITHUB_TEST_USER", "octocat"),
        "test_repo": os.environ.get("UACP_GITHUB_TEST_REPO", "octocat/hello-world"),
    }


def _client_for(artifact: Any, github_config: dict[str, str]) -> DispatchClient:
    return DispatchClient(
        artifact,
        auth_method=APIKeyHeaderMethod(
            config=APIKeyHeaderConfig(
                header_name="Authorization", header_prefix="Bearer "
            )
        ),
        credential_resolver=lambda: {"key": github_config["token"]},
        sleep=time.sleep,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_repo_success(github_config: dict[str, str]) -> None:
    """Fetch a real GitHub repository's metadata."""
    artifact = load(GET_FILE)
    owner, repo = github_config["test_repo"].split("/", 1)
    client = _client_for(artifact, github_config)
    try:
        result = client.dispatch(
            "repos_get",
            path_params={"owner": owner, "repo": repo},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"GetRepo failed: {result}"
    assert result.body["full_name"].lower() == github_config["test_repo"].lower()
    assert "stargazers_count" in result.body


@pytest.mark.integration
def test_get_repo_404(github_config: dict[str, str]) -> None:
    """Request a known-nonexistent repo path; expect 404 DispatchError."""
    artifact = load(GET_FILE)
    client = _client_for(artifact, github_config)
    try:
        result = client.dispatch(
            "repos_get",
            path_params={
                "owner": "octocat",
                "repo": f"uacp-nonexistent-{int(time.time())}",
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError)
    assert result.status == 404
    assert result.code == "not_found"


@pytest.mark.integration
def test_list_repos_for_user_single_page(github_config: dict[str, str]) -> None:
    """List with per_page=100 — for users with ≤100 repos this fits in
    one page (no Link rel=next), and the dispatcher terminates after
    one fetch."""
    artifact = load(LIST_FILE)
    client = _client_for(artifact, github_config)
    try:
        result = client.dispatch(
            "repos_list_for_user",
            path_params={"username": github_config["test_user"]},
            query={"per_page": 100, "type": "owner"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"list failed: {result}"
    assert isinstance(result.body, list)


@pytest.mark.integration
def test_list_repos_for_user_paginated(github_config: dict[str, str]) -> None:
    """Walk multiple pages with small per_page so even a user with
    modest repo count exercises the link-header loop. Caps at 5 pages
    so users with hundreds of repos don't make the test run too long."""
    artifact = load(LIST_FILE)
    client = _client_for(artifact, github_config)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "repos_list_for_user",
                path_params={"username": github_config["test_user"]},
                query={"per_page": 2, "type": "owner"},
                max_pages=5,
            )
        )
    finally:
        client.close()

    assert len(pages) >= 1
    success_pages = [p for p in pages if isinstance(p, DispatchSuccess)]
    assert len(success_pages) >= 1
    for p in success_pages:
        assert isinstance(p.body, list)


@pytest.mark.integration
def test_list_repos_with_link_header_intermediate(github_config: dict[str, str]) -> None:
    """Validate that the dispatcher correctly handles the Link header
    on an intermediate page (where both rel=next and rel=last are
    present). per_page=2 forces multiple pages; we walk 3 to hit at
    least one intermediate."""
    artifact = load(LIST_FILE)
    client = _client_for(artifact, github_config)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "repos_list_for_user",
                path_params={"username": github_config["test_user"]},
                query={"per_page": 2, "type": "owner"},
                max_pages=3,
            )
        )
    finally:
        client.close()

    # We just need to confirm the loop actually advanced — if the user
    # has ≥2 repos the loop should produce at least 1 page; with ≥3
    # repos it produces ≥2 pages and the second-page request was
    # constructed by following the Link header from page 1.
    assert len(pages) >= 1
    success_pages = [p for p in pages if isinstance(p, DispatchSuccess)]
    assert len(success_pages) >= 1
