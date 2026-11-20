"""Synthetic playback isolation: no speech model files or microphone are used."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from offerpilot_voice import models, server


@pytest.fixture
def setup_speech(monkeypatch):
    turn = {
        "turn_no": 1,
        "question": "请介绍经历。",
        "turn_status": "awaiting_answer",
    }
    events = []
    monkeypatch.setattr(server, "inspect", AsyncMock(return_value={"turn": turn}))
    monkeypatch.setattr(
        server, "SileroVADAnalyzer", lambda **_: SimpleNamespace(set_sample_rate=lambda _: None)
    )
    synth = Mock(return_value=SimpleNamespace(samples=np.ones(160), sample_rate=8000))
    fake_tts = SimpleNamespace(available=True, synthesize=synth, close=Mock())
    fake_models = SimpleNamespace(
        stream=object, make_tts=lambda: fake_tts, transcribe=Mock(return_value="")
    )
    processor = server.SpeechProcessor(
        fake_models, SimpleNamespace(send_app_message=events.append), object()
    )
    processor.broadcast_interruption = AsyncMock()
    processor.push_frame = AsyncMock()
    return processor, turn, events, fake_models


@pytest.mark.parametrize(
    "request_flags", [{}, {"read_questions": False}, {"read_questions": "true"}]
)
async def test_no_synthesis_without_explicit_opt_in(setup_speech, request_flags):
    processor, turn, events, _ = setup_speech
    await processor.command({"type": "ready", **request_flags})
    await processor.command({"type": "repeat", **request_flags})
    turn["turn_no"] = 2
    await processor.command({"type": "sync", **request_flags})
    assert [e["turn_no"] for e in events if e["type"] == "ready"] == [1, 1, 2]
    assert processor.engine.phase == "listening"
    assert processor.speaker is None
    processor.tts.synthesize.assert_not_called()
    processor.push_frame.assert_not_awaited()


@pytest.mark.parametrize("expected_issue", ["unavailable", "failed"])
async def test_playback_issue_does_not_stop_asr_and_next_turn_can_play(
    setup_speech, expected_issue
):
    processor, turn, events, models_adapter = setup_speech
    processor.tts.available = expected_issue != "unavailable"
    if expected_issue == "failed":
        processor.tts.synthesize.side_effect = RuntimeError("synthetic failure")
    for kind in ("ready", "repeat"):
        await processor.command({"type": kind, "read_questions": True})
        if processor.speaker is not None:
            await processor.speaker
    assert [e for e in events if e["type"] == "playback_issue"] == [
        {"type": "playback_issue", "reason": expected_issue, "turn_no": 1}
    ]
    assert [e["turn_no"] for e in events if e["type"] == "ready"] == [1, 1]
    assert not any(e["type"] in {"manual", "candidate", "playback"} for e in events)
    assert processor.engine.phase == "listening"
    await processor.engine.feed(b"\x00\x00" * 320, True)
    models_adapter.transcribe.assert_called_once()
    assert processor.engine.phase == "listening"

    processor.tts.available = True
    processor.tts.synthesize.side_effect = None
    turn["turn_no"] = 2
    await processor.command({"type": "sync", "read_questions": True})
    await processor.speaker
    assert [e["turn_no"] for e in events if e["type"] == "playback"] == [2]
    assert any(e["type"] == "playback_done" for e in events)


async def test_barge_in_cancels_old_speech_even_if_native_work_finishes_late(
    setup_speech, monkeypatch
):
    processor, turn, events, _ = setup_speech
    started = asyncio.Event()
    allow_stale_finish = asyncio.Event()
    cancellation = threading.Event()
    to_thread = asyncio.to_thread

    async def complete_late(function, *args, **kwargs):
        if function is processor.tts.synthesize and not started.is_set():
            cancellation_setter = args[1]
            started.set()
            try:
                await allow_stale_finish.wait()
            except asyncio.CancelledError:
                assert cancellation_setter.is_set()
                cancellation.set()
                await allow_stale_finish.wait()
            raise RuntimeError("stale native playback failure")
        return await to_thread(function, *args, **kwargs)

    monkeypatch.setattr(server.asyncio, "to_thread", complete_late)
    await processor.command({"type": "ready", "read_questions": True})
    old_speaker = processor.speaker
    await started.wait()
    await processor.engine.feed(b"\x00\x00" * 320, True)
    assert cancellation.is_set()
    allow_stale_finish.set()
    await old_speaker
    assert not any(e["type"] in {"manual", "playback", "playback_issue"} for e in events)

    turn["turn_no"] = 2
    await processor.command({"type": "sync", "read_questions": True})
    await processor.speaker
    assert [e["turn_no"] for e in events if e["type"] == "playback"] == [2]


async def test_barge_in_during_playback_setup_prevents_synthesis(setup_speech):
    processor, _, events, _ = setup_speech
    setup_started = asyncio.Event()
    allow_setup = asyncio.Event()

    async def broadcast():
        if not setup_started.is_set():
            setup_started.set()
            await allow_setup.wait()

    processor.broadcast_interruption = broadcast
    request = asyncio.create_task(processor.command({"type": "ready", "read_questions": True}))
    await setup_started.wait()
    await processor.engine.feed(b"\x00\x00" * 320, True)
    allow_setup.set()
    await request
    assert processor.speaker is None
    processor.tts.synthesize.assert_not_called()
    assert any(e["type"] == "ready" for e in events)


def test_no_tts_files_does_not_disable_asr(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_SPEECH_MODELS", str(tmp_path))
    asr = tmp_path / "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
    asr.mkdir()
    required = (
        "tokens.txt",
        "encoder-epoch-99-avg-1.int8.onnx",
        "decoder-epoch-99-avg-1.onnx",
        "joiner-epoch-99-avg-1.int8.onnx",
    )
    for name in required:
        (asr / name).write_text("test sentinel")
    monkeypatch.setattr(
        models.sherpa_onnx,
        "OnlineRecognizer",
        SimpleNamespace(
            from_transducer=lambda **_: SimpleNamespace(create_stream=lambda: "ASR stream")
        ),
    )
    monkeypatch.setattr(models, "TurnDetector", Mock())
    manager = models.LocalModels()
    assert manager.stream() == "ASR stream"
    tts = manager.make_tts()
    assert not tts.available
    assert tts._process is None
    tts.close()
    (asr / required[0]).unlink()
    with pytest.raises(ValueError, match="ASR"):
        models.LocalModels()
