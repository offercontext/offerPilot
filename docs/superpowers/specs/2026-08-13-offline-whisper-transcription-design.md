# 离线 Whisper 模拟面试转写设计

## 状态

已与用户逐节确认，允许进入测试先行实施。

## 背景

一期语音模拟面试已经提供浏览器题目朗读、内存录音、本地语音识别、手工校对和显式确认。浏览器原生本地识别的覆盖范围仍受浏览器版本、语言包与设备能力影响。二期在不上传录音、不修改业务 API 的前提下，引入按需下载的离线 Whisper，形成稳定的三层降级：

1. 浏览器明确证明为本地处理的原生识别；
2. 用户主动下载的离线 Whisper；
3. 录音回放后手工整理文字。

## 产品目标

- 用户可在 OfferPilot 内主动下载一个固定的多语言均衡模型，不必手工访问链接、解压或选择文件。
- 录音结束后一次性离线转写，不做实时字幕。
- WebGPU 可用时优先使用 WebGPU；初始化或执行失败时自动降级一次到 WASM。
- 下载、载入、转写、取消、失败、删除和重装都有稳定中文状态。
- 转写结果仍须用户校对并明确确认，才进入现有 Mock Interview Turn。
- 转写期间不发出任何网络请求，音频不上传、不持久化。

## 非目标

- 不新增云端 STT、音频上传 API、录音表或转写历史表。
- 不保存原始录音、PCM、未经确认的转写草稿或转写引擎信息。
- 不做实时字幕、实时打断、说话人分离、声纹、情绪、口音、语速或表达能力评分。
- 不提供多个模型让普通用户自行比较。
- 不在本切片提供手工导入模型文件、本地伴随服务或桌面原生推理进程。
- 不把模型权重提交到普通 Git 仓库，也不随 Web 构建产物分发。

## 方案选择

采用 `Transformers.js + Web Worker`：

- 模型候选为 Transformers.js 兼容的多语言 Whisper Small 量化版本；首个候选仓库为 `onnx-community/whisper-small`。
- Worker 内动态加载 Transformers.js 和 ONNX Runtime Web，主线程不执行模型推理。
- WebGPU 优先；失败时销毁失败管线并以 WASM 重建一次。
- 模型由 Hugging Face Hub 固定 revision 提供，GitHub Release 仅作为未来可选镜像，不进入首期运行逻辑。

不采用 `whisper.cpp WASM`，因为当前切片需要额外维护 WASM 构建、音频转换和线程差异；不采用本地伴随服务，因为其引入安装包、进程管理和 localhost API 安全边界。

## 模块边界

### OfflineWhisperManifest

只读清单固定：

```ts
type OfflineWhisperManifest = {
  schemaVersion: 1;
  modelId: 'onnx-community/whisper-small';
  revision: string;
  displayName: 'Whisper 多语言均衡模型';
  approximateBytes: number;
  maxAudioSeconds: 300;
  license: 'apache-2.0';
  sourceUrl: string;
};
```

`revision` 必须是固定提交哈希，不允许 `main`、`latest` 或其他浮动引用。实施前通过基准选择最终量化文件组合，随后把实际下载文件、总大小和完整性信息固定到 manifest。模型升级必须修改 manifest 版本并走独立验收。

### OfflineModelStore

负责浏览器模型生命周期：

- Cache Storage 保存 Transformers.js 下载的模型与运行时文件；
- IndexedDB 只保存 manifest 版本、ready 标记、实际字节数和最后验证时间；
- 只有 Worker 完成全部模型文件加载后才能写入 ready；
- ready 元数据与缓存不一致时回到 `not_downloaded`，不得尝试使用半成品；
- 删除时清除当前固定模型的缓存、元数据和 Worker 内存，不清理其他应用缓存；
- `navigator.storage.estimate()` 显示容量不足时禁止开始下载；API 不可用时允许用户继续，但明确显示空间无法预检。

模型状态：

```ts
type OfflineModelState =
  | { status: 'not_downloaded' }
  | { status: 'checking' }
  | { status: 'downloading'; receivedBytes: number; totalBytes?: number }
  | { status: 'ready'; modelVersion: string; cachedBytes: number }
  | { status: 'loading'; backend: 'webgpu' | 'wasm' }
  | { status: 'transcribing'; backend: 'webgpu' | 'wasm'; progress?: number }
  | { status: 'incompatible'; reason: string }
  | { status: 'error'; recoverable: boolean; message: string };
```

### OfflineWhisperWorker

Worker 协议使用带 generation 的判别联合类型：

```ts
type WorkerRequest =
  | { type: 'prepare'; generation: number; preferredBackend: 'webgpu' | 'wasm' }
  | { type: 'transcribe'; generation: number; audio: Float32Array; sampleRate: 16000; language: 'zh' }
  | { type: 'cancel'; generation: number }
  | { type: 'dispose'; generation: number };

type WorkerResponse =
  | { type: 'download_progress'; generation: number; loaded: number; total?: number }
  | { type: 'ready'; generation: number; backend: 'webgpu' | 'wasm' }
  | { type: 'transcription_progress'; generation: number; progress?: number }
  | { type: 'completed'; generation: number; text: string; backend: 'webgpu' | 'wasm' }
  | { type: 'failed'; generation: number; category: WorkerFailureCategory; recoverable: boolean };
```

- late response 的 generation 不匹配时丢弃；
- WebGPU 初始化或推理失败时只允许一次 WASM 重建；
- 用户取消后终止当前 generation，保留录音 Blob，不写转写文本；
- dispose 释放 pipeline、PCM、临时数组和 Worker；
- 超过五分钟的音频不进入 Worker；
- 长音频使用模型支持的分段参数处理，只输出连续可编辑文本，不承诺逐字时间轴。

### AudioDecoder

录音 Blob 在主线程通过 Web Audio API 解码，再转换为 `16kHz` 单声道 `Float32Array`，使用 transferable 发送给 Worker。解码失败不调用 Worker，录音仍可回放并允许手工填写。转换缓冲只存在于当前操作内存中。

### TranscriptionCoordinator

协调三层降级：

1. 原生本地识别已有非空结果时直接进入校对，不重复调用 Whisper；
2. 原生结果为空且模型 ready 时调用离线 Whisper；
3. 模型未下载、不可用或 Whisper 终态失败时进入手工填写。

Coordinator 不访问 Mock Interview service，不拥有 Attempt、Turn、幂等键或应用上下文。

### VoiceAnswerComposer

沿用一期组件并新增：

- 首次下载说明、真实下载进度、模型 ready 状态；
- 录音完成后的本地转写状态；
- 取消转写、重新转写和删除/重新下载入口；
- 当前运行后端仅以“GPU 加速 / 兼容模式”面向用户展示，不暴露实现噪音；
- 模型下载期间文字回答仍可用；
- 转写失败保留录音和现有手工文字；
- 只有点击“确认使用这段文字”才调用既有 `onConfirmTranscript`。

### Haru 活动状态

沿用一期 `transcribing`、`success`、`error`，增加更明确的状态文案：

- 下载模型：`正在准备离线语音能力`；
- GPU/WASM 转写：`正在本地整理语音`；
- 取消或回到手工输入：恢复 `idle`；
- reduced-motion 下只更新文字，不播放 Live2D 动作。

Haru 不显示、缓存或朗读未经确认的转写内容。

## 下载来源与缓存

- 主源为 Hugging Face 固定 revision 的公开模型仓库；用户在 OfferPilot 内点击下载。
- 代码仓库只保存 manifest、加载器和第三方许可证，不保存权重。
- 下载模型是本切片唯一新增外联；UI 明确显示来源、大小、用途和“录音不会上传”。
- 转写前记录当前网络审计偏移；从解码开始到转写终态不得产生模型、业务或第三方网络请求。
- GitHub Release 镜像仅在后续独立设计中启用，不在本切片静默回退。

## 数据生命周期

- 模型缓存跨页面保留，直到用户删除、manifest 升级或浏览器清理站点数据。
- 原始录音、Object URL、PCM、Worker 输入和未确认文字只存在于当前页面会话。
- 取消转写保留录音 Blob，允许重试或手工整理。
- 重新录音会删除旧 Blob、旧 URL、旧 PCM、旧转写和旧 generation。
- Mock 回答提交成功后清除音频与转写临时数据。
- 刷新或关闭页面会丢失未提交音频和文字草稿，不承诺恢复。
- 服务端只接收用户确认后的 `answer_text`，无法区分键盘、原生识别或 Whisper。

## 失败与恢复

- 空间不足：不下载，文字、朗读和录音保持可用。
- 下载网络失败：保留完整有效缓存，ready 不成立；允许用户主动重试。
- 缓存损坏或版本不匹配：删除当前模型缓存后重新下载，不使用未知文件。
- WebGPU 不支持、初始化失败、显存不足或推理失败：同一 generation 自动降级一次到 WASM。
- WASM 失败：停止自动尝试，保留录音，提供重新转写和手工整理。
- 解码失败：不启动 Worker，保留可回放录音。
- 超过五分钟：不启动自动转写，提示拆分回答或手工整理。
- Worker 崩溃或页面卸载：废止 generation、terminate Worker、清理 PCM，保留或释放 Blob 依照页面是否仍存在。
- 模型删除过程中禁止开始新转写；删除失败显示未知状态，重新检查缓存后才能继续。

## 可访问性与视觉方向

- 延续一期聚焦的 Answer Studio，不新增独立页面。
- 下载、转写和失败状态不只依赖颜色；进度带真实数值或“不确定”文本。
- 所有按钮至少 40px，具有中文 accessible name、明确禁用原因和 `aria-live` 状态。
- 下载进度使用 `progressbar`；无可靠总量时使用不确定进度，不伪造百分比。
- 键盘可完成下载、取消、重试、手工填写、确认与删除。
- 深色、窄屏和 reduced-motion 保持相同语义。

## 模型选型与性能门槛

实施第一步使用固定中文面试语料比较 Whisper Small 可用量化文件；如果不满足下列门槛，则改用 Whisper Base 量化版本并重新冻结 manifest，不向用户暴露模型选择：

- 语料覆盖 15 秒至 5 分钟、中文、英文技术名词、数字、公司与项目名称；
- 清晰录音字符错误率中位数不高于 15%，P90 不高于 25%；
- 静音和纯噪声不得生成长段虚构文字；
- Windows Chromium 参考设备上，60 秒音频 WebGPU 目标不超过 60 秒，WASM 不超过 180 秒；
- UI 在载入和推理期间保持可操作；
- 实际下载文件总量必须在用户批准的均衡范围内，并在界面显示实际值。

基准结果、设备信息、模型 revision、文件清单和摘要写入发布报告。性能目标是发布选择门槛，不是对所有设备的 SLA；低性能设备超过内部 watchdog 时安全停止并保留手工路径。

## 测试与验收

自动化覆盖：

- manifest 固定 revision、来源白名单、大小、完整性和许可证；
- 模型状态联合类型、容量预检、下载进度、失败、损坏、升级和删除；
- Worker prepare/transcribe/cancel/dispose、generation fencing 和晚到结果；
- WebGPU 到 WASM 只降级一次；
- Blob 解码、16kHz 单声道转换、五分钟边界和异常音频；
- 原生本地识别、Whisper、手工输入三层降级；
- 转写期间 fetch/XHR/业务 service/导航/存储写入边界；
- 未确认文字不进入 `draft.answer`，确认后复用原提交 API；
- Haru 状态、卸载清理和 reduced-motion；
- 下载、转写、取消、重试和删除的真实组件挂载。

浏览器验收分两层：

1. 日常门禁使用本地小型测试资产和固定中文音频，不重复下载正式模型；
2. 发布验收使用正式 manifest，完成真实下载、缓存回读、断网转写、WebGPU/WASM、删除重装、中文校对确认和零音频上传审计。

浏览器使用亮色中文宽屏，候选人为“筱哲”，保存下载、转写、校对和设置管理截图。真实模型未通过时允许使用 Mock 截图展示界面，但必须明确标记，不能替代正式模型发布证据。

## 破坏性变化

无。现有文字模拟面试、后端 API、数据库、Attempt、Turn、幂等键、证据门控和历史读取保持不变。

## 第三方与发布门禁

- 锁定 `@huggingface/transformers` 版本并记录依赖许可；
- 记录 ONNX Runtime Web、Whisper 模型、转换仓库和模型基础许可；
- README/NOTICE 给出模型来源、许可证、下载行为和本地处理边界；
- 未完成真实模型下载与断网转写验收时，不得宣称二期离线转写发布通过。
