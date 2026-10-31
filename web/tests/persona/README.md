# Custom persona synthetic HTTP browser acceptance

This gate runs the production `web/dist` bundle in Chromium against the actual
HTTP application, backed by a fresh temporary synthetic database and an injected
fake chat/title model. It is not a real-provider quality assessment, a user-data
migration test, or installed-desktop acceptance.

The dedicated workflow `.github/workflows/persona-browser.yml` runs only for
`feat/20261031-custom-companion-persona`. It has read-only repository permission,
does not persist checkout credentials, and uses no provider secret, paid
workflow, deployment, dispatch, or publication step. Dependency and Chromium
installation precede application execution.

## Local use in an authorized browser executor

Install the existing locked Python/Node dependencies and build the frontend.
Then start the server with an empty temporary data directory:

```sh
export PYTHONPATH=src:.
export LITELLM_LOCAL_MODEL_COST_MAP=True
uv run python scripts/persona_browser_server.py \
  --data-dir "$(mktemp -d)" --port 18763 --static-dir "$PWD/web/dist" \
  --evidence-dir "$PWD/artifacts/persona-browser"
```

In the same authorized network namespace:

```sh
node --test web/tests/persona/network-guard.test.mjs
node web/tests/persona/run.mjs artifacts/persona-browser
```

By default the runner uses Playwright-managed Chromium. `CHROMIUM_PATH` may name
an already installed and authorized executable. Do not retry browser startup in
an executor where socket/sandbox permission has been denied; use the dedicated
CI path rather than changing permissions, mounts, or binding the app externally.

The server binds only `127.0.0.1`, requires an unused synthetic data directory,
installs the existing Python loopback-only network guard before importing the
application, and injects fake chat/title models. The browser requires a synthetic
sentinel before interacting. Page requests use exact same-origin/read/write
allowlists; external origins, URL credentials, unexpected writes and WebSockets
are denied. The application never loads the user's default data or credentials.

## Evidence

The runner exercises draft-only presets/cancel, explicit save, repeat/lost-response
recovery, multi-window conflicts, append-only restore, empty activity selection,
Chinese/English UI and shared companion naming. Reports, DOM/accessibility
snapshots and screenshots describe synthetic UI behavior only. Fake-model output
is explicitly synthetic, not evidence of naturalness or prompt-injection safety.

CI retains only `artifacts/persona-browser` for 14 days, including failure reports.
The temporary database/configuration remains outside that artifact directory.
Do not include authorization headers, tokens, real user messages or credentials in
reports, and do not publish screenshots to Space without a separate request.

A passing guard test is not a browser pass. The final report must distinguish
checks executed locally from the later CI Chromium result.
