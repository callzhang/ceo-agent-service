# 模型退休第一批：无 DDL 清理验收

状态：2026-10-28 隔离 worktree 候选，实施、SPEC/QUALITY审查通过；尚未发布。

## 范围与基线

Derek 已批准五批实施计划。第一批仅清理无调用、测试专用旧 API、不可达发布
fallback 和执行身份旧参数；不改变 schema、业务行、审查/授权/重试/发送政策。
源码基线为正常合并主线后的 `9742c0fd5`；审计/已批准计划已保存为 `a8d5a2923`。
后续合并和部署以精确提交另行记录，不把基线当作安装版本。

实施前生产安装 `bb9f51b13cb9ccb9bfe6b9369dec3543422f6850`；
schema476对象 SHA-256 为
`8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f`，
FK 检查为空。口径与全库审计相同：非内部 `(type,name,tbl_name,sql)` 排序数组，
UTF-8 JSON ensure_ascii=False/separators=(',',':')。当前 fresh constructor 的
完整对象数组与生产逐项相等，不仅比较对象数量。

发布前 health、status、Attention、History第一页和第二页失败筛选全部 HTTP200；
Attention0，History总数39076，历史失败19条。已保存失败稳定身份用于发布后比较，
历史数值是检查时刻的基线，不要求强行归零。

## 候选变化

- 删除无调用/测试专用 API 和独占 helper，当前功能测试使用现有原生 owner。
- 删除 runs11 旧写入模块和专用测试/gate行，保留实际当前搜索、样本和事件功能。
- 当前 receipt/Reviewed 写入统一 execution_run_id，正面声明当前字段；显式
  旧转换器冻结其来源 writer，不在当前 helper 保留旧列选择。
- 发布选择器严格校验声明后只读当前 owner；删除七个不可达旧表分支。
- 调度测试使用正式 adapter，明确设置模拟进程存活并核对 source/ledger 双方。

## 验收进度

本批删除38个函数/方法（含独占私有 helper）及旧 Run11 DTO/写入模块；精确符号、
测试命令和状态见[结构化证据](validation/2026-10-28-model-retirement-batch1.json)。
七个发布选择器函数保留，只有不可达 fallback 退休。

实施前声明/发布测试16项通过（11.11秒）。候选冻结后的相关分组通过：声明/
发布/生命周期/Provider/Run/Configs67；调度/Attention/dashboard125及三项新增
source-ledger断言；Business/schema/replay/Meeting476；Email/source/Task/Run146；
Reviewed/DingTalk/输入92；creator/inverse110；adoption28；Action release/assembly43；
receipt12；RuntimeAttempt子集51。分组包含重叠，不相加成不同测试总数。
根代理最终独立运行当前 receipt/声明19项通过（4.31秒），完整默认 Ruff57个
变更Python文件通过，diff检查通过。fresh476对象数组再次与生产基线逐项相等。

早期六个旧adoption夹具错误、调度进程存活/claim行夹具错误、四文件import位置
lint均已修正并通过对应复验，不删除有效断言或放宽规则。独立SPEC/QUALITY审查
均通过，没有Important/Critical问题；QUALITY另行复核57文件默认Ruff通过。

尚未完成：最新主线集成复验、标准gate/部署、生产
版本与页面/原生结果/历史回执读回，以及本轮临时文件和已验证备份清理。
无数据库容量缩减声明；未创建生产测试任务或真实发送。
