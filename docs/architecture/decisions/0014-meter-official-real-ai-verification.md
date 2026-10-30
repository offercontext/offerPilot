# ADR-0014: 正式 real-ai 验证的逐发送控制与离线资格验证

状态：离线实现完成，待指定对话复核；付费入口关闭。不是七段验收或发布批准。

日期：2026-10-30。决策来源：维护者授权的 D1–D5 离线补齐任务；本轮实现
由实施方提出，交指定设计评估对话复核，不代替维护者的付费或发布批准。

## Context

正式入口仍是 `oc verify --profile real-ai`；七段及业务断言、真实
`create_app`、Runtime、HITL 和 cleanup 不变。旧固定 10 元账户类和 27 次
TargetedLedger 不能承载新的七段计划，诊断元数据也不能代替回答归档。

实现位于 `scripts/real_ai_control/`，不修改产品代码、评估规则或依赖锁。
当前仅 `python -m scripts.real_ai_control.offline --offline <新目录>` 可启动；
子入口也只接受 `--offline`，强制假凭据和 MockTransport。不存在付费启动
命令。通用模块可供后续冻结执行器复用，离线通过不等于 execution ready。

## Decision

### D1：实际账户与发送账本

- `account.py` 仅复用 `safe_path`、`paid_lock`、`ledger_total`、`atomic_json`；
  不调用 SharedAccount/TargetedLedger，不导入旧预留，不重放扩额分支。
  锁为账户同目录的 `task-budget-paid.lock`。
- 绑定初始账户字节哈希和原累计授权，核对每份历史账本、允许费用、
  守恒、零 refund 和计划指定的全部历史事务根。旧账本只读。
  未闭合 intent、未知 allocation、余额不足或哈希变化拒绝新分配。
- 事务顺序：before → fsync intent → 原子账户更新 → after → fsync receipt。
  中断不回滚、不重放。父死亡使 OS 锁释放后，持久 allocation/intent 仍
  阻止下一轮；不声称死亡后 OS 锁继续存在。
- `journal.py` 保存 schema 3 允许/拒绝哈希链，保持创建时 fd 和文件身份。
  每次追加前后核对完整前缀，fsync 后才返回 ACK 所需行哈希。结算持续持有
  同一 fd/锁，从 held bytes 校验 hash/tail/费用；读取前核对fd/path长度，
  实际 read 最多131073字节，读取后再核对身份/长度，超限即 poison，
  避免外部扩展文件先触发无界内存分配。交易前后继续核验身份；
  不重开路径把截断后的空账本当成零费用。截断、替换、
  链接、部分尾部或持久化失败均禁止续发。
- 64 为全进程允许次数上限；账本总尝试最多65行、131072字节。父端首次
  明确拒绝后不再接受 reserve，客户端停止发送但保留 done/finished 收口通道。
  失败、超时、未知计费及 ACK 后未发送仍保留
  已 allow 费用。只有全树退出且控制/账本/证据完整，未 allow 的分配余额
  才可回到 unallocated；不退还已 allow 的费用。释放前 fsync closure-proof，
  绑定全树退出、原 CLI/控制终态、held ledger、子报告、归档与秘密副本清理。
  close intent 和 closed_runs 均引用该证明哈希，不依赖最后才生成的 result。

### D2：私有管道与逻辑调用

`protocol.py` 限制 UTF-8、单换行帧、131072 字节、严格类型和唯一字段。
请求包含实际 body、非秘密头和真实 Authorization 指纹；父端用私有内存
中的预期 key 重建完整 envelope，不用占位头代验。

`source.py` 分开冻结产品/依赖与控制器 manifest：全部产品 Python、实际
CLI/smoke、隔离/ProcessTree helper、依赖锁、解释器字节与包版本；每次
allowance 前重验。它不是完整系统配置快照，当前 quote 明确为离线夹具。

`channel.py` 的 Binding 绑定 run/plan/source/account/quote/实现。ACK 同时
绑定 Binding、seq、nonce、request hash、plan、费用和预留行哈希。单读
线程按 seq 路由独立 waiter，写锁不跨等待；污染、EOF、超时和错误 ACK 不
重发。终态使用 finished ACK，不依赖标准流包装器关闭立即产生 OS EOF；
父端仍须在终态后观察真正 EOF 和全树退出。

`transport.py` 在 SDK 调用前创建唯一上下文，延续到流迭代、异常和关闭。
每个逻辑调用只尝试一个物理发送，第一次失败也不能重发；SDK role repair
不能借新 nonce 重试。产品原生独立生成/结构修复另建可追踪调用并全部
计费。并发使用 ContextVar 和独立锁。

ACK 上限5秒，耗时计入 elapsed 并从网络剩余 timeout 扣除。更短的产品
timeout/output 不扩大到45秒或8192；流迭代检查逻辑剩余预算。这不修改
历史流式4秒测试。Provider attempt 不等于外部已收到请求。

### D3：双目录与双 listener

`bootstrap.py` 在 app/SQLite 打开前预建两个独立根和两个 listener。目录
按 main、unconfigured 顺序消费；服务按 unconfigured、main 顺序启动实际
uvicorn/app。SQLite 与原网络围栏取交集，仅允许两个精确根；resolve 逃逸
和额外 bind 被拒绝。Windows 私有 socketpair 有局部例外，不开放系统 TEMP。

保留 health 轮询、startup15、原 shutdown30、Knowledge stop(10)/stop(None)、
API waits 和七段顺序。listen 不等于 ready。未配置目录无 key，仍由原代码
断言 `/api/chat` 为503。配置明确关闭 HITL 自动批准、fallback 和 JSON schema。

隔离进程的 HTTPX 使用 trust_env=false，避免环境及 Windows 注册表代理
改变 loopback 路由；这是验证环境差异，不证明历史超时的网络原因。

### D4：全树收口

父级 watchdog 显式冻结，最多900秒，独立于产品阈值，不放宽流式4秒、
Provider45、startup15、shutdown30或 API waits。复用 ProcessTree 的
Windows Job/POSIX 所有权机制，只终止本轮拥有的树。

HTTP thread 退出不等于 Knowledge worker/全树退出。活动服务或 worker
仍在时，目录工厂保留目录并取消自动清理；原业务 cleanup 不改。强杀、
父死亡、控制断裂、尾部不全和所有权未知保留 allocation。完整证据下的
正常产品失败可保守结算，原 CLI 失败不改为产品通过。

### D5：不同层的真实证据

`archive.py` 分开保存 HTTP entity body、SDK choice/chunk 和实际 Assistant
的 content/tool_calls/provider_blocks。tee 不预消费流，保留原顺序、前缀
和 EOF/异常/close；已缓冲响应单独标明 decoded body 层，不声称 TLS wire。

通过受控 loopback 请求 ID 和 ASGI 上下文关联 API、产品调用、逻辑调用、
预留和 Provider ID。`context.py` 在 Runtime.submit/worker_entry 按
turn+generation 逐任务绑定，拒绝的 admission 删除临时映射，replay 保留原
绑定；不全局复制 Thread.start 上下文，避免固定 worker 复用首个 API ID。
无法证明上下文的后台队列保留 API ID=null，不按时间猜测。

单条16MiB，每份 archive 总量256MiB，metadata/body 均受限。父 native 归档
和子 HTTP/SDK 归档各有独立限额，单次合计上限512MiB；源码快照和固定
JSON账证另计。磁盘失败或超限保留前缀并
停止后续模型发送。独立 fault 帧保存阶段、最后成功 ordinal/hash、已提交
前缀及写入结果未知状态；父端单独落盘后保留 allocation。metadata 短写及
摘要前缀变化也拒绝完整结论。原 HTTP/CLI 结果与控制失败分开。

Python text 与 native stderr 均跨 chunk 脱敏并限制字节量；原生 stdout 污染
私有协议会终止控制，不保存原污染帧。key/Authorization/cookie/SDK hidden
参数不写 metadata；脱敏时标 raw_fidelity=false。SDK semantic terminal 与
HTTP raw EOF 分别记录；真实 response 在所有关闭路径显式 close，不为
补齐字节提前 drain 原流。收到 response 即登记到管理器，捕获 hook 抛错也
关闭它；每个 response 单独尝试 close，首次失败不跳过后续 response/inner。
非流式调用也在结束边界清理。SDK 未读尾部继续 unknown，不自动称归档完整。

API/Provider body 和 Assistant 在原 cleanup 前归档。数据库文件在业务
cleanup 前另存并标 live consistency unknown，worker 退出后的副本另标。
秘密配置放在证据目录外的独立 owned 临时根，全树退出后只删除本轮固定
配置副本，原 key 文件不读写；活动目录不提前删除。旧 unknown 不回填。

## Consequences

### 验证与后续激活

`tests/verification_control/` 使用假账户、MockTransport、真实 SDK、私有
管道、owned process、loopback 和故障注入。实际 CLI 探针故意首阶段失败；
余六段保留 not_run，不称为真实七段通过。

后续付费激活仍须绑定冻结实现、独立审查、新鲜普通完整 gate、D57、R39
收口、官方新报价、原明确授权、同一最新共享总账和全部历史事务根。
本轮不预留真实费用、不启动付费 R39/真实七段/Judge、不晋升基线。完整 gate 仍
阻塞；C5 原样回归只是建议，旧人工结论不迁移。

## Alternatives Considered

1. 直接复用旧 SharedAccount/TargetedLedger。未选：固定额度、次数和历史授权
   迁移分支与当前共享账户及七段计划不兼容，调整常量会模糊授权来源。
2. 只检查 retry=0 并在子进程自己扣账。未选：不能阻止 SDK role repair 的
   第二次物理发送，且无法在父死亡、并发或磁盘失败时证明 durable-before-ACK。
3. 只保存现有 audit 元数据和最终数据库。未选：无法恢复逐调用原始响应，
   cleanup 后也不能补齐此前遗漏的 Assistant/SSE，更不能把 null 推成 false。

## Related

- [仓库验收与授权约束](../../../AGENTS.md)
- [问题记录](../../BUGS.md)
- [文档规范](../documentation-rules.md)
