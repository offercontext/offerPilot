"""Bounded, session-owned subprocess for optional native TTS, never for microphone/ASR."""

from __future__ import annotations

import multiprocessing
import os
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

_MAX_SAMPLES = 60 * 48000
_MAX_TEXT_BYTES = 2048  # Runtime already chunks speech to <= 161 Unicode characters.
_STATUS = struct.Struct("!BII")
_READY, _AUDIO, _FAILED = 1, 2, 3


class _Cancelled(Exception):
    pass


def _make_vits(path: Path):
    # Keep VITS initialization and any native fatal exit entirely inside the child.
    import sherpa_onnx

    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(path / "model.onnx"),
                lexicon=str(path / "lexicon.txt"),
                tokens=str(path / "tokens.txt"),
            ),
            num_threads=2,
        ),
        rule_fsts=",".join(str(path / name) for name in ("phone.fst", "date.fst", "number.fst")),
    )
    return sherpa_onnx.OfflineTts(config)


def _work(connection, output, path: Path, factory):
    # Native libraries may write stderr directly; isolate output only inside the TTS child.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    with connection:
        try:
            engine = factory(path)
            connection.send_bytes(_STATUS.pack(_READY, 0, 0))
            shared = np.frombuffer(output, dtype=np.float32)
            while True:
                text = connection.recv_bytes(_MAX_TEXT_BYTES).decode("utf-8")
                audio = engine.generate(text, sid=0, speed=1.0)
                rate = int(audio.sample_rate)
                samples = np.asarray(audio.samples, dtype=np.float32)
                size = len(samples)
                if (
                    not 8000 <= rate <= 48000
                    or samples.ndim != 1
                    or not 0 < size <= rate * 60
                    or not np.isfinite(samples).all()
                ):
                    raise ValueError("Invalid native TTS audio")
                shared[:size] = samples
                connection.send_bytes(_STATUS.pack(_AUDIO, size, rate))
        except (EOFError, BrokenPipeError):
            pass
        except Exception:  # noqa: BLE001 -- return a fixed code, not model paths or question text.
            try:
                connection.send_bytes(_STATUS.pack(_FAILED, 0, 0))
            except (OSError, ValueError):
                pass


class LocalTts:
    def __init__(
        self,
        path: Path,
        *,
        factory=_make_vits,
        startup_timeout: float = 25,
        synth_timeout: float = 12,
    ):
        self._path = path
        self._factory = factory
        self._startup_timeout = startup_timeout
        self._synth_timeout = synth_timeout
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._failed = threading.Event()
        self._process = self._connection = self._output = None

    @property
    def available(self) -> bool:
        if self._failed.is_set() or self._closed.is_set():
            return False
        required = (
            "model.onnx",
            "lexicon.txt",
            "tokens.txt",
            "phone.fst",
            "date.fst",
            "number.fst",
        )
        try:
            return all(
                (path := self._path / name).is_file() and path.stat().st_size > 0
                for name in required
            )
        except OSError:
            return False

    def _check_cancelled(self, cancelled: threading.Event):
        if self._closed.is_set() or cancelled.is_set():
            raise _Cancelled

    def _status(self, timeout: float, cancelled: threading.Event):
        deadline = time.monotonic() + timeout
        while True:
            self._check_cancelled(cancelled)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Local TTS deadline exceeded")
            # Status messages are fixed and tiny. Audio never travels over this pipe.
            if self._connection.poll(min(0.025, remaining)):
                response = _STATUS.unpack(self._connection.recv_bytes(_STATUS.size))
                self._check_cancelled(cancelled)
                return response
            if not self._process.is_alive():
                raise RuntimeError("Local TTS worker exited")

    def _start(self, cancelled: threading.Event):
        self._output = self._context.RawArray("f", _MAX_SAMPLES)
        parent, child = self._context.Pipe()
        self._connection = parent
        process = self._context.Process(
            name="offerpilot-local-tts",
            target=_work,
            args=(child, self._output, self._path, self._factory),
            daemon=True,
        )
        try:
            process.start()
            self._process = process
        finally:
            child.close()
        if self._status(self._startup_timeout, cancelled) != (_READY, 0, 0):
            raise RuntimeError("Local TTS worker failed to start")

    def _discard(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._output = None
        process = self._process
        if process is None:
            return
        if process.is_alive():
            process.terminate()
        process.join(0.3)
        if process.is_alive():
            process.kill()
            process.join(0.7)
        if process.is_alive():
            self._failed.set()  # Retain the handle for a final close; do not start another child.
            return
        process.close()
        self._process = None

    def synthesize(self, text: str, cancelled: threading.Event):
        with self._lock:
            # A stale call still queued in the executor must not tear down a newer worker.
            if self._closed.is_set() or cancelled.is_set():
                return None
            try:
                if not self.available:
                    raise RuntimeError("Local TTS unavailable")
                payload = text.encode("utf-8")
                if not 0 < len(payload) <= _MAX_TEXT_BYTES:
                    raise ValueError("Local TTS text outside the request limit")
                if self._process is None:
                    self._start(cancelled)
                self._check_cancelled(cancelled)
                self._connection.send_bytes(payload)
                status, size, rate = self._status(self._synth_timeout, cancelled)
                if status != _AUDIO or not (8000 <= rate <= 48000 and 0 < size <= rate * 60):
                    raise RuntimeError("Local TTS worker returned invalid audio")
                samples = np.frombuffer(self._output, dtype=np.float32, count=size).copy()
                self._check_cancelled(cancelled)
                return SimpleNamespace(samples=samples, sample_rate=rate)
            except _Cancelled:
                self._discard()
                return None
            except Exception:  # noqa: BLE001 -- all IPC or native failure stays in TTS.
                # An unhealthy worker cannot recover in this session. ASR remains active.
                self._failed.set()
                self._discard()
                raise RuntimeError("Local TTS worker unavailable") from None

    def close(self):
        self._closed.set()
        with self._lock:
            self._discard()
