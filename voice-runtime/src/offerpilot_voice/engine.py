"""Bounded turn aggregation, independent of transport and business writes."""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque

import numpy as np


def control_intent(text: str) -> str | None:
    clean = re.sub(r"[\s，。！？,.!?]", "", text).lower()
    if clean in {"请重复题目", "再说一遍", "重复一下", "repeat", "repeatthequestion"}:
        return "repeat"
    if any(value in clean for value in ("让我想一下", "等一下", "等一等", "letmethink")):
        return "wait"
    if any(value in clean for value in ("解释题意", "什么意思", "没听懂", "纠正刚才", "修改刚才")):
        return "manual"
    return None


class TurnEngine:
    def __init__(self, models, emit, interrupt):
        self.models, self.emit, self.interrupt = models, emit, interrupt
        self.stream = models.stream()
        self.audio = deque(maxlen=400)  # 8 seconds at transport's 20 ms frame size
        self.prefix = ""
        self.partial = ""
        self.revision = 0
        self.turn_no = 0
        self.phase = "idle"
        self.started = 0.0
        self.spoke = False
        self.was_speaking = False
        self.lock = asyncio.Lock()
        self.candidate: asyncio.Task | None = None
        self.last_caption = 0.0
        self.last_audio = 0.0

    @property
    def text(self):
        return (self.prefix + self.partial).strip()

    def begin(self, turn_no: int):
        if self.text or (self.phase == "accepted" and self.spoke):
            raise ValueError("unhandled transcript")
        self.revision += 1
        self.turn_no, self.phase = turn_no, "listening"
        self.stream = self.models.stream()
        self.prefix = self.partial = ""
        self.audio.clear()
        self.started = time.monotonic()
        self.last_audio = self.started
        self.spoke = self.was_speaking = False

    async def feed(self, pcm: bytes, speaking: bool):
        if self.phase not in {"listening", "waiting", "accepted"}:
            return
        self.last_audio = time.monotonic()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio.append(samples)
        if speaking:
            self.revision += 1
            if not self.was_speaking:
                await self.interrupt()
            self.spoke = True
            if self.phase == "waiting":
                self.phase = "listening"
        self.was_speaking = speaking
        async with self.lock:
            self.partial = await asyncio.to_thread(self.models.transcribe, self.stream, samples)
        now = time.monotonic()
        if now - self.last_caption > 0.2:
            self.last_caption = now
            await self.emit(
                "transcript",
                text=self.text,
                turn_no=self.turn_no,
                revision=self.revision,
                supplemental=self.phase == "accepted",
            )
        if self.phase == "accepted" and self.partial:
            await self.manual("回答已发出，续说内容需人工处理；不会追加到下一题。")
        elif now - self.started >= 300:
            await self.manual("已达到五分钟，请核对文字后手动提交。")
        elif (
            self.spoke
            and not speaking
            and self.phase == "listening"
            and (self.candidate is None or self.candidate.done())
        ):
            self.candidate = asyncio.create_task(self.evaluate())

    async def evaluate(self):
        revision = self.revision
        try:
            await asyncio.sleep(0.55)
            if revision != self.revision or self.phase != "listening":
                return
            if time.monotonic() - self.last_audio > 0.2:
                await self.manual("媒体数据中断，请核对文字后手动提交。")
                return
            audio = np.concatenate(tuple(self.audio))[-128000:]
            complete = await asyncio.wait_for(
                asyncio.to_thread(self.models.detector.complete, audio),
                timeout=3,
            )
            if revision != self.revision or self.phase != "listening" or not complete:
                return
            async with self.lock:
                final = await asyncio.to_thread(
                    self.models.transcribe,
                    self.stream,
                    np.zeros(0, dtype=np.float32),
                    final=True,
                )
                self.prefix += final
                self.partial = ""
                self.stream = self.models.stream()
            await asyncio.sleep(0.25)
            if revision != self.revision or self.phase != "listening":
                return
            if time.monotonic() - self.last_audio > 0.2:
                await self.manual("媒体数据中断，请核对文字后手动提交。")
                return
            text = self.text
            if not text:
                await self.manual("未取得最终文字，请使用手动输入。")
                return
            intent = control_intent(text)
            if intent:
                self.phase = "waiting" if intent == "wait" else "manual"
                await self.emit("control", intent=intent, text=text, turn_no=self.turn_no)
                if intent in {"repeat", "wait"}:
                    # Preserve a preceding answer if a wait request occurs after real content.
                    self.prefix = text if intent == "wait" and len(text) > 20 else ""
                    self.partial = ""
                    self.spoke = False
                    self.phase = "waiting"
                return
            if len(re.sub(r"[\W_]", "", text)) < 10:
                await self.manual("回答较短，可能是等待或重播指令，请核对文字后手动处理。")
                return
            self.phase = "accepted"
            self.prefix = self.partial = ""  # New speech is a separate supplement.
            self.spoke = False
            await self.emit("candidate", text=text, turn_no=self.turn_no, revision=revision)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- native inference failures must fail closed
            await self.manual("结束检测或识别失败，请核对文字；不会自动提交。")

    async def manual(self, reason: str):
        self.phase = "manual"
        self.revision += 1
        await self.emit("manual", reason=reason, text=self.text, turn_no=self.turn_no)

    async def close(self):
        self.phase = "closed"
        self.revision += 1
        if self.candidate:
            self.candidate.cancel()
            await asyncio.gather(self.candidate, return_exceptions=True)
