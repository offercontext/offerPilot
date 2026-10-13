"""Dry-run local speech evaluation. Never creates a grant or writes an interview answer."""

import argparse
import asyncio
import json
import math
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import soxr
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from offerpilot_voice.engine import TurnEngine
from offerpilot_voice.models import LocalModels


async def replay(models, samples, end_ms=None):
    events = []
    origin = time.perf_counter()

    async def emit(kind, **data):
        if kind != "transcript":
            events.append(
                {"type": kind, "elapsed_ms": round((time.perf_counter() - origin) * 1000), **data}
            )

    async def interrupt():
        return None

    engine = TurnEngine(models, emit, interrupt)
    engine.begin(1)
    vad = SileroVADAnalyzer(
        sample_rate=16000, params=VADParams(start_secs=0.1, stop_secs=0.3, min_volume=0.4)
    )
    vad.set_sample_rate(16000)
    # Trailing silence permits comparison with the previous 2.5s + 3s countdown path.
    padded = np.concatenate((samples, np.zeros(7 * 16000, dtype=np.float32)))
    silence_from = None
    baseline_ms = None
    had_speech = False
    next_packet = time.perf_counter()
    try:
        for offset in range(0, len(padded), 320):
            frame = (
                (np.clip(padded[offset : offset + 320], -1, 1) * 32767).astype(np.int16).tobytes()
            )
            state = await vad.analyze_audio(frame)
            speaking = state in {VADState.SPEAKING, VADState.STARTING}
            position_ms = offset / 16
            if speaking:
                had_speech, silence_from = True, None
            elif had_speech:
                if silence_from is None:
                    silence_from = position_ms
                if baseline_ms is None and position_ms - silence_from >= 5500:
                    baseline_ms = position_ms
            await engine.feed(frame, speaking)
            next_packet += 0.02
            await asyncio.sleep(max(0, next_packet - time.perf_counter()))
        candidates = [event for event in events if event["type"] == "candidate"]
        first = candidates[0]["elapsed_ms"] if candidates else None
        return {
            "events": events,
            "candidate_count": len(candidates),
            "baseline_end_ms": baseline_ms,
            "annotated_end_ms": end_ms,
            "early_end": first < end_ms if first is not None and end_ms is not None else None,
            "stable_answer_latency_ms": first - end_ms
            if first is not None and end_ms is not None
            else None,
        }
    finally:
        await engine.close()
        await vad.cleanup()


def read_wave(path):
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise ValueError("Evaluation requires mono PCM16 WAV")
        rate = audio.getframerate()
        samples = (
            np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32)
            / 32768
        )
    return soxr.resample(samples, rate, 16000) if rate != 16000 else samples


async def run(args):
    models = LocalModels()
    cases = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else []
    if args.manifest:
        for case in cases:
            end = case.get("end_ms")
            if type(end) not in (int, float) or not math.isfinite(end) or end < 0:
                raise ValueError("Each corpus case requires a finite non-negative end_ms label")
    if args.smoke:
        for text in (
            "我首先检查日志，定位问题以后完成回滚，最后通过指标验证修复结果。",
            "让我想一下",
            "请重复题目",
        ):
            audio = models.synthesize(text, threading.Event())
            cases.append(
                {"id": text, "samples": soxr.resample(audio.samples, audio.sample_rate, 16000)}
            )
    if not cases or len(cases) < args.min_samples:
        raise ValueError(f"At least {args.min_samples} labeled cases are required")
    results = []
    for case in cases:
        samples = case.get("samples")
        if samples is None:
            samples = read_wave(args.manifest.parent / case["path"])
            if case["end_ms"] > len(samples) / 16:
                raise ValueError("end_ms is outside the supplied audio")
        result = await replay(models, samples, case.get("end_ms"))
        results.append({"id": case["id"], **result})
        print(json.dumps({"id": case["id"], "candidates": result["candidate_count"]}), flush=True)
    labeled = [row for row in results if row["annotated_end_ms"] is not None]
    latency = [
        row["stable_answer_latency_ms"]
        for row in labeled
        if row["stable_answer_latency_ms"] is not None
    ]
    report = {
        "dry_run": True,
        "samples": len(results),
        "labeled_samples": len(labeled),
        "candidate_coverage": sum(row["candidate_count"] > 0 for row in results) / len(results),
        "early_end_rate": sum(row["early_end"] is True for row in labeled) / len(labeled)
        if labeled
        else None,
        "stable_answer_latency_p95_ms": float(np.percentile(latency, 95)) if latency else None,
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {args.output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, help="JSON array with id, path and annotated end_ms"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="Use three synthetic Chinese clips in memory"
    )
    parser.add_argument("--min-samples", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("evaluation-results.json"))
    args = parser.parse_args()
    os.environ["PIPECAT_SMART_TURN_LOG_DATA"] = "false"
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
