# 语音模拟面试设计

## 状态

已与用户逐节确认，允许进入实现。

## 背景

OfferPilot 已有事件绑定的文本模拟面试：用户选择明确的 Application、Interview Event、Resume 与当前 JD Version，逐题提交文本回答，最终生成需要人工确认的复盘建议。现有 Attempt、Turn、幂等键、冻结来源、反馈证据和确认边界保持不变。

本切片增加可降级的语音体验，不把模拟面试变成音频资产系统。语音能力拆成三个独立层：题目朗读、回答录音、语音转写。任何一层失败都只能降低体验，不能中断文本回答，也不能放宽现有证据门控。

## 产品目标

1. 用户可以让浏览器朗读当前题目。
2. 用户可以录制、暂停、继续、停止、试听和重录当前回答。
3. 支持明确的浏览器本地识别时，将识别结果放入可编辑确认区。
4. 浏览器本地识别不可用时，仍可试听录音并手工整理文字。
5. 只有用户确认后的文字才能进入现有 Mock Interview Turn。
6. Haru 能以文字和既有动作表达朗读、倾听、转写、成功与失败状态。

## 非目标

- 不保存、上传或建立原始录音数据库。
- 不做声纹、情绪、语速、口音或表达能力评分。
- 不做实时打断、实时追问或嘴型同步。
- 不根据聊天 Provider 猜测其是否支持 STT/TTS。
- 不默认调用远程 STT/TTS。
- 不把未经确认的识别结果写入 Turn、Story、Knowledge 或 Memory。
- 不在首个实现中随 Web 包分发 142 MiB 以上的 Whisper 模型；保留可替换 Transcription Adapter 边界，后续可按需接入 whisper.cpp。本切片优先使用浏览器管理的本地语言包，避免扩大安装体积。

## 分层降级

从高到低依次为：

1. 浏览器 TTS 朗读题目；失败时继续显示文字题目。
2. MediaRecorder 录制回答；权限拒绝或 API 不可用时切回文字输入。
3. `SpeechRecognition.processLocally=true` 且浏览器报告中文语言包可用时，同时生成本地识别文本。
4. 语言包可下载时，由用户主动点击下载；下载失败不删除当前录音。
5. 无本地识别时，用户试听录音并手工输入文字。
6. 文本输入始终可用。

不得静默使用允许浏览器自行选择远端处理的普通 SpeechRecognition。界面只有在 `processLocally`、`available()` 与 `install()` 能力边界满足时，才显示“本机转写”。

## 前端架构

### VoiceInterviewCapability

纯函数与浏览器能力适配层，负责：

- 识别 MediaRecorder、SpeechSynthesis 和本地 SpeechRecognition 支持状态；
- 将浏览器 `available / downloadable / downloading / unavailable` 规范为稳定联合类型；
- 生成本机转写标签，不访问业务 API；
- 不保存录音、不写 localStorage。

### VoiceAnswerComposer

独立组件接收当前问题、禁用状态与 `onConfirmTranscript`：

- 提供文字/语音模式切换；
- 控制题目朗读、麦克风、MediaRecorder、SpeechRecognition 和音频预览；
- 录音 Blob 与 Object URL 只存在当前组件内存；
- 重录、确认成功或组件卸载时停止所有音轨、取消朗读、停止识别并 revoke URL；
- 将本地识别结果或手工内容放入确认区；
- 用户点击“确认使用这段文字”后才调用 `onConfirmTranscript`。

组件不调用 Mock Interview service，不拥有 Attempt、Turn 或幂等键。

### MockInterviewDrawer

现有回答阶段接入 VoiceAnswerComposer：

- 文本模式继续直接编辑现有 `draft.answer`；
- 语音模式的未确认文本不写入 `draft.answer`；
- 确认后才更新 `draft.answer`，随后仍由现有“提交回答”按钮调用原 API；
- pending/working 时冻结语音操作；
- 有未确认录音或文本时关闭 Drawer，需要用户确认丢弃；
- 服务端确认回答成功后通知 VoiceAnswerComposer 释放当前录音。

不修改后端 API、数据库、MockInterviewTurn 或 `answer_text` 契约。

### Haru 活动状态

扩展装饰性活动状态：

- `speaking`：朗读题目；
- `listening`：录音；
- `transcribing`：停止录音后处理本地结果；
- `success`：文字已确认；
- `error`：权限、朗读或转写失败；
- 组件清理时恢复 `idle`。

Haru 状态不是业务事实；`prefers-reduced-motion` 下只更新文字状态，不强制播放 Live2D 动作。

## 数据生命周期

- 原始录音仅存在当前页面内存。
- 当前录音在确认文字提交到服务端成功前保留，以便结果未知时用户继续试听。
- 重新录音会明确删除旧 Blob、旧 Object URL 和旧识别草稿。
- 未确认录音关闭 Drawer 前提示；用户确认丢弃后清理。
- 刷新或关闭浏览器会丢失未确认录音；不做恢复承诺。
- 服务端只看到用户确认后的 `answer_text`，无法区分其来自键盘或语音。

## 无障碍与视觉

- 所有控制具有中文 accessible name、状态文本和不少于 40px 的点击区域。
- 录音计时使用等宽数字；状态不只依赖颜色。
- 波形为装饰，不读取真实音量，不构成能力评分。
- 键盘可完成朗读、模式切换、录音、暂停、停止、试听、重录、编辑与确认。
- 暗色、窄屏与减少动态效果下保持相同语义。
- 视觉方向沿用 OfferPilot 的克制、证据优先界面：一个聚焦的“语音回答台”，不引入另一套页面布局。

## 错误与恢复

- 麦克风权限拒绝：停止请求，显示一次中文说明，保留文字模式。
- MediaRecorder 不可用：禁用录音入口，仍允许 TTS 与文字回答。
- TTS 不可用或播放失败：题目文字仍可读，不阻塞回答。
- 本地识别不可用：显示“本机转写不可用”，录音与手工输入保持可用。
- 语言包下载失败：保留录音，允许重试下载或手工输入。
- Mock 回答提交结果未知：沿用现有原 Turn 幂等键重试；不得重新录音或自动再次转写。

## 测试与验收

自动化覆盖：

- 能力检测与联合类型规范化；
- SpeechRecognition 必须设置 `processLocally=true`；
- 录音、暂停、继续、停止、试听、重录和资源清理；
- 未确认文本不进入 `draft.answer`；
- 确认后才可使用现有提交回答；
- 权限拒绝、TTS 缺失、本地包不可用、下载失败与卸载；
- pending/working 冻结；
- Haru 状态进入与清理；
- 零额外 fetch、零新增业务写入、零音频持久化。

浏览器验收使用亮色中文宽屏，候选人为“筱哲”。如自动麦克风输入无法稳定模拟，允许以测试注入的本地录音与转写结果展示界面，但必须清楚标注为 Mock，不得宣称真实语音识别通过。

## 破坏性变化

无。文本模拟面试、后端 API、数据库、证据契约和历史记录保持兼容。
