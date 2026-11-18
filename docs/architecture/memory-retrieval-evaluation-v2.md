# 记忆检索候选 V2：系统性分析与冻结独立验证

日期：2026-11-18。运行时基线 `abf9d862`；本轮不改变 `src/`、PR base 或部署状态。
结论：**候选不予接入运行时；PR #73 仍不满足检索质量验收。** 降噪不能掩盖必需召回退化与明确无关内容注入。

## 问题分解

[原26查询评估](memory-retrieval-evaluation-v1.md)及其fixture完全保留，SHA未变。
默认预算下 V1 只用1111.5字节，且预算前宏召回已为74.09%；`score==0` 先丢掉了相关条目，
调排序或扩大预算无法恢复。类别加分又允许无内容证据的偏好进入候选，如饮食偏好进入岗位问题。
同时存在三类不同失败：

- 无词面重合的同义表达及旧未分类记录，在候选阶段消失；不是预算排序失败。
- 具体主题和宽泛任务使用同一分类加分，具体问题仍带入整类；否定/换题没有可靠解析。
- 超长记忆必须整条省略，不能以提高召回为由破坏原子预算与历史数据封装。

## 通用实验（离线，未启用）

代码：[candidate_v2.py](../../scripts/memory_experiments/candidate_v2.py)。无新实体词/样本答案词表、模型调用或外部依赖。

1. 分开候选准入与排序。直接词法/概念证据和有限回补为不同通道；偏好/目标的纯分类回补还须职业领域词面证据。
2. 识别具体主题后优先主题内容证据，抑制无主题命中的整类回补；面试准备保留弱辅助通道。
3. 对明确职业任务，未分类记录只有出现经历叙述语法才可弱回补，不持久化分类、不把未知记录全部塞回去。
4. 回补最多8条，在候选阶段有界；直接匹配仍服从原100条候选上限及既有预算。
5. 对中英词法使用当前候选池文档频率降权常见词；它仍无法召回完全不同的同义表达。
6. 泛化承接语法和明确替换话题的局部句式处理，不扩大无关请求继承准备场景的授权。

这仍是启发式词法实验，不能声称理解否定或语义。候选只接收已满足硬过滤的条目，不读库、不写库；
测试覆盖候选回补上限、未知项不全恢复及无关请求不借用绑定，但验证集揭示其他明确无关反例仍失败。

## 冻结和独立性

- 开发集仍是原26查询，没有重标旧标签；其 relevant 在新增分级指标中作为 required，helpful为空。
- 首次候选在开发集4096召回69.40%、无关17.50%；随后在查看独立集前加入候选准入/回补上限及频率排序，
  最终开发集召回66.24%、无关12.00%。首次源码/结果保留在本地 `artifacts/scenario-memory/quality-v2/`，不冒充最终结果。
- 独立作者 `/root/review_memory` 仅参考fixture格式，未看修订方案或开发集内容，先编写21条合成记忆、30查询。
  作者曾审查V1，因此这是独立于本轮调参的验证，不是完全无背景的外部人工研究。
- 标签分 required / helpful / 其余无关，relevant严格等于前两者并集；冻结后不改标签。
- 候选冻结 SHA256：`9331941f1d4d444384cbb1e2e52fa9d15f3447ffade25ef02c82941586911994`。
- [独立fixture](../../evals/fixtures/memory-retrieval-validation-v1.json) SHA256：`f88ff2070f08119c69941c19de157240d10ee4171ddf47b2ed2d81f8f61a58c6`。
- 冻结后才运行并揭示验证集，未据验证结果继续修改候选。验证结果现已曝光，未来调参须换新独立集。

## 开发集分预算结果

每格为“召回 / 返回条目无关比例”，原标签不变。3个ID排列；三策略同池、同预算封装。

| 预算 | 旧截取 | V1 | V2候选 |
|---|---:|---:|---:|
| 1024 | 23.76% / 78.21% | 50.14% / 23.08% | 51.32% / 5.62% |
| 2048 | 48.62% / 80.13% | 64.68% / 40.96% | 65.62% / 10.94% |
| 4096 | 92.32% / 80.00% | 66.92% / 43.88% | 66.24% / 12.00% |

V1预算前召回74.09%，V2为73.65%；候选准入仍是瓶颈。V2在4096只用863字节，不能解释成预算不足。

## 独立集结果

30查询 × 3预算（1600/4096/8192）× 3个ID排列，每策略270次，合计810次投影。
评价仍是独立作者的合成判断，不是用户流量；不能与开发集数字直接比较，也不设任意质量通过阈值。

| 指标 | 旧截取 | V1 | V2候选 |
|---|---:|---:|---:|
| 预算前必需召回 | 100.00% | 91.30% | 84.78% |
| 预算后必需召回 | 66.22% | 89.25% | 84.78% |
| 预算前helpful召回 | 100.00% | 20.54% | 6.25% |
| 预算后helpful召回 | 50.00% | 15.38% | 5.65% |
| 预算前无关比例 | 85.24% | 80.10% | 67.05% |
| 预算后无关比例 | 86.92% | 77.55% | 65.74% |

8192预算下V2必需召回84.78%、helpful6.25%、无关比例67.05%；相关条目全部可单独装下。
默认4096下V2综合召回31.61%，V1为42.32%；必需召回84.78%对91.30%。不能只选降噪指标宣传改进。
该独立集包含通用表达偏好，大部分普通查询的helpful标签非空；空返回准确率仅适用于两条严格空标签查询，
V1和V2均只有50%，不能用开发集18/18代替这一结果。

## 失败案例（保持原标签）

- v07：月食解释且“不需要联系我的求职经历”，候选仍选9条职业/项目记忆；后置否定未理解。
- v08：只输出8×7结果、不要个性化，仍选Scala经历；当前请求的明确排除要求没有成为候选门槛。
- v17：要求C经验且不要替换成C++，仍选C++；仅保持token身份不足以处理提及但排除的实体。
- v11：往返路上时间、无需迁居的约束，漏通勤偏好，错选排队/事件时间经历；频率信号不能替代语义。
- v21：已绑定准备的自然承接句仍漏两条preparation；泛化语法依然过窄。
- “先列结论，再列待核实项”“中文短句和编号说明”等helpful表达偏好普遍漏选，有限表达词规则未覆盖。

部分具体履历任务、换题任务的无关条目减少，但不足以抵消必需召回损失。独立审查明确建议拒绝接入。
本轮**没有**用验证集词汇补规则、隐藏失败、放宽硬过滤，或把候选替换进产品运行时。

## 复现与验证边界

```sh
uv run python scripts/evaluate_memory_retrieval.py --candidate-v2
uv run python scripts/evaluate_memory_retrieval.py --candidate-v2 --fixture evals/fixtures/memory-retrieval-validation-v1.json
uv run pytest -q tests/test_context_sources.py
uv run ruff check scripts/memory_experiments scripts/evaluate_memory_retrieval.py tests/test_context_sources.py
```

输出逐例预算前/后 required、helpful、missing、unwanted及字节；exit0仅代表评估完成。
独立原始结果保存 `artifacts/scenario-memory/quality-v2/validation.json`，SHA256
`8e22c877ba0ddba7c4c9ecb1bc11c3f4a48edd692acc36978f4b708ad10a83be`（后补输出provenance字段前的原始文件）。
原始数值/标签不改。本轮83项相关测试、ruff、diff及冻结摘要/链接核对通过；独立评估与复审已完成。生产代码未改，因此不重复宣称或执行完整release、Windows、浏览器或付费模型门禁。
既有删除、期限、工作区隔离与校验链未改变；本评估不独立证明这些安全性质或真实模型行为。

下一步应先定义“当前请求明确排除”的通用契约，并用新独立集验证；对无词面重合的召回问题，
单独评估可离线运行的本地语义检索（资源、许可、缓存删除、延迟、范围边界均须核查）。在没有这类证据前，
不继续扩充样本词表、不引入收费依赖，也不恢复全部记忆拼接作为默认降级。

## 隔离语义实验（2026-11-18，未接入生产）

结论：BGE-small-zh-v1.5 与 multilingual-e5-small 在当前 Linux CPU 可运行；固定 top-k 无法可靠拒绝无命中与否定请求，不达到上线标准。V2 仍未启用，PR #73 保持 Draft，不改 base、不合并。这里只测找证据；业务约束不能交由 embedding 决定。

### 实验及资源

在忽略目录 `artifacts/semantic-feasibility/venv` 安装独立 CPU 依赖，产品 pyproject/uv.lock 未改。
模型与 revision、文件大小、LFS SHA256/普通文件 Git blob 摘要固定在
[semantic-models.lock.json](../../scripts/memory_experiments/semantic-models.lock.json)；实际依赖清单见
[semantic-python-freeze.txt](../../scripts/memory_experiments/semantic-python-freeze.txt)。这是本机安装清单，尚非 Windows 可复现锁。
下载器只显式访问锁定官方 revision，先校验临时文件再落盘；推理只读本地文件，拒绝目录额外项，不运行 remote code。

- BGE revision `7999e1d3359715c523056ef9478215996d62a620`；权重 SHA256 `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`。
- E5 revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`；权重 SHA256 `1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`。
- 两个模型官方卡声明 MIT。BGE 按官方查询指令、CLS、L2；E5 中英文均添加 query/passage 前缀、masked mean、L2。统一 float32 CPU、2线程、batch8、max512 token；长文截断未专项评估。[BGE 固定模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/7999e1d3359715c523056ef9478215996d62a620/README.md)、[E5 固定模型卡](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)。

环境 Linux 6.18.57 x86_64 / glibc2.41 / Python3.12.14，9逻辑CPU、约9.7GiB内存；未取得CPU型号或容器限额。PyTorch2.8.0+cpu、Transformers4.57.1。
最终同版 probe 一次测量如下；不是统计性能承诺：

| 指标 | BGE | E5 |
|---|---:|---:|
| 权重文件字节 | 95,827,648 | 470,641,600 |
| 模型目录 du -sb（含tokenizer/README/目录） | 96,405,251 | 493,292,184 |
| 新进程就绪（含校验/导入），秒 | 3.891 | 4.831 |
| 其中文件校验，秒 | 0.331 | 0.800 |
| 21文档+30查询编码墙钟，秒 | 0.827 | 0.754 |
| 同批编码进程CPU，秒 | 1.660 | 1.510 |
| 进程全程峰值RSS，字节（Linux ru_maxrss） | 437,415,936 | 830,599,168 |
| 首查询重复20次中位/p95，毫秒 | 23.20 / 23.76 | 22.00 / 31.77 |

共享实验 venv 的 du -sb 为908,888,740字节（uv缓存/硬链接语义使其不是新增物理磁盘占用）。初次BGE/E5新进程就绪8.191/4.626秒，缓存和共享环境影响明显。没有清除OS页缓存，所以**没有真正磁盘冷启动证据**；重复首查询P95也不是查询集P95。`*-time.txt` 是缺少 /usr/bin/time 的失败日志，未采用。
**Windows 安装包增量、冷启动、CPU/内存全部未测**，不能用权重大小代替。也未执行操作系统断网测试；离线加载由HF离线标志及local_files_only配置保证，不能声称已有网络流量审计。

### 冻结数据与结果

复用上文已曝光的30查询/21记忆合成集，标签未改；这次不能再称未参与开发的新留出集。固定 top1/3/5，不选阈值、不按结果调参；3个ID顺序、3预算，每模型1350条对照记录（含重复基线）。候选池已是有效合成记录，不能以此证明用户隔离等数据库约束。
[完整按预算指标及测量摘要](../../scripts/memory_experiments/semantic-observed-results.json)绑定源码、锁、依赖和数据摘要。下表均为4096字节；required/helpful为有对应标签查询的宏平均召回，噪声为选出条目的微平均无关比例。

| 策略 | 必需召回 | 有帮助召回 | 无关比例 | 无命中正确率 |
|---|---:|---:|---:|---:|
| 旧ID顺序截取 | 71.86% | 50.00% | 87.62% | 0% |
| V1词法 | 91.30% | 19.35% | 79.62% | 50% |
| BGE top1 | 73.55% | 2.68% | 26.67% | 0% |
| BGE top3 | 87.68% | 4.46% | 66.67% | 0% |
| BGE top5 | 92.03% | 8.93% | 77.33% | 0% |
| E5 top1 | 77.90% | 4.46% | 20.00% | 0% |
| E5 top3 | 90.22% | 27.68% | 51.11% | 0% |
| E5 top5 | 94.57% | 38.39% | 64.00% | 0% |

4096预算下各语义top-k预算前后召回相同，平均使用约682–1726字节；不足仍来自候选数与排名，而非预算。1600预算有额外裁剪，原始分预算值保留，不能只展示有利预算。E5比BGE更有希望找证据，但两者都不能独立作为召回策略。

失败例：v06借阅预约优化项目在两者top3均漏m01；v10改善病人体验的开发机会仍漏地域/办公偏好；v16明确不考虑医疗仍带入医疗目标。v08算术与v09原样输出均被强制填满top-k。独立审查还复现E5把明确不要的C++排在C请求首位。没有把模型分数当否定理解、事实真伪或结构化约束。

### 可信关联对照与下一步边界

已核对 `ConfirmedMemory/Version/Metadata`：目前没有 job/application/interview/project 外键，因此不能给记忆伪造业务ID、用相关性标签生成“可信关联”来刷分。
现成可靠路径是 `ReadinessContextBinding` 与 `_raw_scope_and_row`：校验conversation/application ID、scope revision、target_event/resume、frozen version IDs、fingerprint以及活动状态；它选的是显式确认的准备来源，不是全部记忆的关联检索。
本轮实际复验该路径与记忆上下文共92项通过，涵盖清除、冻结版本、来源变更及scope漂移；这不是新语义质量对照。

下一轮先做关联契约：记忆版本到实际存在且同工作区的实体ID，以显式用户确认/可信创建流程建立并记录来源，读取时重验删除、版本、权限及过期；旧记录无关联保持未知。以“已验证业务来源 + 现行安全过滤后的既有对照”单独测业务任务，不默认把所有未知条目塞回去。此方案尚未实现迁移或新外键。
约束执行另测：只有用户确认的结构化字段可成为硬筛选条件；类别、自由文本和embedding均不可自动升格。当前请求的否定仅用于本次任务，不自动修改持久偏好。
可选语义候选未来需异步/限时加载，文件缺失、损坏、异常时保持聊天现行路径，不在线下载、不绕过硬过滤。当前脚本缺模型在导入torch前返回unavailable已验证；**尚未接聊天，因此没有生产超时/降级集成证据**。

### 复现与验证

```bash
uv venv artifacts/semantic-feasibility/venv
uv pip install --python artifacts/semantic-feasibility/venv/bin/python torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python artifacts/semantic-feasibility/venv/bin/python -r scripts/memory_experiments/semantic-python-freeze.txt --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python scripts/memory_experiments/download_models.py
artifacts/semantic-feasibility/venv/bin/python scripts/memory_experiments/probe_semantic_cpu.py --family bge --model-dir artifacts/semantic-feasibility/models/bge-small-zh-v1.5 --fixture evals/fixtures/memory-retrieval-validation-v1.json --output artifacts/semantic-feasibility/bge-final.json
.venv/bin/python scripts/memory_experiments/evaluate_semantic.py --fixture evals/fixtures/memory-retrieval-validation-v1.json --scores artifacts/semantic-feasibility/bge-final.json --output artifacts/semantic-feasibility/bge-ranking.json
```

E5替换family=e5、目录=multilingual-e5-small。首次配置需要网络下载公开模型；编码不调用收费API。新脚本ruff通过；92项pytest通过；独立审查核验14个文件与官方预处理，修复下载落盘顺序及额外文件拒绝，最终重跑两模型成功且分数矩阵与初次完全一致。原始结果留在忽略目录，摘要和复现脚本入库；未跑release gate/Windows/真实用户数据/收费模型。生产代码、依赖、UI、数据库均未改，无破坏性变化。

## 发布边界纠正

独立复审发现之前仅把 V2/语义实验隔离，V1 仍被默认记忆加载直接调用。现已将 loader 的内部 `experimental_memory_retrieval` 参数设为默认 False；生产唯一调用不传，默认保持 #72 行为且不关闭用户记忆能力。显式 True 仅供内部实验，False/省略恢复基线，安全过滤先于两条路径。真实数据库到最终模型表面的新增回归和独立复验通过；相关三文件190项通过，ruff及loader mypy通过。个性化禁止和 C/C++ 排除仍未解决，这不是质量验收通过；保持 Draft。
