from __future__ import annotations

import asyncio
import math
import os
import re
import threading
from collections import deque
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner
from pydantic import BaseModel, ConfigDict, Field

from .engine import TurnEngine
from .models import LocalModels

# Upstream diagnostics may print datachannel payloads. Never log media or transcripts.
logger.disable("pipecat")


RUNTIME_PROTOCOL = "offerpilot-voice-v2"


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=32, max_length=128, repr=False)
    sdp: str = Field(min_length=1, max_length=100_000, repr=False)
    type: Literal["offer"]
    protocol: Literal["offerpilot-voice-v2"] | None = None


async def inspect(grant: Offer):
    base = os.getenv("OFFERPILOT_API_URL", "http://127.0.0.1:8080").rstrip("/")
    url = urlsplit(base)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("API must be on loopback")
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.post(
            base + "/api/interview-voice/runtime/inspect",
            json={
                "session_id": grant.session_id,
                "token": grant.token,
            },
        )
        response.raise_for_status()
        return response.json()


class SpeechProcessor(FrameProcessor):
    def __init__(self, models, connection, grant):
        super().__init__()
        self.connection, self.grant = connection, grant
        self.tts = models.make_tts()
        self.engine = TurnEngine(models, self.emit, self.interrupt)
        self.vad = SileroVADAnalyzer(
            sample_rate=16000,
            params=VADParams(
                start_secs=0.1,
                stop_secs=0.3,
                min_volume=0.4,
            ),
        )
        self.vad.set_sample_rate(16000)
        self.playback_generation = 0
        self.playback_issue_turn = 0
        self.speaker: asyncio.Task | None = None
        self.command_lock = asyncio.Lock()
        self.cancel_speech = threading.Event()
        self.command_task: asyncio.Task | None = None
        self.question_id = ""
        self.playback_receipts = deque(maxlen=8)  # Approximate browser receipts, memory only.

    async def emit(self, kind, **data):
        self.connection.send_app_message({"type": kind, **data})

    async def interrupt(self):
        self.playback_generation += 1
        generation = self.playback_generation
        self.cancel_speech.set()
        if self.speaker:
            self.speaker.cancel()
        await self.emit("interrupted", playback_id=generation)
        await self.broadcast_interruption()
        return generation

    async def playback_issue(self, turn_no, reason):
        if turn_no != self.playback_issue_turn:
            self.playback_issue_turn = turn_no
            await self.emit("playback_issue", reason=reason, turn_no=turn_no)

    async def speak(self, question):
        generation = await self.interrupt()
        if generation != self.playback_generation:
            return  # Candidate speech can interrupt even during playback setup.
        turn_no = self.engine.turn_no
        question_id = self.question_id
        if not self.tts.available:
            await self.playback_issue(turn_no, "unavailable")
            return
        cancelled = self.cancel_speech = threading.Event()

        async def run():
            try:
                # Sentence chunks bound non-cancellable native inference; stale output is discarded.
                for sentence in re.findall(r"[^。！？!?\n]{1,160}[。！？!?\n]?", question):
                    audio = await asyncio.to_thread(self.tts.synthesize, sentence, cancelled)
                    if generation != self.playback_generation:
                        return
                    if audio is None or len(audio.samples) == 0:
                        raise ValueError("No synthesized audio")
                    await self.emit(
                        "playback",
                        playback_id=generation,
                        question_id=question_id,
                        turn_no=turn_no,
                    )
                    pcm = (np.clip(audio.samples, -1, 1) * 32767).astype(np.int16).tobytes()
                    size = int(audio.sample_rate * 0.02) * 2
                    for offset in range(0, len(pcm), size):
                        if generation != self.playback_generation:
                            return
                        await self.push_frame(
                            OutputAudioRawFrame(
                                audio=pcm[offset : offset + size],
                                sample_rate=audio.sample_rate,
                                num_channels=1,
                            )
                        )
                        await asyncio.sleep(0.02)  # Do not queue seconds of uninterruptible audio.
                await self.emit("playback_done", playback_id=generation)
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 -- isolate native synthesis failure
                if generation == self.playback_generation:
                    await self.playback_issue(turn_no, "failed")

        self.speaker = asyncio.create_task(run())

    async def command(self, message):
        if not isinstance(message, dict):
            return
        async with self.command_lock:
            kind = message.get("type")
            if kind in {"ready", "sync", "repeat"}:
                snapshot = await inspect(self.grant)
                turn = snapshot.get("turn")
                if not turn or turn["turn_status"] != "awaiting_answer":
                    return
                if turn["turn_no"] != self.engine.turn_no:
                    async with self.engine.lock:
                        self.engine.begin(turn["turn_no"])
                if self.engine.phase in {"listening", "waiting"}:
                    self.question_id = str(turn.get("question_idempotency_key") or turn["turn_no"])
                    if message.get("read_questions") is True:
                        await self.speak(turn["question"])
                    elif self.speaker and not self.speaker.done():
                        await self.interrupt()
                    await self.emit("ready", turn_no=turn["turn_no"])
            elif kind == "stop":
                await self.interrupt()
                await self.engine.manual("已停止，请核对文字后手动提交。")
            elif kind == "interrupt":
                await self.interrupt()
            elif kind == "playback_receipt":
                position = message.get("position_seconds")
                playback = message.get("playback_id")
                if (
                    message.get("question_id") == self.question_id
                    and type(playback) is int
                    and 0 <= playback <= self.playback_generation
                    and type(position) in (int, float)
                    and math.isfinite(position)
                    and 0 <= position <= 3600
                ):
                    self.playback_receipts.append(
                        {
                            "question_id": self.question_id,
                            "playback_id": playback,
                            "approximate_position_seconds": position,
                            "interrupted": True,
                        }
                    )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            try:
                if frame.sample_rate != 16000 or frame.num_channels != 1:
                    raise ValueError("unexpected audio format")
                state = await self.vad.analyze_audio(frame.audio)
                await self.engine.feed(frame.audio, state in {VADState.SPEAKING, VADState.STARTING})
            except Exception:  # noqa: BLE001 -- isolate native audio/model failure
                await self.engine.manual("麦克风或识别异常，请切换手动输入。")
        elif isinstance(frame, InputTransportMessageFrame):
            if self.command_task is None or self.command_task.done():

                async def command():
                    try:
                        await self.command(frame.message)
                    except (httpx.HTTPError, ValueError, KeyError):
                        await self.engine.manual("会话已变化，请暂停并恢复当前题目。")

                self.command_task = asyncio.create_task(command())
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self):
        self.cancel_speech.set()
        try:
            if self.command_task:
                self.command_task.cancel()
                await asyncio.gather(self.command_task, return_exceptions=True)
            if self.speaker:
                self.speaker.cancel()
                await asyncio.gather(self.speaker, return_exceptions=True)
            await self.engine.close()
        finally:
            await asyncio.to_thread(self.tts.close)
            await self.vad.cleanup()
            await super().cleanup()


def create_app():
    handler = SmallWebRTCRequestHandler(ice_servers=[])
    active: dict[str, asyncio.Task] = {}
    models = None
    gate = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal models
        try:
            models = await asyncio.to_thread(LocalModels)
        except Exception:  # noqa: BLE001 -- health must remain available without optional models
            # Health reports unready; no model paths, audio or secrets in logs.
            models = None
        yield
        for task in list(active.values()):
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ready": models is not None, "protocol": RUNTIME_PROTOCOL}

    @app.post("/offer")
    async def offer(grant: Offer):
        if grant.protocol != RUNTIME_PROTOCOL:
            raise HTTPException(409, "Incompatible voice protocol")
        async with gate:
            if models is None:
                raise HTTPException(503, "Local models unavailable")
            try:
                await inspect(grant)
            except Exception as exc:
                raise HTTPException(403, "Inactive voice session") from exc
            # One local inference session; prevent shared native-model races and overload.
            if active:
                raise HTTPException(409, "Voice runtime is occupied; retry after disconnect")

            async def connected(connection):
                transport = SmallWebRTCTransport(
                    connection,
                    TransportParams(
                        audio_in_enabled=True,
                        audio_out_enabled=True,
                        audio_in_sample_rate=16000,
                        audio_out_sample_rate=24000,
                    ),
                )
                speech = SpeechProcessor(models, connection, grant)
                worker = PipelineWorker(
                    Pipeline([transport.input(), speech, transport.output()]),
                    params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
                    enable_rtvi=False,
                    idle_timeout_secs=None,
                )

                @transport.event_handler("on_client_disconnected")
                async def disconnected(_transport, _client):
                    await worker.cancel()

                async def watch():
                    while True:
                        await asyncio.sleep(4)
                        try:
                            await inspect(grant)
                        except (httpx.HTTPError, ValueError, KeyError):
                            await speech.emit("expired")
                            await worker.cancel()
                            return

                async def run():
                    watcher = asyncio.create_task(watch())
                    try:
                        runner = WorkerRunner(handle_sigint=False)
                        await runner.add_workers(worker)
                        await runner.run()
                    finally:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
                        await connection.disconnect()
                        active.pop(grant.session_id, None)

                active[grant.session_id] = asyncio.create_task(run())

            return await handler.handle_web_request(
                SmallWebRTCRequest(sdp=grant.sdp, type="offer"),
                connected,
            )

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8766, access_log=False)


if __name__ == "__main__":
    main()
