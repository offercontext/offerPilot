# 实验性实时面试语音

该可选进程为模拟面试提供本地流式识别、智能结束检测和可打断播报。真实面试准备与快速练习共用同一链路。默认不开启；标准文字和逐轮确认语音仍可独立使用。

## 安装与启动

需要 uv、Python 3.11–3.13，以及支持麦克风/WebRTC 的浏览器。主服务仍保留原 Python 版本要求。以下 PowerShell 命令从仓库根目录执行：

```powershell
Set-Location voice-runtime
uv sync --frozen
uv run python scripts/download_models.py
$env:OFFERPILOT_SPEECH_MODELS = (Resolve-Path .models).Path
$env:OFFERPILOT_API_URL = "http://127.0.0.1:8080"
uv run offerpilot-voice
```

另开终端，从仓库根目录启动主服务：

```powershell
$env:OFFERPILOT_VOICE_RUNTIME_URL = "http://127.0.0.1:8766"
uv run oc start
```

若主服务使用其他端口，同步调整 `OFFERPILOT_API_URL`。浏览器通过主服务代理交换 SDP，媒体直接连同一服务器上的语音进程。两个服务必须位于同一可信主机；内部地址只接受 HTTP loopback，不通过系统代理。当前没有 TURN、中继、独立远端语音服务或 Docker 打包支持。推荐本机 localhost；跨设备需要 HTTPS 麦克风权限及可直达的 WebRTC 网络，不保证公网/NAT 可用。

模型下载是显式步骤（压缩包约 550 MB，解压和依赖另占空间），从 sherpa-onnx 官方 release 下载并校验固定 SHA-256。模型不随仓库提交；启动时不下载、不回退到云端。缺少识别或结束检测模型时，health 返回 ready=false，界面提供文字模式。缺少朗读模型时，实时识别仍可正常开启。主服务和独立语音进程必须一并更新到语音协议 v2；版本不匹配时实时语音不开启，仍可使用标准文字和逐轮确认语音。模型许可与使用条件请核对各模型的上游说明。

版本锁定在 `uv.lock`：Pipecat 1.12.0、sherpa-onnx/core 1.13.8；Silero 和 Smart Turn V3 由 Pipecat 包提供。ASR 使用 bilingual streaming Zipformer 2023-02-20；TTS 使用中文 VITS aishell3。升级前需重新验证结束检测与 native ONNX runtime 的兼容性。

## 使用与恢复

进入模拟面试回答工作台，展开“实时对话 · 实验功能”，勾选本次连接自动记录授权，再开启。题目始终显示文字；如需朗读，可在开启前勾选“朗读面试题（仅本次连接）”，默认不读。朗读模型在本次连接首次使用时由独立子进程加载；子进程崩溃、超时或播放失败时，继续看文字并用麦克风回答。朗读子进程不接收麦克风音频。正常断开、授权撤销或受控停服会回收子进程；如果在原生合成卡住时强制结束语音进程，子进程可能仍在运行，需要手动结束。未经逐题人工核对的回答有明确标签。只有本次 attempt 的有效 session/epoch 可以自动记录；同一练习的其他页面需要明确接管。

- 说话可以打断题目播报；等待、重播、解释请求不应被当成正式答案。短于 10 个字母数字字符的转写会转人工核对，以降低短指令误识别后的误提交风险。
- 模型判断结束后才提交最终转写；固定静音不是提交条件。识别/检测失败、丢失媒体、五分钟上限转人工核对。
- 暂停、隐藏页面、切到依据、关闭回答工作台或折叠活动面板都会停止收音并撤销授权。恢复必须再次主动授权。
- 暂停不能撤回已经发出的保存请求；结果未知时用原尝试恢复，保留原请求 key、原文和 acceptance。提交后的续说单独显示，不追加到下一题。
- 目前一个语音进程同时服务一场对话。连接断开后可能需要等待几秒释放资源。

音频在浏览器和 OfferPilot 服务器间传输，不保存原始录音、不记录转写日志。正式答案按原产品规则持久化，并附输入来源及确认方式；恢复用的原文和会话信息暂存于浏览器 sessionStorage。播报中断回执只在进程内有界保存，位置为近似秒数，不表示用户准确听到了哪些字。出题、反馈仍调用主服务当前配置的模型。

## 验证与当前限制

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run python scripts/evaluate_turns.py --smoke
uv run python scripts/evaluate_turns.py --manifest corpus.json --min-samples 200
```

语料 manifest 是数组：每项含 `id`、相对 manifest 的单声道 PCM16 WAV `path`、标注的回答结束时间 `end_ms`。脚本只进行 dry-run，不创建授权、不写入面试回答；显式输出转写和统计到本地 JSON，请按语料的保密要求保管。

已验证引擎的失败/恢复边界，以及使用合成音频的真实 WebRTC 连接、出声、停止和授权失效。真实本地模型 smoke 已发现中文识别误差，尤其是短指令；短文本安全降级不能消除长文本误识别。尚未完成 200 条真实中文标注语料、用户麦克风、耳机及外放回声验收，不能据此宣称误截断率或延迟达标。采用自动记录前请确认该实验功能适合当前练习；需要准确文本时使用人工核对模式。

架构边界见 [ADR-0020](../docs/architecture/decisions/0020-scope-realtime-interview-voice.md)。
