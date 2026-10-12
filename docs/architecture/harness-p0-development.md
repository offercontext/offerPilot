# Harness P0 开发与增量选测

实现依据：[P0 实施方案](harness-evaluation-p0-implementation.md)。Harness T1–T7 与选测 S1–S4 是两套独立验收；成功报告始终带 `release_eligible=false`。完整发布仍使用原 release gate。

## 隔离评估

在源码 checkout 中执行 `uv sync --locked`。每个被测 checkout 都要有自己的 `.venv`。运行器固定使用调用方的 bridge、observer、fixture 和 grader，通过目标 checkout 的解释器导入目标产品；目标版本不需要安装 evaluation 包。

先创建 variants JSON，路径换成实际绝对路径：

```json
[
  {"variant_id":"baseline","checkout":"D:/work/reference","interpreter":"D:/work/reference/.venv/Scripts/python.exe"},
  {"variant_id":"candidate","checkout":"D:/work/candidate","interpreter":"D:/work/candidate/.venv/Scripts/python.exe"}
]
```

POSIX 解释器为 `.venv/bin/python`。两个 checkout 路径必须不同；单版本配置只使用 candidate。单版本三条、A/B 六条 PlannedTrial 在启动前冻结，每项 deterministic 一次。运行期间不要修改源码、暂存、依赖或配置；诊断使用新输出目录。

```powershell
uv run oc eval validate --suite evals/suites/harness-p0-v1.json
uv run oc eval run --suite evals/suites/harness-p0-v1.json --variants .codex/variants.json --output artifacts/harness/p0-001
uv run oc eval compare --baseline artifacts/harness/p0-001 --candidate artifacts/harness/p0-001 --policy harness-p0-v1 --output artifacts/harness/comparison-001.json
```

同一 A/B run 可作为比较器两侧；两个独立 candidate-only run 也可通过参数指定比较角色。同一 candidate-only run 不能充当 A/B。比较核对 suite、spec、fixture、observer/bridge/grader、评估器代码、时区、依赖锁、目标 Python/SQLite/dependencies 和控制环境。绝对解释器路径可以不同；其他实验变量目前仅允许产品快照。

输出包括 `run-manifest.json`、每项 `result.json`、`summary.json`、`comparison.json` 和 Markdown 摘要。原始 HTTP、token、确认参数、业务数据库和控制日志位于 `private/`，分享报告时不要分享整个目录。`compare` 会验证 trial 身份并回读封存片段重新评分；固定评估器快照不可用时拒绝换代码重评分。

E01 查询真实工具结果和最终回答，要求覆盖表零提交 DML。E04 在 Runtime 确认卡中把提案 15 点编辑批准为 16 点，核对接收顺序、最终参数、单次业务效果和保存回执。E06 在 legacy 回执真实持久化后杀掉服务，复用原数据库重启并回放两次；要求保存回执稳定、零模型/执行器/工具调用及零覆盖表 DML。

候选可配置业务负对照 `fault`：`wrong_target`、`wrong_payload`、`duplicate_effect`、`unapproved_compensation`、`bad_receipt`、`late_write`。这类运行标为 diagnostic，保留独立计划，不进入正常 suite 分母；必须观察到故障候选失败才算检出负对照。`shutdown_timeout` 是停机探针，预期 incomplete 而非业务 fail。禁止 baseline 注入故障。

退出码：0 操作完成，1 已知必需规则失败，2 配置/能力/不可比错误，3 证据不足或执行不完整。已知失败优先于其他错误；0 不表示发布通过。`validate` 的 0 只表示配置有效，不启动产品。

## 观察与保护边界

持久触发器只装在每项私有 `data.db` 的 `applications/application_events`，覆盖普通 INSERT/UPDATE/DELETE 的所有列，回滚不会留提交记录；序号表示观察顺序，不表示事务数。普通 no-op UPDATE 也记录 DML，业务效果按 old/new 差异判定。REPLACE、级联、DDL、其他表/库和物理文件篡改不在声明覆盖内；schema/trigger 改变或日志缺口使完整性规则 unknown。

服务退出并验证所属子孙进程回收后才封存 SQLite backup。Windows 使用 Job Object；POSIX 使用独立 process group 和控制器死亡 watchdog。停机、ACK、故障屏障或回收失败保留 gap，不准判 pass；不完整计数记录观察下界和 null 精确值。

没有真实 provider、费用或 token 消耗，子进程不继承供应商凭据，默认 HITL 保持开启。网络保护覆盖 Python socket、UDP、DNS 审计及外部进程启动，只允许本 trial loopback 和标准库内部 socketpair；违规先持久记录再阻止并判失败。这是受信任 checkout 的评估桥，不是抵抗恶意 native/ctypes 代码的 OS 沙箱。

## 增量选测

```powershell
uv run python scripts/select_tests.py validate
uv run python scripts/select_tests.py plan --base origin/master --profile dev --output artifacts/test-plans/dev-001 --harness-variants .codex/variants.json
uv run python scripts/select_tests.py execute --plan artifacts/test-plans/dev-001/selection-plan.json --review-evidence .codex/review.json
```

base 必须是已存在的本地 ref；命令不自动 fetch。变更基线和显式 Harness 实验 baseline 可以不同，均记录真实 SHA。Harness candidate 必须是本次计划 root。计划冻结 committed/staged/worktree/untracked、rename/copy 两端、内容/模式、index、锁文件、环境和实际 pytest/Vitest catalog；执行前后变化要求重生成。

`dev` 为直接测试、依赖消费者回归及运行面 p0 的并集；直接/关联 p2 仍必跑。`merge` 加全局 p0；`full` 收集全集。映射、采集器、分组、选测控制及高风险规则扩大全量，并保留 build/static/smoke/Harness/独立审查检查。无法提供两 checkout、静态资源、必要平台/授权或独立审查时显示 blocked，不删除必跑项。

分类事实在 `tests/selection-map.json`。首版保守将几乎所有文件设为 p0，只有无安全写入的主题样式测试设为 p2；没有承诺后端测试数量明显减少。降低优先级必须按失败后果逐文件审查。pytest marker 支持有效 `-m p0`；新测试必须登记，冲突分类、失效路径和收集失败为配置错误。未知分类保守纳入并报告。

前端由安装版本 Vitest 的实际列表发现文件，执行整文件并核对唯一 assertion ID；零断言、todo/skip、重复/缺报告不能通过。后端按含参数化 ID 的 nodeid 对账。五组后端、十组前端的 `group_catalog` 检查验证当前全集恰好归属一次；该检查是收集/分组证据，不等于已经执行全量测试。

独立审查输入为调用者提供的 attestation：`worktree_fingerprint`、`approved`、`reviewer_id`、`evidence_ref`。它不认证审查者身份；应关联实际外部审查记录，不能用手写 `approved:true` 代替 AGENTS 要求的独立审查。自动执行器不提供审批或真实模型授权。

计划、日志只写被忽略的 `artifacts/test-plans/` 或仓库外，不覆盖已有目录。执行报告单列 planned、实际执行/通过/失败/跳过、blocked/error 检查；已知测试失败不会被损坏报告、超时或清理异常覆盖。0 仅表示该冻结计划满足要求，仍不替代完整 release gate。
