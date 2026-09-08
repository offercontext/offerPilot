# P1 动作展示施工约定

状态：已实现并完成相关验收，2026-09-08。依据交接方案 `5b3bbb2` §6、§15。
基线为 `origin/main bf79ca8`，P0 前置提交 `608272c`；不新增 Timeline 或执行协议。

## 冻结接口

所有 JSON 字段使用 snake_case。`ActionPresentationV1`：

- `schema_version: 1`、`source_kind: agent | product_action`、`operation_id: string`。
- `source_revision: string`、`presentation_revision: string` 为不透明来源与展示指纹，不是时间游标。
- `title: string`、`target: string | null`、`summary: string`、`source_label: string`。
- `decision: undecided | approved | modified | rejected | cancelled | expired | not_applicable | unknown`。
- `execution: not_started | running | committed | failed | unknown`。
- `evidence: verified | incomplete | unavailable`。
- `undo: unsupported | available | running | undone | conflict | unknown`。
- `available_actions: Array<approve | modify | reject | undo | refresh>`，仅为能力提示，不能用于授权。

拒绝/取消/过期不能与已提交组合；撤销后的原执行仍为已提交。证据缺失时不得推测成功。
P0 回执原文复用可信交付，不再次解释。旧记录无法证明批准方式时为 unknown。

`PilotTurnItemV1` 包含 `schema_version: 1`、`item_id`、`kind`、`message_id: number | null`、
`operation_id: string | null`、`content: string`、`action: ActionPresentationV1 | null`。
kind 限 `user_message | assistant_message | action | error_info | run_boundary`。
消息身份为 `message:<id>`；操作为 `agent_operation:<operation_id>`。
顺序来自现有消息顺序与操作关联；没有可靠关联的旧记录不猜测合并。

只读接口：`GET /api/chat/conversations/{id}/presentation` 返回
`{schema_version: 1, conversation_id, items: PilotTurnItemV1[]}`；
`GET /api/product-actions/{operation_id}/presentation` 返回 `ActionPresentationV1`。
显示读取不调用 Provider、executor、确认、补偿或有副作用的恢复。

## 实施与验证

1. 后端两个 Builder 复用 Ledger/原消息/Pending 与 Product Action bundle，验证来源完整性和当前来源有效性；复用现有 Metadata 文案，不复制工具能力字典。
2. 前端共享 ActionCard，Pilot/Haru 共用 Controller 的展示快照、请求归属和原确认通道；Product 页面保留现有 owner draft/proof 与原决定/Undo 通道。
3. 不透明 revision 只判相等；异步读取用请求 generation 丢弃旧响应，不能按 hash 字符串排序。未知 schema 安全最小展示并禁用操作。
4. 消息键使用持久 ID；未保存增量使用明确临时 ID，保存后以服务端快照替换。操作待确认到终态复用同一 ID。
5. 测试状态组合、来源冲突/删除、敏感字段隔离、P0 一致性、旧版本、重复读取零执行；前端测试跨入口状态、过期响应、未知结果与按钮禁用、外壳切换不重复请求。独立 CR 后运行相关测试、类型检查、构建与浏览器走查。

## 数据与回滚

P1 按需读取既有事实，不新增表、不改确认凭据。展示部署回滚可停止使用新读取接口，
保留 P0 兼容回执读取与恢复；不能降级到不认识 P0 策略的旧执行器。
不承诺页面断线后继续执行；该能力属于 P3B。破坏性变化：无。

## 验收记录

- 独立子代理 CR：Product 展示及不可变历史证明、Agent Undo 只读验证、前端刷新与请求归属
  均完成，无剩余阻断问题；来源软删除和输入证明校验问题已修复并回归。
- 真 API：`pytest -q tests/test_action_presentation.py`，17 passed。覆盖修改确认回执、
  Legacy 决定、来源失效、篡改、四类创建撤销、Product 决定及撤销后历史证明；
  重复展示 GET 断言无 DML、无新增模型调用，缺失身份返回 404。
- 前端相关回归：21 文件、274 passed；最新刷新稳定性与两种外壳回归：4 文件、25 passed。
  `npm run build` 通过 TypeScript 与 Vite 构建，保留现有大 chunk 提示。
- `ruff check src tests/test_action_presentation.py` 通过；
  `mypy --platform win32 src` 通过，171 源文件。Linux 默认平台检查遇到现有 Windows
  keyring 的 ctypes 类型差异，按项目 Windows 目标平台重验通过。
- Coordinator、Product compensation、compensation registry 联合回归 153 passed、1 failed：
  20 路撤销并发偶发 `readiness_signal_version_integrity`，立即独立重验 1 passed，
  再完整复跑 `tests/product_actions/test_compensation.py`，85 passed。
  失败调用链位于本次未改的 Product compensation，未调用新增展示 Builder 或修改的
  历史输入证明读取方法；该并发风险保留记录，不以一次重验掩盖首次失败。
- 内置浏览器使用独立临时数据库和假 Provider：待确认卡片、只读刷新、修改后批准回执、
  Pilot → Haru 切换、原撤销入口及已撤销终态通过。主模型和标题模型计数均保持 2，
  展示、切换和撤销没有新增模型调用。Product 页面由组件测试与真实 API 集成测试覆盖。
- 全量前端曾运行：236 文件通过、4 文件失败（2260 tests passed）。其中新增导入影响
  ApplicationDetail 图标 mock，已通过抽出纯解析模块修复并回归；性能用例并发运行超时，
  独立重验 133 passed，审计 20.4 秒。历史 desktop 范围 gate 要求工作区无非 web 改动，
  提交后干净工作区复验 9 passed。剩余 `workspaceDrilldown` 对现有 OfferCompareDrawer 的 `<Drawer`
  约束失败，已核对基线同样包含该代码，本次未修改，不宣称全量前端通过。
- 未运行 P1 完整 release gate、Docker、安装及真实 Provider 验收：本次为 P1 功能交付，
  未发布部署；完整发布验收仍需另行执行。P2—P5 未包含在本次变更中。
