# Search and Select Visual System Design

日期：2026-08-29  
状态：已获用户确认（B + C）

## Goal

统一 React 控制台中搜索框、下拉筛选和高频状态筛选的视觉与交互，解决不同页面控件各自为政、浏览器默认样式突兀、移动端密度失控的问题。

## Selected direction

采用 B + C 的组合：

- B `Control bar` 是默认容器。搜索框占主要宽度，下拉筛选与搜索框放在同一浅色、圆角、带边框的控件条内。
- C `Chip filters` 用于高频、低基数的状态筛选，例如 Attention 的 Failed / Processing / Pending，以及 History 的状态筛选。
- 搜索框、下拉和 chip 共享同一设计 token：边框、圆角、focus ring、文字颜色、禁用态和触控高度。

## Component boundaries

### SearchField

- `value`、`placeholder`、`onChange`、`onClear`、`aria-label` 由页面传入。
- 有值时显示清除按钮；清除按钮具有明确的 accessible name。
- 使用搜索图标、统一高度和 focus-within ring。
- 保留文本输入原生键盘行为；`Escape` 清除为增强行为，不阻断提交或输入法。

### SelectField

- 使用原生 `<select>`，外层提供统一视觉容器和下拉箭头。
- 保留键盘、屏幕阅读器和移动端原生选择体验。
- 页面继续控制当前值与 URL query，同一字段不在组件内部复制状态。

### FilterBar

- 桌面：`minmax(0, 1fr)` 搜索框 + 固定/有限宽度 select。
- 移动端：自动单列堆叠，控件高度不低于 40px。
- 支持左侧主要筛选和右侧分页/总数信息，不改变原有 query 参数。

### FilterChip

- 适用于有限、可快速扫描的状态集合。
- active 状态使用浅绿色背景、深绿色文字和明确边框；数量作为次级强调。
- 不把无限类别、长文本或复杂多选塞进 chip。

## Application scope

- Agent：任务列表搜索采用统一 SearchField；不改变本地任务过滤行为。
- Tasks：搜索、类型、状态、排序、每页数量；控制条采用 B，有限状态可逐步采用 C。
- History：搜索、状态、对象类型、每页数量；状态可采用 C。
- Feedback：搜索、反馈状态、每页数量；状态可采用 C。
- Attention：搜索/状态快速筛选（若当前页面已有状态控件）；Failed / Processing / Pending 使用 C。
- WeChat connector：联系人搜索采用统一 SearchField；类型或范围筛选使用 SelectField。
- Settings：已有 pill/tab、输入和 select 使用相同 token，修正 selected/focus 对比度，但不改变页面 IA；Agent Runtime 的模型选择也使用统一 SelectField。

## Accessibility and behavior

- 每个输入和 select 有显式 label 或等价 `aria-label`。
- focus 状态同时使用颜色和轮廓，不只依赖颜色变化。
- selected chip 使用 `aria-pressed` 或等价语义；原生 select 保持原生语义。
- 清除、筛选、分页不会改变既有 URL 参数命名和历史行为。
- 加载、错误、空态由页面继续负责，组件只负责视觉和局部交互。

## Non-goals

- 不引入新的 UI 框架或重量级 select popover。
- 不改变 API、数据模型、筛选语义、路由和持久化逻辑。
- 不在本次改造中重构表格数据渲染或移动端卡片布局。

## Verification

- 组件测试：有值/无值、清除、focus、disabled、select change、chip selected。
- 页面测试：Tasks、History、Feedback、Attention、WeChat 的 query 参数保持不变。
- 响应式检查：桌面 1280px 与移动 390px 不产生横向页面溢出，控件可点击且标签可读。
- 构建检查：TypeScript、Vite build、前端完整测试、`git diff --check`。
