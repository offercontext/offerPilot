# Runtime Diagnostics Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Settings runtime diagnostics fixed-height and server-paginated.

**Architecture:** The JSONL reader returns a bounded newest-relative offset page with metadata. The Settings view queries one page at a time, confines that page to a 360px viewport, and polls only page one.

**Tech Stack:** Python, FastAPI, React, TypeScript, TanStack Query v5, Ant Design, pytest, Vitest.

---

### Task 1: Paginate the Diagnostics API

**Files:**
- Modify: `src/offerpilot/diagnostics.py:1-45`
- Modify: `src/offerpilot/api.py:14,1310-1312`
- Modify: `tests/test_diagnostics_api.py:1-23`

- [ ] **Step 1: Add failing API tests for offset pages and validation**

```python
def test_get_logs_returns_newest_relative_pages_and_metadata(tmp_path):
    for number in range(1, 6):
        append_log_entry(tmp_path, "INFO", f"entry-{number}")
    with (tmp_path / "logs" / "offerpilot.log").open("a", encoding="utf-8") as handle:
        handle.write("not-json\n[]\n")
    client = TestClient(create_app(data_dir=tmp_path))

    assert client.get("/api/logs?limit=2&offset=0").json() == {
        "entries": [{"level": "INFO", "message": "entry-4"}, {"level": "INFO", "message": "entry-5"}],
        "total": 5, "limit": 2, "offset": 0, "has_more": True,
    }
    assert [item["message"] for item in client.get("/api/logs?limit=2&offset=2").json()["entries"]] == ["entry-2", "entry-3"]
    assert client.get("/api/logs?limit=2&offset=4").json() == {
        "entries": [{"level": "INFO", "message": "entry-1"}],
        "total": 5, "limit": 2, "offset": 4, "has_more": False,
    }

def test_get_logs_handles_empty_and_invalid_pages(tmp_path):
    append_log_entry(tmp_path, "INFO", "only-row")
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/api/logs?limit=20&offset=20").json() == {
        "entries": [], "total": 1, "limit": 20, "offset": 20, "has_more": False,
    }
    assert client.get("/api/logs?limit=0").status_code == 400
    assert client.get("/api/logs?limit=101").status_code == 400
    assert client.get("/api/logs?offset=-1").status_code == 400
```

- [ ] **Step 2: Run the test to verify the red state**

Run: `uv run pytest tests/test_diagnostics_api.py -q`

Expected: page metadata and offset assertions fail because the route returns only `entries`.

- [ ] **Step 3: Implement streaming page reading and a validated route**

```python
from collections import deque

class LogPage(TypedDict):
    entries: list[LogEntry]
    total: int
    limit: int
    offset: int
    has_more: bool

def read_recent_log_page(data_dir: Path, *, limit: int, offset: int) -> LogPage:
    retained: deque[LogEntry] = deque(maxlen=offset + limit)
    total = 0
    path = _log_path(data_dir)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                total += 1
                retained.append({"level": str(parsed.get("level") or "INFO"), "message": str(parsed.get("message") or "")})
    rows = list(retained)
    end = max(0, len(rows) - offset)
    entries = rows[max(0, end - limit):end]
    return {"entries": entries, "total": total, "limit": limit, "offset": offset, "has_more": offset + len(entries) < total}
```

Replace the API route after importing `Query` and `read_recent_log_page`:

```python
@app.get("/api/logs")
def get_logs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return read_recent_log_page(resolved_data_dir, limit=limit, offset=offset)
```

- [ ] **Step 4: Run the focused backend verification**

Run:

```powershell
uv run pytest tests/test_diagnostics_api.py -q
uv run ruff check src/offerpilot/diagnostics.py src/offerpilot/api.py tests/test_diagnostics_api.py
```

Expected: chronological page rows, malformed-row exclusion, `has_more`, out-of-range pages, and query validation all pass. The repository-wide validation handler intentionally maps invalid query parameters to HTTP 400.

- [ ] **Step 5: Commit the API boundary**

```powershell
git add src/offerpilot/diagnostics.py src/offerpilot/api.py tests/test_diagnostics_api.py
git commit -m "feat: AI paginate runtime diagnostics logs"
```

### Task 2: Expose a Typed LogsPage Client Contract

**Files:**
- Modify: `web/src/services/chat.ts:64-72,124-127`
- Create: `web/src/services/chat.logs.test.ts`

- [ ] **Step 1: Write the failing service request test**

```ts
const get = vi.fn().mockResolvedValue({
  data: { entries: [{ level: 'WARNING', message: 'retry' }], total: 41, limit: 20, offset: 20, has_more: true },
});
vi.mock('./http', () => ({ createApiClient: () => ({ get }) }));

it('requests one paged diagnostics response', async () => {
  const { getLogs } = await import('./chat');
  await expect(getLogs(20, 20)).resolves.toEqual({
    entries: [{ level: 'WARNING', message: 'retry' }], total: 41, limit: 20, offset: 20, has_more: true,
  });
  expect(get).toHaveBeenCalledWith('/logs', { params: { limit: 20, offset: 20 } });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web; npm.cmd test -- --run src/services/chat.logs.test.ts`

Expected: `getLogs` has no offset argument and returns a bare list.

- [ ] **Step 3: Implement LogsPage and pass both query parameters**

```ts
export interface LogsPage {
  entries: LogEntry[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export async function getLogs(limit = 20, offset = 0): Promise<LogsPage> {
  const { data } = await http.get<LogsPage>('/logs', { params: { limit, offset } });
  return {
    entries: data.entries ?? [], total: data.total ?? 0, limit: data.limit ?? limit,
    offset: data.offset ?? offset, has_more: data.has_more ?? false,
  };
}
```

- [ ] **Step 4: Run the service and type checks**

Run:

```powershell
cd web
npm.cmd test -- --run src/services/chat.logs.test.ts
npm.cmd exec tsc -- -b
```

Expected: the request includes `limit` and `offset`, and all callers use `LogsPage` rather than an array.

- [ ] **Step 5: Commit the client contract**

```powershell
git add web/src/services/chat.ts web/src/services/chat.logs.test.ts
git commit -m "feat: AI add paged runtime log service"
```

### Task 3: Render a Fixed, Paged Diagnostics View

**Files:**
- Modify: `web/src/components/SettingsView.tsx:1-100,144-184`
- Modify: `web/src/components/SettingsView.test.ts:1-26`
- Create: `web/src/components/SettingsView.pagination.test.tsx`

- [ ] **Step 1: Write failing jsdom tests for page query, viewport, and error retry**

Mock `useQuery`, `useQueryClient`, and the Settings services. With a page-two result (`total=41`, `offset=20`), assert the runtime query is:

```ts
expect(useQuery).toHaveBeenCalledWith(expect.objectContaining({
  queryKey: ['runtime-logs', 20, 20],
  refetchInterval: false,
}));
```

For page one, render SettingsView and assert:

```ts
const viewport = view.container.querySelector('[aria-label="运行日志列表"]') as HTMLElement;
expect(viewport.style.height).toBe('360px');
expect(viewport.style.overflowY).toBe('auto');
```

For an initial error with no data, assert `button[aria-label="重试日志加载"]` exists, dispatch its click, verify `refetch` is called once, and verify the text does not contain `暂无日志`. Extend the existing raw source test with `Pagination`, `LOG_PAGE_SIZE`, the retry label, and `360`.

- [ ] **Step 2: Run tests to prove the red state**

Run: `cd web; npm.cmd test -- --run src/components/SettingsView.pagination.test.tsx src/components/SettingsView.test.ts`

Expected: no pagination query key, viewport, or retry control exists.

- [ ] **Step 3: Implement page state, latest-page polling, refresh, and bounded UI**

```tsx
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Divider, Empty, Pagination, Skeleton, Space, Spin, Tag, Typography } from 'antd';
import { useState } from 'react';

const LOG_PAGE_SIZE = 20;
const LOG_VIEWPORT_STYLE = { height: 360, overflowY: 'auto', overscrollBehavior: 'contain' } as const;

const [logPage, setLogPage] = useState(1);
const logOffset = (logPage - 1) * LOG_PAGE_SIZE;
const queryClient = useQueryClient();
const logsQuery = useQuery({
  queryKey: ['runtime-logs', LOG_PAGE_SIZE, logOffset],
  queryFn: () => getLogs(LOG_PAGE_SIZE, logOffset),
  placeholderData: keepPreviousData,
  refetchInterval: logPage === 1 ? 15000 : false,
});
const logsPage = logsQuery.data;

function refreshLogs() {
  setLogPage(1);
  void queryClient.invalidateQueries({ queryKey: ['runtime-logs', LOG_PAGE_SIZE, 0] });
}
```

Use `refreshLogs` for the existing refresh button. Render initial loading only when there is no page; render an error Alert with `aria-label="重试日志加载"` when an initial request fails; retain prior page data plus a warning/retry Alert if a refresh fails. Render only this page inside the accessible bounded region and never concatenate earlier pages:

```tsx
{logsPage ? (
  <>
    <div aria-label="运行日志列表" style={LOG_VIEWPORT_STYLE}><LogList entries={logsPage.entries} /></div>
    <Pagination size="small" current={logPage} pageSize={LOG_PAGE_SIZE} total={logsPage.total} showSizeChanger={false} onChange={setLogPage} />
    {logsQuery.isFetching ? <Spin size="small" aria-label="正在加载日志页" /> : null}
  </>
) : null}
```

- [ ] **Step 4: Run UI/service tests and compile**

Run:

```powershell
cd web
npm.cmd test -- --run src/components/SettingsView.pagination.test.tsx src/components/SettingsView.test.ts src/services/chat.logs.test.ts
npm.cmd exec tsc -- -b
```

Expected: page offset, 360px containment, newest-page-only polling, refresh reset, retry, and type checks pass.

- [ ] **Step 5: Commit the Settings diagnostics UI**

```powershell
git add web/src/components/SettingsView.tsx web/src/components/SettingsView.test.ts web/src/components/SettingsView.pagination.test.tsx
git commit -m "feat: AI paginate runtime diagnostics UI"
```

### Task 4: Verify and Review

**Files:**
- Verify only: all files changed in Tasks 1-3

- [ ] **Step 1: Run focused cross-layer verification**

```powershell
uv run pytest tests/test_diagnostics_api.py -q
cd web
npm.cmd test -- --run src/services/chat.logs.test.ts src/components/SettingsView.pagination.test.tsx src/components/SettingsView.test.ts
npm.cmd run build
```

Expected: API pages, client parameters, fixed view behavior, and production bundle pass.

- [ ] **Step 2: Walk the feature in the in-app browser**

1. Open Settings and verify diagnostics stays 360px while the current page scrolls.
2. With more than 20 local rows, confirm page 2 sends `offset=20` and does not poll after 15 seconds.
3. Refresh from page 2 and confirm it returns to page 1/offset zero.
4. Force a local `/api/logs` failure and verify retry appears instead of `暂无日志`.

- [ ] **Step 3: Independent review and release gate**

Request review of validation, newest-relative ordering, bounded retention, stale-page behavior, and keyboard pagination. Resolve every finding with a focused test. Then run:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
cd web
npm.cmd test -- --run
npm.cmd run build
cd ..
uv run oc smoke --static-dir web/dist
```

Expected: every command exits zero before handoff.
