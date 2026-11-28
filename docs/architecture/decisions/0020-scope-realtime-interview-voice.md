# ADR-0020: 用受限会话接入实时面试语音

- Status: Accepted（实验功能，非发布验收通过）
- 日期: 2026-10-13
- Decider: 用户确认方案后授权实施
- 依据: 本次实时语音方案讨论；现有模拟面试 attempt/turn 契约

## Context

浏览器内逐轮确认语音使用显式核对与固定静音倒计时，无法支持自然接话和可打断播报。实时模式需要允许本场授权内自动记录，同时保持既有幂等保存、资料版本校验、恢复及其他写工具 HITL 边界。

## Decision

保留标准流程，增加 opt-in 实时模式，覆盖真实面试准备和快速练习。主服务仍是唯一业务事实源。独立 Python 3.11–3.13 进程用 Pipecat/WebRTC、Silero、Smart Turn V3、sherpa-onnx 流式 ASR 和本地 VITS；主服务 Python 要求不变。运行时只持有读当前题的受限 capability，不能调用保存答案工具。

每次连接经显式 consent 创建 session，绑定 attempt、context、consent revision、独占 epoch、45 秒租约及一小时硬到期。浏览器每 10 秒续租；worker 每 4 秒检查授权。主服务在答案写事务中验证 owner、租约、题目和 source。接管撤销旧 owner；人工新写不能绕过活动会话。撤销后仅允许完全相同请求身份、原文和 acceptance 的已成功写入重放。

worker 聚合流式修订并撤销被续说打断的结束候选；只有模型确认结束和 ASR 最终文本才产生 candidate。浏览器的一个 coordinator 调用既有 answer→next-question API；新旧事件必须通过 turn guard。短转写、超时、断流、五分钟上限和不明结果进入人工处理。续说在候选提交后属于独立补充，不得绑定下一题。

暂停/隐藏/离开收音视图先同步停止 tracks，再撤销会话，不取消已发出的业务写入。浏览器请求前保留原恢复身份；重启连接需要再次授权。资料变化、连接失效、失败恢复不得静默恢复收音。题目始终以文字展示。仅本次连接选择朗读时，语音进程才启动有时限的 TTS 子进程并朗读主服务校验后的当前题；子进程只接收题目文字，连接结束时停止。朗读模型缺失、合成子进程退出或超时、浏览器播放失败只提示阅读文字，不改变 ASR 就绪、麦克风授权或业务推进。主服务和语音进程通过语音协议 v2 校验配对，否则回退标准模式；中断以 generation 丢弃旧音频，并保存有界内存回执（question_id、playback_id、近似位置），不声称精确听闻内容。

新增 additive migration 0041：会话与回答来源各用一张独立表，旧 turn 表不变，可空历史字段明确显示来源未知。记录 input_source、acceptance_method、voice_session_id、epoch 和 consent_revision。通用 Chat/Agent 写工具的 HITL 不变。
工作区备份保留已提交回答及接受来源，但不会恢复麦克风授权。导入只对受支持的旧版精确 schema 在隔离副本添加固定表，不改原始 ZIP 或当前工作区。

## Consequences

媒体与业务隔离，主服务不引入重型语音依赖；无原始录音持久化、无云端语音 fallback。代价是额外模型安装、独立运行进程、单并发，以及 ASR 文本未经逐轮校对的准确性风险。轮次检测使用 pinned Pipecat adapter，升级必须复验其 fail-closed 行为。

合成音频能验证链路但不能证明真实中文质量。真实模型 smoke 已暴露短指令识别错误，增加短文本人工核对降级；仍需 200 条真实标注语料与耳机/外放测试。入口保持实验状态，不以本次局部验证替代 release gate。当前不支持 TURN 或容器部署。

## Alternatives Considered

1. 继续仅以静音倒计时自动结束：容易把思考停顿当结束，无法覆盖本次自然交互目标；作为原逐轮核对模式继续保留。
2. 云端全双工语音模型直接拥有业务工具：引入语音成本、外部音频传输及工具写入授权耦合，不符合本次本地语音边界。
3. 将 Pipecat/ONNX 直接装入主服务：扩大主服务版本及依赖面，妨碍现有 Python 兼容；改为可选独立进程。

## Related

- [安装、操作与验收限制](../../../voice-runtime/README.md)
- [Python rewrite 契约](../../python-rewrite-contract.md)
- [P0 发布清单](../../p0-release-checklist.md)
