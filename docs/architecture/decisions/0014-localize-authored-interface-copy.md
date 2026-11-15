# ADR-0014: 在展示边界切换中英文界面

- Status: Implemented on feature branch; pending acceptance
- Date: 2026-10-15
- Decider: 用户要求网页和 Windows 桌面提供设置内语言切换；本记录说明实现边界，发布仍待验收。

## Context

界面中文散落在页面、共享组件、辅助模型、错误提示和 Pilot/Haru 展示中。只切换导航或依赖 DOM 文本替换会遗漏确认框、验证、辅助技术标签，且可能错误翻译用户材料或改变业务身份。

网页与独立桌面 Haru 窗口可以复用展示代码，但不应假定 Electron 不同 session partition 共享 localStorage。桌面壳与网页语言实现现整合到最新主线的独立功能分支；源码整合不等于 Windows 同包实装验收通过。

## Decision

1. `web/src/i18n` 持有 `zh-CN` / `en-US`、共享词条、插值和 React 订阅。默认中文。中文源文案也是默认 fallback；确有上下文歧义时使用独立 context key。
2. `t(source, values?, context?)` 仅用于产品编写的界面文案，不接收简历正文、邮件正文、用户输入、AI 回复、角色名或模型文件。插值按原值保留，由 React 转义。
3. 分域词条共同形成一个目录。同键不同译文是错误；缺失词条、占位符不一致和原始可见中文由测试/审计发现。目录使用无原型对象，未知输入不会访问 Object.prototype。
4. 网页偏好保存在当前 origin 的 `offerpilot.locale`，通过 storage 事件同步同 origin 的其他窗口。存储受限时，当前窗口仍可切换，但不保证跨重启保存。
5. 不以 locale 作为工作区或表单的 React key。组件订阅语言，只有展示投影随语言重算；请求身份、任务所有权、状态枚举、确认 payload 和用户草稿不变。
6. Ant Design 使用对应 locale；传给会缓存 options 的组件时按渲染生成新展示对象。已存在验证错误可重新验证其字段，但不提交表单。静态确认框原地更新文案，不关闭、不批准、不重建任务。已显示的应用自有 Toast 使用订阅组件更新文本，保留原实例和计时器；来自服务器的未知错误文本原样保留。
7. 日期/数值按语言展示，时区、币种、金额和选中日期不转换。业务周统计固定已有 Sunday 边界，不随 Day.js 界面 locale 改变。
8. 需要保留的提示状态保存源文案或可重新投影的语义。`UiTextError` 保存固定界面错误的源文案，允许已有错误同步换语言。来自服务的未知诊断和已有内容不做猜测性翻译。
9. Windows 集成在最新主线的独立功能分支移植。原生菜单和独立 Haru 窗使用主进程持久偏好及受限、校验枚举/来源的 IPC；只有合法主窗可改偏好。磁盘保存失败不能报告已保存。主网页分支不宣称已完成实装 EXE 验收。

## Consequences

- 语言切换是展示偏好，不是新的业务授权，不触发邮件同步、模型调用、写操作或确认重放。
- 既有用户/AI 内容可能继续使用原语言。界面语言不保证未来 AI 输出语言；生成语言策略属于独立需求。
- 本地化不改变恢复记录格式；已审核的创建恢复适配器保留原字节边界，错误在展示层本地化。
- 所有词条本地打包，不使用付费翻译服务，也不把内容发送到翻译 API。
- 词条维护需要覆盖新组件；目录完整性、双语行为测试和 mocked 浏览器验收是持续门禁。

## Alternatives Considered

- DOM 扫描/文本替换：拒绝。会碰触用户内容，破坏辅助标签和 React 所有权，也难以保留插值与确认状态。
- 只在入口切换 Ant Design locale：拒绝。只能覆盖组件库默认文字，不能覆盖产品自己的页面/错误/状态。
- 完整引入外部 i18n 运行时：当前两种语言不需要新运行时依赖。保留可按语义拆 context key 的本地目录与小型订阅接口。

## Related

- [文档规范](../documentation-rules.md)
- [ADR-0006: 单条岗位录入的原子创建与恢复](./0006-reliable-single-application-intake.md)
- [ADR-0007: 共享动作展示](./0007-project-action-presentation.md)
- [ADR-0010: Runtime 持有执行](./0010-own-pilot-execution-in-runtime.md)
