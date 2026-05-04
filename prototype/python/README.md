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

### NotebookLM (session_cookie) integration tests

The integration tests under `tests/providers/test_notebooklm.py` exercise `session_cookie` auth (§2.10) against Google NotebookLM via the same `batchexecute` RPC endpoint the web UI uses. **Read §2.10 of the spec carefully before running these.** NotebookLM has no public API; the connection replays browser session cookies, which is a grey-zone practice that may violate Google's Terms of Service. The artifact requires `tos_acknowledged: true` (literal boolean) at the spec-loader level, and every dispatch emits an `audit-log INFO risk=tos_violation_potential` event per §6.6.

Setup is unique to `session_cookie`:

1. **Install Playwright** in the prototype environment so a browser is available for capture:
   ```bash
   uv run pip install playwright
   uv run playwright install chromium
   ```

2. **Capture browser session state.** The `uacp capture-storage-state` CLI subcommand is a **stub** in v1.0; it prints the manual capture recipe rather than running an interactive Playwright session. Run the stub to see the recipe:
   ```bash
   uv run python -m uacp_prototype.cli capture-storage-state \
       --provider notebooklm \
       --output ~/.uacp/storage/notebooklm.json
   ```
   Or run the equivalent Playwright snippet directly: launch a headed Chromium, navigate to `https://notebooklm.google.com/`, sign in with your Google account, confirm at least one notebook is visible, then call `context.storage_state(path=...)` to write the cookies + origins to disk. Set the file to mode `0600` afterwards — captured state is a credential.

3. **Populate `prototype/python/.env`** with:
   ```
   UACP_NOTEBOOKLM_STORAGE_STATE=$HOME/.uacp/storage/notebooklm.json
   UACP_NOTEBOOKLM_TEST_NOTEBOOK_ID=<an id you have access to>
   UACP_NOTEBOOKLM_TEST_MESSAGE="hello from uacp integration"
   ```

4. **(Optional, recommended for NotebookLM)** **Install the `stealth` extras** to enable Scrapling-backed anti-bot resilience per §4.10 (added in `v1.1`):
   ```bash
   uv sync --extra stealth
   ```
   Without the extras, dispatch falls back to the default `httpx` transport with a logged warning — Google's NotebookLM endpoints often return 403 / 429 / Cloudflare challenge pages to plain `httpx` requests but accept the same request from a browser-fingerprint-matching client. Anti-bot bypass is best-effort; when it works, dispatches succeed transparently; when it doesn't, the dispatch surfaces the canonical error per §4.6 the same as any other failure. Per §2.10 the ToS-violation-risk acknowledgment + §6.6 audit logging apply regardless of which transport carries the bytes — Scrapling does not relax those obligations.

   The NotebookLM example artifacts at `examples/notebooklm/list-notebooks.uacp` and `examples/notebooklm/send-chat-message.uacp` carry `dispatch.transport: "stealth"` to declare the affinity explicitly. Implementations that don't recognize the `stealth` value gracefully fall back to their default backend per §4.10.

5. **Run the integration tests**:
   ```bash
   uv run pytest tests/providers/test_notebooklm.py -m integration
   ```

The five tests are: `list_notebooks_success` (authenticated batchexecute call returns 200 and decodes as text); `list_notebooks_after_csrf_refresh` (mutates the captured `_csrf_token` cookie to a known-stale value, asserts the §2.10 refresh-and-retry path acquires a fresh token and the retry succeeds); `send_chat_message_success` (POST a chat message into a notebook); `send_chat_message_with_invalid_notebook` (graceful failure shape — accepts either a `DispatchError` or a `DispatchSuccess` carrying the RPC error envelope, since the artifact doesn't declare a `failure_predicate`); `refusal_when_storage_state_missing` (asserts `SessionCookieAuthError` when the credential resolver returns no `storage_state`).

Captured storage state is sensitive — treat it like a password. Per §6.7, recapture every **30 days** at minimum (Google rotates session cookies). Never commit captured state to git, never share it across machines, and add the storage path to your local `.gitignore` if it lands inside a working tree.

The two `.uacp` artifacts at `examples/notebooklm/` are produced through the §3.8 LLM-inference path (`source.type: inferred`, `source.model: anthropic/claude-haiku-4.5`, `source.reviewed_at` populated) — the operator described the integration in natural language, the LLM proposed an operation shape, the operator reviewed and confirmed it via `connections.ingest_nl.confirm_and_persist(approved=True)`. The `tests/unit/test_end_to_end_notebooklm_mock.py` suite demonstrates the full inference → review → confirm → load → dispatch loop with a recorded LLM response.

## MCP composition

UACP composes with the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) per Principle 4. The prototype ships an MCP server at `src/uacp_prototype/mcp/server.py` that walks a directory of `.uacp` files and exposes each operation as an MCP tool. Any MCP-aware client (Claude Code, Claude Desktop, Cursor, anything speaking MCP) can connect over stdio and call UACP-defined operations as tools.

### Starting the server

```bash
uv run python -m uacp_prototype.mcp --uacp-dir examples/
```

Against the prototype's `examples/` tree the server advertises **10 tools** — one per operation across the five validated providers:

| Tool name | UACP source | Auth method |
|---|---|---|
| `google_gmail_users_messages_send` | `examples/google/gmail-send.uacp` | `oauth2_authorization_code` |
| `google_calendar_events_list` | `examples/google/google-calendar-list.uacp` | `oauth2_authorization_code` |
| `slack_chat_postmessage` | `examples/slack/chat-postMessage.uacp` | `x-oauth2-workspace` |
| `slack_conversations_list` | `examples/slack/conversations-list.uacp` | `x-oauth2-workspace` |
| `aws_s3_getobject` | `examples/aws/s3-getobject.uacp` | `aws_sigv4` |
| `aws_s3_listobjectsv2` | `examples/aws/s3-listobjectsv2.uacp` | `aws_sigv4` |
| `github_repos_get` | `examples/github/repos-get.uacp` | `api_key_header` |
| `github_repos_list_for_user` | `examples/github/repos-list-for-user.uacp` | `api_key_header` |
| `notebooklm_notebooklm_list_notebooks` | `examples/notebooklm/list-notebooks.uacp` | `session_cookie` |
| `notebooklm_notebooklm_send_chat_message` | `examples/notebooklm/send-chat-message.uacp` | `session_cookie` |

### How the operation→tool mapping works

For each `.uacp` file under the configured directory:

- **Provider name** comes from the artifact's parent directory (`examples/google/...` → provider `google`); when an artifact lives directly at the configured root, the artifact's `name` field or filename stem is used instead.
- **Tool name** is `<provider>_<operation_id>`, with dots and slashes normalized to underscores (per the OpenAI / Anthropic / Google `^[a-zA-Z0-9_-]{1,128}$` tool-name validation regex). The 128-char cap is enforced by truncation; collisions on truncated names are an artifact-naming concern.
- **Tool input schema** is derived from the operation's request shape per §3.2: `path_params` mirrors the operation's `path_parameters` schema, `query` mirrors `query_parameters`, `body` mirrors the request body's inline `schema`, plus an optional `extra_headers` map. Keyword argument names match `DispatchClient.dispatch`.
- **Tool execution** dispatches through the existing UACP runtime (`auth/`, `dispatch/`, `lifecycle/`, `security/`). The same security and dispatch invariants the spec enforces for direct UACP consumers apply transparently to MCP-side callers.

### Connecting from Claude Code

Add the MCP server to your project-level `.claude/mcp.json` (or your user-level config). The server expects to be invoked from a directory where the prototype is installed (`uv run` activates the right environment automatically):

```json
{
  "mcpServers": {
    "uacp": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/UACP/prototype/python",
        "run", "python", "-m", "uacp_prototype.mcp",
        "--uacp-dir", "examples"
      ]
    }
  }
}
```

When Claude Code starts, it connects over stdio, calls `tools/list`, and the 10 UACP-defined tools become available alongside any other MCP servers configured. Claude can then call `github_repos_get`, `slack_conversations_list`, `aws_s3_listobjectsv2`, etc., the same way it calls native MCP tools.

### Credential resolution

The default dispatch factory pulls credentials from these locations:

| Auth method | Source |
|---|---|
| `oauth2_authorization_code` | env var `UACP_<PROVIDER>_ACCESS_TOKEN` (run an OAuth flow first, e.g. via the integration test harness) |
| `x-oauth2-workspace` | env var `UACP_<PROVIDER>_BOT_TOKEN` |
| `api_key_header` / `api_key_query` | env var `UACP_<PROVIDER>_API_KEY` |
| `aws_sigv4` | env vars `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`) |
| `session_cookie` | env var `UACP_<PROVIDER>_STORAGE_STATE` (path to a Playwright `storage_state.json` file) |

`<PROVIDER>` is the artifact's `name` field uppercased with `-` and `.` replaced by `_`. For ad-hoc dispatch via Claude Code, set the env vars in the shell that launches Claude Code (or in your `.claude/mcp.json` `env` block).

### Transport selection (v1.1)

Per §4.10 (added in `v1.1`), the dispatch client supports pluggable HTTP transport backends. The MCP server's default factory invokes `select_transport_for_artifact` per artifact, which applies this decision tree:

1. If the artifact's `dispatch.transport` field is set to `"default"` → `HttpxTransport`.
2. If set to `"stealth"` and the optional `stealth` extras are installed (`uv sync --extra stealth`) → `ScraplingTransport` (Camoufox-driven, anti-bot fingerprinting). Falls back to `HttpxTransport` with a logged warning when the extras aren't installed.
3. If `dispatch.transport` is unset and the artifact uses `session_cookie` auth, the same auth-method-affinity rule applies — stealth when available, httpx otherwise.
4. Otherwise → `HttpxTransport`.

The `examples/notebooklm/*.uacp` files declare `dispatch.transport: "stealth"` to advertise their affinity. Anti-bot bypass is best-effort; the §4.1 — §4.9 dispatch contract is preserved across both backends per §4.10 conformance — retries, rate limits, audit logging, and canonical error mapping all apply identically.

### Verifying composition

The MCP-composition test suite at `tests/integration/test_mcp_composition.py` (marked `@pytest.mark.mcp_integration`, skipped by default) pairs the prototype's `UACPServer` with the MCP SDK's `ClientSession` over in-process memory streams and asserts: tool count + names, tool-schema derivation, tool execution returning `DispatchSuccess`, argument pass-through, canonical error propagation per §4.6, credential-resolution-failure surfacing, and multi-provider routing. Run with:

```bash
uv run pytest tests/integration/test_mcp_composition.py -m mcp_integration
```

## Session capture (v1.1)

Per §3.12 (added in `v1.1`), UACP supports browser-instrumented session capture as a schema source: the user opens the target service in a real browser, demonstrates the actions they want UACP to learn, and the prototype records the resulting HTTP traffic for downstream operation synthesis (Stage 11.2). Stage 11.1 ships the recording side end-to-end — the synthesis pass that turns captured traffic into `.uacp` operations is the next session.

### Privacy and ToS

Captures contain everything the user's logged-in browser sees during the session — cookies, response bodies, private documents, emails, anything visible in the DevTools Network panel. The prototype encrypts captures at rest under §6.3 envelope encryption (AES-256-GCM, per-capture data-encryption-key wrapped by the user's master KEK at `~/.uacp/master.key`) and never transmits the captured artifact off-device. The user is responsible for the actions they demonstrate. Per §2.10's `session_cookie` discussion, capture-driven discovery against grey-zone providers (services without a public API) carries the same ToS-violation-risk posture; replaying the captured traffic later through `session_cookie` auth requires the operator's explicit `tos_acknowledged: true` ack at the spec-loader level.

### Installing Playwright

The capture engine uses Playwright as its default backend. Playwright is in the optional `capture` extras group:

```bash
uv sync --extra capture
uv run playwright install chromium
```

### Running a capture

```bash
uv run python -m uacp_prototype.cli capture-session \
  --initial-url https://example.com/ \
  --output secret://local-keyring/example-capture
```

Optional flags:

- `--browser playwright|scrapling` — default `playwright`. The `scrapling` value transparently delegates to Playwright in v1.1.x because Scrapling 0.3's API is dispatch-only (no long-session traffic-event hooks); the Stage 11.0 dispatch transport (ScraplingTransport) still uses Scrapling's stealth posture for replay. Stage 11.2+ may revisit when Scrapling exposes a session-capable browser API.
- `--provider <name>` — optional provider hint that lands in audit-log payloads + the deterministic capture-id seed.

The flow:

1. The CLI prints `Opening <url> in a browser. Log in if needed, then demonstrate the actions you want UACP to learn. When you're done, return to this terminal and press Enter to stop recording.`
2. A non-headless browser window opens at the initial URL. The user logs in, navigates, performs the operations they want UACP to learn.
3. The CLI prints `Captured N request(s) so far...` every 5 seconds.
4. The user returns to the terminal and presses Enter (or closes the browser window — the recorder detects the disconnect and finalizes cleanly).
5. The CLI prints `Captured M request(s) over T seconds. Persisting...` followed by `Capture stored at secret://local-keyring/example-capture. Use it as the source.capture_ref of a session_capture-sourced operation in a .uacp file (per §3.12).`

### What the captured artifact contains

The on-disk artifact is an AES-256-GCM-encrypted blob at `~/.uacp/secrets/example-capture.enc`. After decryption (which happens transparently when Stage 11.2's synthesis pass loads it via `secret://local-keyring/example-capture`), the artifact carries:

- **HAR 1.2-format entries** — every `request` / `response` pair Playwright observed during the session, including method, URL, headers, body, status, response body, and timing data. Suitable for inspection by standard HAR tools after explicit decryption.
- **`storage_state`** — the post-session cookies + localStorage payload Playwright produces via `context.storage_state()`. This is the session-credential surface that Stage 11.2 routes into a §2.10 `session_cookie` connection's `storage_state_ref`.
- **Metadata** — user-agent string, viewport size, the recorder's checkpoint cadence, the operator-provided `--provider` hint.

The artifact's `capture_id` is a deterministic SHA-256 hash of `(initial_url + captured_at + provider)` truncated to 16 hex chars per §3.12, so the same capture session is never accidentally stored twice. The user-supplied `--output` URI is honored verbatim — the storage path is whatever name the operator chose, regardless of the deterministic `capture_id`.

### Resilience

Every 30 seconds the recorder checkpoints its in-progress capture to `~/.uacp/captures/in-progress/<id>.har.tmp` (mode 0600, plaintext, deleted on clean stop). On mid-session crash, browser disconnect, terminal close, or `Ctrl-C`:

- The signal handler sets the recorder's disconnect event so the await loop exits.
- `recorder.stop()` runs in the cleanup path and finalizes whatever was captured up to that point into an encrypted artifact via the same `store_capture` path.
- If the process dies before the cleanup path runs, the temp checkpoint file at `~/.uacp/captures/in-progress/<id>.har.tmp` is recoverable via `recover_in_progress(capture_id)` from `uacp_prototype.capture.recorder`. This is a manual recovery surface in v1.1; an `uacp resume-capture` CLI command lands in a future minor.

### Audit trail

Per §6.6, the recorder emits structured INFO-level events at:

- **`capture started`** — `id=<capture_id> backend=<playwright|scrapling> initial_url=<url>`.
- **`capture stopped`** — `id=<capture_id> requests=<count> duration_ms=<elapsed>`.
- **`capture stored`** — `ref=<secret://...> id=<capture_id> requests=<count> duration_ms=<elapsed> host=<initial_host>`.

Audit payloads NEVER carry captured cookies, `Authorization` header values, or `storage_state` contents. The audit trail is an operator-visible signal; the encrypted blob is the data store.

### Forward reference: Stage 11.2 (operation synthesis)

The captured artifact at `secret://local-keyring/example-capture` is the input to Stage 11.2's operation-synthesis pass. That session will load the artifact, cluster the captured requests into candidate operations per §3.12's clustering rules (same endpoint + method → operation; URL pattern → path parameters; per-frequency → required vs optional parameters), refine the inference via the §3.8 LLM-inference path, and produce a `.uacp` file with `source.type: "capture"` operations referencing this `capture_ref`. Stage 11.1 stops at "the captured artifact is stored cleanly with a stable reference."

### Verifying capture end-to-end

The capture-integration suite at `tests/integration/test_capture_session_live.py` (marked `@pytest.mark.capture_integration`, skipped by default) launches a real Playwright-driven Chromium against `httpbin.org` and asserts the recorder lands at least one entry that survives the encrypted-storage round-trip. Run with:

```bash
uv sync --extra capture
uv run playwright install chromium
uv run pytest tests/integration/test_capture_session_live.py -m capture_integration
```

## Operation synthesis from captures (v1.1)

Stage 11.2 closes the §3.12 capture pipeline: a captured session (Stage 11.1) becomes a draft `.uacp` file via deterministic clustering + LLM synthesis, gated on explicit user approval per §3.12 + §3.8 mandatory-user-review.

### Pipeline

1. The CLI loads + decrypts the encrypted-at-rest capture via `secret://...` reference.
2. `uacp_prototype.capture.analyzer.analyze_capture` clusters the captured requests into candidate operations (deterministic; no LLM): same-method-and-path-signature requests group together; variable-shaped path segments (UUID / integer / slug / hex / email) become path parameters; query parameters and body keys become candidate operation parameters with required/optional inference from frequency; third-party-domain requests + image/font/CSS/JS asset loads + favicons + OPTIONS preflights get filtered out as noise.
3. `uacp_prototype.connections.ingest_capture.synthesize_from_capture` builds the LLM prompt from the structured AnalysisResult (the LLM never sees raw HAR), calls the LLM, parses the JSON response, drops any operations the LLM hallucinated outside the candidate-cluster set, and returns a `CaptureSynthesisDraft` with §3.12 provenance (`source.type=capture`, `captured_at`, `user_intent`, `capture_ref`, `confidence`; `reviewed_at` is unset until approval).
4. The CLI renders the draft, prompts approve/edit/refine/abort, and only persists on explicit approval. The persisted `.uacp` file's `source.reviewed_at` field is the operator's signature; loading via `spec.loader.load` rejects any capture-sourced operation with `reviewed_at` missing or empty (spec-level enforcement, not just a UX prompt).

### Running synthesis

```bash
# Step 1: capture (Stage 11.1)
uv run python -m uacp_prototype.cli capture-session \
  --initial-url https://api.example.com/ \
  --output secret://local-keyring/example-capture

# Step 2: synthesize
uv run python -m uacp_prototype.cli synthesize-from-capture \
  --capture-ref secret://local-keyring/example-capture \
  --intent "I logged in and listed my projects." \
  --output ./example.uacp
```

The interactive review prompt accepts:

- `a` (or `approve`) — write the draft to `--output` with `source.reviewed_at` stamped at the current UTC time.
- `e` (or `edit`) — open the assembled `.uacp` JSON in `$EDITOR`; on save, the edited file replaces the in-memory draft and the CLI returns to the review prompt.
- `r` (or `refine`) — prompt for one-line natural-language feedback and call the LLM again with the prior draft + the operator's correction. The refinement loop is capped at 3 rounds per the §3.12 + §3.8 stability rule (after that, the operator is told to switch to manual editing).
- `x` (or `abort`) — exit without writing. The capture artifact remains; re-run synthesize-from-capture later to try again.

### Auth block selection

The synthesis pipeline produces the **operations** block; the **authentication** block is the operator's decision and is not synthesized. The drafted `.uacp` file ships with `authentication: {}` and a `dispatch.base_url` derived from the capture's primary host. The operator fills in the matching auth block before the file passes `uacp validate` — typically `session_cookie` (per §2.10) for capture-driven flows against grey-zone providers, or one of the OAuth methods for providers with public APIs. Auth-block-from-capture inference (e.g., detecting the appropriate `session_cookie` cookie whitelist from the capture's `auth_artifacts` summary) is a future-`v1.x` candidate; Stage 11.2 stops at the operations level.

### LLM provider

The synthesis flow uses the same `LLMCallable` Protocol as the §3.8 inference path (see `connections/ingest_nl.py`). The default callable wraps OpenRouter (`build_default_openrouter_callable`); operators set `OPENROUTER_API_KEY` + optional `UACP_LLM_MODEL` (default `anthropic/claude-haiku-4.5`). Tests inject a deterministic mock LLM via the same Protocol — synthesis quality testing happens against recorded responses, not live API calls.

Per §6.6 the synthesis emits four audit events: `synthesis started` (capture_ref + candidate count + intent_len), `synthesis llm-call completed` (kept_ops + dropped_ops + raw_chars), `synthesis user-reviewed` (decision letter), `synthesis file-persisted` (output_path + operation_count). Audit payloads NEVER carry the operator's full intent text or any captured cookie / Authorization values — the recorder + analyzer's auth-value scrubbing posture extends through the synthesis layer.

### Hallucination drop

The brief's hard rule "an LLM that returns operations beyond the candidate list should have those operations dropped at validation" is enforced mechanically by `_filter_to_candidates`: the LLM's output operations are matched against the analyzer's `(method, path_template)` set, and any operation that doesn't match a candidate lands in `draft.dropped_operations` (visible to the operator via the review render) but never in the persisted file. Even if the LLM ignores the system-prompt instruction not to invent, the filter catches it.

## Spec correspondence

Every module's docstring names the spec sections it implements. Module-level test files under `tests/unit/` exercise the spec's MUSTs at the unit level. The end-to-end mock test under `tests/unit/test_end_to_end_mock.py` ties the layers together.
