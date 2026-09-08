# Voice Interview Coaching 浏览器验收

日期：2026-08-14
分支：`codex/feat/20260813-voice-interview-coaching`

## 验收范围

- 亮色中文界面，视口 `1455 × 1100`。
- 实际生产构建可加载；语音工作区使用真实产品组件与中文 Mock PCM 演示，避免依赖测试机器麦克风。
- 验证等待开口、正在聆听、长停顿提示、临时字幕、完成录音、最终文字校对、显式确认和表达节奏复盘。
- 确认 Haru 在文字核对阶段使用 `reviewing_voice`，仅在用户显式确认后进入 `success`。
- 确认录音和 PCM 不上传，不新增 API、数据库或跨领域写入。

## 截图

- `D:\Users\yuqi.chen\Desktop\offerpilot-voice-coaching-live-20260813.png`
- `D:\Users\yuqi.chen\Desktop\offerpilot-voice-coaching-review-20260813.png`

## 验收结论

- 实时状态区、波形、临时字幕和长停顿提示在单视口内清晰可见。
- 最终文字必须由用户确认；临时字幕和录音不会直接提交到模拟面试。
- 表达复盘仅展示本次录音的可测量事实：时长、有效发声、停顿、文字节奏和口头语位置，不生成能力评分。
- 取消离线转写后仍可试听并手工整理文字；离线转写重试成功后恢复校对状态。
- 5 分钟安全上限从 MediaRecorder 启动时计时；主动暂停从录音时长与 VAD 时间轴中排除。

## 自动化验证

- 前端十组门禁：`152 files / 1120 tests`，全部通过。
- 语音与 Haru 重点套件：`21 files / 134 tests`，全部通过。
- `VoiceAnswerComposer` 最终边界：`27 tests`，全部通过。
- TypeScript、生产构建、Ruff、Mypy、`oc smoke`、`oc verify --profile local`、`git diff --check`：通过。
- 独立代码复审：无 P0/P1/P2。

## 边界说明

截图使用中文 Mock PCM，仅证明界面、状态机和本地数据边界；不宣称当前测试设备的真实麦克风识别准确率或离线模型推理时延。真实设备表现仍取决于浏览器的 MediaRecorder、AudioWorklet/Web Audio 与 WebGPU/WASM 支持。
