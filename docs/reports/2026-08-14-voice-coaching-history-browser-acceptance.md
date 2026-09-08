# 语音表达成长档案浏览器验收报告

日期：2026-08-14

分支：`codex/feat/20260814-voice-coaching-history`

固定实施基线：`9cba245f97a8624d25f8f742f43102d6f1eee293`

## 结论

第四期“语音表达成长档案”功能实现与隔离浏览器验收通过：用户完成语音回答后，可在二次确认卡中保存不可变的文字摘要快照；面试页可查看历史、趋势、来源详情、删除记录，并从历史弱项启动一次新的定向练习；Pilot 提供只读本地快捷入口。保存、读取、趋势、删除与导航均不调用 AI/Provider，不上传或持久化音频、PCM。

完整发布门禁尚不能标记为全绿：后端五组中的 `misc` 存在两个未被本分支修改的既有失败，详见“验证结果”。本分支未推送、未合并。

## 浏览器验收范围

- 浏览器：Codex 内置浏览器。
- 模式：亮色、中文。
- 视口：`1455×1100`。
- 候选人案例：筱哲。
- 数据与服务：独立临时数据库、独立本地端口。
- 音频限制：因自动化环境无法稳定取得实体麦克风，保存确认卡使用本地 Mock PCM/MediaRecorder 数据演示；Mock 数据仅用于驱动界面，不进入 API 请求或数据库。
- Provider 限制：仅为创建模拟面试问题使用本地受控 OpenAI-compatible Provider；成长档案保存、列表、趋势、删除和导航的 Provider 调用数均为 0。

## 验收结果

1. 语音回答提交后展示“保存到成长档案”二次确认卡；可检查确认转写、表达摘要、反思和训练重点，未确认时不写入。
2. 成长页展示至少两条不可变历史、表达趋势和下一步练习；趋势只基于已保存快照确定性计算。
3. 来源详情显示冻结问题、确认答案、指标、停顿区间、用户反思和来源关系；删除只删除成长快照，不删除原模拟面试回答。
4. “针对这个问题再练一次”打开新的模拟面试草稿，并携带本地训练重点与来源快照 ID；不会自动发送消息或调用 Provider。
5. Pilot 的“查看表达成长”仅执行本地导航，不发送聊天消息。
6. 尝试清理已有成长快照所依赖的 Attempt 时，后端拒绝破坏性删除；已提交但未保存成长快照的语音回答同样不会因关闭界面被自动删除。
7. 保存结果未知、幂等冲突和来源变化均使用稳定错误码与指纹对账；不把另一份快照误认为当前内容已保存。

## 请求与写入审计

- 成长档案请求只访问本地 `/api/mock-interviews/.../voice-coaching-snapshots` 相关端点。
- 快照载荷只包含确认后的文本、确定性表达指标、停顿区间、反思、训练重点和冻结来源标识。
- 音频 Blob、PCM、浏览器 SpeechRecognition 临时文本均未进入保存请求或数据库。
- 保存、列表、趋势、删除、历史查看、定向复练导航和 Pilot 入口均为 0 次 AI/Provider 调用。
- 未写入 Knowledge、Memory、Interview Story 或 Application 状态。
- 浏览器验收完成后，服务、受控 Provider、浏览器标签、AudioContext、MediaRecorder、端口 `65420/65421` 与临时数据均已清理；两端口已确认释放。

## 截图证据

| 场景 | 文件 | 尺寸 | SHA-256 |
|---|---|---:|---|
| 保存前二次确认 | `offerpilot-voice-coaching-save-confirmation-20260814.png` | 1455×1100 | `3fbc854069a385df2fbd23903c2f7cdeb5b2ea0e5e242dd7cf5acc480152b2c9` |
| 成长总览与趋势 | `offerpilot-voice-coaching-growth-overview-20260814.png` | 1455×1100 | `ac4c56eeff071f01c77fa9078b4269bae4e555fde787ffc1ddff4b4a1e2f6353` |
| 已确认历史详情 | `offerpilot-voice-coaching-confirmed-history-20260814.png` | 1455×1100 | `3145525971e4a613bcf4a777d1bf4ac22768a5387e242800be6d4fdb47851601` |
| 从弱项发起定向复练 | `offerpilot-voice-coaching-focused-practice-20260814.png` | 1455×1100 | `b2b07299c9a8a62cc68486fe2aa5612c9a62598e834894e8bceab355ddd7ba6a` |
| Pilot 本地快捷入口 | `offerpilot-voice-coaching-pilot-entry-20260814.png` | 1455×1100 | `d66788af4fc0ae24df8dfa42ea65ab5b6c6a618b5a84d045a2d0f167be482e42` |

截图保存在 `D:\Users\yuqi.chen\Desktop`，均已逐张回读检查。

## 验证结果

通过：

- 前端十组门禁：157 个测试文件、1151 tests 全部通过，manifest/source fingerprint/aggregate 校验通过。
- Voice Coaching 后端专项：migration、repository、API 共 28 passed。
- 最新清理与安全边界前端专项：32 passed。
- Ruff：通过。
- Mypy：72 个 source files 通过。
- TypeScript 与生产构建：通过。
- `uv run oc smoke --static-dir web/dist`：通过。
- `uv run oc verify --profile local --static-dir web/dist`：通过。
- 独立代码复审：最终未发现 P0/P1/P2。
- 固定 baseline allowlist：无越界文件。

后端五组门禁收集 2089 个唯一 node ID；结果为 2083 passed、4 个既定 Windows symlink 权限 skip、2 failed：

1. `tests/test_application_jd_browser_harness.py::test_application_jd_implementation_scope_is_machine_checked`：缺少独立 JD 发布门禁要求的 `OFFERPILOT_APPLICATION_JD_BASELINE_FILE` 环境变量。
2. `tests/test_cutover_files.py::test_readme_states_the_product_boundary_and_core_capabilities`：测试仍断言旧 README 文案“准备面试、进行文本模拟与复盘”，当前 main 的 README 已改为“准备面试、进行文字或语音模拟与复盘”。

`README.md`、`tests/test_cutover_files.py`、`tests/test_application_jd_browser_harness.py` 相对固定 baseline 均无变更，因此上述两项不由本分支引入；但五组 aggregate 仍不能宣称通过。

## 数据与兼容性

- 新增 `0022_voice_coaching_snapshots` 迁移及只读历史 API。
- 无破坏性迁移，无已有字段或接口删除。
- 快照物理删除仅作用于 Voice Coaching 领域；历史来源 Attempt 与已提交回答保留。

## 剩余风险

- 本次界面验收使用 Mock PCM，没有覆盖真实麦克风硬件、系统权限弹窗或浏览器语音识别引擎差异；这些能力由前三期既有测试与降级路径承担。
- 后端完整 aggregate 仍受上述两个 baseline 既有门禁问题阻塞；修复或提供对应 JD 门禁环境后需在当前 HEAD 重跑。
- 分支未推送、未合并。
