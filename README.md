# OfferPilot — 开源、本地优先的 AI 求职与投递管理工具

[简体中文](README.md) | [English](README.en.md)

**集中管理投递进度、简历、面试与 Offer，让每一轮准备都有记录可查。**

OfferPilot 由 offercontext 维护，是面向个人求职者的开源 AI 求职工作台。
你可以在本地管理不同公司和岗位的投递记录，整理简历与岗位描述，
进行模拟面试、保存复盘，并比较收到的 Offer。

**English:** OfferPilot by offercontext is an open-source, local-first
AI job application tracker for individual job seekers. It supports resume
management, interview preparation, mock interviews, interview reviews,
offer comparison and salary negotiation preparation.

支持 Docker 或源码方式在本机部署。基础投递记录无需先配置 AI；
使用 AI 辅助功能时，再连接自己的模型服务。

[快速开始](#快速开始)｜[用户指南](docs/product-manual/产品说明书.md)｜[常见问题](#常见问题)

## 主要功能

| 功能 | 具体用途 |
| --- | --- |
| 投递管理 | 按公司、岗位和阶段跟进求职进度，关联岗位描述与面试安排 |
| 简历与材料准备 | 管理简历版本，结合岗位要求生成并审阅材料建议 |
| 面试准备与模拟 | 根据简历和岗位描述练习问答，查看 AI 追问与反馈 |
| 面试复盘 | 保存面试记录，选择并确认需要继续练习的重点 |
| Offer 对比与谈薪 | 比较已填写的薪酬、福利和答复期限，准备谈薪沟通 |
| Pilot AI 助手 | 结合求职资料查询、分析、起草内容或提出修改建议 |

Pilot 是内置 AI 助手，Haru 是可选角色入口；隐藏 Haru 不影响使用 Pilot。

本仓库介绍本地部署版本。在线服务可访问
[offerContext Hub](https://hub.offercontext.cn)；
其功能与数据处理方式以站内说明为准，不默认与本地版本一致或互通。

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

## 常见问题

### 不配置 AI，也能使用 OfferPilot 吗？

可以。记录投递、保存岗位描述和手动编辑资料等基础功能不要求先配置 AI。
材料生成、模拟问答和 AI 分析等功能需要配置相应的模型服务。

### 本地优先是否意味着资料不会离开设备？

不是。业务数据保存在本地工作区，但使用 AI 功能时，
相关资料会发送给你配置的模型服务，并可能产生服务商费用。
本地保存不等于所有 AI 计算都在本地完成。

### OfferPilot 会自动投递简历或联系招聘方吗？

不会。OfferPilot 用于管理求职过程、准备材料和整理建议，
不自动向招聘方投递或发送消息。关键求职记录的修改默认需要用户确认。

详细操作与问题排查见[用户指南](docs/product-manual/产品说明书.md#s12)。

## 许可证

[AGPLv3](LICENSE)

### 第三方角色与运行时

桌面宽屏的 Pilot 看板娘使用 Live2D 官方样例角色 Haru 受付版与 Cubism Core。相关角色、模型数据及运行时版权归 Live2D Inc. 所有，不包含在 OfferPilot 的 AGPLv3 授权中；使用与分发需同时遵守 [Live2D 样例模型条款](https://www.live2d.com/eula/live2d-sample-model-terms_en.html) 与 [Live2D SDK 许可](https://www.live2d.com/en/sdk/license/)。

> This content uses sample data owned and copyrighted by Live2D Inc. The sample data are utilized in accordance with terms and conditions set by Live2D Inc. This content itself is created at the author’s sole discretion.

### 离线语音模型与运行时

可选离线转写使用 Apache-2.0 许可的 `@huggingface/transformers`、ONNX Runtime Web 与 [`onnx-community/whisper-small`](https://huggingface.co/onnx-community/whisper-small)。模型固定到 revision `461d552a09349d5d0d0779b40dd79800eaa3e35a`，不会提交到 Git 仓库或打入模型权重；用户主动下载后仅缓存在当前浏览器。详细说明见 [`web/public/offline-whisper-NOTICE.md`](web/public/offline-whisper-NOTICE.md)。
