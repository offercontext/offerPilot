"""Real WebRTC transport with synthetic audio; never requests a user microphone."""

import asyncio
import json
from fractions import Fraction
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from offerpilot_voice import server


class SilenceTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.pts = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        frame = AudioFrame.from_ndarray(
            np.zeros((1, 960), dtype=np.int16), format="s16", layout="mono"
        )
        frame.sample_rate, frame.pts, frame.time_base = 48000, self.pts, Fraction(1, 48000)
        self.pts += 960
        return frame


class Models:
    available = True
    detector = SimpleNamespace(complete=lambda _audio: True)

    def stream(self):
        return object()

    def transcribe(self, stream, audio, *, final=False):
        return ""

    def make_tts(self):
        return self

    def close(self):
        pass

    def synthesize(self, text, cancelled):
        samples = (np.sin(np.arange(8000) * 0.1) * 0.1).astype(np.float32)
        return SimpleNamespace(samples=samples, sample_rate=8000)


async def test_new_runtime_health_reports_v2_and_legacy_offer_never_inspects(monkeypatch):
    from unittest.mock import AsyncMock

    inspect = AsyncMock()
    monkeypatch.setattr(server, "inspect", inspect)
    monkeypatch.setattr(server, "LocalModels", Models)
    app = server.create_app()
    token = "s" * 64
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://runtime"
        ) as client,
    ):
        assert (await client.get("/health")).json() == {
            "ready": True,
            "protocol": "offerpilot-voice-v2",
        }
        response = await client.post(
            "/offer", json={"type": "offer", "session_id": "legacy", "sdp": "sdp", "token": token}
        )
        assert response.status_code == 409
        assert token not in response.text
        inspect.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_webrtc_ready_audio_stop_and_revocation(monkeypatch):
    monkeypatch.setattr(server, "LocalModels", Models)
    valid = True

    async def inspect(_grant):
        if not valid:
            raise ValueError("revoked")
        return {
            "turn": {"turn_no": 1, "question": "请介绍经历。", "turn_status": "awaiting_answer"}
        }

    monkeypatch.setattr(server, "inspect", inspect)
    app = server.create_app()
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    track = SilenceTrack()
    peer.addTrack(track)
    channel = peer.createDataChannel("pipecat")
    events = []
    got_ready = asyncio.Event()
    got_audio = asyncio.Event()
    consumers = []

    @channel.on("message")
    def message(raw):
        event = json.loads(raw)
        events.append(event)
        if event["type"] == "ready":
            got_ready.set()

    @channel.on("open")
    def opened():
        channel.send(json.dumps({"type": "ready", "read_questions": False}))

    @peer.on("track")
    def on_track(remote):
        async def receive():
            while True:
                frame = await remote.recv()
                if np.any(frame.to_ndarray()):
                    got_audio.set()

        consumers.append(asyncio.create_task(receive()))

    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://runtime",
            ) as client,
        ):
            assert (await client.get("/health")).json()["ready"] is True
            await peer.setLocalDescription(await peer.createOffer())
            response = await client.post(
                "/offer",
                json={
                    "session_id": "session",
                    "token": "x" * 64,
                    "type": "offer",
                    "protocol": "offerpilot-voice-v2",
                    "sdp": peer.localDescription.sdp,
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            await peer.setRemoteDescription(
                RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            )
            await asyncio.wait_for(got_ready.wait(), timeout=15)
            assert not got_audio.is_set()
            assert not any(e["type"] == "playback" for e in events)
            channel.send(json.dumps({"type": "repeat", "read_questions": True}))
            await asyncio.wait_for(got_audio.wait(), timeout=15)
            assert any(e["type"] == "playback" for e in events)
            channel.send(json.dumps({"type": "stop"}))
            for _ in range(30):
                if any(e["type"] == "manual" for e in events):
                    break
                await asyncio.sleep(0.1)
            assert any(e["type"] == "manual" for e in events)
            assert not any(e["type"] == "candidate" for e in events)
            valid = False
            for _ in range(60):
                if any(e["type"] == "expired" for e in events):
                    break
                await asyncio.sleep(0.1)
            assert any(e["type"] == "expired" for e in events)
    finally:
        track.stop()
        await peer.close()
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
