# 岗位匹配、Offer 比较与谈薪地图

[返回业务总图](README.md) · [投递和 JD](applications.md) · [简历与材料](materials.md) · [Pilot](pilot-runtime.md)

核对基线：2026-10-28，`454af3d7`。本页描述当前实现；需求来源沿用 [AGENTS §4](../../../AGENTS.md#4-事实源)。

## 1. 入口与对象

| 用户说法 | 实际入口 | 边界 |
|---|---|---|
| 岗位初筛、值不值得继续、深入分析 | [OpportunityFitReviewDrawer](../../../web/src/components/OpportunityFitReviewDrawer.tsx)、[PilotOpportunityFitV2Card](../../../web/src/features/pilot/PilotOpportunityFitV2Card.tsx) → [opportunityFitReviews](../../../web/src/services/opportunityFitReviews.ts) | V2 review session + triage/deep_review stage；不要混用[简历匹配记录](materials.md) |
| Offer 卡片、薪酬、接受/拒绝 | [OfferCenterView](../../../web/src/components/OfferCenterView.tsx)、[offers service](../../../web/src/services/offers.ts) → [OffersRepository](../../../src/offerpilot/repositories/offers.py) | Offer 状态与 Application 状态分开；工具受限入口核对投递范围 |
| 比较多个 Offer、增加维度 | [OfferCompareDrawer](../../../web/src/components/OfferCompareDrawer.tsx)、[OfferComparisonDimensionPanel](../../../web/src/components/OfferComparisonDimensionPanel.tsx) → [OfferComparisonRepository](../../../src/offerpilot/repositories/offer_comparison.py) | 自定义维度和值；缺失信息显式列出，不猜测取值 |
| 谈薪建议、保存沟通提纲、继续聊 | [OfferNegotiationDrawer](../../../web/src/components/OfferNegotiationDrawer.tsx) → offers service → [OfferNegotiationRepository](../../../src/offerpilot/repositories/offer_negotiation.py) | Proposal 与确认后的 Brief 分开；没有向招聘方发送或自动改价的副作用 |

## 2. 岗位匹配 V2 执行与恢复

HTTP 在 [api.py](../../../src/offerpilot/api.py)：`create_opportunity_fit_review` → [create_triage_v2](../../../src/offerpilot/repositories/opportunity_fit_reviews.py)；`confirm_opportunity_fit_triage` → `confirm_triage_v2`；`create_opportunity_fit_deep_review` → `create_deep_review_v2`。

1. 初筛先冻结来源、保存 session/stage 和生成资格，再调用模型。同 key 的请求要匹配原来源，活跃租约期间返回已有 stage；过期接管递增 generation 并换 provider token。
2. [AI 契约](../../../src/offerpilot/ai/opportunity_fit_reviews.py) `_validate_v2_stage` / `_validate_v2_refs` / `_generate` 校验结构和引用。provider 异常可能留下 unknown，不能一概当成“模型没有执行”。
3. 初筛结果通过当前 JD、有效期、签名和 generation 条件确认；确认凭据一次性消费，重复确认是 consumed 错误。
4. 深入分析必须引用同一 review 下已确认的 triage，继承父阶段冻结快照；不是用这次请求重新拼简历事实。生成前后检查 JD 和所有权，迟到结果不能替换新代。
5. 历史显示查 [opportunityFitHistory](../../../web/src/features/applicationTasks/opportunityFitHistory.ts)，草稿/会话交接查 [pilotOpportunityFitLifecycle](../../../web/src/features/pilot/pilotOpportunityFitLifecycle.ts)。历史旧 schema 与当前可继续操作资格分开。

依据：[岗位匹配设计](../../superpowers/specs/2026-07-21-opportunity-fit-review-design.md)、[Pilot 引导设计](../../superpowers/specs/2026-07-21-pilot-guided-opportunity-fit-design.md)；两者为历史设计记录，当前阶段与凭据以实现核对。

## 3. Offer 比较与谈薪

`structured_offer_comparison` → `OfferComparisonRepository.comparison_payload` 使用选中活跃维度，输出 `missing`。归档维度不能用于新增比较值，但 `clear_value` 仍允许清除历史值。

谈薪链：`preview_offer_negotiation` → `create_offer_negotiation_proposal` → `prepare_or_replay` → [ai/offer_negotiation.py](../../../src/offerpilot/ai/offer_negotiation.py) → `complete_ready` → 用户选块/编辑 → `confirm_offer_negotiation_proposal` → `confirm_proposal`。

- `prepare_or_replay` 保存 Offer、维度与用户诉求快照，并决定重放/等待/接管；模型在事务外执行。
- ready 写入受 revision 和 provider token 约束；最终确认还检查 Offer 来源。模型返回不自动构成用户确认。
- 每个 Proposal 最多一份 Brief。已有 Brief 直接返回；首次保存时验证选中块及编辑范围。
- [pilotHandoff](../../../web/src/features/offerNegotiation/pilotHandoff.ts) 负责继续讨论的上下文；[owner 草稿](../../../web/src/features/offerNegotiation/useOfferNegotiationOwnerDrafts.ts) 负责界面持有状态。不能用新会话覆盖仍待核对的原建议。

依据：[比较与谈薪设计](../../superpowers/specs/2026-08-01-offer-comparison-negotiation-design.md)、[Pilot 交接设计](../../superpowers/specs/2026-09-02-offer-negotiation-pilot-handoff-design.md)。

## 4. 症状、证据和测试

| 症状 | 先查 | 回归入口 |
|---|---|---|
| 初筛重复、确认过期/已消费 | review/stage ID、generation、key、JD 版本、凭据摘要和 expires_at | [匹配 API](../../../tests/test_opportunity_fit_reviews_api.py)、[AI 引用/修复](../../../tests/test_opportunity_fit_reviews_ai.py)、[前端请求](../../../web/src/services/opportunityFitReviews.test.ts) |
| 深入分析用了新简历或旧岗位 | 父 triage 的 snapshot/fingerprint、当前 JD、provider token | 同上；先确认父阶段继承关系 |
| 比较漏值或归档后异常 | Offer IDs、dimension IDs、missing、archived_at | [比较维度](../../../tests/test_offer_comparison_dimensions.py)、[比较 UI](../../../web/src/components/OfferCompareDrawer.test.tsx) |
| 谈薪卡在生成、结果未知、重复确认 | proposal ID、revision、attempt_status、租约、来源 fingerprint、Brief | [Repository](../../../tests/test_offer_negotiation_repository.py)、[API](../../../tests/test_offer_negotiation_api.py)、[安全构造](../../../tests/test_offer_negotiation_safe_construction.py)、[Drawer](../../../web/src/components/OfferNegotiationDrawer.test.tsx) |
| Offer 越权或所属不符 | 普通 HTTP 与 scoped 工具是否走了不同入口 | [Offer API](../../../tests/test_offers_api.py)、[工具管线](../../../tests/tool_pipeline/test_offers.py) |
