"""Exercise real worker processes with synthetic native-engine behavior."""

import asyncio
import multiprocessing
import os
import threading
import time
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from offerpilot_voice.tts import LocalTts


class SyntheticEngine:
    def __init__(self, exit_on_generate=False, entered=None):
        self.exit_on_generate = exit_on_generate
        self.entered = entered

    def generate(self, text, **_):
        if self.exit_on_generate:
            self.entered.set()
            os._exit(73)
        if text == "stall":
            if self.entered is not None:
                self.entered.set()
            time.sleep(120)
        return SimpleNamespace(samples=[0.1, 0.2], sample_rate=8000)


def create_engine(_path, *, stage=None, entered=None):
    if stage == "load":
        entered.set()
        os._exit(72)
    if stage == "load_stall":
        entered.set()
        time.sleep(120)
    return SyntheticEngine(exit_on_generate=stage == "generate", entered=entered)


@pytest.fixture
def tts_path(tmp_path):
    for name in ("model.onnx", "lexicon.txt", "tokens.txt", "phone.fst", "date.fst", "number.fst"):
        (tmp_path / name).write_text("test sentinel")
    return tmp_path


@pytest.mark.parametrize("stage", ["load", "generate"])
async def test_native_hard_exit_reaps_child_and_leaves_recognition_active(
    monkeypatch, tts_path, stage
):
    from offerpilot_voice import server

    entered = multiprocessing.get_context("spawn").Event()
    child = LocalTts(tts_path, factory=partial(create_engine, stage=stage, entered=entered))
    events = []
    model = SimpleNamespace(stream=object, transcribe=Mock(return_value=""), make_tts=lambda: child)
    monkeypatch.setattr(
        server,
        "inspect",
        AsyncMock(
            return_value={
                "turn": {"turn_no": 1, "question": "请介绍经历。", "turn_status": "awaiting_answer"}
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "SileroVADAnalyzer",
        lambda **_: SimpleNamespace(set_sample_rate=lambda _: None, cleanup=AsyncMock()),
    )
    processor = server.SpeechProcessor(
        model, SimpleNamespace(send_app_message=events.append), object()
    )
    processor.broadcast_interruption = AsyncMock()
    try:
        await processor.command({"type": "ready", "read_questions": True})
        await asyncio.wait_for(processor.speaker, timeout=45)
        assert [{"type": "playback_issue", "turn_no": 1, "reason": "failed"}] == [
            event for event in events if event["type"] == "playback_issue"
        ]
        assert any(event["type"] == "ready" for event in events)
        assert not any(event["type"] in {"manual", "candidate"} for event in events)
        assert entered.is_set(), "native adapter reached the explicit hard-exit path"
        assert not child.available
        assert child._process is None
        await processor.engine.feed(b"\x00\x00" * 320, True)
        model.transcribe.assert_called_once()
        assert processor.engine.phase == "listening"
    finally:
        await processor.cleanup()


def test_synthesis_wall_deadline_reaps_poisoned_child_without_restarting(tts_path):
    child = LocalTts(tts_path, factory=create_engine, synth_timeout=0.5)
    try:
        first = child.synthesize("warmup", threading.Event())
        assert np.allclose(first.samples, [0.1, 0.2])
        process = child._process
        pid = process.pid
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="worker unavailable"):
            child.synthesize("stall", threading.Event())
        assert time.monotonic() - started < 5
        assert not child.available
        assert child._process is None
        assert all(p.pid != pid for p in multiprocessing.active_children())
        with pytest.raises(RuntimeError, match="worker unavailable"):
            child.synthesize("no retry in poisoned session", threading.Event())
        assert child._process is None
    finally:
        child.close()


async def test_active_cancellation_replaces_child_but_stale_call_keeps_healthy_worker(tts_path):
    entered = multiprocessing.get_context("spawn").Event()
    child = LocalTts(tts_path, factory=partial(create_engine, entered=entered))
    cancelled = threading.Event()
    pending = None
    try:
        await asyncio.to_thread(child.synthesize, "warmup", cancelled)
        original = child._process
        await asyncio.to_thread(child.synthesize, "healthy reuse", cancelled)
        assert child._process is original
        pending = asyncio.create_task(asyncio.to_thread(child.synthesize, "stall", cancelled))
        assert await asyncio.to_thread(entered.wait, 10)
        cancelled.set()
        assert await asyncio.wait_for(pending, timeout=5) is None
        assert child._process is None
        assert child.available

        sound = await asyncio.to_thread(child.synthesize, "next question", threading.Event())
        assert np.allclose(sound.samples, [0.1, 0.2])
        current = child._process
        assert current is not original
        assert child.synthesize("queued stale question", cancelled) is None
        assert child._process is current
    finally:
        await asyncio.to_thread(child.close)
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
    assert child._process is None
    assert not child.available


def test_startup_wall_deadline_stops_worker_without_running_asr_in_child(tts_path):
    entered = multiprocessing.get_context("spawn").Event()
    child = LocalTts(
        tts_path,
        factory=partial(create_engine, stage="load_stall", entered=entered),
        startup_timeout=5,
    )
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="worker unavailable"):
            child.synthesize("cold startup", threading.Event())
        assert entered.is_set(), "child reached the synthetic native load stall"
        assert time.monotonic() - started < 10
        assert child._process is None
        assert not child.available
    finally:
        child.close()


async def test_close_cancels_stalled_synthesis_and_cannot_reopen_worker(tts_path):
    entered = multiprocessing.get_context("spawn").Event()
    child = LocalTts(tts_path, factory=partial(create_engine, entered=entered))
    cancelled = threading.Event()
    pending = asyncio.create_task(asyncio.to_thread(child.synthesize, "stall", cancelled))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await asyncio.wait_for(asyncio.to_thread(child.close), timeout=5)
        assert await asyncio.wait_for(pending, timeout=5) is None
        assert child.synthesize("no reopen", threading.Event()) is None
        assert child._process is None
        assert not child.available
    finally:
        await asyncio.to_thread(child.close)
        await asyncio.gather(pending, return_exceptions=True)
