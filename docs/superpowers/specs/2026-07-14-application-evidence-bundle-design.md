# Application Evidence Bundle 设计

日期：2026-07-14
状态：已完成设计讨论，等待规格评审
范围：下一代能力；不改变 v0.1 release scope。

## 1. 问题与决策

OfferPilot 当前的 Application、Resume、JD analysis 和 Material Kit 都可以继续编辑。仅以 `status=applied` 或 Material Kit 的 `submitted` 标记，无法回答“用户当时确认投递了什么”，也无法让后续面试准备、结果归因或校准安全地引用历史材料。

本设计新增独立、追加式的 **Application Evidence Bundle**（投递证据包）。它在用户明确执行“确认已投递”时，复制 OfferPilot 内部已有来源为不可变快照；它记录的是**用户确认的提交版本**，不是招聘平台回执，也不声称能证明招聘方实际打开过该材料。

核心不变量：

> 每一份用户确认的投递，都能解析到确认时的岗位来源、JD、Resume 和 Material Kit 内容；后续修改来源对象不能改变该历史记录。

## 2. 目标与非目标

### 2.1 目标

- 为同一 Application 保存零到多份、按时间排序的用户确认投递快照。
- 只接受 OfferPilot 内部的 Resume、JD snapshot 和 Material Kit 作为第一代来源。
- 在确认前让用户核对来源，在确认时检测来源是否已变化。
- 原子创建证据包、必要的状态推进和 `application_events` 时间线事件。
- 提供可读历史与完整详情，但不提供 in-app 更新或删除能力。
- 保持 `application_events` 的 `event_type + subtype + tags` 领域契约。

### 2.2 非目标

- 外部 PDF、邮件附件、招聘站回执、浏览器抓取、自动投递或 credentialed scraping。
- 对材料做 PDF render、ATS 验证或上传外部制品仓库。
- AI 自动校准、自动面试准备，或将证据包立即暴露为 Pilot 写工具。
- 全局制品/版本图谱、跨 Application 去重或数据迁移时的猜测性补全。
- 将 Material Kit 的可编辑状态当作提交事实。

## 3. 方案选择

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 冻结现有 `ApplicationMaterialKit` | 不采用 | 它是每个 Application 一份的可编辑草稿，无法自然表达多次投递与历史版本。 |
| 新增独立 `ApplicationEvidenceBundle` | **采用** | 将确认时复制的事实与可编辑来源分离，支持追加历史且不改变现有 Application 语义。 |
| 先建通用制品版本图谱 | 暂缓 | 会扩大到全局附件、版本和权限基础设施，超出最小闭环。 |

## 4. 数据模型

新增表 `application_evidence_bundles`。它使用追加式记录；业务层不实现更新或删除方法，也不暴露 `PUT` / `PATCH` / `DELETE` API。

| 字段 | 说明 |
| --- | --- |
| `id` | 自增主键。 |
| `application_id` | 指向 Application；正常应用删除是软删除，证据包随其隐藏。物理隐私清除留给未来专门流程。 |
| `sequence` | 同一 Application 从 `1` 开始递增；与 `application_id` 组成唯一约束。 |
| `submitted_at` | 用户确认的实际投递时间，要求 RFC3339 时区时间；可为历史时间，不能为未来。 |
| `confirmed_at` | OfferPilot 创建快照的服务器时间。 |
| `confirmation_kind` | 第一代固定为 `user_asserted`，显式区别于未来可能的 `platform_verified`。 |
| `idempotency_key` | 前端为一次确认生成的 UUID；与 `application_id` 组成唯一约束。重复请求返回原记录。 |
| `snapshot_json` | 规范化 JSON，保存下面定义的完整不可变快照。 |
| `bundle_sha256` | `snapshot_json` 的 canonical JSON UTF-8 SHA-256，用于审计与展示。 |
| `created_at` | 数据库创建时间。 |

建立 `application_id` 索引，并让同一 Application 的详情按 `sequence DESC` 查询。`ApplicationEvidenceBundle` 可以保留来源对象 ID 作为 provenance，但详情和后续消费者必须以复制内容为准，不能回读当前对象替代快照。

### 4.1 `snapshot_json` 形状

```json
{
  "schema_version": 1,
  "application": {
    "id": 42,
    "company_name": "示例公司",
    "position_name": "后端工程师",
    "job_url": "https://example.test/jobs/42",
    "source": "web"
  },
  "jd": {
    "text": "确认时的 JD 原文",
    "sha256": "…",
    "jd_analysis_id": 9
  },
  "resume": {
    "resume_id": 7,
    "title": "后端简历",
    "content_json": { "…": "…" },
    "sha256": "…"
  },
  "material_kit": {
    "material_kit_id": 13,
    "content_json": { "…": "…" },
    "sha256": "…"
  }
}
```

hash 规则固定为：对象 JSON 先解析，再以稳定键排序、紧凑分隔符和 UTF-8 序列化；JD 使用原始 UTF-8 文本；整包 hash 覆盖 `snapshot_json`。非法 JSON 不静默降级为字符串，不能形成可确认的证据包。

## 5. 创建前提与确认流程

确认入口只使用现有 Application 的内部 Material Kit。服务端预览和确认时都必须检查：

1. Application 存在且未软删除；
2. 存在属于该 Application 的 Material Kit；
3. Material Kit 关联一个未删除的 Resume；
4. `jd_snapshot` 去除空白后非空；
5. Resume 与 Material Kit 的 `content_json` 都是有效 JSON object。

流程如下：

```text
读取确认预览 → 用户核对来源与投递时间 → POST 确认
                                            ↓
                       再次读取来源并比较 preview hashes
                                            ↓
      原子写入 Evidence Bundle + custom/submission_confirmed event
      （若当前为 pending，则同一事务推进为 applied）
```

`pending` 确认投递时才推进为 `applied`；`applied`、`written_test`、`interview`、`offer` 与 `closed` 不得回退。若需要补录历史投递，用户仍使用相同确认动作并填写过去的 `submitted_at`；系统不因看到旧的 `submitted` 标记而自动生成快照。

事务内新增的时间线事件使用：

- `event_type=custom`
- `subtype=submission_confirmed`
- `tags` 至少包含 `submission_evidence` 与 `bundle:<id>`

事件只是时间线投影；证据包才是提交内容的权威来源。即使事件以后被编辑或删除，也不能改变快照内容。

## 6. API 契约

| Endpoint | 行为 |
| --- | --- |
| `GET /api/applications/{id}/evidence-bundles/preview` | 返回当前内部来源摘要、hash、是否可确认及不可确认原因；不写数据。 |
| `POST /api/applications/{id}/evidence-bundles` | 请求含 `submitted_at`、`idempotency_key` 和预览中的 `expected_bundle_sha256`；创建或幂等返回证据包。 |
| `GET /api/applications/{id}/evidence-bundles` | 返回按 `sequence DESC` 排序的轻量历史，不返回大文本快照。 |
| `GET /api/applications/{id}/evidence-bundles/{bundle_id}` | 返回完整、不可编辑快照。 |

新增 read endpoint 是为了让 hash 计算、JSON 校验和来源核对都只由服务端实现，避免前后端 canonical JSON 规则漂移。确认请求的 `expected_bundle_sha256` 与确认瞬间重新构造的 hash 不同，返回 `409`，用户必须重新核对。

错误契约：

| 情形 | 结果 |
| --- | --- |
| Application 或嵌套 bundle 不存在/已隐藏 | `404 {"error": "…"}` |
| 来源缺失、非法 JSON、未来投递时间或错误 UUID | `422 {"error": "…"}` |
| 预览后来源发生变化 | `409 {"error": "提交材料已变化，请重新核对"}` |
| 已使用相同 `(application_id, idempotency_key)` | `200` 返回最初成功创建的 bundle，不重复写事件。 |

## 7. 界面与兼容策略

第一代入口位于现有 Material Kit 界面：

- `draft` 与 `ready` 保持可编辑准备状态；
- 将直接选择 `submitted` 替换为“确认已投递”动作；
- 确认弹窗展示 Application、JD、Resume、Material Kit 的来源摘要、hash、实际投递时间和 `用户确认，非平台回执` 标签；
- 缺失前提时禁用确认按钮，并列出缺失项；
- 确认成功后显示“已确认投递 N 次”、最近确认时间与只读历史详情。

存量 `ApplicationMaterialKit.status=submitted` 不自动转换，因为无法知道当时的具体内容。界面将其标注为“旧投递标记，缺少证据快照”，并提供填写历史时间后重新确认的入口。旧 API 读取该状态仍可兼容；新界面和新写入不再把它作为提交事实。

## 8. 可靠性、隐私与边界

- 前端为每次确认生成 UUID 幂等键；网络重试或重复点击不会制造多份记录。
- 所有写入在同一数据库事务中完成，任一步失败都回滚，不能留下只改状态或只写事件的半成品。
- 快照只能使用本地已有字段；本能力不会访问招聘网站、上传文件或调用模型，因此不新增数据出境。
- 不向 Pilot 暴露写工具。后续若新增只读查询，必须明确返回 `confirmation_kind`，不得把 `user_asserted` 描述成平台验证。
- 证据包的不可变性是应用与 API 契约；本迭代不承诺抵御直接操纵本地 SQLite 文件的设备所有者。

## 9. 验收与测试

后端测试至少覆盖：

1. 确认成功会复制来源内容、保存各级 hash 和 bundle hash；
2. 确认后修改 Resume、JD 或 Material Kit，旧 bundle 详情保持内容不变；
3. `pending` 推进为 `applied`，更后阶段或 `closed` 不回退；
4. 写入 bundle 和 `custom/submission_confirmed` 事件的原子性；
5. 缺失来源、非法 JSON、未来时间、hash 冲突与 idempotency 重试；
6. 同一 Application 的 sequence 递增，旧 `submitted` 标记不产生自动快照；
7. 不存在 update/delete route，隐藏 Application 的 bundle 不可读取。

前端测试至少覆盖：

1. 预览来源和 `user_asserted` 标签可见；
2. 缺少 Resume、JD 或 Material Kit 时禁用确认并解释原因；
3. hash 冲突后要求刷新预览；
4. 成功后刷新历史计数和详情；
5. 旧 `submitted` 标记显示迁移提示，而非已验证投递。

完成标准是上述不变量和测试通过，而不是新增确认按钮或快照行本身。指标只在后续产品实验中定义：证据包覆盖率、来源冲突率、历史材料引用覆盖率和用户主动更正率；不以生成量或申请量作为成功代理。
