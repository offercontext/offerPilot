# Harness P4：有范围、可失效的 Context Contributor

> 状态：实现、来源失效验证、浏览器走查与独立 CR 完成；完整门禁剩余项见总计划 §5。范围来自已批准路线图 §10；用户已授权推进至 P5。

**Goal:** 在统一模型输入预算内消费确认信息和来源明确的历史，不扩大工具权限或改变当前用户意图。

**Architecture:** 领域 Repository 管理真值与生命周期；受控 Loader 在读取边界校验当前有效来源；
Projector 只对已冻结数据做确定性排序、去重和预算。每项能力独立开关与版本，缺失来源不伪装 ready。

## 1. 已核验的基线

- Readiness 已有当前版本、撤回、Evidence 和明确目标面试的 preparation selection loader；普通 Chat 还没有目标绑定。
- Confirmed Memory 无模型、版本、确认、查看、编辑、撤回或删除能力，作为本阶段显式领域前置项目。
- Knowledge Note 有捕获确认，但缺少通用修订/归档/删除及 Note/Evidence 双路消费。
- Older Summary 无持久化、消息范围、失效或生成链路，最后实现。
- 当前服务采用同一工作区认证 token，没有多账户用户身份；使用服务端工作区作用域，不接受客户端自报 owner。
- 当前 Projector 的未来来源固定 disabled；启用要同步契约、来源加载、预算、Manifest 与测试。

## 2. P4A：明确目标的 Readiness

- [x] 使用当前 Application、明确 target application event、显式确认 Signal version IDs 和有效 Evidence。
- [x] 优先接入现有准备/练习 owner；沿用 target lifecycle、source completed、current version、fingerprint 和撤回校验。
- [x] Chat/Haru 没有明确目标时返回 not_applicable，不从 Application 下猜测一个面试，也不做全局注入。
- [x] 有效目标入口需冻结目标和选择；下一次模型投影重新校验，来源变化、撤回或取消后停止注入。
- [x] 内容表述为用户为本次面试选择的准备重点，不能变成长期能力判断。
- [x] 独立 enable/version/budget 与开关对照测试，更新现有禁止未授权桥接的静态门禁。

## 3. P4B：Confirmed Memory 领域前置与消费

- [x] 建立服务端工作区范围的 Memory item、不可变版本、当前指针和幂等 mutation 记录。
- [x] 创建/修改是显式用户确认操作；修改追加版本，CAS 防止旧页面覆盖新值；重试复用原 mutation ID。
- [x] 支持查看当前值与版本、编辑、撤回；撤回立即退出消费。删除清除内容副本，保留不含正文的拒绝重放身份。
- [x] UI 提供个人偏好管理入口，明确保存的是用户确认信息；不自动保存模型推测或练习表现。
- [x] API 不允许客户端声明其他用户或工作区，不接受任意来源身份作为确认凭证。
- [x] Loader 仅取 active/current/confirmed 版本；每次投影校验生命周期和版本，不在 Contributor 中建另一套记忆表。
- [x] 当前请求优先于 Memory；Memory 可影响表达与排序，不证明外部事实，不获得工具授权。
- [x] 同步 models/schemas/repository/API、适用工具契约、前端 services/types/UI 和测试。

## 4. P4C：Knowledge 生命周期与双路检索

- [x] 补齐 Note 当前版本、修订、归档、删除与引用有效性；沿用现有 Knowledge 模型和确认成果。
- [x] 同一受控读取边界独立召回当前 Note 与有效 Evidence；必须能返回未被任何 Note 引用的 Evidence。
- [x] 验证 Source lifecycle、active snapshot、Evidence 归属、Note current version、来源 revision/fingerprint。
- [x] 合并、去重、确定性排序、分配独立预算；引用标识可回读到当前授权来源。
- [x] 归档、删除、更新或撤回后下一次投影失效；权限、归属和引用完整性失败 fail closed。
- [x] 不引入 Brief、完整附件、草稿、自动业务导入或旧 Wiki/vector 路径。
- [x] 测试 Note 命中、Evidence 独立命中、重复引用、陈旧版本、归档/删除、来源缺失与预算裁剪。

## 5. P4D：Older Conversation Summary

- [x] 摘要持久化明确消息范围、源消息 IDs/revisions/digest、生成版本、引用和当前状态，保留原消息。
- [x] 生成有显式触发、独立次数/输入/输出/时间预算与缓存；投影函数内不调用模型。
- [x] 区分用户陈述、来源支持的事实与模型推断；旧摘要不成为当前指令、ToolResult 或授权。
- [x] 消息编辑/删除、范围变化、Conversation 删除或权限失效时禁止消费旧摘要。
- [x] 原历史与摘要覆盖范围去重；生成失败回退有界原消息，不自动反复生成。
- [x] 覆盖缓存命中、不重复生成、删除失效、错误引用、范围变更、失败回退和来源开关对照。

## 6. 共用 Projector 集成与验收

- [x] 新 Contributor contract 使用独立名称、enable/version/budget，明确 ready/not_applicable/disabled/unavailable。
- [x] 静态 policy、当前请求、active control 和工具结果仍优先；工具调用/结果配对不被裁剪破坏。
- [x] 所有可选来源计入同一个 Provider 候选输入预算；不在 Prompt 尾部绕过 Projector 追加内容。
- [x] 来源载入保持有界连接、读取时限和容量；Projector 保持纯、可重复，诊断只记录身份/数量/预算。
- [x] 可选依赖暂不可用允许有界降级；权限、范围或完整性失败绝不降级为可注入。
- [x] 子代理 CR、相关后端测试/静态检查、前端测试/构建、假 Provider 浏览器走查；真实 Provider 由用户安排。
- [x] 记录事实源变更、迁移与开关对照证据；与 P3B/P5 最终整合提交，完整验证与剩余项见[总计划](2026-09-09-harness-p3b-p5-delivery.md)。
