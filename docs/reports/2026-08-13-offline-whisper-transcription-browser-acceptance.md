# 离线 Whisper 语音转写浏览器验收

日期：2026-08-13

分支：`codex/feat/20260813-offline-whisper-transcription`

## 结论

离线语音转写二期已完成前端实现与浏览器验证：模型仅在用户点击后下载到浏览器，录音在页面内解码，转写在 Web Worker 中执行，转写文字必须由用户显式确认后才进入既有 Mock Interview 回答流程。未新增后端 API、数据库、音频上传或自动提交。

真实浏览器已完成固定 revision 模型下载、缓存回读、WebGPU runtime 初始化、缓存删除和纯静音保护验证。中文回答界面使用可控 Mock 录音事件展示，未使用麦克风或外部语音服务。

## 真实模型证据

- 模型：`onnx-community/whisper-small`
- 固定 revision：`461d552a09349d5d0d0779b40dd79800eaa3e35a`
- 实际浏览器缓存规模：约 561 MB
- 下载来源：Hugging Face；未调用 AI Provider
- 浏览器 runtime：WebGPU 初始化成功
- 刷新后：从现有缓存重新装载 runtime，不要求重新下载
- 清理：验收结束后已通过产品界面删除模型缓存和 IndexedDB 就绪元数据

实际验证时，零 PCM 首次暴露出 Whisper 对静音生成重复文本的风险。实现随后增加两道确定性保护：推理前的静音/低能量检测，以及推理后的重复片段检测。重新验证后，静音会直接显示“没有检测到清晰语音”，不会生成或确认回答文字。

## UI 证据说明

- `01`–`05` 是可控 Mock UI 演示，用于展示下载、录音、校对和显式确认状态；不代表真实麦克风准确率证据。它们拍摄于真实下载尺寸校准前，卡片中的 466 MB 是早期估算，最终产品口径已更新为 561 MB。
- `06` 是真实模型完成下载并从缓存回读后的设置页，显示当前产品口径 561 MB。
- 所有截图均未展示密钥、音频内容或用户真实求职数据。

| 文件 | 尺寸 | SHA-256 | 说明 |
|---|---:|---|---|
| `01-model-download-progress-mock.png` | 1525×650 | `3bd906fbe436d2a4ad0e177f02157c9db3f601457d8a92b0e3c46981851160f0` | Mock 下载进度 |
| `02-recording-local-only-mock.png` | 1525×1242 | `f23ec2ffafcdf5a7628ad1b6740b4a1c7be28f8d5264f70cf7f11a5a4a6a6e7c` | Mock 页面内录音 |
| `03-transcript-confirmation-mock.png` | 1525×970 | `399bbc216aba1ad627cc2b0ab49a0bfe5e9c342eec695d3cd9638637062d8644` | Mock 转写确认全景 |
| `04-transcript-review-detail-mock.png` | 1525×970 | `4ec5ddff54d1c625e709e02dddcb0eab5896392f017d565609f4cdadea53f011` | Mock 转写校对细节 |
| `05-model-ready-settings-mock.png` | 1525×650 | `c3bbd543801a1e85374ab5e3b686873a5a6df5f62c89ac44e17d558022daa476` | Mock 设置页 ready 状态 |
| `06-real-model-ready-settings.png` | 1540×980 | `1e985248603cfffb91e14a68b2ca4351e4542d617b5cd5df874de9771e1d9011` | 真实模型缓存回读 |

## 安全与边界

- 模型权重未进入 Git，也未打入 `web/dist`。
- 录音 Blob、PCM 与未确认转写不上传、不持久化。
- 浏览器原生本地转写已有非空结果时，不重复运行 Whisper。
- 只有确认后的文字进入既有回答字段；取消、失败或关闭保留手工回答路径。
- WebGPU 失败时同一 generation 最多降级一次 WASM；相关路径由自动化覆盖。
- 单次音频上限为 5 分钟。

## 已执行验证

- 离线 Whisper 定向 Vitest
- 前端全量 Vitest
- TypeScript 与生产构建
- Ruff、Mypy
- `oc smoke --static-dir web/dist`
- `oc verify --profile local --static-dir web/dist`
- `git diff --check`
- 构建产物模型权重/音频扫描

## 剩余风险

- 本轮未使用真实麦克风录制普通话样本，因此不把 Mock 中文文本当作准确率证据。
- WASM 降级由自动化覆盖，未在本机浏览器真实跑完整模型性能基准。
- 不同设备的下载耗时、WebGPU 支持和 561 MB 缓存配额会不同；产品保留明确的容量错误、删除模型和手工回答路径。
