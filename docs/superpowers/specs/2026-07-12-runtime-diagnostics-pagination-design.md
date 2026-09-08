# Runtime Diagnostics Pagination Design

## Goal

Keep the Settings runtime-diagnostics section at a stable height while making
local log retrieval paged. A growing log file must not keep extending the page
or cause the browser to render every returned entry.

## Current State

`GET /api/logs?limit=<n>` calls `read_recent_log_entries`, which reads the log
file and returns a single list. `SettingsView` always asks for 20 entries and
maps the complete response directly into the page. There is no page metadata,
page selection, or height boundary around the log list.

## Options Considered

1. Server-side offset pagination with a fixed client viewport. Chosen. It
   bounds each response and provides a familiar way to inspect older local
   diagnostics.
2. Fetch all logs once and paginate in the browser. Rejected because network
   and render cost still grow with the log file.
3. Cursor pagination based on byte offsets. Rejected for now: the local JSONL
   file has no persistent sequence id, and a cursor contract would add
   complexity without a demonstrated volume requirement.

## API Contract

`GET /api/logs` accepts these query parameters:

- `limit`: page size; default `20`, inclusive range `1..100`.
- `offset`: number of newest valid log entries to skip; default `0`, minimum
  `0`.

It returns:

```json
{
  "entries": [{ "level": "INFO", "message": "server started" }],
  "total": 41,
  "limit": 20,
  "offset": 0,
  "has_more": true
}
```

`offset=0` represents the most recent page. Entries within a page remain in
chronological order (oldest to newest) so the latest line is at the bottom of
the current page. Malformed JSONL rows remain excluded from both `entries` and
`total`, preserving current diagnostics behavior.

The reader scans the JSONL file line-by-line and keeps only the requested tail
window in memory while counting valid rows. It must not use `read_text()` to
materialize the entire log file. The API returns a normal empty page for an
offset beyond the available entries and uses FastAPI validation for invalid
`limit` or `offset` values.

## Settings UI

The diagnostics card keeps its header and refresh button. The log viewport is
fixed at `360px`; its current page can scroll internally for long messages,
but the Settings page never grows as more records are written.

The default view is page 1 (the newest 20 logs). A compact Ant Design
pagination control below the viewport changes `offset` and starts a new API
query. It uses the server-provided `total` and a fixed page size of 20; this
release intentionally has no page-size selector.

Polling continues every 15 seconds only on the newest page. Older pages stop
automatic polling so new writes cannot shift an operator's current offset
while they inspect history. The refresh button always resets to page 1 and
refetches it. During page changes, keep the prior page visible until the new
one arrives and show a small loading state. A failed page query shows a retry
control instead of treating the page as empty.

## Frontend Data Flow

`getLogs(limit, offset)` returns a `LogsPage`, not a bare array. The React
Query key includes both values: `['runtime-logs', limit, offset]`. `SettingsView`
owns the page state and derives `offset = (page - 1) * 20`.

`LogList` receives only `page.entries`; pagination renders from `page.total`.
Refreshing or entering the Settings screen starts at offset zero. The client
never concatenates pages or retains an unbounded list of diagnostics.

## Error Handling And Accessibility

- The fixed log region has an accessible label and only it scrolls.
- Pagination uses Ant Design's native page controls and remains reachable by
  keyboard.
- An empty successful page shows the existing empty state.
- A failed request keeps the previous data when available, exposes a Chinese
  retry action, and never presents a failure as "暂无日志".
- Existing malformed log rows stay invisible rather than breaking the page.

## Testing

Backend tests cover newest-page ordering, second-page offsets, total and
`has_more`, malformed-row exclusion, empty/out-of-range pages, and invalid
pagination parameters.

Frontend tests cover the typed service query parameters, fixed viewport style,
page query-key changes, latest-page-only polling, refresh reset to page 1, and
error retry behavior. Build verification remains required.

## Boundaries

- No persistent log index, database migration, external logging service, or
  retention/deletion policy.
- No changes to log entry schema or unrelated Settings sections.
- This does not guarantee constant disk I/O for every arbitrarily deep offset;
  it guarantees bounded API payload and bounded in-memory row retention for
  the requested page window.
