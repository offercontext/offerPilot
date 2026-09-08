# 简历事实体检设计

**状态：** 已复审通过

**日期：** 2026-08-06

**实现基线：** `origin/main@b4363b0`

**目标分支：** `feat/20260806-resume-evidence-audit`

## 1. 背景

OfferPilot 已经具备简历库、主简历与派生简历、材料 Proposal、人工确认和证据门控。当前不足不是再增加一个自由生成入口，而是用户在调用 AI 前缺少一份稳定、低成本、可以解释的简历检查清单：哪些核心结构为空、哪些经历缺少可核验的结果或规模信息、哪些内容重复，以及哪些问题仅凭当前结构化数据无法判断。

本设计借鉴以下开源项目的机制，但不复制其产品结论：

- `ResumeSkills` 将 ATS、bullet、量化和版本管理拆成独立任务；OfferPilot 借鉴任务拆分，但拒绝 ATS 总分、岗位匹配百分比和估算未知数字。
- `career-ops` 优先使用确定性检查和完整性校验；OfferPilot 借鉴“先做低成本检查，再决定是否需要 AI”，但不增加招聘平台扫描、表单填写或自动投递。
- `ai-job-search` 强调事实来源和实际提交版本；本切片只检查当前简历，不建立投递档案，也不修改来源事实。

本功能与正在开发的 Application JD Versions 并行。它只读取当前前端已经加载的 `Resume`，不读取 JD，不修改后端、数据库、API、服务层或下游 Proposal，因此应保持近乎零合并冲突。

## 2. 第一性原理与产品边界

简历体检能可靠回答的只有三类问题：

1. **可观察：** 当前 Resume JSON 中是否存在某个字段、数组或原文片段。
2. **可重复：** 相同输入是否始终产生相同检查结果。
3. **可解释：** 每条提示是否能够回指到字段路径或明确说明缺少哪个字段。

简历体检不能可靠回答：

- 招聘方实际使用了哪种 ATS；
- 简历一定会不会通过 ATS；
- 某个岗位与候选人的匹配概率；
- 未被用户提供的数字、影响或成果；
- PDF 字体、表格、图片、页眉页脚和真实分页是否合适，除非未来引入独立的文件渲染检查。

因此，本功能只输出“已具备”“建议检查”“无法判断”，不输出分数、等级、通过率、排名或录用判断。

## 3. 用户目标

用户在简历详情中打开“简历事实体检”，立即看到一份只读检查结果：

- 当前已经具备哪些核心信息；
- 哪些结构化内容为空或明显异常；
- 哪些经历可以向本人追问真实的数量、规模、频率、时间或结果；
- 哪些版式问题当前无法判断；
- 每条提示依据的是哪个字段或原文片段。

用户关闭、刷新或重新打开时，系统基于当前 Resume 重新计算，不创建记录，也不保留诊断状态。

## 4. 非目标

本期不做：

- AI 调用、格式修复重试或 Provider 配置；
- JD 读取、岗位定制、关键词匹配或 Opportunity Fit；
- 自动改写、生成派生简历或 Material Proposal；
- Pilot 入口、Chat 写入或任务卡；
- 数据库表、迁移、API、Repository 或 Service；
- 读取招聘网站、外部 URL 或本地文件系统；
- 保存“已忽略”“已处理”或用户回答；
- PDF/Word 视觉渲染和分页检测；
- ATS 分数、简历总分或“建议/不建议投递”。

## 5. 信息架构与入口

入口位于现有简历编辑/详情抽屉中，使用一个清晰的“简历事实体检”区块或页签，不改变简历库整体布局。

显示顺序：

1. 功能说明：“只检查当前简历中可观察的信息，不会修改简历，也不会调用 AI。”
2. 结果摘要：分别显示“已具备、建议检查、无法判断”的条数，不合成为总分。
3. 按类别展示检查项：核心结构、经历内容、可补充事实、版式能力边界。
4. 每条检查项可展开查看：检查原因、字段路径和安全截断后的原文摘录。

没有任何诊断结果时显示中文空状态，不把“没有发现问题”表述为“保证通过 ATS”。

## 6. 纯函数输入与输出

新增一个不依赖 React、网络或全局状态的纯函数模块。概念接口如下：

```ts
type ResumeAuditStatus = 'present' | 'review' | 'unknown';

type ResumeAuditFinding = {
  id: string;
  category: 'structure' | 'experience' | 'facts' | 'format';
  status: ResumeAuditStatus;
  title: string;
  explanation: string;
  source?: {
    path: string;
    excerpt: string;
  };
};

type ResumeAuditResult = {
  findings: ResumeAuditFinding[];
  counts: Record<ResumeAuditStatus, number>;
};

function auditResume(resume: Resume): ResumeAuditResult;
```

约束：

- 不修改输入对象；
- 相同输入产生字节语义一致的结果顺序；
- `id` 和排序规则固定，不能使用随机数、当前时间或数组索引猜测身份；
- 原文摘录保留用户原文，不做 Unicode 规范化；
- 摘录只做安全长度截断，不改写内容；
- 遇到未知对象结构时返回 `unknown` 或跳过该细项，不能抛出异常；
- 不把 `raw_text` 当作已经解析成功的结构化字段。

## 7. 首期检查规则

### 7.1 核心结构

只检查当前 `ResumeContent` 已定义的字段：

- `contact`
- `education`
- `experience`
- `projects`
- `skills`
- `career_intent`

判定语义：

- 存在至少一个非空可见值：`present`；
- 字段明确存在但为空：`review`；
- 字段形状无法安全识别：`unknown`。

教育、项目或求职意向为空只能提示“建议检查是否需要补充”，不能断言所有简历都必须包含。

### 7.2 经历内容

经历数组存在时，仅在能够安全识别字符串 bullet 的情况下做以下确定性检查：

- 纯空白 bullet；
- `trim()` 后完全相同的重复 bullet；
- 超过固定 Unicode code point 上限的异常长 bullet；
- 经历项存在，但没有可识别的 bullet/achievement/highlight 字符串集合。

字段形状未知时显示“当前结构无法判断经历要点”，不能把整个经历判为空。

### 7.3 可补充事实

该检查不是要求每条 bullet 都包含数字。它只在可识别的经历 bullet 集合中提供保守提示：

- 整份经历内容没有出现任何阿拉伯数字时，提示用户考虑补充真实的数量、规模、频率、时间或结果；
- 提示必须使用“如有真实数据，可以补充”，不得使用“必须量化”或“建议估算”；
- 系统不生成数字、不提供范围、不把其他人的数据套入当前简历；
- 已出现数字只代表“存在量化表达”，不代表数字真实或充分。

### 7.4 无法判断的版式

固定展示一条 `unknown`：当前结构化内容不能判断原始文件的字体、表格、图片、页眉页脚、分页和 ATS 解析效果。

该提示的作用是公开能力边界，不能提供伪造的“ATS 兼容”结论。

## 8. 展示与交互要求

- 所有固定文案使用中文，用户原文保持原样；
- 状态标签应具有文字含义，不能只依赖颜色；
- “建议检查”使用中性提示色，不使用失败或危险语义；
- 原文摘录默认折叠，避免页面过长；
- 字段路径使用等宽样式，但不得让长路径造成横向溢出；
- 组件挂载、展开、折叠和关闭均不得发出 HTTP 请求；
- 不提供“立即修复”“AI 优化”或任何会误导用户认为会发生写入的按钮；
- 页面窄宽度下保持可读，但本期不调整简历模块整体布局。

## 9. 错误与异常输入

- `content_json` 为空或不是可识别对象：显示“当前简历内容不足，暂时无法完成结构化体检”；
- `parse_status` 表示解析失败：明确提示只能检查已经保存的结构化字段；
- 数组中混入数字、对象、`null` 或嵌套异常：忽略不可识别元素，并在相关检查项显示 `unknown`；
- CJK、emoji、组合字符和换行不得导致崩溃或错误截断代理对；
- 任何异常都不得退化为“全部通过”。

## 10. 隔离开发边界

预计允许修改：

```text
web/src/lib/resumeEvidenceAudit.ts
web/src/lib/resumeEvidenceAudit.test.ts
web/src/components/ResumeEvidenceAuditPanel.tsx
web/src/components/ResumeEvidenceAuditPanel.test.tsx
web/src/components/ResumeEditorDrawer.tsx
web/src/components/ResumeLibraryView.module.css
相关的 ResumeEditorDrawer 挂载测试
docs/superpowers/specs/*resume-evidence-audit*
docs/superpowers/plans/*resume-evidence-audit*
docs/reports/*resume-evidence-audit*
```

明确禁止修改：

```text
src/offerpilot/**
tests/**
web/src/services/**
web/src/types/**
web/src/layout/AppShell.tsx
web/src/components/ApplicationDetail.tsx
Material、Opportunity Fit、Interview Preparation、Mock Interview 相关文件
JD 版本相关文件
```

如果实现发现必须突破上述边界，应停止开发并重新复审设计，而不是顺手扩展。

## 11. 测试与验收

### 11.1 纯函数测试

- 完整结构、明确空字段和未知字段形状；
- 空白 bullet、重复 bullet、异常长 bullet；
- 无数字时仅提示补充真实事实；
- 数字存在时不宣称真实性；
- CJK、emoji、组合字符和换行摘录；
- 输入对象未被修改；
- 相同输入结果及顺序稳定；
- 不存在日期、随机数或环境相关结果。

### 11.2 组件与挂载测试

- 三种状态及中文解释正确显示；
- 展开后显示正确字段路径与原文；
- 无结果和无法判断状态不会显示“ATS 已通过”；
- 实际挂载 ResumeEditorDrawer 后可以打开、查看、关闭体检；
- 所有 Resume 写 service、AI service 和通用请求 spy 调用次数均为 0；
- 不新增 Pilot、材料或 Application 导航副作用。

### 11.3 发布验证

- 前端定向测试；
- 前端全量测试；
- `tsc -b`；
- 生产构建；
- `git diff --check`；
- 相对固定 baseline 的文件 allowlist 检查；
- 使用中文合成简历进行亮色模式浏览器走查；
- 浏览器确认没有网络写请求和控制台错误；
- 与 `feat/20260805-application-jd-versions` 比较改动文件交集，除设计文档目录外应为空。

本功能不调用 AI，因此不运行真实 Provider 验收，也不产生 Provider 费用。

## 12. 破坏性变化与风险

### 破坏性变化

无。没有 API、数据库、模型、服务或持久化变化。

### 主要风险

1. **规则被误解为招聘结论。** 通过中性文案、无分数和公开“无法判断”边界降低风险。
2. **半结构化 Resume JSON 造成误报。** 只在识别到明确结构时诊断，其余返回 `unknown`。
3. **量化提示诱导编造。** 文案明确只补充真实数据，不估算、不生成数字。
4. **功能入口膨胀。** 首期只在现有简历详情中增加一个区块，不新增顶级导航或 Pilot 工具。

## 13. 后续候选能力

只有真实使用证明只读体检有价值后，才考虑：

- 用户回答事实补充问题并显式确认；
- 把已确认事实沉淀到 Resume 或 Knowledge Evidence；
- 将体检发现交给现有 evidence-gated Material Proposal；
- 独立的 PDF 渲染与版式检查。

这些能力不属于本次实施，不得提前预埋数据库或 API。
