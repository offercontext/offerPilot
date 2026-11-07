# Harness P0 实施与验证记录

日期：2026-10-12。对应[实施方案](harness-evaluation-p0-implementation.md) T1–T7 和 §19 S1–S4；命令见[开发指南](harness-p0-development.md)。实现与针对性验证完成，未进行完整发布验收，报告均保留 `release_eligible=false`。

## 1 交付与快照

- 实施工作树：`D:/Users/yuqi.chen/.codex/worktrees/harness-evaluation-p0/jobAggregation`；分支 `feat/20261012-harness-evaluation-p0`。原工作区未修改。
- 上游代码基线：`5b9605bab343c4f95ede2c0c61ecd47bb7d923a1`。设计来自用户指定的 `harness-evaluation-design` 工作树。
- 最终真实 Harness 运行的候选提交：`3471dd068c70b6a0e03ea6fc4e065455c255b6f4`，工作区干净；之后 `5726ebcc` 仅修正选测报告中 code 3 的行级分类，后续提交仅整理文档。
- bootstrap 参考提交：`388790e6a471628cee173c6abf27a8ad9eb6eb82`，工作树 `harness-p0-reference/jobAggregation`，使用自己的 `.venv/Scripts/python.exe`。该参考保留相同产品修复，移除 evaluation 包、evals 和评估测试，用于证明跨 checkout 启动不依赖目标安装候选评估包；它不是历史产品功能基线，不据此宣称功能改善。
- Windows 11，Python 3.12.13，SQLite 3.50.4；observer/bridge/grader `p0-v1`，schema 1。最终 evaluator hash `757965593d92140841ee3db049ea79966749f3298019655219534e78f0110880`；锁文件 hash `456ac277ffbb355ca96fc68b54d05b104f42fd96b5a0b258decab35ff30456f8`，未修改锁文件。
- 两个 checkout 的 commit、diff/files/lock hash、解释器、实际 import 来源、依赖版本、数据库位置和 SQLite 设置保存在 `run-manifest.json` 与 epoch ready 记录中。后续文档提交不改变已测试快照，也不使旧选测计划可直接复用。

交付包括严格身份与规则契约、独立提交观察与分片封存、确定性 HTTP/HITL 驱动、持久化屏障与同库恢复、A/B compare、`oc eval`，以及 Git 影响分析、pytest/Vitest 实际采集、冻结计划、受控执行和报告对账。未将评估门禁接入完整 release gate。

产品改动只有投递读取规范化修复：naive 时间转换采用 SQLAlchemy committed value，避免读取把模型标脏并触发 `updated_at` 写入；真实字段修改仍正常持久化。前端仅为两组参数化测试标题补充唯一索引。无 schema 迁移、数据 reset 或 HITL 默认值变更。破坏性变化：无。

## 2 最终真实 A/B 与退步检测

命令在实施工作树执行；variants 文件位于被忽略的 `.codex/`，配置上述真实 checkout 和各自解释器。

```powershell
uv run oc eval run --suite evals/suites/harness-p0-v1.json --variants .codex/variants-ab.json --output artifacts/harness/ab-final-3471
uv run oc eval run --suite evals/suites/harness-p0-v1.json --variants .codex/variants-negative.json --output artifacts/harness/negative-final-3471
uv run oc eval compare --baseline artifacts/harness/ab-final-3471 --candidate artifacts/harness/ab-final-3471 --policy harness-p0-v1 --output artifacts/harness/normal-comparison-final-3471.json
uv run oc eval compare --baseline artifacts/harness/ab-final-3471 --candidate artifacts/harness/negative-final-3471 --policy harness-p0-v1 --output artifacts/harness/negative-comparison-final-3471.json
```

| 实际执行 | 结果 | 证据（工作树相对路径） |
| --- | --- | --- |
| 正常 A/B：三项 × 两 checkout × 一次 | planned 6，pass 6，fail 0，incomplete 0；CLI exit 0 | `artifacts/harness/ab-final-3471/`；run `d29ceaf3-89a5-4288-b2b8-6eb53d7150b3` |
| 正常 compare，选择同一 A/B run 的两 variant | 三项 comparable/passing；exit 0 | `artifacts/harness/normal-comparison-final-3471.json` |
| 诊断 candidate `wrong_target`，三项 | pass 1，fail 2，incomplete 0；E04/E06 有实际 witness；CLI exit 1 | `artifacts/harness/negative-final-3471/`；run `e70f3454-bab8-4333-b1bb-5981d700ada8` |
| 正常 baseline 对诊断 candidate compare | E04/E06 均 `new_failure`；故障被检测，exit 1 | `artifacts/harness/negative-comparison-final-3471.json` |

E01 走真实 Runtime HTTP 查询与只读工具，精确范围和零提交 DML 通过。E04 走暂停、编辑批准时间、确认和完成链路，目标技术面试更新为 16:00，其他对象无变化，批准顺序、有效参数、单次效果和保存回执通过。

E06 使用真实 legacy confirm 持久化回执屏障；父进程核对 committed operation 与 assistant/tool 消息后停止原进程，再以同一数据库启动 epoch 2，两次重放返回原 operation/conversation 的保存内容。恢复无模型、工具、primary executor、外部网络尝试或所覆盖业务 DML；未修改租约制造恢复条件。

诊断由独立提交观察器与对应规则识别错误；`faulted` 要有实际 witness，检测标记还要求关联失败规则，不能用其他失败冒充。上述单次运行不提供三次稳定性统计、真实模型质量、费用/token 比较或正式基线晋升。

## 3 观察器与失败边界

最终核心回归包含 `test_commit_observer.py`、`test_observer_crash.py`、`test_verdicts.py`、`test_read_run.py`；实际 HTTP 探针另行执行。

| 探针 | 实际证明 |
| --- | --- |
| O01/O02/O05 | 读取无 DML；合法批准后的 old/new 与顺序；批准文件 hash、HTTP ingress 与提交因果绑定 |
| O03/O04 | 未批准提交再补偿仍留下变化；rollback 不留下提交记录 |
| O06/O07 | 子进程 `os._exit(23)` 后仍能读取已提交记录；绕过 Ledger 的直接 INSERT/UPDATE/DELETE 均观察到 |
| O08/O09 | 缺失/损坏分片或索引不能 pass；其他完整片中的已知失败保留；fail+unknown 仍 exit 1 |
| O10/O11 | SAVEPOINT rollback/batch 生效；no-op DML 留记录，记录数不等于业务效果；恢复 no-op 写也失败 |
| O12/O14 | 观察器开/关不改变合法业务最终状态；锁、日志表丢失、schema/trigger 改动产生 error/gap |
| O13 | 响应尾部提交仍被封存；停机超时保留 incomplete 与计数下界；Windows Job/POSIX group 在控制器死亡后回收所属进程 |

七项真实负例在 `2d9d2ce4` 对应源码运行并保留原始证据：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/evaluation/test_p0_cases.py -k 'negative_control or response_tail or shutdown_failure' --basetemp artifacts/harness/negative-controls-final
```

结果 **7 passed，3 deselected**：wrong_target、wrong_payload、duplicate_effect、unapproved_compensation、bad_receipt 得到预期 fail；late_write 在 `driver_finished` 后真实提交，zero-DML 规则 fail；shutdown_timeout 得到 incomplete，精确计数 null，保留观察下界。每项目录有 `trial-config.json`、`trial-result.json`、封存分片、SQLite 与控制日志。后续恢复身份检查不影响这些 E01/E04 探针；受影响 E06 已在最终两 checkout 与诊断运行重验。旧快照证据不混入新 manifest。

观察覆盖限隔离库 `applications/application_events` 普通 INSERT/UPDATE/DELETE 全列，不覆盖 REPLACE、级联、DDL、其他表/库及物理文件篡改。保护是受信任 checkout 的 Python 评估桥，不能抵抗恶意 native/ctypes 绕过，不声明 OS 沙箱。

## 4 增量选测验证

| 验证 | 实际结果 |
| --- | --- |
| 当前 pytest/Vitest 实际收集 | 6,686 条：6,433 后端参数化 nodeid + 253 前端文件；unclassified 0，`catalog-final-3471.json` |
| 五组后端/十组前端完整归属核对 | 每个当前条目恰好归属一次，agent loop 两文件进入 agent 组；`group-catalog-final-3471.json` |
| Git 多来源、rename/delete、新测试、依赖闭包、直接 p2、未知扩大、marker、快照与报告错误 | Windows 30 passed；报告分类修正后重跑，`selection-final-status.log` |
| 同一实现的平台子集 | Ubuntu/WSL Python 3.12.3、Git 2.43：27 passed，3 deselected；未安装完整 Linux 产品依赖的真实产品 CLI 和两个模拟门禁参数未在该子集运行，已在 Windows 验证 |
| 实际临时仓库 plan→execute | Windows/Linux 各正常行为 exit 0、实际 `value=2` 回归 exit 1；各保留直接 p2 一项，静态检查与实际 pytest 报告对账均执行 |

临时仓库完整计划、日志和报告在 `artifacts/harness/selection-execution-nt/`、`selection-execution-posix/`；平台测试日志为 `selection-posix-final.log`。进程回收探针通过控制器 `os._exit` 与实际子孙存活检查验证，不只模拟返回码。

分类首版保守，除主题样式文件外几乎全是 p0。高风险、映射和选测控制变化强制全量，保留静态/build/smoke/Harness/review/group 检查；未承诺后端耗时减少。分组归属证明全集覆盖，不代表本轮执行了后端全集或整套 Windows 分组脚本。

计划绑定 source/index/untracked/environment、collector、catalog 和两 variant 快照；源码、锁文件或文档变化后须重生成。独立审查字段是调用者 attestation，未认证审查者来源；不能用手填 `approved:true` 代替实际审查。

## 5 回归与静态检查

| 命令/范围 | 结果与快照 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q tests/evaluation tests/test_selection --ignore=tests/evaluation/test_p0_cases.py --ignore=tests/evaluation/test_read_normalization.py` | `3471dd06` 最终核心 77 passed，`core-final-3471.log`；真实 HTTP 运行另列 |
| `tests/evaluation/test_verdicts.py tests/evaluation/test_read_run.py` | 13 passed；public success 正例、错误 conversation/内部 committed/failed 反例，修复前两项红测已复现 |
| applications repo/read normalization、应用 API、事件、scope 与评估相关回归 | 65 passed；真实字段修改仍写入、读取无额外 DML；受影响 repo 范围随后 13 passed |
| `uv run pytest -q tests/test_database.py tests/test_auth_api.py tests/tool_pipeline/test_application_events.py tests/tool_authority/test_scoped_reads.py tests/test_managed_runtime_api.py tests/pilot_runtime/test_confirmation_cutover.py tests/pilot_runtime/test_receipt_delivery_recovery.py tests/test_write_operations.py tests/test_cli.py` | 142 passed；产品代码在 `c97d19e5` 后未变化，沿用该证据 |
| ruff：evaluation、subject、selector、相关测试与 CLI | 最终 All checks passed；product repo 已通过对应检查 |
| mypy：evaluation、subject、selector 和入口 | 最终 21 source files 无问题；报告状态修正单文件再次通过；product repo 等受影响范围此前通过 |
| `npm test` 单次运行，`.codex/frontend-verified.json` | 253 文件、2,428 断言通过；最终对账 missing/extra/duplicate/skip/empty 全 0，`frontend-reconciliation-final.json` |
| `npm run build` | TypeScript/Vite 通过；之后前端仅改参数化测试标题，完整测试已重跑 |
| 真实注册 CLI `oc verify --profile local --static-dir web/dist` | 16 项本地 HTTP/auth/HITL/恢复/CRUD 检查通过，确有 `Verify local passed`，`smoke-delivery.log` |
| `uv build` | sdist/wheel 构建通过；Harness 开发资源按源码 checkout 使用，未声明安装版资源支持 |

证据路径无特别说明时位于 `artifacts/harness/`。较早产品回归输出在本聊天的实际命令结果中；未把不同快照的报告合成全量 gate。现有 Starlette 弃用警告未阻断验证。

## 6 独立审查与交付边界

Harness/selection 分别由未参与实现的 `/root/harness_review`、`/root/selection_review` 独立只读审查。关闭问题包括故障检测错误归因、无真实命令入口却返回 0、门禁缺完成证据、exit 3 分类、replay 身份及公开状态混用。修复后执行受影响回归、真实 A/B、诊断 compare 和报告对账；主任务确认实际命令输出。

最后一行 code 3 状态映射经 ruff/mypy、30 项回归与独立 diff 审查；现有 code 3 参数走缺报告分支，没有有效报告聚合为 3 的专用行级断言。该修正仅统一报告文字，退出码与阻断行为未改变，接受这项非阻断覆盖缺口。

本期未运行完整 `scripts/release-gate.ps1`、后端 6,433 条全集、全部 Windows 分组执行、Docker smoke、完整安装门禁、自然租约恢复、真实模型或三次稳定性验收：本次是 P0 工程实施与针对性验证，未作为发布候选，真实模型不在费用授权及本期范围。门禁均保留，不用增量通过替代发布验收。没有推送、PR、部署或飞书文档改写。
