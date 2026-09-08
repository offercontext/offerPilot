# 简历版本差异审阅设计

**状态：** 已复审通过

**日期：** 2026-08-06

**实现基线：** `main@db9fad6`

**目标分支：** `feat/20260806-resume-version-review`

## 1. 目标与边界

OfferPilot 已支持主简历、复制简历、手工编辑和岗位定制。用户复制并保存岗位版本后，需要能够人工确认相对于另一份已保存简历究竟发生了哪些结构化变化。

本切片增加一个只读、确定性的“简历版本差异审阅”能力：

- 只比较当前前端已经加载的两份 `Resume` 的 `content_json`。
- 不调用 AI，不读取 JD，不访问招聘平台或外部 URL。
- 不新增 API、数据库、service、共享类型或后端代码。
- 不修改简历，不写入草稿，不提供自动接受、回滚或自动替换。
- 不判断哪个版本更好，不生成 ATS、匹配、通过率或质量分数。
- 只描述已保存内容中的真实结构化差异。

`title`、`is_master`、`source`、`parent_resume_id` 只用于展示版本关系和选择默认基准，不计入内容差异。

## 2. 入口与版本选择

入口放在现有简历卡片中，当前切片不扩展顶层导航，也不修改 `AppShell`、`ApplicationDetail` 或编辑器的保存流程。用户点击“对比版本”后打开 `ResumeVersionCompareDrawer`。

Drawer 使用当前 `ResumeLibraryView` 已加载的列表，不发起新的读取请求。当前卡片对应的简历是对比目标，候选基准为列表中除目标自身外的其他简历。

版本选择规则：

1. Drawer 首次打开，或 `target.id` 发生变化时：如果 `parent_resume_id` 对应的父简历仍在候选列表中，自动选择该父简历。
2. 父简历不存在或已删除时，不自动选择任何其他简历；用户必须手动指定基准。
3. 没有父简历时不自动选择，用户手动指定基准。
4. 普通列表刷新不得覆盖用户已经手动选择的基准。只要目标和基准仍存在，就保留选择并基于最新已加载的 `content_json` 重新计算。
5. 候选列表稳定排序：父简历优先，其余候选按 `id` 降序。
6. 目标简历从列表中消失时，安全关闭 Drawer 并清空本地选择；基准简历消失时，清空基准且不自动替换。
7. 标签只展示真实事实：标题、`#ID`、`主简历`、`父版本` 或普通版本关系，不虚构 `v1`、`v2` 等版本号。
8. Drawer 固定提示“仅比较当前已保存的简历内容”，明确不读取编辑器中尚未保存的草稿。

没有可选基准时显示“暂无可比较版本”；没有选定基准时不生成差异摘要。

## 3. 纯函数模块

新增 `web/src/lib/resumeVersionDiff.ts`，不依赖 React、网络、全局状态或浏览器 API。

公开类型固定为安全值，不暴露原始 `unknown`：

```ts
type DiffModule =
  | 'contact'
  | 'education'
  | 'experience'
  | 'projects'
  | 'skills'
  | 'career_intent'
  | 'other';

type DiffKind = 'added' | 'removed' | 'changed';

type DiffText = {
  full: string;
  preview: string;
  truncated: boolean;
};

type DiffValue = {
  valueType: 'string' | 'number' | 'boolean' | 'null' | 'object' | 'array' | 'unsupported';
  text: DiffText;
};

type ResumeDiffItem = {
  kind: DiffKind;
  module: DiffModule;
  path: string;
  before?: DiffValue;
  after?: DiffValue;
};

type ResumeVersionDiffResult = {
  items: ResumeDiffItem[];
  counts: {
    added: number;
    removed: number;
    changed: number;
  };
  identical: boolean;
};

function diffResumeContent(
  baselineContent: unknown,
  targetContent: unknown,
): ResumeVersionDiffResult;
```

该函数只比较传入的 `content_json`，不接收完整 `Resume`，避免把版本元数据混入内容差异。

## 4. 差异与安全输出规则

比较顺序固定如下：

1. 所有值先用 `Object.is` 判断是否为同一值。相同引用直接视为相同，包括同一循环对象、同一函数、同一 Proxy 或其他不支持值。
2. 值不相同时，合法 JSON 值进入递归比较；循环对象、异常容器等进入 `unsupported` 差异处理。
3. 两个结构相似但引用不同的循环对象不尝试深度判等，直接视为 `changed`。

安全展示规则：

- 字符串保留原文。
- 有限数字、布尔值和 `null` 使用固定字面量。
- 合法对象和数组使用键排序后的 canonical JSON；数组保持原索引顺序。
- `undefined`、BigInt、Symbol、函数、循环结构、异常 getter、抛错 Proxy、稀疏数组和异常容器只输出固定中文占位 `（无法安全展示）`；嵌套在合法容器中时统一编码为 `{"__offerpilot_unsupported__":"（无法安全展示）"}`，不调用不可信的 `toString()`，不把原始值放入公开结果。
- 所有对象属性使用 own-property 判断，区分字段不存在与字段存在但值为 `undefined`。
- 合法对象仅限原型为 `Object.prototype` 或 `null` 的普通对象，并且只能包含 own、可枚举、字符串键、数据属性；Symbol key、非枚举 own 属性、accessor、其他原型或自定义类都使当前对象成为 `unsupported` 容器。
- 合法数组仅限原型为 `Array.prototype` 的普通稠密数组；除内建 `length` 外不得有额外 own 属性，所有索引必须存在且为数据属性。稀疏数组、数组额外属性、数组 accessor 或其他原型都使当前数组成为 `unsupported` 容器。
- 检查 `Reflect.ownKeys`、原型、属性描述符或安全序列化时若发生异常，都将该对象或数组视为当前路径的 `unsupported` 结构；读取数据属性使用 descriptor.value，绝不执行 getter。
- 稀疏数组和异常容器按该数组或对象路径产生一条不支持结构差异，不逐项遍历。

JSON Pointer 规则：

- 根路径使用空字符串 `""`。
- 每个对象键和数组索引组成一个路径段。
- 路径段严格转义：`~` 转为 `~0`，`/` 转为 `~1`。
- 模块由 JSON Pointer 解码后的第一个路径段确定；未知字段和根路径归入 `other`。

递归与计数规则：

- 对象取两侧 own-property 键集合，按键名确定性排序后递归。
- 数组严格按索引比较，界面固定提示“数组按位置比较，不推断经历、公司或项目是否为同一项”。
- 容器新增或删除只产生该容器路径的一条差异，不继续展开子项。
- 同一路径最多产生一条差异。
- 同一路径两侧都是可安全递归的对象或数组时递归；类型不同、`null`、空字符串或空容器变化产生一条 `changed`。
- 每个差异项只含安全的 `before` 或 `after` `DiffValue`，不存在的一侧省略。
- 差异按模块顺序、JSON Pointer、`added → removed → changed` 稳定排序；由于同一路径最多一项，`kind` 仅作为最终兜底键。
- `identical` 严格等于 `items.length === 0`。

文本规则：

- `full` 保存安全格式化后的完整文本。
- 容器的完整文本不得直接对原始值调用 `JSON.stringify`：先递归构造只包含合法 JSON 标量、数组、普通对象和固定不支持节点的 safe tree，再对该 safe tree 做键排序后的 canonical serialize。
- `preview` 最多保留 160 个 Unicode code point；超出时追加 `…`。
- 不进行 Unicode 规范化，不拆分 emoji 代理对，不改变组合字符原文。
- `truncated` 仅在确实发生截断时为 `true`。

## 5. Drawer 展示

新增 `web/src/components/ResumeVersionCompareDrawer.tsx`，只接收目标、候选列表、打开状态和关闭回调，不导入 Resume service 或 AI service。

显示结构：

1. 顶部展示基准版本和当前版本的真实标签，并提供基准选择控件。
2. 未选择基准时显示选择提示，不显示虚假的零差异摘要。
3. 选择基准后展示新增、删除、修改数量。
4. `identical` 时展示中文空状态。
5. 有差异时按“联系方式、教育经历、工作经历、项目经历、技能、求职意向、其他结构化字段”的固定顺序分组。
6. 每项显示中文状态、规范 JSON Pointer 路径，以及修改前/修改后的安全文本。
7. 新增项只显示修改后，删除项只显示修改前，修改项左右展示两侧内容。
8. 宽屏左右展示修改前后，窄屏上下排列，不改变差异语义。
9. 长文本只有在 `truncated === true` 时才显示 `<details>` 展开控件；未截断内容不生成展开控件。
10. 切换目标或基准后清除此前展开状态，避免沿用上一组差异的 UI 状态。
11. Drawer 不提供接受、回滚、自动写入、自动替换或导航按钮。

## 6. 文件边界

预计允许新增或修改：

```text
web/src/lib/resumeVersionDiff.ts
web/src/lib/resumeVersionDiff.test.ts
web/src/components/ResumeVersionCompareDrawer.tsx
web/src/components/ResumeVersionCompareDrawer.test.tsx
web/src/components/ResumeCard.tsx
web/src/components/ResumeCard.test.tsx
web/src/components/ResumeLibraryView.tsx
web/src/components/ResumeLibraryView.module.css
web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx
docs/superpowers/specs/2026-08-06-resume-version-review-design.md
docs/superpowers/plans/2026-08-06-resume-version-review.md
docs/reports/2026-08-06-resume-version-review-browser-acceptance.md
```

明确禁止修改：

```text
web/src/services/**
web/src/types/**
web/src/layout/AppShell.tsx
web/src/components/ApplicationDetail.tsx
src/offerpilot/**
tests/**
任何 JD、Opportunity Fit、材料、面试或 Pilot 文件
```

不得通过新增 service、API、数据库字段、共享类型或后端逻辑扩大范围。

## 7. 测试与验收

### 7.1 纯函数测试

`web/src/lib/resumeVersionDiff.test.ts` 必须覆盖：

- 完全一致、新增、删除、修改字段。
- 对象、数组、空容器、`null`、空字符串。
- 缺失属性与值为 `undefined` 的区别。
- `Object.is` 优先规则：同一不支持值引用不产生差异；不同引用的循环对象产生 `changed`。
- `NaN`、`Infinity`、BigInt、Symbol、函数、异常 getter、抛错 Proxy、稀疏数组不抛异常并输出安全占位。
- JSON Pointer 转义、根路径、未知字段和模块归类。
- 对象键排序、模块排序、数组按索引、容器新增删除不展开、同路径最多一项。
- 中文、emoji、组合字符和换行的 160 code point 截断边界。
- 摘要计数、稳定排序和 `identical === items.length === 0`。

### 7.2 组件与真实挂载测试

组件测试必须覆盖：

- 父简历首次打开或目标 ID 变化时默认选择。
- 父简历不存在、已删除或无父简历时不自动替换。
- 手动基准在普通列表刷新后保持。
- 候选稳定排序和真实关系标签。
- 目标删除后安全关闭，基准删除后清空选择。
- 未保存编辑器草稿不参与比较。
- 只有真实截断内容显示 `<details>`；切换目标或基准后展开状态清除。
- 宽屏/窄屏布局只改变排版。

增加具体文件 `web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx` 的真实 `ResumeLibraryView` 挂载测试：初始 `listResumes` 只读查询可以发生；点击“对比版本”、选择基准、查看差异、展开/折叠和关闭期间，Resume 写入、AI、导航和 history 调用必须均为 0。测试使用已加载的合成 Resume，不触发复制、编辑或保存。

### 7.3 浏览器验收

浏览器验收必须使用亮色模式、中文合成简历和临时隔离数据目录：

```text
主简历 → 复制为岗位版本 → 编辑并保存几处内容
→ 打开版本对比 → 确认父简历默认选择
→ 切换基准、查看摘要、展开修改前后
```

复制和编辑只用于准备演示数据，验收结束后清理临时目录和合成记录，不使用用户真实简历。

网络审计只允许本地静态资源与本地 `/api`；不得调用 AI Provider、招聘平台或其他外部 URL。对比页面本身不得产生任何写请求。

### 7.4 发布门禁

最终运行：

```text
cd web && npm test -- --run
cd web && npx tsc -b
cd web && npm run build
git diff --check
```

同时机器化证明：

- 当前版本分支的固定 fork point 是 `db9fad6`，必须验证它是 `feat/20260806-resume-version-review` 的祖先；当前版本自有文件集合从 `db9fad6..feat/20260806-resume-version-review` 计算，并合并当前工作区的暂存、未暂存和未跟踪文件。
- JD 分支的真实 fork point 不假定为 `db9fad6`，而是运行 `git merge-base feat/20260805-application-jd-versions db9fad6` 计算；当前基线应得到 `b4363b0`，并从该 fork point 到 JD 分支头计算 JD 分支自有文件集合。
- 只有在分别按上述两个真实 fork point 得到两组“分支自有文件”后，才求文件集合交集；不能用一个共同的 `db9fad6` 直接比较两个分支。
- 任何祖先校验失败、JD fork point 变化或交集非零，都立即停止实施并重新划分文件边界。
- allowlist 检查覆盖已提交、暂存、未暂存和未跟踪文件，不能只使用 `git diff --name-only`。
- 通过 allowlist 和禁止路径检查机器化证明没有后端、API、数据库、service 或共享类型改动。

## 8. 破坏性变化与风险

本切片不改变 API、数据库、后端模型、服务层、共享类型、持久化或导航协议，因此无破坏性变化。

主要风险是用户把“按位置比较”误解为经历身份匹配，界面必须持续显示固定提示；以及异常 JavaScript 值影响展示，纯函数必须在公开结果边界前完成安全归一化，绝不向 UI 传递原始对象。

本设计不包含代码实现、推送或合并操作；经批准后的测试先行实施步骤记录在配套实施计划中。
