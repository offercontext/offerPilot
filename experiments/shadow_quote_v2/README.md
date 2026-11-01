# Shadow quote v2：端到端离线候选

本目录验证新协议的完整离线路径，不是产品 guard，也不是可运行的付费入口。
没有 provider 调用、环境变量或密钥读取、网络 transport、自动重试或线上配置。
原型保留旧协议、原始请求、validator和失败结果不变。独立线上适配位于
`scripts/companion_ai_validation/shadow_quote_driver.py`，不是本目录的模拟入口。

## 为什么是新版本

旧 run 40812959831 的首条响应给出非空引用，但 offset 是0/0，结构校验失败，
其余五例未尝试。累计为49次、估算0.414284元，未结预留为0。本目录不会修正
该原响应，更不将旧批次的剩余五例视作可继续调用的授权。

v2 的目标位置由程序从原六例固定数据读取，模型仅返回 `target_id`，
不再回抄或计算 target offset。每条 evidence 仅返回 `source_id` 与逐字 `quote`，
程序用指定来源内唯一精确匹配计算 Python Unicode codepoint、end-exclusive 范围。
这是显式的新协议，不兼容的旧输出不会被自动迁移或修复。

模型输出字段严格为：`protocol`、`target_id`、`verdict`、`evidence`、`rationale`。
protocol固定`shadow-quote-v2`；verdict保留supported/contradicted/unknown/not_factual/ambiguous。
证据最多4条，理由最多500字符；supported/contradicted必须有证据。
程序绑定固定case、target与本次输入哈希，拒绝错误ID、重复JSON键、额外字段、工具要求。
它只能防止明显串例或结构损坏，不能证明模型认真读过该输入或正确理解目标。

## 定位与语义边界

引用非空、最多128 codepoints；指定来源必须存在，来源ID唯一。
零匹配、多匹配（含重叠）、不完整Unicode字素簇均拒绝，不规范化、不模糊匹配，
不选第一处。代码点位置不是UTF-8字节、UTF-16 code units或字素簇序号。
过长引用拒绝，短而唯一的整段或词语仍可能无关、过宽或断章取义；这些必须人工评分。
定位成功不等于语义正确，JSON schema通过也不是可靠性的证明。
s01的ambiguous是已有盲标的合理备选，不能自动当错；s04的原分歧同样不改。

## 离线链路与证据

固定prompt/schema → 六例请求字节与输入hash → 内置fake响应 → 原始字节捕获 →
严格解析/唯一定位/验证 → 逐例报告和模拟账本。失败即停，后续not_attempted，
不补答、不重试、不追加第七次；未知用量保留模拟3元预留并停止。
输出统一标记SIMULATED，fake token数量与费用不是新增实际消耗，也不写真实账本。
49次/0.414284元基线由固定第五批原始ZIP核验，前四批原件仍由旧harness保留。

fake输出是程序设计的结构样例，成功率没有模型质量含义。输出目录必须新建，
不得重开或覆盖前一次证据。本原型不是生产级断电/磁盘故障可恢复事务系统；
生产适配必须继续采用已有durability、预算、准入、本人审批和历史完整性保护。

## target变化的取舍

固定target_id避免模型数错/抄错offset，也缩小了输出表面积；目标范围本身仍可能
由实验作者选错或覆盖不足。程序只审固定目标，不可推断未选草稿其他部分通过。
若未来需要模型发现新目标，那是另一种任务，不能靠本协议暗中扩展。

## 独立线上适配与授权边界

2026-10-30 用户已另行批准最多六次独立 v2 批次，截止 2026-11-02 00:00 UTC，
不是续跑旧五例。线上适配与五批真实账链位于 `scripts/companion_ai_validation/`，
仍须完整 gate/CI、新 H/R 与变量回读、本人环境审核。此目录的 fake CLI 仍只离线，
绝不因批准而调用 provider 或将模拟账本升级为真实账本。
模型遵从率、语义评分及产品效果须在实际运行后分别审查，当前没有新证据。
详见该目录 README 和本目录 experiment-plan.md。

## 运行离线演练

使用仓库已锁定的Python环境，在仓库根目录运行：

```bash
PYTHONPATH=src:. python -m experiments.shadow_quote_v2.harness --output-dir /tmp/new-v2-demo
```

目录必须尚不存在。可用`--fault unknown`或`--fault malformed`等固定故障演练；
只有六例全部结构通过时CLI返回0，否则返回1，报告仍保存且语义质量标记始终false。
没有`--live`或可注入网络发送器参数。输出`requests.jsonl`、`responses.jsonl`、
`ledger.jsonl`、`report.json`全部是模拟证据，不可提交作真实provider用量。
模拟成本使用固定测试数据，不读取或虚构provider usage；不改变真实0.414284元余额。
