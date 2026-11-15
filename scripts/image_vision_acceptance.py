"""Four-call, opt-in image acceptance through the real product HTTP API.

Dry run: python scripts/image_vision_acceptance.py --report-dir /tmp/image-check
Live run: supply OFFERPILOT_IMAGE_DEEPSEEK_API_KEY and add --execute.
Focused follow-up: add --history-only to run the job image and historical-image follow-up.
No credential, production data, or provider response payload is written to config.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from threading import Lock
import time
from typing import Any, cast
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont


MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
KEY_ENV = "OFFERPILOT_IMAGE_DEEPSEEK_API_KEY"
CALL_LIMIT = 4
OUTPUT_LIMIT = 512
CASES = ("geometry", "chinese", "job", "history")
EXPECTED: dict[str, dict[str, Any]] = {
    "geometry": {"red_circles": 3, "blue_squares": 2, "green_triangles": 1, "total": 6},
    "chinese": {
        "title": "面试安排通知", "date": "2026-11-03", "time": "14:30",
        "instruction": "请勿携带纸质简历", "location": "三号楼二层会议室",
    },
    "job": {
        "company": "星河科技", "role": "后端开发工程师", "city": "杭州",
        "mode": "混合办公", "requirements": ["Python", "SQL", "三年以上经验"],
        "contact": None,
    },
    "history": {"salary": "18–22K ×14", "deadline": "2026-11-15"},
}
PROMPTS = {
    "geometry": "只识别图片，不调用工具、不保存业务资料。只返回 JSON："
    '{"red_circles":整数,"blue_squares":整数,"green_triangles":整数,"total":整数}。',
    "chinese": "逐字读取图片，只返回 JSON，键为 title、date、time、instruction、location。"
    "日期写 YYYY-MM-DD，时间写 HH:MM，其他字段保留原文和否定词。不要调用工具。",
    "job": "读取岗位截图，只返回 JSON，键为 company、role、city、mode、requirements、contact。"
    "requirements 为原文条目数组；未提供的联系方式写 null。只回答这些字段，"
    "不要额外描述其他内容，不调用工具，不创建或修改任何业务资料。",
    "history": "请重新查看上一张岗位原图，读取之前没有询问的薪资和截止日期。"
    "只返回 JSON，键为 salary 和 deadline，日期用 YYYY-MM-DD。不要调用工具。",
}


class AcceptanceError(RuntimeError):
    """Only finite, content-free error codes cross the report boundary."""


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "api.deepseek.com"
            or parsed.path not in ("", "/", "/v1", "/v1/")
            or parsed.query or parsed.fragment):
        raise AcceptanceError("endpoint_not_allowed")
    return value.rstrip("/")


def _font_path(explicit: Path | None) -> Path:
    candidates = [explicit] if explicit else [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise AcceptanceError("cjk_font_unavailable")


def create_fixtures(directory: Path, font_path: Path | None = None) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(_font_path(font_path)), 30)
    geometry = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(geometry)
    for x in (55, 250, 445):
        draw.ellipse((x, 35, x + 90, 125), fill="#e02020")
    for x in (55, 250):
        draw.rectangle((x, 200, x + 90, 290), fill="#2050e0")
    draw.polygon(((490, 190), (435, 290), (545, 290)), fill="#159447")
    chinese = Image.new("RGB", (800, 390), "white")
    draw = ImageDraw.Draw(chinese)
    for row, line in enumerate([
        "面试安排通知", "日期：2026-11-03", "时间：14:30",
        "请勿携带纸质简历", "地点：三号楼二层会议室",
    ]):
        draw.text((35, 30 + row * 65), line, fill="black", font=font)
    job = Image.new("RGB", (800, 690), "#f3f6fa")
    draw = ImageDraw.Draw(job)
    draw.rounded_rectangle((15, 15, 785, 675), radius=12, fill="white")
    for row, line in enumerate([
        "星河科技", "后端开发工程师", "工作城市：杭州", "办公方式：混合办公",
        "薪资：18–22K ×14", "岗位要求：", "Python", "SQL", "三年以上经验",
        "截止日期：2026-11-15",
    ]):
        draw.text((35, 30 + row * 63), line, fill="#17263b", font=font)
    paths = {}
    for name, image in (("geometry", geometry), ("chinese", chinese), ("job", job)):
        path = directory / f"{name}.png"
        image.save(path, format="PNG")
        paths[name] = path
    return paths


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace("–", "-")


def grade(case: str, answer: str) -> dict[str, Any]:
    text = answer.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        actual = json.loads(text)
    except (TypeError, ValueError):
        actual = None
    fields = {}
    for key, expected in EXPECTED[case].items():
        value = actual.get(key) if isinstance(actual, dict) else object()
        if type(expected) is int:
            ok = type(value) is int and value == expected
        elif isinstance(expected, str):
            ok = isinstance(value, str) and _normalized(value) == _normalized(expected)
        elif isinstance(expected, list):
            ok = (isinstance(value, list) and all(isinstance(item, str) for item in value)
                  and sorted(_normalized(item) for item in value)
                  == sorted(_normalized(item) for item in expected))
        else:
            ok = isinstance(actual, dict) and key in actual and value is expected
        fields[key] = ok
    return {"passed": all(fields.values()), "fields": fields}


def _image_hashes(messages: list[dict[str, Any]]) -> list[str]:
    hashes = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") != "image_url":
                continue
            url = part.get("image_url", {}).get("url", "")
            if not isinstance(url, str) or not re.match(r"^data:image/(png|jpeg);base64,", url):
                raise AcceptanceError("non_inline_image_blocked")
            try:
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
            except ValueError:
                raise AcceptanceError("image_payload_invalid") from None
            hashes.append(hashlib.sha256(raw).hexdigest())
    return hashes


class GuardedTransport:
    """A fixed four-call test guard, not a replacement provider/runtime."""

    def __init__(self, api_key: str, invoke: Any) -> None:
        self._key = api_key
        self._invoke = invoke
        self._lock = Lock()
        self.records: list[dict[str, Any]] = []
        self.case = ""
        self.expected_hash = ""

    async def __call__(self, **kwargs: Any) -> Any:
        validate_endpoint(str(kwargs.get("api_base", "")))
        if kwargs.get("api_key") != self._key or kwargs.get("model") != f"openai/{MODEL}":
            raise AcceptanceError("provider_identity_changed")
        if kwargs.get("stream") is not True or self.case not in CASES:
            raise AcceptanceError("unexpected_provider_call")
        hashes = _image_hashes(kwargs.get("messages", []))
        if hashes != [self.expected_hash]:
            raise AcceptanceError("image_transport_mismatch")
        with self._lock:
            if len(self.records) >= CALL_LIMIT:
                raise AcceptanceError("provider_call_limit")
            if any(record["case"] == self.case for record in self.records):
                raise AcceptanceError("duplicate_case_call")
            record: dict[str, Any] = {
                "case": self.case, "image_sha256": hashes, "status": "started",
                "max_tokens": OUTPUT_LIMIT, "usage": None,
            }
            self.records.append(record)
        kwargs.update(timeout=45, num_retries=0, max_retries=0, max_tokens=OUTPUT_LIMIT,
                      extra_body={"thinking": {"type": "disabled"}},
                      stream_options={"include_usage": True})
        if kwargs.get("tools"):
            kwargs["tool_choice"] = "none"
        started = time.monotonic()
        try:
            stream = await self._invoke(**kwargs)
        except Exception:
            record["status"] = "provider_failed"
            record["latency_ms"] = int((time.monotonic() - started) * 1000)
            raise AcceptanceError("provider_failed") from None

        async def bounded_stream() -> Any:
            try:
                async for chunk in stream:
                    value = chunk if isinstance(chunk, dict) else chunk.model_dump()
                    for choice in value.get("choices", []):
                        if choice.get("delta", {}).get("tool_calls"):
                            raise AcceptanceError("tool_call_blocked")
                    usage = value.get("usage")
                    if isinstance(usage, dict):
                        record["usage"] = {
                            key: usage[key] for key in ("prompt_tokens", "completion_tokens",
                                                       "total_tokens")
                            if type(usage.get(key)) is int and usage[key] >= 0
                        }
                    yield chunk
                record["status"] = "completed"
            except Exception:
                record["status"] = "provider_failed"
                raise AcceptanceError("provider_failed") from None
            finally:
                record["latency_ms"] = int((time.monotonic() - started) * 1000)
                close = getattr(stream, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:
                        record["status"] = "provider_failed"
                        raise AcceptanceError("provider_failed") from None
        return bounded_stream()


async def _official_transport(invoke: Any, **kwargs: Any) -> Any:
    """Keep TLS, credentials and redirects on the explicitly authorized service."""
    import httpx
    from openai import AsyncOpenAI

    base = validate_endpoint(kwargs["api_base"])
    sdk = AsyncOpenAI(
        api_key=kwargs["api_key"], base_url=base if base.endswith("/v1") else base + "/v1",
        max_retries=0, timeout=45,
        http_client=httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=45),
    )
    try:
        stream = await invoke(**kwargs, client=sdk)
    except BaseException:
        await sdk.close()
        raise

    async def owned_stream() -> Any:
        try:
            async for chunk in stream:
                yield chunk
        finally:
            try:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            finally:
                await sdk.close()
    return owned_stream()


def _poll(client: Any, turn_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        if response.status_code != 200:
            raise AcceptanceError("status_read_failed")
        status = response.json()
        if status.get("worker_done") and status.get("state") not in {"running", "queued", "accepted"}:
            return cast(dict[str, Any], status)
        time.sleep(0.05)
    raise AcceptanceError("runtime_poll_timeout")


def run_acceptance(report_dir: Path, *, execute: bool = False,
                   font_path: Path | None = None, history_only: bool = False) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    selected_cases = ("job", "history") if history_only else CASES
    report: dict[str, Any] = {
        "status": "dry_run", "model": MODEL, "endpoint": BASE_URL,
        "synthetic_only": True, "planned_calls": len(selected_cases),
        "scope": "history_only" if history_only else "all",
        "provider_calls": [], "cases": [],
        "cost": {"currency": "CNY", "estimate": None,
                 "input_per_million": 2, "output_per_million": 8,
                 "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                 "checked_on": "2026-10-23",
                 "note": "Peak, uncached upper estimate, not a bill; missing usage stays unknown."},
    }
    guard: GuardedTransport | None = None
    try:
        paths = create_fixtures(report_dir / "fixtures", font_path)
        report["fixtures"] = {
            name: {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in paths.items()
        }
        if not execute:
            return report
        key = os.environ.get(KEY_ENV, "")
        if not key or key != key.strip():
            raise AcceptanceError("dedicated_api_key_missing")
        # No global credentials, provider/config files or production database are consulted.
        env = {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}
        for name in ("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", "OFFERPILOT_PROVIDER_AUDIT_FILE",
                     "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE"):
            env[name] = ""
        with patch.dict(os.environ, env), tempfile.TemporaryDirectory(
            prefix="offerpilot-image-acceptance-"
        ) as temporary, ExitStack() as stack:
            from fastapi.testclient import TestClient
            import litellm
            from offerpilot import api, config
            from offerpilot.ai import client as model_client
            from offerpilot.pilot_runtime import composition

            stack.enter_context(patch.object(litellm, "set_verbose", False))
            stack.enter_context(patch.object(litellm, "suppress_debug_info", True))

            profile = config.AIProviderProfile(
                id="image-acceptance", provider="openai_compatible", model=MODEL,
                base_url=BASE_URL, api_key=key, supports_vision=True,
                context_window=131072, max_output_tokens=OUTPUT_LIMIT,
            )
            in_memory = config.Config(
                providers=[profile], active_provider_id=profile.id,
                confirmation_secret=secrets.token_urlsafe(32),
            )
            for module in (api, config, composition):
                stack.enter_context(patch.object(module, "load_config", lambda _: in_memory))
            original_acompletion = getattr(model_client, "acompletion")
            guard = GuardedTransport(
                key, lambda **kwargs: _official_transport(original_acompletion, **kwargs)
            )
            stack.enter_context(patch.object(model_client, "acompletion", guard))
            stack.enter_context(patch.object(model_client, "completion",
                                            side_effect=AcceptanceError("sync_call_blocked")))
            app = api.create_app(data_dir=Path(temporary), title_model=None)
            app.state.runtime_manager.default_max_model_calls = 1
            app.state.runtime_manager.default_timeout_seconds = 60
            with TestClient(app) as client:
                capability = client.get("/api/chat/image-capability").json()
                if not capability.get("enabled") or capability.get("model") != MODEL:
                    raise AcceptanceError("image_capability_unavailable")
                job_conversation = 0
                job_ref: dict[str, Any] = {}
                for case in selected_cases:
                    before = len(guard.records)
                    payload: dict[str, Any] = {"message": PROMPTS[case], "request_id": str(uuid4())}
                    if case == "history":
                        payload["conversation_id"] = job_conversation
                        ref = job_ref
                    else:
                        upload = client.post("/api/chat/attachments", files={
                            "file": (paths[case].name, paths[case].read_bytes(), "image/png"),
                        })
                        if upload.status_code != 201:
                            raise AcceptanceError("image_upload_failed")
                        ref = upload.json()
                        payload["image_attachment_ids"] = [ref["id"]]
                    guard.case, guard.expected_hash = case, ref["sha256"]
                    started = client.post("/api/pilot/runtime/v1/turns", json=payload)
                    if started.status_code != 202:
                        raise AcceptanceError("turn_admission_failed")
                    accepted = started.json()
                    terminal = _poll(client, accepted["turn_id"])
                    if terminal["state"] != "completed" or len(guard.records) != before + 1:
                        raise AcceptanceError("turn_not_completed")
                    answer = terminal.get("terminal", {}).get("response", {}).get("message", "")
                    if not isinstance(answer, str) or key in answer or "data:image/" in answer:
                        raise AcceptanceError("unsafe_answer")
                    saved = client.get(
                        f"/api/chat/conversations/{accepted['conversation_id']}"
                    ).json()
                    if not any(item.get("image_attachments") == [ref] for item in saved):
                        raise AcceptanceError("history_attachment_missing")
                    result = {"case": case, "answer": answer, **grade(case, answer),
                              "state": terminal["state"], "image_sha256": ref["sha256"]}
                    if case == "job":
                        job_conversation, job_ref = accepted["conversation_id"], ref
                        result["withheld_fields_leaked"] = (
                            "18" in answer or "22" in answer or "2026-11-15" in answer
                        )
                    if case == "history":
                        result["original_image_resent"] = True
                        result["text_memory_excluded"] = not report["cases"][-1][
                            "withheld_fields_leaked"
                        ]
                        result["passed"] = result["passed"] and result["text_memory_excluded"]
                    report["cases"].append(result)
            # Assert the API never wrote a credential-bearing config during the run.
            if (Path(temporary) / "config.json").exists():
                raise AcceptanceError("unexpected_config_file")
        report["status"] = "passed" if all(item["passed"] for item in report["cases"]) else "failed"
    except AcceptanceError as exc:
        report.update(status="blocked", error_code=str(exc))
    except Exception:
        report.update(status="blocked", error_code="acceptance_failed")
    finally:
        if guard is not None:
            report["provider_calls"] = guard.records
            known_costs = []
            for record in guard.records:
                usage = record.get("usage") or {}
                if all(type(usage.get(name)) is int
                       for name in ("prompt_tokens", "completion_tokens")):
                    amount = (usage["prompt_tokens"] * 2 + usage["completion_tokens"] * 8) / 1e6
                    record["estimated_cost_cny"] = round(amount, 8)
                    known_costs.append(amount)
                else:
                    record["estimated_cost_cny"] = None
            if guard.records and len(known_costs) == len(guard.records):
                report["cost"]["estimate"] = round(sum(known_costs), 8)
        (report_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Opt in to the selected model cases")
    parser.add_argument("--history-only", action="store_true",
                        help="Run only the job image and historical-image follow-up (two calls)")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--report-dir", type=Path)
    destination.add_argument("--output", type=Path, help="Report JSON path; fixtures go beside it")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    report_dir = args.output.parent if args.output else args.report_dir
    report = run_acceptance(report_dir, execute=args.execute, font_path=args.font,
                            history_only=args.history_only)
    if args.output and args.output.name != "report.json":
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps({"status": report["status"], "calls": len(report["provider_calls"]),
                      "error_code": report.get("error_code")}, ensure_ascii=False))
    return 0 if report["status"] in {"passed", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
