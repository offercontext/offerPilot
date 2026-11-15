# 题库与复习地图

[返回业务总图](README.md) · [面试模拟](interview-mock.md) · [知识系统](knowledge-memory.md)

核对基线：2026-10-28，`454af3d7`。此页只记录当前题库实现；[Knowledge 架构](../knowledge-system.md)中的练习长期方向不自动等于本接口已实现。

## 1. 业务和入口

用户说“刷题、背题、参考答案、已掌握、待复习”时，先查 [QuestionBankView](../../../web/src/components/QuestionBankView.tsx) → [questions service](../../../web/src/services/questions.ts) → [api.py](../../../src/offerpilot/api.py) 的 `/api/questions` 系列 → [QuestionsRepository](../../../src/offerpilot/repositories/questions.py)。

`Question` 是题库条目，`QuestionReview` 是用户评分记录。它们不等于模拟面试的 `MockInterviewAttempt/Turn`，也不代表 Knowledge Evidence 已被核验。

## 2. 正常链与业务边界

| 动作 | 关键符号 | 规则 |
|---|---|---|
| 手动维护题目 | `create_question` / `update_question` / `delete_question` → Repository CRUD | 分类、难度、答案、标签和状态以当前 schemas/参数解析为准 |
| 从复盘生成 | `generate_questions` → [ai.workflows.generate_questions](../../../src/offerpilot/ai/workflows.py) | 当前支持 source=notes，从笔记的 questions 字段拼输入；无来源在加载模型前拒绝。topic 是保存分类，不是另一个知识来源 |
| 模型结果入库 | `persist_generated_questions` → `QuestionsRepository.bulk_create` | `question_hash` 做 NFKC/大小写/空白标点归一化；既查旧题也查本批重复，不是语义去重 |
| 练习评分 | `create_question_review` → `QuestionsRepository.add_review` | HTTP 只接受 1/2/3；评分、次数、掌握状态和下次复习时间一起保存；当前间隔为 1/3/7 天 |
| 待复习和统计 | `list_due_questions` / `question_stats` → `list_due` / `stats` | today_reviews 按 UTC 当天；当前 streak_days 只有今天练过的 0/1，不应解释成已实现连续天数算法 |

## 3. 症状与证据

- 生成零题：区分所选笔记 questions 为空、模型结构无效、全部被文本去重；`count` 和 `skipped` 不是模型输出条数的完整分类，非结构项可能直接忽略。
- 题目丢失或重复：核对 question hash、application_id 过滤与本批请求；别用模拟 Attempt ID 查询。
- “掌握但仍到期”：查看评分、review 时间和 next_review_at；不要以页面当前日期推断 UTC 统计。
- 需要证据支撑的知识问答：转到[知识与上下文地图](knowledge-memory.md)，不要把普通参考答案认作 Evidence。

## 4. 验证入口

[题库 API](../../../tests/test_questions_api.py)、[页面模式](../../../web/src/components/QuestionBankView.modes.test.tsx)、[生成交互](../../../web/src/components/QuestionBankView.generation.test.tsx)、[基础页面](../../../web/src/components/QuestionBankView.test.tsx)。共享结构化模型解析位于 `ai/workflows.py` 的 `parse_json_reply`，与题库持久化分别定位。
