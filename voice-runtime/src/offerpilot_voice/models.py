"""Local-only inference adapters. Model paths are explicit; nothing downloads at startup."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import sherpa_onnx
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3


class TurnDetector(LocalSmartTurnAnalyzerV3):
    def __init__(self):
        super().__init__(cpu_count=1)
        self._log_data = False  # Never permit the upstream debug audio recorder.
        self.lock = threading.Lock()

    def complete(self, audio: np.ndarray) -> bool:
        # Pinned Pipecat adapter: bypass BaseSmartTurn's timeout/silence fail-open path.
        with self.lock:
            result = self._predict_endpoint(audio)
        if result.get("prediction") not in (0, 1):
            raise ValueError("invalid endpoint result")
        return result["prediction"] == 1


class LocalModels:
    def __init__(self):
        self.asr_lock = threading.Lock()
        self.tts_lock = threading.Lock()
        root = Path(os.environ["OFFERPILOT_SPEECH_MODELS"])
        asr = root / "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
        tts = root / "vits-icefall-zh-aishell3"
        required = [
            asr / name
            for name in (
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            )
        ] + [
            tts / name
            for name in (
                "model.onnx",
                "lexicon.txt",
                "tokens.txt",
                "phone.fst",
                "date.fst",
                "number.fst",
            )
        ]
        if any(not path.is_file() or path.stat().st_size == 0 for path in required):
            raise ValueError("Local model installation is incomplete")
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(asr / "tokens.txt"),
            encoder=str(asr / "encoder-epoch-99-avg-1.int8.onnx"),
            decoder=str(asr / "decoder-epoch-99-avg-1.onnx"),
            joiner=str(asr / "joiner-epoch-99-avg-1.int8.onnx"),
            num_threads=2,
            sample_rate=16000,
            enable_endpoint_detection=False,
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(tts / "model.onnx"),
                    lexicon=str(tts / "lexicon.txt"),
                    tokens=str(tts / "tokens.txt"),
                ),
                num_threads=2,
            ),
            rule_fsts=",".join(str(tts / name) for name in ("phone.fst", "date.fst", "number.fst")),
        )
        self.tts = sherpa_onnx.OfflineTts(config)
        self.detector = TurnDetector()

    def stream(self):
        return self.recognizer.create_stream()

    def transcribe(self, stream, audio: np.ndarray, *, final: bool = False) -> str:
        with self.asr_lock:
            stream.accept_waveform(16000, audio)
            if final:
                stream.accept_waveform(16000, np.zeros(8000, dtype=np.float32))
                stream.input_finished()
            while self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)
            return self.recognizer.get_result(stream).strip()

    def synthesize(self, text: str, cancelled: threading.Event):
        with self.tts_lock:
            if cancelled.is_set():
                return None
            return self.tts.generate(
                text,
                sid=0,
                speed=1.0,
                callback=lambda _samples, _progress: int(not cancelled.is_set()),
            )
