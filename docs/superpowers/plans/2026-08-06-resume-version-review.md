# 简历版本差异审阅实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在简历库中提供只读、确定性的已保存简历 `content_json` 版本差异审阅，并以安全 JSON 值、规范 JSON Pointer 和稳定排序展示修改前后内容。

**Architecture:** 用不依赖 React 或网络的 `resumeVersionDiff.ts` 递归比较两个 `content_json`，在公开结果边界完成值归一化和 Unicode 安全截断。`ResumeVersionCompareDrawer` 负责基准选择、空状态和差异展示；`ResumeLibraryView` 只从已有 `resumes` 查询结果管理目标版本和 Drawer，不新增读取或写入 service。

**Tech Stack:** React 18、TypeScript、Ant Design、TanStack Query、Vitest、jsdom、Vite、PowerShell、本地 `OFFERPILOT_DATA` 隔离目录。

---

## 0. 固定边界与执行前检查

工作目录和分支固定为：

```text
D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260806-resume-version-review
feat/20260806-resume-version-review
```

当前版本分支 fork point 固定为 `db9fad6`。JD 分支不得使用相同基线；必须运行 `git merge-base feat/20260805-application-jd-versions db9fad6`，当前应得到 `b4363b0`。

实现前运行：

```powershell
git status --short --branch
git merge-base --is-ancestor db9fad6 feat/20260806-resume-version-review
if ($LASTEXITCODE -ne 0) { throw 'feature fork point is not an ancestor' }
$jdBase = (git merge-base feat/20260805-application-jd-versions db9fad6).Trim()
if (-not $jdBase.StartsWith('b4363b0')) { throw "unexpected JD fork point: $jdBase" }
```

实现过程中只允许下列路径：

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

禁止修改 `web/src/services/**`、`web/src/types/**`、`web/src/layout/AppShell.tsx`、`web/src/components/ApplicationDetail.tsx`、`src/offerpilot/**`、`tests/**`，以及任何 JD、Opportunity Fit、材料、面试或 Pilot 文件。

每个任务先写红灯测试，再实现最小行为，再运行对应测试。所有提交信息遵守仓库规则，例如 `test: AI define resume diff contract`、`feat: AI add resume version comparison`。

## Task 1: 建立纯函数公开契约的红灯测试

**Files:**

- Create: `web/src/lib/resumeVersionDiff.test.ts`
- Reference only: `web/src/types/resume.ts`（只读取 `ResumeContent` 语义，不修改类型）

- [ ] **Step 1: 写入公开输出类型和基础差异测试**

在测试文件中只从 `./resumeVersionDiff` 导入 `diffResumeContent`，使用以下测试辅助函数和断言结构，确保实现不能把原始 `unknown` 放进结果：

```ts
import { describe, expect, it } from 'vitest';
import { diffResumeContent } from './resumeVersionDiff';

function itemAt(result: ReturnType<typeof diffResumeContent>, path: string) {
  const item = result.items.find((candidate) => candidate.path === path);
  if (!item) throw new Error(\`missing diff item at \${path}\`);
  return item;
}

describe('diffResumeContent', () => {
  it('returns an empty result for identical content and stable object-key order', () => {
    const result = diffResumeContent(
      { contact: { email: 'a@example.com', name: '林晓' } },
      { contact: { name: '林晓', email: 'a@example.com' } },
    );

    expect(result.items).toEqual([]);
    expect(result.counts).toEqual({ added: 0, removed: 0, changed: 0 });
    expect(result.identical).toBe(true);
  });

  it('reports added, removed, changed fields with escaped JSON Pointer paths', () => {
    const result = diffResumeContent(
      { contact: { 'a/b~c': '旧值' }, projects: [{ name: '旧项目' }] },
      { contact: { 'a/b~c': '新值', email: 'a@example.com' }, projects: [{ name: '新项目' }, { name: '第二项目' }] },
    );

    expect(result.items.map(({ kind, path }) => ({ kind, path }))).toEqual([
      { kind: 'changed', path: '/contact/a~1b~0c' },
      { kind: 'added', path: '/contact/email' },
      { kind: 'changed', path: '/projects/0/name' },
      { kind: 'added', path: '/projects/1' },
    ]);
    expect(result.counts).toEqual({ added: 2, removed: 0, changed: 2 });
  });

  it('distinguishes an absent property from an own property whose value is undefined', () => {
    const result = diffResumeContent(
      { contact: {} },
      { contact: { email: undefined } },
    );

    const item = itemAt(result, '/contact/email');
    expect(item.kind).toBe('added');
    expect(item.after?.valueType).toBe('unsupported');
    expect(item.after?.text.full).toBe('（无法安全展示）');
  });
});
```

- [ ] **Step 2: 写入数组、容器和稳定模块顺序测试**

继续在同一测试文件中加入：数组严格按索引比较；数组新增项是一条 `/skills/1` 差异；新增或删除整个对象/数组只产生容器路径一条差异；模块顺序为 `contact`、`education`、`experience`、`projects`、`skills`、`career_intent`、`other`；根路径为 `""` 且归入 `other`；未知顶层字段归入 `other`。

```ts
it('compares arrays by stable index and does not infer item identity', () => {
  const result = diffResumeContent(
    { experience: [{ company: '甲公司' }, { company: '乙公司' }] },
    { experience: [{ company: '乙公司' }, { company: '甲公司' }] },
  );

  expect(result.items.map((item) => item.path)).toEqual([
    '/experience/0/company',
    '/experience/1/company',
  ]);
  expect(result.items.every((item) => item.kind === 'changed')).toBe(true);
});

it('emits one atomic item when a container is added or removed', () => {
  const added = diffResumeContent({}, { education: [{ school: '示例大学' }] });
  const removed = diffResumeContent({ projects: [{ name: '示例项目' }] }, {});

  expect(added.items).toHaveLength(1);
  expect(added.items[0]).toMatchObject({ kind: 'added', path: '/education' });
  expect(added.items[0].after?.valueType).toBe('array');
  expect(removed.items).toHaveLength(1);
  expect(removed.items[0]).toMatchObject({ kind: 'removed', path: '/projects' });
});

it('orders modules and assigns root and unknown fields to other', () => {
  const result = diffResumeContent(
    { other: 'old', skills: [], career_intent: {}, contact: {} },
    { other: 'new', skills: ['TypeScript'], career_intent: { target_roles: ['后端'] }, contact: { name: '林晓' } },
  );

  expect(result.items.map((item) => item.module)).toEqual([
    'contact',
    'skills',
    'career_intent',
    'other',
  ]);
});
```

- [ ] **Step 3: 写入 Object.is、不支持容器和 getter 安全测试**

测试同一不支持值引用、不同循环对象、`NaN`、`Infinity`、Symbol、BigInt、函数、异常 getter、抛错 Proxy、非枚举属性、Symbol key、自定义原型、稀疏数组和数组额外属性；所有这些值都必须不抛异常并返回安全值。

```ts
it('uses Object.is before recursive or unsupported handling', () => {
  const sameFunction = () => 'same';
  const sameCycle: Record<string, unknown> = {};
  sameCycle.self = sameCycle;

  expect(diffResumeContent({ value: sameFunction }, { value: sameFunction }).items).toEqual([]);
  expect(diffResumeContent({ value: sameCycle }, { value: sameCycle }).items).toEqual([]);

  const leftCycle: Record<string, unknown> = {};
  const rightCycle: Record<string, unknown> = {};
  leftCycle.self = leftCycle;
  rightCycle.self = rightCycle;
  const different = diffResumeContent({ value: leftCycle }, { value: rightCycle });
  expect(different.items).toHaveLength(1);
  expect(different.items[0].kind).toBe('changed');
  expect(different.items[0].after?.valueType).toBe('unsupported');
});

it.each([
  ['undefined', undefined],
  ['NaN', Number.NaN],
  ['Infinity', Number.POSITIVE_INFINITY],
  ['function', () => 'unsupported'],
  ['Symbol', Symbol('unsupported')],
  ['BigInt', BigInt(1)],
] as const)('renders %s as a safe unsupported value', (_label, value) => {
  expect(() => diffResumeContent({}, { value })).not.toThrow();
  const item = itemAt(diffResumeContent({}, { value }), '/value');
  expect(item.after?.valueType).toBe('unsupported');
  expect(item.after?.text.full).toBe('（无法安全展示）');
});

it('never executes accessors or Proxy traps while producing safe output', () => {
  let getterCalls = 0;
  const withGetter = Object.defineProperty({}, 'name', {
    enumerable: true,
    get() {
      getterCalls += 1;
      throw new Error('getter must not run');
    },
  });
  const throwingProxy = new Proxy({ name: 'x' }, {
    ownKeys() {
      throw new Error('proxy trap');
    },
  });

  expect(() => diffResumeContent({ value: withGetter }, { value: throwingProxy })).not.toThrow();
  expect(getterCalls).toBe(0);
  expect(diffResumeContent({ value: withGetter }, { value: throwingProxy }).items[0].after?.valueType).toBe('unsupported');
});

it('rejects non-JSON container shapes at the container path', () => {
  const symbolKey = Symbol('hidden');
  const withSymbol = { name: 'x', [symbolKey]: 'hidden' };
  const withNonEnumerable = Object.defineProperty({ name: 'x' }, 'hidden', {
    value: 'hidden',
    enumerable: false,
  });
  const sparse = [] as unknown[];
  sparse[1] = 'value';
  const extraArrayProperty = ['value'] as unknown[] & { extra?: string };
  extraArrayProperty.extra = 'extra';

  for (const value of [withSymbol, withNonEnumerable, Object.create({ inherited: true }), sparse, extraArrayProperty]) {
    const result = diffResumeContent({ value: {} }, { value });
    expect(result.items).toHaveLength(1);
    expect(result.items[0].path).toBe('/value');
    expect(result.items[0].after?.valueType).toBe('unsupported');
  }
});

it('builds a safe canonical tree for nested unsupported values in a container', () => {
  let getterCalls = 0;
  const nestedGetter = Object.defineProperty({ value: undefined }, 'getter', {
    enumerable: true,
    get() {
      getterCalls += 1;
      throw new Error('nested getter must not run');
    },
  });
  const cycle: Record<string, unknown> = {};
  cycle.self = cycle;
  const added = {
    nested: {
      missingValue: undefined,
      functionValue: () => 'no raw function text',
      symbolValue: Symbol('no raw symbol text'),
      bigintValue: BigInt(1),
      cycle,
      nestedGetter,
    },
  };

  const result = diffResumeContent({}, { projects: [added] });
  expect(() => result.items[0].after?.text.full).not.toThrow();
  expect(result.items).toHaveLength(1);
  expect(result.items[0].path).toBe('/projects');
  expect(result.items[0].after?.valueType).toBe('array');
  expect(result.items[0].after?.text.full).toContain('__offerpilot_unsupported__');
  expect(result.items[0].after?.text.full).not.toContain('no raw function text');
  expect(result.items[0].after?.text.full).not.toContain('no raw symbol text');
  expect(getterCalls).toBe(0);

  const removedResult = diffResumeContent({ projects: [added] }, {});
  expect(removedResult.items).toHaveLength(1);
  expect(removedResult.items[0].path).toBe('/projects');
  expect(removedResult.items[0].before?.text.full).toContain('__offerpilot_unsupported__');
  expect(removedResult.items[0].before?.text.full).not.toContain('no raw function text');
});
```

- [ ] **Step 4: 写入 Unicode 截断边界测试**

固定测试 160 个 code point 不截断、161 个 code point 截断；使用 161 个 emoji 验证不产生孤立代理项；使用组合字符验证不做 NFC/NFD 规范化；断言 `full` 保留原始文本、`preview` 只在截断时追加 `…`。

- [ ] **Step 5: 运行纯函数测试确认红灯**

运行：

```powershell
cd web
npm.cmd test -- src/lib/resumeVersionDiff.test.ts --run
```

预期：失败，因为 `web/src/lib/resumeVersionDiff.ts` 尚未创建；测试文件本身必须被 Vitest 收集，不能因导入路径或 TypeScript 语法错误而跳过。

- [ ] **Step 6: 提交纯函数红灯测试**

```powershell
git add -- web/src/lib/resumeVersionDiff.test.ts
git commit -m "test: AI define resume diff contract"
```

## Task 2: 实现安全、确定性的纯函数 diff

**Files:**

- Create: `web/src/lib/resumeVersionDiff.ts`
- Test: `web/src/lib/resumeVersionDiff.test.ts`

- [ ] **Step 1: 定义导出类型和入口函数**

导出设计文档中的 `DiffModule`、`DiffKind`、`DiffText`、`DiffValue`、`ResumeDiffItem`、`ResumeVersionDiffResult` 和 `diffResumeContent`。入口只接收两个 `unknown` 内容值，结果只能包含 `DiffValue`，不得出现 `before: unknown` 或 `after: unknown`。

- [ ] **Step 2: 实现 Object.is 优先和 own-property 遍历**

递归比较函数必须先执行：

```ts
if (Object.is(left, right)) return;
```

对象属性通过 `Reflect.ownKeys` 和 own-property descriptor 判断。读取数据属性使用 `descriptor.value`；遇到 accessor、Symbol key、非枚举 own 属性、抛异常的 Proxy trap 或异常原型检查，立即把当前路径归一化为 `unsupported`，绝不读取 getter。

- [ ] **Step 3: 实现普通 JSON 容器识别**

只接受：

```text
对象：Object.prototype 或 null 原型；own、可枚举、字符串键、数据属性。
数组：Array.prototype 原型；连续存在的 0..length-1 数据索引；除内建 length 外没有 own 属性。
```

任何自定义原型、Date/Map/Set/class 实例、Symbol key、非枚举 own 属性、accessor、稀疏数组、数组额外属性或异常容器都在当前路径返回 `unsupported`，不递归子项。

- [ ] **Step 4: 实现差异粒度和稳定排序**

两侧容器都合法时，对象按键名排序、数组按索引递归；一侧缺失时在容器路径产生单条 `added` 或 `removed`；两侧叶子或容器类型不同在当前路径产生单条 `changed`。路径使用 JSON Pointer，路径段按 `~0`、`~1` 转义。差异排序固定为模块顺序、路径、kind 兜底。

- [ ] **Step 5: 实现安全值和 canonical JSON**

使用固定占位文本 `（无法安全展示）` 和固定嵌套节点 `{"__offerpilot_unsupported__":"（无法安全展示）"}`。字符串使用原文；有限数字、布尔、`null` 使用固定字面量；`NaN`、`Infinity` 和其他不支持标量映射为该固定节点。普通对象和数组必须先由递归 safe-tree 构造器处理每个嵌套值，再对 safe tree 做排序键后的 canonical JSON；绝不对原始容器调用 `JSON.stringify`。循环、函数、Symbol、BigInt、`undefined`、异常 getter、抛错 Proxy 和异常子容器只进入固定节点，不调用不可信 `toString()`。所有最终文本通过 `Array.from(text)` 按 code point 截断到 160，超出时追加 `…`，不规范化 Unicode。

- [ ] **Step 6: 实现计数和 identical**

在最终排序后从 `items` 计算 `counts`，并只使用以下定义：

```ts
const identical = items.length === 0;
```

- [ ] **Step 7: 运行纯函数测试确认绿灯**

```powershell
cd web
npm.cmd test -- src/lib/resumeVersionDiff.test.ts --run
```

预期：纯函数测试全部通过，并验证输入对象未被修改。

- [ ] **Step 8: 提交纯函数实现**

```powershell
git add -- web/src/lib/resumeVersionDiff.ts web/src/lib/resumeVersionDiff.test.ts
git commit -m "feat: AI add deterministic resume version diff"
```

## Task 3: 编写 Drawer 展示与生命周期测试

**Files:**

- Create: `web/src/components/ResumeVersionCompareDrawer.test.tsx`
- Reference: `web/src/lib/resumeVersionDiff.ts`

- [ ] **Step 1: 固定组件 Props 和可访问控件名称**

测试按以下 Props 使用组件，确保实现不需要 service 或全局列表查询：

```ts
type ResumeVersionCompareDrawerProps = {
  open: boolean;
  target: Resume;
  candidates: Resume[];
  onClose: () => void;
};
```

基准选择控件使用可访问名称 `基准版本`；关闭按钮使用 `关闭版本对比`；差异项路径使用 `code` 文本；数组提示固定包含“数组按位置比较”。

- [ ] **Step 2: 写入默认选择和候选排序测试**

覆盖：首次打开父简历优先；父简历不存在时选择为空；无父简历时选择为空；父简历优先、其余按 `id` 降序；标签包含标题、`#ID` 和真实的 `主简历`/`父版本` 关系，不出现 `v1` 或 `v2`。

- [ ] **Step 3: 写入目标变化与列表刷新生命周期测试**

使用 `rerender` 验证：同一目标的候选内容刷新不覆盖手动选择；`target.id` 变化时才重新应用新目标的父简历；基准被移除时选择清空且不自动选择其他版本；目标内容变化时差异重新计算。

- [ ] **Step 4: 写入差异和展开状态测试**

覆盖：只比较 `content_json`；标题、来源、主简历标志和关系变化不产生内容差异；完全一致显示中文空状态；新增/删除/修改显示正确两侧；未截断文本没有 `<details>`；截断文本有 `<details>`；切换目标或基准后所有此前展开的 `<details>` 回到关闭状态。

- [ ] **Step 5: 运行 Drawer 测试确认红灯**

```powershell
cd web
npm.cmd test -- src/components/ResumeVersionCompareDrawer.test.tsx --run
```

预期：失败，因为组件尚未创建；测试必须能挂载 Ant Design 容器并进入断言，而不是因缺少模块直接被跳过。

## Task 4: 实现只读 ResumeVersionCompareDrawer

**Files:**

- Create: `web/src/components/ResumeVersionCompareDrawer.tsx`
- Test: `web/src/components/ResumeVersionCompareDrawer.test.tsx`
- Modify: `web/src/components/ResumeLibraryView.module.css`

- [ ] **Step 1: 实现候选基准排序和默认选择**

在组件内部以 `target.id` 和 `open` 生命周期控制默认选择：仅首次打开或 `target.id` 变化时选择仍存在的父简历；普通 `candidates` 刷新不得覆盖手动选择。候选排序先放父简历，再按 `id` 降序；当前目标始终排除。

- [ ] **Step 2: 实现只读选择控件和状态文案**

使用原生 `select` 或等价的可访问控件展示候选；无候选时显示“暂无可比较版本”，无基准时显示选择提示；固定展示“仅比较当前已保存的简历内容”和“数组按位置比较，不推断经历、公司或项目是否为同一项”。

- [ ] **Step 3: 实现差异摘要和分组展示**

选定基准后调用 `diffResumeContent(base.content_json, target.content_json)`。摘要只显示新增、删除、修改数量；分组顺序固定为联系方式、教育经历、工作经历、项目经历、技能、求职意向、其他结构化字段；每项显示安全状态、JSON Pointer 和安全值文本。

- [ ] **Step 4: 实现安全展开和响应式排版**

只有 `DiffText.truncated === true` 时创建 `<details>`；展开状态使用路径集合保存，在目标或基准变化时清空。宽屏使用左右 grid，窄屏使用上下 grid；使用 CSS 只改变布局，不改变差异内容。

- [ ] **Step 5: 运行 Drawer 测试确认绿灯**

```powershell
cd web
npm.cmd test -- src/components/ResumeVersionCompareDrawer.test.tsx --run
```

预期：Drawer 测试全部通过，且组件导入中没有 `@/services/resumes`、`@/services/ai`、`fetch` 或导航调用。

- [ ] **Step 6: 提交 Drawer 实现**

```powershell
git add -- web/src/components/ResumeVersionCompareDrawer.tsx web/src/components/ResumeVersionCompareDrawer.test.tsx web/src/components/ResumeLibraryView.module.css
git commit -m "feat: AI render read-only resume version comparison"
```

## Task 5: 编写 ResumeCard 与 ResumeLibraryView 集成红灯测试

**Files:**

- Modify: `web/src/components/ResumeCard.test.tsx`
- Create: `web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx`
- Reference: `web/src/components/ResumeCard.tsx`、`web/src/components/ResumeLibraryView.tsx`

- [ ] **Step 1: 增加 ResumeCard 对比入口测试**

给 ResumeCard 的测试 Props 增加 `onCompare` spy，断言每张可比较卡片有中文 `对比版本` 按钮，并点击后只调用 `onCompare(resume.id)` 一次。

- [ ] **Step 2: 写入真实 ResumeLibraryView 挂载测试**

测试文件固定为 `web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx`，使用 `@vitest-environment jsdom`、真实 `ResumeLibraryView`、`QueryClientProvider` 和合成中文 Resume。mock 仅提供初始 `listResumes` 只读结果；所有 Resume 创建、复制、更新、删除和上传函数、AI service、fetch、XHR、`history.pushState`、`history.replaceState` 均建立 spy。

测试主流程：

```ts
render(<ResumeLibraryView />);
await screen.findByRole('button', { name: '对比版本' });
await user.click(screen.getByRole('button', { name: '对比版本' }));
expect(screen.getByText('仅比较当前已保存的简历内容')).toBeInTheDocument();
await user.selectOptions(screen.getByRole('combobox', { name: '基准版本' }), '1');
expect(screen.getByText('/experience/0/highlights/0')).toBeInTheDocument();
await user.click(screen.getByText('展开完整内容'));
await user.click(screen.getByText('展开完整内容'));
await user.click(screen.getByRole('button', { name: '关闭版本对比' }));
```

初始 `listResumes` 调用允许为 1 次；从点击对比、选择基准、查看差异、展开/折叠到关闭期间，所有 Resume 写入、AI、fetch、XHR、导航和 history 调用必须为 0。测试不得触发复制、编辑或保存。

- [ ] **Step 3: 运行集成测试确认红灯**

```powershell
cd web
npm.cmd test -- src/components/ResumeCard.test.tsx src/components/ResumeLibraryView.versionCompare.mount.test.tsx --run
```

预期：因 ResumeCard 没有对比入口、ResumeLibraryView 没有 Drawer 接入而失败。

## Task 6: 接入卡片和简历库，不增加 service 或类型改动

**Files:**

- Modify: `web/src/components/ResumeCard.tsx`
- Modify: `web/src/components/ResumeCard.test.tsx`
- Modify: `web/src/components/ResumeLibraryView.tsx`
- Modify: `web/src/components/ResumeLibraryView.module.css`
- Test: `web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx`

- [ ] **Step 1: 给 ResumeCard 增加本地对比回调**

新增 `onCompare?: (id: number) => void`，在现有卡片操作区添加 `对比版本` 按钮；点击只调用回调，不调用 service，不改变 Resume。`onCompare` 未传入时不渲染按钮。

- [ ] **Step 2: 在 ResumeLibraryView 管理 compareTargetId**

新增 `compareTargetId: number | null` 状态和本地 `setCompareTargetId` 回调；仅当 `resumes.length > 1` 时给卡片传入 `onCompare={() => setCompareTargetId(r.id)}`，因此只有当前简历时不显示无效入口。从当前 `resumes` 查询目标对象并把同一查询结果作为 candidates 传入 Drawer。

- [ ] **Step 3: 处理列表刷新和删除安全状态**

当 `compareTargetId` 在最新 `resumes` 中不存在时清空并关闭 Drawer；基准由 Drawer 自己在候选列表中消失时清空，不自动替换。不要在普通 query refresh 时重置 Drawer 内用户选择。

- [ ] **Step 4: 挂载 Drawer 并保持现有 service 行为不变**

在简历库已有内容区域挂载 `ResumeVersionCompareDrawer`，传入 `open={compareTarget !== null}`、目标、完整已加载列表和关闭回调。不得修改 `web/src/services/resumes.ts`、`web/src/types/resume.ts` 或任何后端文件。

- [ ] **Step 5: 运行集成测试确认绿灯**

```powershell
cd web
npm.cmd test -- src/components/ResumeCard.test.tsx src/components/ResumeLibraryView.versionCompare.mount.test.tsx --run
```

预期：卡片入口、真实库挂载、基准选择、差异查看、展开/折叠、关闭和零副作用断言全部通过。

- [ ] **Step 6: 提交简历库接入**

```powershell
git add -- web/src/components/ResumeCard.tsx web/src/components/ResumeCard.test.tsx web/src/components/ResumeLibraryView.tsx web/src/components/ResumeLibraryView.module.css web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx
git commit -m "feat: AI connect resume version comparison"
```

## Task 7: 浏览器验收并记录报告

**Files:**

- Create: `docs/reports/2026-08-06-resume-version-review-browser-acceptance.md`

- [ ] **Step 1: 构建前端并准备一次性验收 harness**

先在临时数据目录之外完成生产构建；服务启动、合成数据准备、浏览器操作、网络审计和清理必须全部位于同一个 `try/finally` 中。`try` 之前只计算路径、端口并保存旧环境变量，不启动进程、不写数据库：

```powershell
cd web
npm.cmd run build
cd ..

$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-resume-version-review-' + [Guid]::NewGuid().ToString('N'))
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()
$previousData = $env:OFFERPILOT_DATA
$server = $null

function Get-ProcessTreeIds([int]$rootId) {
  $rootId
  Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $rootId |
    ForEach-Object { Get-ProcessTreeIds ([int]$_.ProcessId) }
}
```

以下单一 harness 从启动服务开始包住合成数据准备、浏览器验收和网络审计；`finally` 无论中途哪一步失败都负责递归清理：

```powershell
try {
  New-Item -ItemType Directory -Force -Path $tempData | Out-Null
  $env:OFFERPILOT_DATA = $tempData
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
  "Set-Location '$((Get-Location).Path)'; `$env:OFFERPILOT_DATA = '$tempData'; uv run oc start --port $port"
  )
$healthUri = "http://127.0.0.1:$port/api/health"
$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
  try {
    $health = Invoke-RestMethod -Uri $healthUri -TimeoutSec 2
    if ($health) { $ready = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw "local OfferPilot did not become healthy: $healthUri" }

$resumeBody = @{
  title = '中文主简历'
  source = 'manual'
  content_json = @{
    contact = @{ name = '林晓'; email = 'lin.xiao@example.com' }
    education = @(@{ school = '示例大学'; degree = '计算机科学' })
    experience = @(@{ company = '示例科技'; title = '后端工程师'; highlights = @('负责订单服务') })
    projects = @(@{ name = '数据平台'; highlights = @('搭建服务接口') })
    skills = @('TypeScript', 'FastAPI')
    career_intent = @{ target_roles = @('后端工程师'); target_locations = @('上海') }
    raw_text = '中文合成简历，仅用于版本差异审阅验收。'
  }
} | ConvertTo-Json -Depth 12
$resume = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/resumes" -ContentType 'application/json' -Body $resumeBody
if (-not $resume.id) { throw 'synthetic Chinese resume was not created' }

$secondaryBody = @{
  title = '中文备用版本'
  source = 'manual'
  content_json = @{
    contact = @{ name = '林晓'; email = 'lin.xiao@example.com' }
    education = @(@{ school = '示例大学'; degree = '软件工程' })
    experience = @(@{ company = '备用科技'; title = '平台工程师'; highlights = @('维护数据平台') })
    projects = @(@{ name = '监控项目'; highlights = @('完善告警流程') })
    skills = @('Python', 'PostgreSQL')
    career_intent = @{ target_roles = @('平台工程师'); target_locations = @('杭州') }
    raw_text = '中文备用简历，仅用于切换基准验收。'
  }
} | ConvertTo-Json -Depth 12
$secondary = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/resumes" -ContentType 'application/json' -Body $secondaryBody
if (-not $secondary.id) { throw 'secondary synthetic Chinese resume was not created' }

  Write-Host "Open http://127.0.0.1:$port in the in-app browser."
  Write-Host 'Complete the Chinese resume copy/edit/compare flow and browser network audit now.'
  [void](Read-Host 'Press Enter only after browser acceptance and network audit finish')
}
finally {
  try {
    if ($server) {
      $processIds = @(Get-ProcessTreeIds ([int]$server.Id) | Sort-Object -Descending)
      foreach ($processId in $processIds) {
        Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
      }
    }

    $portReleased = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
      $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
      if ($listeners.Count -eq 0) { $portReleased = $true; break }
      Start-Sleep -Milliseconds 250
    }
    if (-not $portReleased) { throw "harness port was not released: $port" }

    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $tempFull = [IO.Path]::GetFullPath($tempData)
    $tempLeaf = [IO.Path]::GetFileName($tempFull)
    $tempParent = [IO.Path]::GetDirectoryName($tempFull)
    if (-not $tempFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $tempParent.TrimEnd('\', '/') -ne $tempRoot.TrimEnd('\', '/') -or
        -not $tempLeaf.StartsWith('offerpilot-resume-version-review-', [StringComparison]::OrdinalIgnoreCase)) {
      throw "refusing to remove non-temporary path: $tempFull"
    }
    if (Test-Path -LiteralPath $tempFull) { Remove-Item -LiteralPath $tempFull -Recurse -Force }
    if (Test-Path -LiteralPath $tempFull) { throw "temporary data cleanup failed: $tempFull" }
  }
  finally {
    if ($null -eq $previousData) { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
    else { $env:OFFERPILOT_DATA = $previousData }
  }
}
```

该准备请求只针对 `$tempData` 的本地 `/api`，不使用真实用户数据；浏览器随后从主简历复制出目标岗位版本，因此目标版本的父简历是 `$resume.id`，候选基准至少包含父简历和 `$secondary.id`。从浏览器点击“对比版本”开始才进入本功能的零写请求审计区间。

- [ ] **Step 2: 使用内置浏览器完成中文流程**

在亮色模式中执行：主简历 → 复制为岗位版本 → 编辑并保存联系方式、工作要点和技能几处内容 → 打开“对比版本” → 确认父简历默认选中 → 查看新增、删除、修改摘要 → 手动切换到“中文备用版本”基准并确认差异重新计算 → 展开并折叠长文本。

复制和编辑只用于准备演示数据；版本对比打开后不得点击任何保存、复制、删除或其他写入动作。

- [ ] **Step 3: 进行浏览器网络审计**

从点击“对比版本”开始记录请求，直到完成基准切换、展开/折叠和关闭：允许本地静态资源与本地 `/api`；不得出现 AI Provider、招聘平台或其他外部 URL；Resume 写入、AI 请求和导航调用必须为 0。网络审计失败必须抛错并仍进入同一个 `finally`。

- [ ] **Step 4: 写入验收报告**

报告必须包含：分支和 commit、浏览器模式、合成数据说明、父简历默认选择、手动切换到第二基准、摘要和展开结果、网络审计结论、清理路径和结果、端口已释放的证明、未发现真实数据访问的证明。报告不得保留合成简历内容或真实用户数据。

## Task 8: 全量验证、隔离门禁和最终提交

**Files:**

- Create or modify: `docs/reports/2026-08-06-resume-version-review-browser-acceptance.md`
- Read-only verification: all files in the repository

- [ ] **Step 1: 运行定向前端测试**

```powershell
cd web
npm.cmd test -- src/lib/resumeVersionDiff.test.ts src/components/ResumeVersionCompareDrawer.test.tsx src/components/ResumeCard.test.tsx src/components/ResumeLibraryView.versionCompare.mount.test.tsx --run
```

预期：所有新增纯函数、Drawer、卡片和真实 ResumeLibraryView 挂载测试通过。

- [ ] **Step 2: 运行前端全量测试、TypeScript 和生产构建**

```powershell
cd web
npm.cmd test -- --run
npx.cmd tsc -b
npm.cmd run build
cd ..
```

预期：Vitest 全量无失败，TypeScript 编译成功，Vite 生产构建成功。既有警告必须单独记录，不能将失败测试视为通过。

- [ ] **Step 3: 执行完整 fork point、allowlist 和 JD 交集检查**

使用以下 PowerShell 集合算法，覆盖已提交、暂存、未暂存和未跟踪文件：

```powershell
$featureBranch = 'feat/20260806-resume-version-review'
$jdBranch = 'feat/20260805-application-jd-versions'
$featurePath = (Get-Location).Path
$featureBase = (git merge-base $featureBranch db9fad6).Trim()
$jdBase = (git merge-base $jdBranch db9fad6).Trim()
if (-not $featureBase.StartsWith('db9fad6')) { throw "unexpected feature fork point: $featureBase" }
if (-not $jdBase.StartsWith('b4363b0')) { throw "unexpected JD fork point: $jdBase" }
git merge-base --is-ancestor db9fad6 $featureBranch
if ($LASTEXITCODE -ne 0) { throw 'feature fork point is not an ancestor' }
git merge-base --is-ancestor $jdBase $jdBranch
if ($LASTEXITCODE -ne 0) { throw 'JD fork point is not an ancestor' }

$jdPath = $null
$worktreeRecords = @(git worktree list --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'unable to enumerate git worktrees' }
$currentWorktreePath = $null
foreach ($record in $worktreeRecords) {
  if ($record.StartsWith('worktree ')) { $currentWorktreePath = $record.Substring(9) }
  if ($record -eq "branch refs/heads/$jdBranch") { $jdPath = $currentWorktreePath }
}
if ([string]::IsNullOrWhiteSpace($jdPath) -or -not (Test-Path -LiteralPath $jdPath)) {
  throw "unable to locate JD worktree for $jdBranch"
}
function Get-GitNameOnly([string]$repo, [string[]]$gitArgs) {
  $result = @(& git -C $repo @gitArgs)
  if ($LASTEXITCODE -ne 0) { throw "git file-state query failed in $repo: $($gitArgs -join ' ')" }
  return $result
}

$allowlist = @(
  'web/src/lib/resumeVersionDiff.ts',
  'web/src/lib/resumeVersionDiff.test.ts',
  'web/src/components/ResumeVersionCompareDrawer.tsx',
  'web/src/components/ResumeVersionCompareDrawer.test.tsx',
  'web/src/components/ResumeCard.tsx',
  'web/src/components/ResumeCard.test.tsx',
  'web/src/components/ResumeLibraryView.tsx',
  'web/src/components/ResumeLibraryView.module.css',
  'web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx',
  'docs/superpowers/specs/2026-08-06-resume-version-review-design.md',
  'docs/superpowers/plans/2026-08-06-resume-version-review.md',
  'docs/reports/2026-08-06-resume-version-review-browser-acceptance.md'
)
$featureFiles = @(
  Get-GitNameOnly $featurePath @('diff', '--name-only', "$featureBase..$featureBranch")
  Get-GitNameOnly $featurePath @('diff', '--name-only')
  Get-GitNameOnly $featurePath @('diff', '--cached', '--name-only')
  Get-GitNameOnly $featurePath @('ls-files', '--others', '--exclude-standard')
) | Where-Object { $_ } | Sort-Object -Unique
$jdFiles = @(
  Get-GitNameOnly $jdPath @('diff', '--name-only', "$jdBase..$jdBranch")
  Get-GitNameOnly $jdPath @('diff', '--name-only')
  Get-GitNameOnly $jdPath @('diff', '--cached', '--name-only')
  Get-GitNameOnly $jdPath @('ls-files', '--others', '--exclude-standard')
) | Where-Object { $_ } | Sort-Object -Unique
$unexpected = @($featureFiles | Where-Object { $_ -notin $allowlist })
$intersection = @($featureFiles | Where-Object { $_ -in $jdFiles }) | Sort-Object -Unique
$bannedPrefixes = @('src/offerpilot/', 'web/src/services/', 'web/src/types/', 'tests/')
$banned = @($featureFiles | Where-Object {
  $path = $_
  @($bannedPrefixes | Where-Object { $path.StartsWith($_) }).Count -gt 0
})
if ($unexpected.Count -ne 0) { $unexpected; throw 'allowlist violation' }
if ($intersection.Count -ne 0) { $intersection; throw 'JD branch intersection is non-zero' }
if ($banned.Count -ne 0) { $banned; throw 'forbidden backend/service/type/test path changed' }
```

该检查通过 allowlist、禁止前缀和分支交集三重断言，证明没有 `src/offerpilot/**`、`web/src/services/**`、`web/src/types/**`、`tests/**` 或其他共享模块改动。

- [ ] **Step 4: 运行格式检查并确认工作区状态**

```powershell
git diff --check
git status --short --branch
```

预期：`git diff --check` 无输出；提交前只存在 allowlist 内的已审阅文件；提交后 `git status --short --branch` 无 dirty 文件。

- [ ] **Step 5: 请求独立代码复审**

复审范围固定为：纯函数安全值/容器边界、Object.is 优先顺序、JSON Pointer 与稳定排序、Drawer 生命周期、真实 ResumeLibraryView 零副作用测试、fork point 集合算法和 allowlist。复审发现的 P0/P1/P2 必须在本计划范围内修复并重新运行受影响测试。

- [ ] **Step 6: 创建最终提交**

```powershell
git add -- web/src/lib/resumeVersionDiff.ts web/src/lib/resumeVersionDiff.test.ts web/src/components/ResumeVersionCompareDrawer.tsx web/src/components/ResumeVersionCompareDrawer.test.tsx web/src/components/ResumeCard.tsx web/src/components/ResumeCard.test.tsx web/src/components/ResumeLibraryView.tsx web/src/components/ResumeLibraryView.module.css web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx docs/superpowers/specs/2026-08-06-resume-version-review-design.md docs/superpowers/plans/2026-08-06-resume-version-review.md docs/reports/2026-08-06-resume-version-review-browser-acceptance.md
git commit -m "feat: AI add resume version review"
```

最终提交前必须再次运行 `git diff --check`、fork point/allowlist/JD 交集检查和 `git status --short --branch`，并在交付中报告所有命令结果、浏览器验收清理结果和任何未执行项目。
