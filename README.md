# OfferPilot — 本地优先的 AI 求职工作台

> 把简历、投递、面试与 Offer 放在一个由你掌控的本地工作台；AI 提供建议，你决定下一步。

OfferPilot 是一个开源、本地优先的 AI 求职工作台，用于集中管理简历、投递进度、面试安排、复盘和 Offer。

你可以围绕一个目标岗位整理资料、准备材料、记录进展，并把面试中的经验用于下一轮准备。需要 AI 时，再连接自己的模型服务，由 Pilot 协助查询、分析和起草。

本仓库提供 OfferPilot 本地部署版本。另可访问 [offerContext Hub](https://hub.offercontext.cn) 使用在线服务；网站功能与数据处理方式以站内说明为准，不代表与本地版本功能一致或数据互通。

[快速开始](#快速开始)｜[用户指南](docs/product-manual/产品说明书.md)｜[常见问题](docs/product-manual/产品说明书.md#s12)

## 它能帮你做什么

- **简历与投递管理**：保留简历版本，跟进不同公司和岗位的进度与日程。
- **岗位分析与材料准备**：结合简历和岗位要求，分析匹配情况，准备有针对性的投递材料。
- **面试准备与模拟练习**：针对已安排的面试准备问题，也可以选择岗位资料和简历独立练习；AI 根据回答继续追问，并在练习结束后提供反馈。
- **复盘与经验整理**：选择值得保留的复盘片段，整理并确认后保存为笔记，方便后续查阅；也可以把复盘重点用于下一次面试准备。
- **Offer 对比与谈薪准备**：比较已填写的薪酬、福利、截止日等条件，整理需要沟通的问题。
- **Pilot AI 助手**：结合求职资料查询、分析、起草内容或提出修改。

Pilot 是 OfferPilot 内置的 AI 助手，Haru 是它的可选角色入口。你可以直接在页面操作，也可以通过 Pilot 获得帮助；隐藏 Haru 后仍可使用 Pilot 侧边栏。

## 真实界面

以下为本地部署开发版的实际界面，截图更新于 2026 年 9 月。不同版本的功能与界面可能存在差异，截图对应版本见[用户指南](docs/product-manual/产品说明书.md)。

### 1. 跟进每个岗位的投递进度

在同一看板中查看公司、岗位与当前阶段，按待投递、已投递、笔试、面试、Offer 和结束整理进展。

![投递看板：按公司、岗位和阶段跟进](docs/product-manual/screenshots/R03-05-page-create-saved.png)

### 2. 为目标岗位准备投递材料

在投递详情中选择简历和本次使用的岗位描述（JD），进入材料工作区生成并审阅建议。确认后创建岗位简历版本，保留基础简历。

![投递准备：对照岗位与简历审阅 AI 生成的优化建议草稿](docs/product-manual/screenshots/04-03-material-generated.png)

### 3. 结合求职资料使用 AI 助手

在同一个会话中查询投递进展、整理岗位资料或起草下一步计划，并对照右侧参考资料核对建议。

![完整 Pilot 页面：左侧会话列表、中间对话与新建投递确认卡、右侧参考资料及底部输入区](docs/product-manual/screenshots/R03-02-pilot-create-confirm.png)

### 4. 面试前练习，面试后整理经验

从已安排的面试进入练习，或直接使用“快速模拟”，选择并核对本次岗位资料与简历后开始问答。独立练习不要求先创建真实投递或日程。

![新版文字模拟面试：沿用筱哲与远帆科技案例，查看中文提问依据并回答 AI 追问](docs/product-manual/screenshots/R08-studio-evidence.png)

### 5. 比较 Offer 条件，准备谈薪

录入 Offer 后可以比较已知薪酬条件、补充自定义比较维度，并进入谈薪准备或谈薪教练，整理下一次沟通的重点。

![Offer 横向对比：查看等宽摘要卡片、年薪与回复时间差，并逐项核对薪酬、福利和截止日](docs/product-manual/screenshots/R08-offer-comparison-polished.png)

## 快速开始

选择 Docker 或源码方式启动，完成其中一种即可：

- Docker 方式需要 Git 和可用的 Docker 环境，不必单独安装 Python 或 Node.js。
- 源码方式需要 Git、uv、Python 和 Node.js/npm。项目声明 Python 最低版本为 3.10；当前 Docker 构建使用 Python 3.12 和 Node.js 20，这些构建版本不等于源码运行的最低要求。

### Docker

```bash
git clone https://github.com/offercontext/offerPilot.git offerpilot
cd offerpilot

docker build -t offerpilot .
docker run --rm -p 127.0.0.1:8080:8080 -v offerpilot-data:/data offerpilot
```

打开 `http://localhost:8080`。

### 从源码启动

```bash
git clone https://github.com/offercontext/offerPilot.git offerpilot
cd offerpilot
uv sync
cd web
npm ci
npm run build
cd ..
uv run oc start
```

打开 `http://localhost:8080`。上述两种方式均以本机访问为默认用途。

### 数据保存在哪里

| 启动方式 | 默认数据位置 |
| --- | --- |
| 源码启动 | 用户主目录下的 `~/.offerpilot`，可通过 `OFFERPILOT_DATA` 调整 |
| 上述 Docker 命令 | `offerpilot-data` 数据卷，挂载到容器内的 `/data` |

### 第一次使用

先记录一个岗位，不必先配置 AI。打开投递看板，添加公司和岗位，再进入详情保存岗位描述。

需要 AI 辅助时，再到“设置 → AI 与模型 → 配置 AI”填写模型服务信息，测试连接、应用到 Provider 列表，最后点击页面顶部“保存”。完整步骤见[第一次使用指南](docs/product-manual/产品说明书.md#start)。

## 隐私、费用与使用限制

- 本地部署将业务数据保存在你的设备上。使用 AI 功能时，相关资料会发送给你配置的模型服务；本地存储不代表所有 AI 处理都在本地完成。模型调用可能产生服务商费用。
- 模拟面试录音只存在于当前页面，不上传、不持久化；离线 Whisper 模型仅在你主动点击后从 Hugging Face 下载到浏览器缓存。
- Pilot 对关键求职记录的修改默认需要你的确认；请核对系统确认卡后再执行。OfferPilot 不会自动投递，也不会替你向招聘方发送消息。
- AI 输出可能包含错误。采用前请核对经历、数字、日期和承诺；是否投递、接受 Offer 或如何谈薪，仍由你决定。

## English

OfferPilot is a local-first AI job-search workspace for keeping resumes, applications, interviews, confirmed learnings, and offers together. It helps you prepare and review; you keep control of every important action.

Follow the [Quick start](#快速开始) for Docker or source setup. You can record applications and job descriptions without configuring AI. AI features send relevant materials to your configured model service and may incur provider fees. OfferPilot does not auto-apply, send messages to recruiters, or decide which offer you should accept.

## 许可证

[AGPLv3](LICENSE)

### 第三方角色与运行时

桌面宽屏的 Pilot 看板娘使用 Live2D 官方样例角色 Haru 受付版与 Cubism Core。相关角色、模型数据及运行时版权归 Live2D Inc. 所有，不包含在 OfferPilot 的 AGPLv3 授权中；使用与分发需同时遵守 [Live2D 样例模型条款](https://www.live2d.com/eula/live2d-sample-model-terms_en.html) 与 [Live2D SDK 许可](https://www.live2d.com/en/sdk/license/)。

> This content uses sample data owned and copyrighted by Live2D Inc. The sample data are utilized in accordance with terms and conditions set by Live2D Inc. This content itself is created at the author’s sole discretion.

### 离线语音模型与运行时

可选离线转写使用 Apache-2.0 许可的 `@huggingface/transformers`、ONNX Runtime Web 与 [`onnx-community/whisper-small`](https://huggingface.co/onnx-community/whisper-small)。模型固定到 revision `461d552a09349d5d0d0779b40dd79800eaa3e35a`，不会提交到 Git 仓库或打入模型权重；用户主动下载后仅缓存在当前浏览器。详细说明见 [`web/public/offline-whisper-NOTICE.md`](web/public/offline-whisper-NOTICE.md)。
