# Voice Interview Coaching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Mock Interview 语音回答上增加本地 VAD、顺序临时转写、确定性降级和只读表达节奏复盘，同时保持显式确认与零音频上传边界。

**Architecture:** 纯函数模块负责 VAD 状态、片段合并和表达节奏统计；`VoiceSessionController` 只协调本地音频帧、单并发离线转写与 generation fencing；浏览器适配器负责 AudioWorklet/AnalyserNode 和资源清理；`VoiceAnswerComposer` 继续持有现有录音、最终转写与确认入口，并组合新的状态和复盘组件。后端、API、数据库及共享后端类型不变。

**Tech Stack:** React 18、TypeScript、Web Audio API、AudioWorklet、MediaRecorder、现有离线 Whisper Worker、Vitest/JSDOM、CSS Modules。

---

## 0. 固定基线与文件边界

实施基线固定为最后一次修改本计划的提交。开始实施时写入系统临时文件 `offerpilot-voice-interview-coaching-baseline.txt`；后续独立 PowerShell 进程只读取该文件，不重新计算。

允许新增或修改：

```text
web/src/features/mockInterviewVoice/voiceActivityDetector.ts
web/src/features/mockInterviewVoice/voiceActivityDetector.test.ts
web/src/features/mockInterviewVoice/voiceTranscriptSegments.ts
web/src/features/mockInterviewVoice/voiceTranscriptSegments.test.ts
web/src/features/mockInterviewVoice/voiceDeliverySummary.ts
web/src/features/mockInterviewVoice/voiceDeliverySummary.test.ts
web/src/features/mockInterviewVoice/voiceSessionController.ts
web/src/features/mockInterviewVoice/voiceSessionController.test.ts
web/src/features/mockInterviewVoice/voiceCaptureRuntime.ts
web/src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts
web/src/features/mockInterviewVoice/voiceActivity.worklet.ts
web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.tsx
web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.test.tsx
web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.module.css
web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx
web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx
web/src/features/mockInterviewVoice/VoiceAnswerComposer.module.css
web/src/components/MockInterviewDrawer.tsx
web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
web/src/features/pilotMascot/live2dRuntime.ts
web/src/features/pilotMascot/live2dRuntime.test.ts
web/src/features/pilotMascot/PilotMascot.tsx
web/src/features/pilotMascot/PilotMascot.test.tsx
web/src/features/pilotMascot/PilotMascot.module.css
docs/superpowers/specs/2026-08-13-voice-interview-coaching-design.md
docs/superpowers/plans/2026-08-13-voice-interview-coaching.md
docs/reports/2026-08-13-voice-interview-coaching-browser-acceptance.md
```

禁止修改：

```text
src/offerpilot/**
tests/**
web/src/services/**
web/src/types/**
数据库迁移
README.md
```

- [ ] **Step 1: 持久化基线**

```powershell
$plan = 'docs/superpowers/plans/2026-08-13-voice-interview-coaching.md'
$baseline = (git log -1 --format=%H -- $plan).Trim()
$locator = Join-Path $env:TEMP 'offerpilot-voice-interview-coaching-baseline.txt'
Set-Content -LiteralPath $locator -Value $baseline -Encoding ascii
git cat-file -e "$baseline^{commit}"
```

预期：退出码 0，文件保存唯一 baseline SHA。

- [ ] **Step 2: 每次提交前执行范围检查**

```powershell
$baseline = (Get-Content (Join-Path $env:TEMP 'offerpilot-voice-interview-coaching-baseline.txt') -Raw).Trim()
$changed = @(
  git diff --name-only "$baseline..HEAD"
  git diff --name-only --cached
  git diff --name-only
  git ls-files --others --exclude-standard
) | Where-Object { $_ } | Sort-Object -Unique
```

将 `$changed` 与上面的精确 allowlist 比较；任何越界文件都停止实施并先修订计划。

## 1. 表达节奏摘要纯函数

**Files:**
- Create: `web/src/features/mockInterviewVoice/voiceDeliverySummary.ts`
- Create: `web/src/features/mockInterviewVoice/voiceDeliverySummary.test.ts`

- [ ] **Step 1: 先写失败测试**

测试公开契约：

```ts
export type VoiceDeliverySummary = {
  totalDurationMs: number;
  voicedDurationMs: number;
  pauseCount: number;
  longestPauseMs: number;
  speechRateCpm?: number;
  fillerOccurrences: Array<{ text: string; count: number; transcriptOffsets: number[] }>;
  source: 'local_audio_and_confirmed_transcript';
};

buildVoiceDeliverySummary({
  startedAtMs: 0,
  endedAtMs: 60_000,
  voicedRanges: [[1_000, 9_000], [10_500, 20_000]],
  transcript: '嗯我先定位日志，然后完成回滚。',
})
```

断言：总时长、有效发声、仅统计首末发声之间不少于 800ms 的停顿、最长停顿、按 Unicode code point 计算 CPM、口头语最长匹配与 code point offset；覆盖 5 秒边界、空文本、emoji、组合字符、重叠词、异常区间、输入不变性及不出现评分字段。

- [ ] **Step 2: 运行并确认 RED**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/voiceDeliverySummary.test.ts
```

预期：因模块不存在失败。

- [ ] **Step 3: 实现最小纯函数**

实现 `buildVoiceDeliverySummary(input, fillerLexicon?)`、稳定区间规范化、Unicode code point 扫描；默认词表固定为 `['嗯', '呃', '然后', '就是说', '那个']`，不做情绪、能力或综合评分。

- [ ] **Step 4: 运行 GREEN 并提交**

```powershell
npm.cmd test -- --run src/features/mockInterviewVoice/voiceDeliverySummary.test.ts
git add web/src/features/mockInterviewVoice/voiceDeliverySummary.ts web/src/features/mockInterviewVoice/voiceDeliverySummary.test.ts
git commit -m "feat: AI add voice delivery summary"
```

## 2. VAD 状态与确定性片段合并

**Files:**
- Create: `web/src/features/mockInterviewVoice/voiceActivityDetector.ts`
- Create: `web/src/features/mockInterviewVoice/voiceActivityDetector.test.ts`
- Create: `web/src/features/mockInterviewVoice/voiceTranscriptSegments.ts`
- Create: `web/src/features/mockInterviewVoice/voiceTranscriptSegments.test.ts`

- [ ] **Step 1: 写 VAD 失败测试**

定义并测试：

```ts
type VoiceFrame = { atMs: number; durationMs: number; rms: number; peak: number };
type VoiceActivityEvent =
  | { type: 'calibrating'; untilMs: number }
  | { type: 'speech_started'; atMs: number }
  | { type: 'speech_continued'; fromMs: number; toMs: number }
  | { type: 'short_pause'; fromMs: number; toMs: number }
  | { type: 'long_pause'; fromMs: number; toMs: number };
```

覆盖 800ms 校准、`max(0.015, noiseRms * 3)`、160ms 连续发声、1.2s 短停顿、2.5s 长停顿、噪声高/NaN/乱序帧、可注入阈值和时钟。

- [ ] **Step 2: 运行 VAD RED，再实现并验证 GREEN**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/voiceActivityDetector.test.ts
```

实现 `VoiceActivityDetector`，所有状态由显式帧时间推进，不调用 `Date.now()` 或真实 timer；再次运行预期通过。

- [ ] **Step 3: 写片段合并失败测试**

定义：

```ts
type TranscriptSegment = {
  sequence: number;
  generation: number;
  startMs: number;
  endMs: number;
  text: string;
};

mergeTranscriptSegments(segments, generation): string
```

覆盖稳定 sequence、迟到旧 generation 丢弃、2 秒重叠文本的最大公共 code point 前后缀去重、中文/emoji、空结果、重复句和乱序输入。

- [ ] **Step 4: 运行 RED，最小实现，再提交**

```powershell
npm.cmd test -- --run src/features/mockInterviewVoice/voiceTranscriptSegments.test.ts
git add web/src/features/mockInterviewVoice/voiceActivityDetector* web/src/features/mockInterviewVoice/voiceTranscriptSegments*
git commit -m "feat: AI add deterministic voice activity analysis"
```

## 3. 会话控制器与单并发离线转写

**Files:**
- Create: `web/src/features/mockInterviewVoice/voiceSessionController.ts`
- Create: `web/src/features/mockInterviewVoice/voiceSessionController.test.ts`

- [ ] **Step 1: 写控制器失败测试**

控制器公开接口：

```ts
type VoiceSessionControllerDependencies = {
  now: () => number;
  transcribe: (pcm: Float32Array) => Promise<string>;
  onState: (state: VoiceSessionState) => void;
  onInterimTranscript: (text: string) => void;
};

interface VoiceSessionController {
  start(generation: number, sampleRate: number): void;
  acceptFrame(frame: Float32Array, atMs: number): void;
  finish(): Promise<void>;
  pause(): void;
  resume(): void;
  cancel(): void;
  dispose(): void;
  getVoicedRanges(): ReadonlyArray<readonly [number, number]>;
}
```

测试 20 秒片段、2 秒 PCM 重叠、任一时刻仅一次 `transcribe`、队列超过 1 或首片耗时超过片长时切换批量模式、generation fencing、pause/resume、5 分钟强制进入 reviewing 而不确认、cancel/dispose 丢弃迟到结果。

- [ ] **Step 2: 运行 RED**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/voiceSessionController.test.ts
```

- [ ] **Step 3: 最小实现并验证 GREEN**

控制器只保存当前会话 PCM、队列、临时字幕和 VAD 区间；`finish()` 只完成本地队列与状态，不调用业务 API。降级时停止新增片段任务，但保留完整 PCM 供最终批量转写。

- [ ] **Step 4: 提交**

```powershell
git add web/src/features/mockInterviewVoice/voiceSessionController.ts web/src/features/mockInterviewVoice/voiceSessionController.test.ts
git commit -m "feat: AI coordinate local voice sessions"
```

## 4. AudioWorklet 与 AnalyserNode 浏览器适配器

**Files:**
- Create: `web/src/features/mockInterviewVoice/voiceActivity.worklet.ts`
- Create: `web/src/features/mockInterviewVoice/voiceCaptureRuntime.ts`
- Create: `web/src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts`

- [ ] **Step 1: 写资源与降级失败测试**

定义可注入浏览器依赖并测试：AudioWorklet 可用时优先加载本地模块并输出 PCM/RMS/peak；不支持或加载失败时使用 AnalyserNode 仅提供 VAD 帧并标记 `batchOnly=true`；AudioContext、source、worklet/analyser、timer 在 stop/dispose/初始化中途失败时恰好清理一次；不得 fetch 远端 URL。

- [ ] **Step 2: 运行 RED**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts
```

- [ ] **Step 3: 实现 worklet 与 runtime**

`voiceActivity.worklet.ts` 每累计约 20ms 单声道帧后发送可转移 `Float32Array`、RMS、peak；runtime 通过 `new URL('./voiceActivity.worklet.ts', import.meta.url)` 加载。Analyser fallback 只抽样 RMS/peak，不伪造可用于 Whisper 的完整 PCM。

- [ ] **Step 4: GREEN 与提交**

```powershell
npm.cmd test -- --run src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts
git add web/src/features/mockInterviewVoice/voiceActivity.worklet.ts web/src/features/mockInterviewVoice/voiceCaptureRuntime.ts web/src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts
git commit -m "feat: AI capture local voice activity"
```

## 5. 复盘卡片与键盘交互

**Files:**
- Create: `web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.tsx`
- Create: `web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.test.tsx`
- Create: `web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard.module.css`

- [ ] **Step 1: 写组件失败测试**

覆盖中文标签、计算口径说明、无综合分/排名/情绪词、口头语按钮定位文本、暂停按钮调用 `audio.currentTime`、音频释放后禁用暂停定位、40px 命中区域、窄屏单列、深色和 reduced-motion。

- [ ] **Step 2: RED、最小实现、GREEN**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/VoiceDeliverySummaryCard.test.tsx
```

组件只接收 `summary`、`transcriptRef`、可选 `audioRef`，无 service、fetch、AI 或持久化入口。

- [ ] **Step 3: 提交**

```powershell
git add web/src/features/mockInterviewVoice/VoiceDeliverySummaryCard*
git commit -m "feat: AI show voice delivery review"
```

## 6. 集成 VoiceAnswerComposer 与 Haru 状态

**Files:**
- Modify: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx`
- Modify: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx`
- Modify: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.module.css`
- Modify: `web/src/components/MockInterviewDrawer.tsx`
- Modify: `web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx`
- Modify: `web/src/features/pilotMascot/live2dRuntime.ts`
- Modify: `web/src/features/pilotMascot/live2dRuntime.test.ts`
- Modify: `web/src/features/pilotMascot/PilotMascot.tsx`
- Modify: `web/src/features/pilotMascot/PilotMascot.test.tsx`
- Modify: `web/src/features/pilotMascot/PilotMascot.module.css`

- [ ] **Step 1: 先扩展真实挂载测试**

测试用户流程：朗读题目 → 开始录音 → VAD 等待/发声/长停顿 → 临时字幕 → 完成回答 → 最终离线转写 → 编辑 → 显式确认 → 显示复盘；断言长停顿和 300 秒都不调用 `onConfirmTranscript`，确认仅一次。

测试降级：AudioWorklet→Analyser batch-only→现有 Whisper batch→本机识别→播放/手工→文字；首次片段积压后不再发片段转写；没有模型时不访问 Hugging Face。

测试清理：重录、切题、成功提交、关闭 Drawer、卸载都会停止 stream/runtime/worker、撤销 Blob URL，旧 generation 晚到结果不更新 DOM；页面隐藏只暂停状态推进，不推断遗漏音频。

- [ ] **Step 2: 运行 RED**

```powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
```

- [ ] **Step 3: 最小集成**

保持现有 `onConfirmTranscript` 唯一业务出口。新增状态文案、波形、临时字幕、批量降级提示和摘要卡；最终转写继续使用完整 MediaRecorder Blob 解码结果，临时字幕绝不直接进入正式回答。扩展 `VoiceAnswerActivity`/`PilotMascotActivity` 表达 `waiting_for_speech`、`speech_paused` 与仅供校对的 `reviewing_voice`，Live2D 只改变本地动画与文字，只有用户显式确认后才进入 `success`。

- [ ] **Step 4: 运行 GREEN 与相关回归**

```powershell
npm.cmd test -- --run src/features/mockInterviewVoice src/components/MockInterviewDrawer.cleanup.interaction.test.tsx src/features/pilotMascot
```

- [ ] **Step 5: 提交**

```powershell
git add web/src/features/mockInterviewVoice web/src/components/MockInterviewDrawer.tsx web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx web/src/features/pilotMascot/live2dRuntime.ts web/src/features/pilotMascot/live2dRuntime.test.ts
git commit -m "feat: AI add voice interview coaching flow"
```

## 7. 验证、独立复审与浏览器证据

**Files:**
- Create: `docs/reports/2026-08-13-voice-interview-coaching-browser-acceptance.md`

- [ ] **Step 1: 前端完整门禁**

```powershell
cd web
npm.cmd test -- --run
npm.cmd run build
```

预期：所有测试与构建退出码 0；不接受仅因超时而推断通过。

- [ ] **Step 2: 仓库静态与本地门禁**

```powershell
uv run ruff check .
uv run mypy src
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
```

预期：全部退出码 0。因为本期不修改后端，完整 Python 分组门禁只在发布合并前执行，不用真实 Provider 证明本地语音行为。

- [ ] **Step 3: 独立代码复审**

复审重点：音频零上传、临时字幕不提交、显式确认、generation fencing、单 Worker、资源清理、5 分钟边界、VAD 不被表述为能力评价、无新增后端/API/数据库。所有 P0/P1/P2 修复或如实记录后才能继续。

- [ ] **Step 4: 内置浏览器亮色中文验收**

使用候选人“筱哲”、中文题目和宽屏亮色模式。真实麦克风可用时验证录音；不可用时用明确标注的 Mock PCM 展示界面，不把 Mock 当准确率或延迟证据。至少保存以下截图：等待开口、正在回答、长停顿、临时字幕、最终校对、确认后复盘、批量降级。

网络审计要求：除用户主动模型下载外，语音阶段无 Hugging Face、AI Provider 或其他外部请求；无音频请求体；无 Knowledge/Memory/Story/Application 写入。

- [ ] **Step 5: 写报告并提交**

报告记录真实/Mock 边界、截图路径、视口尺寸、资源清理、网络审计、测试命令和剩余风险。随后：

```powershell
git add -f docs/reports/2026-08-13-voice-interview-coaching-browser-acceptance.md
git commit -m "docs: AI record voice coaching acceptance"
```

- [ ] **Step 6: 最终范围与工作区检查**

```powershell
$baselineFile = Join-Path $env:TEMP 'offerpilot-voice-interview-coaching-baseline.txt'
$baseline = (Get-Content $baselineFile -Raw).Trim()
git cat-file -e "$baseline^{commit}"
git diff --check "$baseline..HEAD"
git status --short
```

仅在所有门禁通过且工作区干净后删除 baseline 文件；失败时保留以便同一基线继续收口。不得自动推送或合并。
