# Offline Whisper Notice

OfferPilot 的可选离线语音转写使用以下第三方组件：

- `@huggingface/transformers` 4.2.0，Apache License 2.0；
- ONNX Runtime Web（由 Transformers.js 引入），MIT License；
- `onnx-community/whisper-small`，基于 OpenAI Whisper Small 转换的 ONNX 模型，Apache License 2.0。

模型来源：<https://huggingface.co/onnx-community/whisper-small>

固定 revision：`461d552a09349d5d0d0779b40dd79800eaa3e35a`

模型权重不包含在 OfferPilot Git 仓库中，也不随 Web 构建产物分发。只有用户在界面中明确点击“下载离线模型”后，浏览器才会从 Hugging Face 下载所需文件，并把它们保存在 OfferPilot 专属的浏览器缓存中。

录音、解码后的 PCM 与未确认的转写草稿不会上传到 Hugging Face、AI Provider 或 OfferPilot 后端，也不会持久化。用户删除模型时，仅清理 OfferPilot 的离线 Whisper 缓存与就绪元数据。
