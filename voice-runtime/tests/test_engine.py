import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest

from offerpilot_voice.engine import TurnEngine, control_intent


class Models:
    def __init__(self, text="我先定位日志再完成回滚并验证修复", complete=True):
        self.text = text
        self.detector = SimpleNamespace(complete=lambda _audio: complete)

    def stream(self):
        return SimpleNamespace(text="")

    def transcribe(self, stream, audio, *, final=False):
        if np.any(audio):
            stream.text = self.text
        return stream.text


def setup(models=None):
    events = []

    async def emit(kind, **payload):
        events.append({"type": kind, **payload})

    async def interrupt():
        events.append({"type": "interrupt"})

    engine = TurnEngine(models or Models(), emit, interrupt)
    engine.begin(1)
    return engine, events


async def audio(engine, seconds, *, speaking=False):
    until = time.monotonic() + seconds
    pcm = (np.ones(320, dtype=np.int16) * (1000 if speaking else 0)).tobytes()
    while time.monotonic() < until:
        await engine.feed(pcm, speaking)
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_silence_never_submits_and_complete_audio_only_emits_one_candidate():
    engine, events = setup()
    await audio(engine, 0.1)
    assert not any(e["type"] == "candidate" for e in events)
    await audio(engine, 0.1, speaking=True)
    await audio(engine, 1.1)
    candidates = [e for e in events if e["type"] == "candidate"]
    assert len(candidates) == 1
    assert candidates[0]["text"] == "我先定位日志再完成回滚并验证修复"
    assert candidates[0]["turn_no"] == 1
    await engine.close()


@pytest.mark.asyncio
async def test_resumed_speech_withdraws_pending_candidate_without_losing_text():
    engine, events = setup()
    await audio(engine, 0.1, speaking=True)
    await audio(engine, 0.35)
    await audio(engine, 0.15, speaking=True)
    assert not any(e["type"] == "candidate" for e in events)
    assert engine.text == "我先定位日志再完成回滚并验证修复"
    await engine.close()


@pytest.mark.asyncio
async def test_speech_during_endpoint_inference_invalidates_result():
    models = Models()
    models.detector.complete = lambda _audio: time.sleep(0.4) or True
    engine, events = setup(models)
    await audio(engine, 0.1, speaking=True)
    await audio(engine, 0.65)
    await audio(engine, 0.4, speaking=True)
    assert not any(e["type"] == "candidate" for e in events)
    await engine.close()


@pytest.mark.asyncio
async def test_detector_failure_goes_manual_never_auto_submits():
    models = Models()

    def fail(_audio):
        raise TimeoutError("inference deadline")

    models.detector.complete = fail
    engine, events = setup(models)
    await audio(engine, 0.1, speaking=True)
    await audio(engine, 0.8)
    assert engine.phase == "manual"
    assert not any(e["type"] == "candidate" for e in events)
    assert events[-1]["text"] == "我先定位日志再完成回滚并验证修复"
    await engine.close()


@pytest.mark.asyncio
async def test_dropped_audio_is_not_end_of_answer():
    engine, events = setup()
    await audio(engine, 0.1, speaking=True)
    await audio(engine, 0.05)
    await asyncio.sleep(0.7)
    assert engine.phase == "manual"
    assert not any(e["type"] == "candidate" for e in events)
    await engine.close()


@pytest.mark.asyncio
async def test_wait_and_repeat_never_become_answers():
    for text, intent in (
        ("让我想一下", "wait"),
        ("请重复题目", "repeat"),
        ("这是什么意思", "manual"),
    ):
        engine, events = setup(Models(text))
        await audio(engine, 0.1, speaking=True)
        await audio(engine, 1.1)
        assert not any(e["type"] == "candidate" for e in events)
        assert any(e.get("intent") == intent for e in events)
        await engine.close()


@pytest.mark.asyncio
async def test_five_minute_limit_and_supplement_are_manual_only():
    engine, events = setup()
    engine.started -= 301
    await audio(engine, 0.1, speaking=True)
    assert engine.phase == "manual"
    assert not any(e["type"] == "candidate" for e in events)
    await engine.close()
    engine, events = setup()
    engine.phase = "accepted"
    await audio(engine, 0.1, speaking=True)
    assert engine.phase == "manual"
    with pytest.raises(ValueError, match="unhandled"):
        engine.begin(2)
    await engine.close()


def test_controls_do_not_turn_ordinary_repetition_discussion_into_commands():
    assert control_intent("我会重复一次实验来确认结果") is None


@pytest.mark.asyncio
async def test_short_misrecognized_commands_require_manual_review():
    for text in ("但我想一场", "负责我", "好的"):
        engine, events = setup(Models(text))
        await audio(engine, 0.1, speaking=True)
        await audio(engine, 1.1)
        assert engine.phase == "manual"
        assert not any(e["type"] == "candidate" for e in events)
        await engine.close()
