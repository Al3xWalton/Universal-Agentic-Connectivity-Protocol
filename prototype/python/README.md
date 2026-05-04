# UACP Python Prototype

This is the reference implementation of the [Universal Agentic Connectivity Protocol](../../) (UACP) `v1.x` specification. It is the first conforming implementation: the spec's MUSTs are exercised here, and gaps surface as either spec corrections (committed against the canonical docs) or open questions (logged for Stage 9 prototype freeze).

The prototype is Python 3.12+, managed with [`uv`](https://github.com/astral-sh/uv), and deliberately small. It implements the protocol layer: loading and validating `.uacp` artifacts, OAuth 2.0 authorization-code with PKCE, HTTPS dispatch with retries / pagination / rate-limit handling, the connection state machine, lazy refresh, AES-256-GCM encryption-at-rest, and OpenAPI / Google-discovery ingestion. Real-provider validation lives under `tests/providers/` as `@pytest.mark.integration` tests that the operator runs with their own credentials.

## Layout

```
prototype/python/
├── pyproject.toml                # uv project; deps: httpx, pydantic, cryptography, jsonschema, pytest
├── README.md                     # this file
├── src/uacp_prototype/
│   ├── spec/                     # .uacp loader, JSON Schema validation, pydantic models per §3.1
│   ├── auth/                     # AuthMethod Protocol + OAuth 2.0 authcode/PKCE per §2.2.1
│   ├── dispatch/                 # HTTP client + pagination loops + streaming placeholder per §4
│   ├── lifecycle/                # State machine per §5.1, refresh per §5.2
│   ├── security/                 # secret:// resolver + AES-256-GCM envelope encryption per §6
│   ├── connections/              # OpenAPI / Google-discovery ingestion per §3.6
│   └── cli.py                    # `uacp validate`, `uacp ingest-openapi`, `uacp dispatch`
├── examples/google/
│   ├── gmail-send.uacp           # Gmail users.messages.send
│   └── google-calendar-list.uacp # Calendar events.list with cursor pagination
└── tests/
    ├── unit/                     # mock-based unit tests; run during normal pytest
    └── providers/                # @pytest.mark.integration; require operator OAuth setup
```

## Running

```bash
cd /Users/alexanderwalton/Desktop/UACP/prototype/python

# install dependencies into a uv-managed venv
uv sync --extra dev

# run unit tests (no integration tests; mocks for HTTP and time)
uv run pytest tests/unit

# CLI
uv run uacp --help
uv run uacp validate examples/google/gmail-send.uacp
uv run uacp ingest-openapi https://www.googleapis.com/discovery/v1/apis/gmail/v1/rest --output gmail.uacp
```

## Conformance posture

This prototype satisfies the `MUST` requirements across:

- **Stage 2** (authentication) — OAuth 2.0 authorization-code with PKCE; Slack's workspace-scoped OAuth flavor (Stage 8b, registered as `x-oauth2-workspace` per §7.3 in-development extension); AWS Signature Version 4 per §2.5.1 (Stage 8c, implemented from scratch using only `hashlib` + `hmac` — no boto3 / botocore dependency, validated against AWS-published test vectors); API-key authentication per §2.4 (Stage 8d, both `api_key_header` and the disrecommended `api_key_query` flavors). OAuth 2.0 client-credentials, OAuth 2.0 device-code, OAuth 1.0a, HMAC-signature, and `custom_auth` are stubs that raise `NotImplementedError` and will be implemented in the remaining provider session (Stage 8e).
- **Stage 3** (schema) — hand-authored canonical JSON loading and OpenAPI 3.x / Google discovery ingestion. `curl`-paste and LLM inference are stubs.
- **Stage 4** (dispatch) — HTTPS-only, retry policy, pagination patterns (cursor / offset / link_header / none), canonical error shape, rate-limit handling, body-predicate failure detection per §3.3 + §4.6 (Stage 8b — converts 200-with-ok=false envelopes into canonical DispatchErrors), body-format dispatching per §3.3 (Stage 8c — `format` discriminator on response bodies: json / xml / binary / text; XML→dict conversion in stdlib `xml.etree`; JSONPath subset works against XML-derived dicts so cursor pagination resolves nested cursor fields like S3's `$.ListBucketResult.NextContinuationToken`), RFC 8288 conformance for link-header pagination per §3.4 (Stage 8d — case-insensitive rel matching, multi-rel entries, multi-header concatenation, relative URI resolution, link-parameter tolerance, comma-in-brackets / comma-in-quotes handling). Streaming is placeholder.
- **Stage 5** (lifecycle) — seven-state machine, lazy refresh (the `MUST` floor; proactive refresh is `SHOULD` and not implemented in this prototype), refresh-token rotation handled atomically.
- **Stage 6** (security) — `secret://` resolver, AES-256-GCM envelope encryption-at-rest, `local-keyring` simulated as a filesystem store at `~/.uacp/secrets/`, master key at `~/.uacp/master.key`. `vault` and `aws-secrets-manager` resolvers raise `NotImplementedError` until corresponding provider sessions land.

## Integration tests — operator setup

The integration tests under `tests/providers/test_google.py` execute real OAuth 2.0 flows against Google's authorization server and real Gmail / Calendar API calls. They are skipped by default; running them requires:

1. **Register an OAuth client on Google Cloud Console.**
   - Visit https://console.cloud.google.com/, create or select a project.
   - APIs & Services → Library → enable **Gmail API** and **Google Calendar API**.
   - APIs & Services → Credentials → Create Credentials → OAuth client ID → application type "Desktop app". Download the client JSON.
   - APIs & Services → OAuth consent screen → set up the consent screen for "External" user type (or "Internal" if your account is in a Workspace), add the scopes `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/calendar.readonly`. Add your own email address to the test users.

2. **Populate `.env` in `prototype/python/`** with:
   ```
   UACP_GOOGLE_CLIENT_ID=<client id from the JSON>
   UACP_GOOGLE_CLIENT_SECRET=<client secret from the JSON>
   UACP_GOOGLE_TEST_EMAIL=<the email you added as a test user>
   UACP_GOOGLE_REDIRECT_URI=http://localhost:8765/oauth/callback
   ```
   `python-dotenv` is not a project dependency; export the variables directly or `source .env` before running.

3. **Run the integration tests** with the marker enabled:
   ```bash
   uv run pytest tests/providers -m integration
   ```
   The first run launches the OAuth flow in a browser; subsequent runs reuse the persisted token under `~/.uacp/secrets/`.

The test sends an email to the test address and lists the next ten calendar events on the test account. Both are non-destructive against the test account (the email lands in the user's own inbox; the list is read-only).

### Slack integration tests

The integration tests under `tests/providers/test_slack.py` exercise the workspace-scoped OAuth flow + chat.postMessage + conversations.list. They are skipped by default; running them requires:

1. **Create a Slack app** at https://api.slack.com/apps → "Create New App" → "From scratch". Choose a development workspace you control.
2. **OAuth & Permissions** → Bot Token Scopes → add `chat:write` and `channels:read` (optionally `channels:history` for future message-read tests). Under "Redirect URLs" add `http://localhost:8765/oauth/callback`.
3. **Install to Workspace.** Copy the Client ID and Client Secret from "Basic Information".
4. **Pick or create a test channel** in the workspace. After install, `/invite @<botname>` from the channel so the bot is a member. Capture the channel id (`Cxxxxxxxxxx`).
5. **Populate `.env` in `prototype/python/`** with:
   ```
   UACP_SLACK_CLIENT_ID=<client id>
   UACP_SLACK_CLIENT_SECRET=<client secret>
   UACP_SLACK_TEST_CHANNEL_ID=Cxxxxxxxxxx
   UACP_SLACK_REDIRECT_URI=http://localhost:8765/oauth/callback
   ```
6. **Run the integration tests**:
   ```bash
   uv run pytest tests/providers/test_slack.py -m integration
   ```

The five tests are: OAuth flow end-to-end (the fixture itself proves it; the named test asserts the parsed tokens carry the expected `xoxb-` prefix and team metadata); chat.postMessage success (sends a real message to the test channel); chat.postMessage with an invalid channel id (asserts the §3.3/§4.6 body-predicate machinery converts Slack's 200-with-ok=false-error=channel_not_found into a canonical `DispatchError(status=200, code=not_found)`); conversations.list single page; conversations.list paginated through the cursor (`response_metadata.next_cursor` — exercises the §3.4 cursor pattern against a deeply-nested cursor location, capped at 3 pages).

### AWS S3 integration tests

The integration tests under `tests/providers/test_aws.py` exercise SigV4 signing + S3 GetObject (binary response) + S3 ListObjectsV2 (XML response with cursor pagination). They are skipped by default; running them requires:

1. **Create an AWS IAM user with programmatic access.** **Do NOT use root credentials.** **Do NOT attach AdministratorAccess or any wildcard policy.** Security-by-default: scope the user to the minimum needed.
2. **Attach a minimal IAM policy** granting only `s3:GetObject` and `s3:ListBucket` on a single named test bucket:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:ListBucket"],
         "Resource": [
           "arn:aws:s3:::your-test-bucket",
           "arn:aws:s3:::your-test-bucket/*"
         ]
       }
     ]
   }
   ```
3. **Create the bucket** in `us-east-1` (the artifact's region; multi-region support is a Stage 9 concern). Upload at least one object with a known key (e.g., `hello.txt` containing `hello world\n`) so `test_get_object_success` has something to fetch.
4. **Populate `prototype/python/.env`** with:
   ```
   UACP_AWS_ACCESS_KEY_ID=AKIA...
   UACP_AWS_SECRET_ACCESS_KEY=...
   UACP_AWS_TEST_BUCKET=your-test-bucket
   UACP_AWS_REGION=us-east-1
   UACP_AWS_TEST_KEY=hello.txt
   # Optional, for STS-derived temporary credentials:
   UACP_AWS_SESSION_TOKEN=...
   ```
5. **Run the integration tests**:
   ```bash
   uv run pytest tests/providers/test_aws.py -m integration
   ```

The five tests are non-destructive (read-only): GetObject success (downloads the test object); GetObject 404 (asserts a 404 DispatchError for a nonexistent key); ListObjectsV2 single page (XML decodes correctly to a dict); ListObjectsV2 paginated (max_pages=3 cap, advances through the bucket if it has more than 2 objects); ListObjectsV2 with prefix filter.

### GitHub integration tests

The integration tests under `tests/providers/test_github.py` exercise `api_key_header` auth (Authorization: Bearer) and RFC 8288 link-header pagination against GitHub's REST API. They are skipped by default; running them requires:

1. **Create a Personal Access Token** at https://github.com/settings/tokens (classic) OR https://github.com/settings/personal-access-tokens (fine-grained). **Fine-grained PATs are recommended** for least-privilege access. A classic PAT works too — both formats land at the wire as `Authorization: Bearer <token>` and UACP doesn't distinguish them.

2. **Token scopes / permissions**:
   - Public-repo reads only: **no scopes / permissions needed**. GitHub serves public repos without auth, but rate-limits unauth'd calls aggressively (60/hour); a PAT with no scopes raises that to 5,000/hour and is sufficient for the integration tests.
   - Private-repo reads: `repo` scope (classic) or `Contents: Read` + `Metadata: Read` (fine-grained).

3. **Populate `prototype/python/.env`** with:
   ```
   UACP_GITHUB_TOKEN=ghp_...                    # or github_pat_... or gho_...
   UACP_GITHUB_TEST_USER=octocat                 # any GitHub username
   UACP_GITHUB_TEST_REPO=octocat/hello-world    # owner/repo string
   ```

4. **Run the integration tests**:
   ```bash
   uv run pytest tests/providers/test_github.py -m integration
   ```

The five tests are read-only and non-destructive: get_repo_success (fetches a real repository's metadata); get_repo_404 (asserts 404 DispatchError for a nonexistent path); list_repos_for_user_single_page (per_page=100 fits in one page for typical users); list_repos_for_user_paginated (per_page=2 forces multiple pages; max_pages=5 cap exercises the RFC 8288 link-header loop); list_repos_with_link_header_intermediate (validates the dispatcher advances correctly when both rel=next and rel=last are present on an intermediate page).

The default `UACP_GITHUB_TEST_USER=octocat` works for everyone — Octocat is GitHub's mascot account with many public repos suitable for exercising pagination.

## Spec correspondence

Every module's docstring names the spec sections it implements. Module-level test files under `tests/unit/` exercise the spec's MUSTs at the unit level. The end-to-end mock test under `tests/unit/test_end_to_end_mock.py` ties the layers together.
