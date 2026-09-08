# 离线 Whisper 模拟面试转写实施计划

> 设计依据：`docs/superpowers/specs/2026-08-13-offline-whisper-transcription-design.md`

## 目标与边界

在现有一期语音模拟面试上增加按需下载的浏览器内 Whisper 转写：Web Worker 内运行，WebGPU 优先、WASM 降级；录音与 PCM 不上传、不持久化；转写文本必须由用户校对并显式确认后，才沿用既有 Mock Interview 回答提交路径。

本切片不新增后端 API、数据库、音频上传、云端 STT、实时字幕、说话人识别或自动提交。模型权重不进入 Git 或 Web 构建产物。

## 固定实现口径

- 模型：`onnx-community/whisper-small`。
- Revision：`461d552a09349d5d0d0779b40dd79800eaa3e35a`，禁止使用浮动 `main`。
- 依赖：锁定一个明确的 `@huggingface/transformers` 版本并提交 lockfile。
- 量化组合：先采用兼顾 WebGPU/WASM 的量化文件组合；真实浏览器基准不达标时，必须在独立提交中更换 manifest，不在运行时让普通用户选模型。
- 自动转写最长 300 秒；超过边界保留录音与手工填写路径。
- 录音停止后批量转写，不实现实时字幕。
- 浏览器原生本地识别已有非空结果时不重复运行 Whisper。
- WebGPU 初始化或推理失败后，同一 generation 最多自动降级一次到 WASM。
- 下载是唯一新增外联；从音频解码开始到转写终态，网络审计必须为 0。

## Task 0：固定实施基线与范围

1. 记录最后修改本计划的提交为固定 baseline，后续差异检查始终复用，不中途重算。
2. 保存 allowlist，覆盖：
   - `web/package.json`
   - `web/package-lock.json`
   - `web/src/features/mockInterviewVoice/**`
   - `web/src/components/MockInterviewDrawer*`
   - `web/src/components/SettingsView*`
   - `web/vite.config.ts`
   - 必要的 Haru 状态文案测试
   - `README.md`
   - 本设计、计划、第三方 notice、验收报告与 `artifacts/2026-08-13-offline-whisper-transcription/**`
3. 明确禁止修改 `src/offerpilot/**`、`tests/**`、迁移、API、共享后端类型和业务 service。
4. 每次提交前收集已提交、暂存、未暂存及未跟踪文件，与 allowlist 机器化比较。

## Task 1：模型 manifest、状态与本地元数据（TDD）

### 测试先行

新增：

- `web/src/features/mockInterviewVoice/offlineWhisperManifest.test.ts`
- `web/src/features/mockInterviewVoice/offlineModelStore.test.ts`

覆盖：

- model id、固定 40 位 revision、Apache-2.0 来源、估算字节和 300 秒边界；
- 拒绝 `main`、`latest`、空 revision 和非 Hugging Face 白名单来源；
- IndexedDB ready 元数据与 manifest schema/revision 不一致时回到 `not_downloaded`；
- storage estimate 足够、不足、不可用三态；
- 下载失败不写 ready，删除只清当前模型命名空间；
- IndexedDB/CacheStorage 不可用或抛错时返回稳定中文错误，不影响文字回答；
- 联合状态中 unknown/partial 不伪造百分比。

### 实现

新增：

- `offlineWhisperManifest.ts`
- `offlineModelStore.ts`
- `offlineWhisperTypes.ts`

要求：

- manifest 是只读常量；
- metadata 只保存 schema、revision、cachedBytes、verifiedAt 和 ready；
- 缓存键带 OfferPilot 专属前缀，不清其他站点缓存；
- 所有公开入口 fail-safe，不将 IndexedDB/Cache 异常抛到 React render。

## Task 2：Worker 协议、generation fencing 与音频解码（TDD）

### 测试先行

新增：

- `offlineWhisperCoordinator.test.ts`
- `audioDecoder.test.ts`
- `offlineWhisperRuntime.test.ts`

覆盖：

- prepare/transcribe/cancel/dispose 协议；
- late generation 完成或失败响应全部丢弃；
- cancel 后不写入文本，保留录音；dispose 终止 Worker；
- WebGPU 失败只降级一次，WASM 再失败进入手工路径；
- 原生本地识别有非空文本时不调用 Worker；
- 立体声和非 16k 音频确定性下混、重采样为 16k mono Float32Array；
- 无效 Blob、AudioContext 解码异常、0 长度和 300/301 秒边界；
- PCM 使用 transferable，完成/取消/失败后释放引用；
- 转写窗口内 fetch/XHR/业务 service/导航均为 0。

### 实现

新增：

- `offlineWhisperProtocol.ts`
- `offlineWhisperCoordinator.ts`
- `audioDecoder.ts`
- `offlineWhisper.worker.ts`
- `offlineWhisperRuntime.ts`

要求：

- 主线程只负责解码与协调；推理和依赖动态加载均在 Worker；
- Worker 创建和消息处理均可注入，测试不下载真实模型；
- runtime 对 Transformers.js 封装在单文件，固定 revision、language=`zh`、task=`transcribe`；
- progress callback 只发布下载/加载进度，不泄露 URL 查询或本地音频；
- WebGPU pipeline 销毁后才创建 WASM pipeline；
- Worker 失败时清理 partial pipeline 和待处理输入。

## Task 3：锁定依赖与第三方说明

1. 安装并锁定 `@huggingface/transformers`，检查许可证与实际子依赖。
2. 使用 Vite Worker URL 创建 Worker，确认生产构建把 Worker 拆成独立产物且模型权重不进入 `dist`。
3. 新增/更新第三方 notice，写明：
   - Transformers.js 与 ONNX Runtime Web 许可证；
   - `onnx-community/whisper-small` 来源、固定 revision、Apache-2.0；
   - 模型由用户主动从 Hugging Face 下载到浏览器缓存；
   - 录音与转写不上传；
   - GitHub 普通仓库不存模型权重。
4. 测试产物中不存在 `.onnx`、模型缓存或音频样本。

## Task 4：下载与模型管理 UI（TDD）

### 测试先行

新增：

- `OfflineWhisperModelCard.test.tsx`
- `SettingsView.offlineWhisper.test.tsx`

覆盖：

- 未下载、容量不足、空间未知、下载中、ready、损坏、删除中、失败与重装；
- 用户点击前没有 Hugging Face 请求；
- 下载进度有已知总量时展示数值，无总量时使用不确定进度；
- 下载期间文字回答与页面导航仍可用；
- 删除前确认，删除后不能自动转写；
- 40px 命中区、键盘操作、aria-live、progressbar、深色与窄屏 class 契约；
- StrictMode 不重复下载或创建两个 Worker。

### 实现

新增：

- `OfflineWhisperModelCard.tsx`
- `OfflineWhisperModelCard.module.css`
- `OfflineWhisperProvider.tsx`

并接入 `SettingsView`：

- 标题“离线语音转写”；
- 首次下载解释约 561MB（以当前固定 revision 的真实浏览器下载结果为准）、来源、用途和隐私边界；
- 展示“GPU 加速/兼容模式”而非实现器细节；
- 支持下载、重试、删除和重新检查；
- 设置页不读取或展示任何录音/转写文本。

## Task 5：接入 Answer Studio 与 Haru（TDD）

### 测试先行

扩充：

- `VoiceAnswerComposer.test.tsx`
- `MockInterviewDrawer.voice.test.tsx`
- 必要的 Haru 挂载测试

覆盖：

- 原生本地文本优先；无文本且模型 ready 时自动开始 Whisper；
- 未下载时显示首用卡，用户可下载或继续手工填写；
- 加载、转写、取消、WASM 降级、失败、重新转写；
- 取消/失败保留 audio 与既有手工文本；
- 未确认转写不进入 `draft.answer`，确认后只调用既有 `onConfirmTranscript`；
- submit success、重新录音、关闭 Drawer 和卸载清除 Blob URL/PCM/Worker；
- Haru 下载时显示“正在准备离线语音能力”，转写时显示“正在本地整理语音”，不显示内容；
- reduced-motion 只更新状态文本；
- 模型下载外，录音/解码/转写期间 fetch、XHR、Mock service 与导航写入为 0。

### 实现

- `VoiceAnswerComposer` 保存当前录音 Blob，并把本地原生识别结果和 Whisper 结果分开管理；
- 录音 stop 后按 coordinator 规则决定是否转写；
- 草稿区明确标识“离线转写草稿”，仍允许全文编辑；
- 自动转写结果只有明确确认后进入既有回答文本；
- 所有异步动作带 generation，props/Attempt/Turn 变化时废止旧结果；
- 不修改 Mock Interview API、Attempt、Turn 或提交 payload。

## Task 6：浏览器验收、模型基准与截图

使用内置 Codex browser、亮色中文、宽屏、候选人“筱哲”。

### 日常 Mock 验收

用可注入 mock runtime 完成：

1. 首次下载说明；
2. 真实进度样式；
3. 本地转写中；
4. 转写草稿校对与显式确认；
5. 设置页 ready 与删除管理；
6. 窄屏、深色、键盘焦点和 reduced-motion。

截图文件写入：

`artifacts/2026-08-13-offline-whisper-transcription/`

每张截图必须回读检查，不含空白大块、遮挡、截断、浏览器开发工具或密钥。

### 正式模型发布验收

仅在用户明确触发下载后执行：

- 从固定 revision 下载并记录实际文件、总字节和耗时；
- 刷新后从缓存回读；
- 断网后完成中文录音转写，转写期间网络请求为 0；
- WebGPU 和 WASM 各至少一次；
- 删除、重新下载；
- 15 秒、60 秒及可控长音频的准确率与耗时；
- 纯静音/噪声不生成长段虚构内容。

若正式模型或设备门槛未通过，截图必须标“Mock 界面演示”，发布报告保持阻塞，不能把 Mock 作为真实模型证据。

## Task 7：最终门禁与提交

最小定向门禁：

```powershell
cd web
npm.cmd test -- --run web/src/features/mockInterviewVoice
npm.cmd run build
```

随后运行完整门禁：

```powershell
cd web
npm.cmd test -- --run
npm.cmd run build
cd ..
uv run ruff check .
uv run mypy src
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
```

最终检查：

- allowlist 无越界；
- `git diff --check <baseline>..HEAD` 退出码 0；
- 工作区除待提交报告外干净；
- `web/dist` 不含模型权重、录音、密钥；
- 运行时转写网络审计为 0；
- 报告如实区分 Mock UI 与真实模型证据；
- 临时服务、Worker、浏览器标签、端口、音频和模型测试缓存已清理。

按小步 conventional commits 提交：

1. `docs: AI plan offline Whisper transcription`
2. `feat: AI add offline Whisper runtime`
3. `feat: AI integrate offline voice transcription`
4. `test: AI verify offline Whisper browser flow`
5. `docs: AI record offline Whisper verification`

## 破坏性变化

无。文字模拟面试、浏览器原生本地识别、录音回放、Mock Interview API、数据库及后端证据契约保持不变。未下载模型的用户继续使用一期路径。
