# 第五期“可确认的连续语音面试模式”发布报告

## 交付范围

- 新增纯前端 `ContinuousVoiceSessionController`，以 generation fencing 管理朗读、等待开口、VAD 候选结束、倒计时、停止、转写、人工确认、回答提交和下一题。
- `VoiceAnswerComposer` 增加受控语音意图，继续复用现有 TTS、MediaRecorder、VAD、浏览器识别和离线 Whisper；媒体阶段不发业务请求，只有确认文字后才进入原有回答 API。
- Interview Studio 接入连续模式，保留标准文字/语音模式、证据栏、追问引用、幂等 key、结果未知重试和人工确认语义。
- 补强 pending `getUserMedia`、TTS、MediaRecorder `onstop`、页面隐藏、组件卸载、重录、转写及 StrictMode 的资源清理和代际隔离。
- 结果未知时把 Attempt、时间线、原始 operation key 和状态写入 `sessionStorage`，重新打开 Studio 可使用原 key 恢复；连续语音的表达复盘快照沿用原保存链路。
- Studio 回答区在宽窄屏使用紧凑 Surface；Haru 的普通页尺寸、状态提示和 Studio 位置继续分离，默认位置受安全区约束。

## 破坏性变化

无。没有数据库迁移、公开 API 或后端领域模型变化；没有修改现有 Mock Interview、Voice Coaching、HITL、lease/CAS/fencing、202、结果未知和历史只读语义。

## 浏览器验收

使用中文候选人“筱哲”、亮色主题完成真实投递 Studio 的准备、首题、证据化追问、完成复盘、Haru 拖动及 1440×900 / 390×844 无溢出验收。已有截图位于 [artifacts/continuous-voice-interview](../../artifacts/continuous-voice-interview/)。

本轮要求的“外部 Chrome + 可授权麦克风”闭环尚未完成：Chrome 浏览器绑定当前不可用，无法在不伪造结果的前提下执行真实 MediaRecorder → VAD → Whisper → 人工确认 → 自动追问。此前 in-app browser 仅验证了麦克风不可用时的安全降级，不能替代本项验收。

## 验证结果

- 后端分组门禁全部通过：2103 tests；agent 454、domain 73、knowledge 659（4 个既定允许跳过）、proposals 431、misc 486。
- 前端分组门禁全部通过：164 个文件、1193 个测试；本轮新增的连续语音恢复与 StrictMode 边界测试包含在 features 组的 303 个测试中。
- `uv run ruff check .`、`uv run mypy src`（73 个源文件）和 `npm.cmd run build` 通过。
- `uv run oc verify --profile local --static-dir web/dist` 通过，包含真实 HTTP、HITL 确认、CRUD、proposal terminal matrix、outcome feedback 和清理。
- `uv run oc verify --profile real-ai --static-dir web/dist` 通过，完成 interview preparation、material、opportunity fit、interview review、knowledge capture、mock interview 及 HITL 写确认。
- Windows PowerShell 会在子进程错误输出中插入换行和空格；对既有 Story Harness 的错误码断言做了仅测试侧的空白归一化，未改变 Harness 或产品语义。

## 独立代码复审

复审范围覆盖连续语音状态机、Composer 受控意图、Studio 提交/恢复、资源 fencing、Haru 位置和测试门禁；在修复首次结果未知 key 持久化、复盘保存不阻塞追问、确定性错误分类、报告迁移、计划截图路径和 StrictMode 启动竞态后，最终复审结论为：无剩余 P0/P1/P2。

## 2026-08-16 复盘生成可见性修复

- 根因确认：既有 Attempt 11 的反馈 Provider 返回了 `blank_value`，API 在约 39.7 秒后按严格契约返回 `502 mock_interview_unverifiable`；按钮此前没有生成中反馈，终态提示又位于已滚到底部的对话滚动区顶部，因此用户感知为“点击无反应”。
- `blank_value` 现在仅沿用既有结构修复机制重试一次，继续使用同一冻结输入和严格证据验证；第二次仍为空时保持终态 `mock_interview_unverifiable`，不放宽证据、语义或 Provider 重试边界。
- Studio 顶部增加不会随对话滚动消失的状态区；生成期间显示“正在生成复盘，通常需要几十秒”，按钮进入 loading/`aria-busy` 并防止重复提交。终态失败与结果未知会聚焦可见恢复区，分别提供“重新开始练习”或“使用原 key 重试”。
- 既有 Attempt 11 与全部本地数据保持不变；修复通过后应使用新 Attempt 验证，不重写历史终态。
- 定向后端验证：180 passed；定向前端验证：2 files / 23 passed；生产构建、静态 Smoke、Ruff、Mypy、`git diff --check` 均通过。
- 有界真实 AI 验收仅执行一次并通过：`attempt_1:success`，未发生盲目重试。
- 用户随后完成的新 Attempt 12 已由 API 返回 `201`，仓储状态为 `feedback_ready`，并持久化 `proposal_status=normal` 的非空复盘；这次“仍没有结果”不是 Provider 或后端失败，而是长对话滚动位置停留在上方，新增的底部复盘卡没有进入当前视野。
- 复盘成功后，顶部固定状态区现在明确显示“复盘已生成”并提供“查看复盘”；页面会自动滚动到复盘卡并把键盘焦点移入结果。五轮长对话挂载回归证明成功结果位于滚动区外仍有可见通知，自动定位和手动重新定位均可用；焦点具有可见轮廓。`safe_empty` 会明确说明“暂无可验证建议”，不再伪装成非空建议。

## 剩余事项

- 必须在已安装并连接 ChatGPT 浏览器扩展的外部 Chrome 中完成一次真实麦克风闭环后，才具备最终发布验收证据；这是当前唯一未完成的外部验收前置条件，不是产品代码剩余风险。
- 本分支不推送、不合并，等待用户审核。

## 截图索引

- [准备中心](../../artifacts/continuous-voice-interview/01-preparation-1440x900-light-xizhe.png)
- [首题与证据](../../artifacts/continuous-voice-interview/02-first-question-1440x900-light-xizhe.png)
- [连续语音预检](../../artifacts/continuous-voice-interview/03-continuous-voice-preflight-1440x900-light-xizhe.png)
- [追问证据](../../artifacts/continuous-voice-interview/04-evidence-followup-1440x900-light-xizhe.png)
- [完成复盘](../../artifacts/continuous-voice-interview/05-completed-review-1440x900-light-xizhe.png)
- [Haru 拖动](../../artifacts/continuous-voice-interview/06-haru-drag-1440x900-light-xizhe.png)
- [窄屏准备中心](../../artifacts/continuous-voice-interview/07-preparation-390x844-light-xizhe.png)
- [窄屏 Studio](../../artifacts/continuous-voice-interview/08-studio-390x844-light-xizhe.png)
