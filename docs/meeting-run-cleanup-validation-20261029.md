# Meeting 结果写入与旧 Run 入口清理验收

本轮无 DDL。Meeting 结果更新从补默认行再 UPSERT 改为一次显式 `(task_id,result_kind)` UPSERT；插入保留原默认值，更新只修改调用方传入的字段。事务、生命周期、外部执行、审查、授权、重试和回执语义不变。

删除无运行时调用的 `app/logical_run_storage.py`。保留当前 Run owner 测试，退休旧来源 writer 专用断言；验收脚本通过 `runs.id` 与 `run_provenance` 查询 Meeting 详情，不再依赖旧容器列、固定 Project54 或固定40条历史记录。显式历史声明／迁移工具保持原有范围。

## 本地证据

- Meeting 回归先在旧实现观察2项失败（每次更新实际写2条SQL）；修复后 Meeting 及新增回归157项通过，29.69秒。
- 原生 Run 与验收脚本测试8项通过，5.96秒。Ruff、差异检查及独立 SPEC／QUALITY 审查通过。新增两份回归及当前 Run owner 测试纳入标准门禁。
- 当前生产派生副本514,969,600bytes，quick_check=ok。更新后的脚本读取13个API：History两页、Meeting筛选、Attention、队列、任务与待办、Task详情、Meeting Run400681、Attempt16397及Email预览／原文不可用。持久值逐表fingerprint前后一致；邮件来源为替身，未启动lifespan、worker或真实发送。
- 发布前生产安装676770955；完整sqlite_schema清单464项（含SQLite内部声明），本文的固定排序／编码SHA256为9188be45ccf5967c560fef01b1e13462d30673d58c7884575dc31fd0835b1690，FK异常为空。发布后将使用同一算法逐项比较；不与此前采用其他筛选规则的463项摘要混用。

## 首轮完整门禁与时间夹具修正

精确提交e90567bd0首次完整门禁：7989通过、1失败、2跳过、1取消选择，538.11秒；生产未切换、未创建部署备份。唯一失败是历史Runs11声明对照测试：仅created_at显式固定，但RuntimeAttempt默认started_at/updated_at在两份顺序执行的库中跨秒。窄修仅固定两条测试连接的SQLite current_timestamp，保留原DDL及逐值／序列断言；整文件99项通过，独立SPEC／QUALITY通过。标准门禁须在新提交重新通过。

## 发布与清理

与发布负责人合并共享门禁，精确提交 `d68ffd943b2201b302f652d09c57d0747268ad74` 已标准部署，包含本轮运行时清理e90567bd0及独立测试时钟修正5ab435fb3。完整门禁7990通过、2跳过、1取消选择，439.41秒；10分钟／2GB、安静窗口、备份和失败回滚均保留。并行负责人自己的已批准退订修复独立提交／负责，不属于本轮清理的政策修改。

独立生产读回：PID95719、health200；frontend stamp395fce9b6e3ddd924881471c36fbb654ac6ef7dd与安装frontend tree一致。完整464项SQL逐项相同、摘要9188be45…1690不变，FK异常仍为空。API队列快照active0／queued4、Attention1；History两个10条分页不重叠、Meeting筛选和原生详情400690均200。页面重新加载Meeting400681，正确显示Task417899和sent，实际翻History第二页并应用Meeting筛选（2897条）。该已发送Task的task_results全行摘要仍f63b3b9a…5c37，消息／发送结果／日历事实未变化。没有新建生产测试任务、真实发送或重放旧消息。

本轮失败门禁副本和4个精确空模板已核实无打开引用后删除，共140,914,086逻辑字节；不当作数据库容量收益。临时验收标签已关闭。本轮生产副本、测试、脚本、日志和SDD独占根目录均已删除，共548,624,110逻辑字节；共同门禁残留的空库模板及其sidecar也在核实Task/Run为空、无打开引用后删除（5,787,648逻辑字节）。成功门禁clone由标准流程自动清理。

共同发布备份由发布负责人完成CEOB标记、integrity=ok、与生产schema一致／FK0及代次核对后删除；精确代次inode839526085／mtime_ns1793332688212989325／514,969,600bytes。本人随后持备份目录锁独立确认主文件和三个SQLite sidecar均不存在、目录为空。已保留生产数据库与原生trajectory，没有删除其他任务文件；逻辑删除大小不宣称为数据库文件缩小或物理磁盘回收量。

一次协同状态报告称8765无监听，本人立即用本机有权限的只读请求和lsof复核：health200、同一listener PID95723仍在；未观察到服务中断，也未因此重启或改写生产。

本轮代码、测试、标准部署、生产读回和清理均已完成；不宣称后续Attempt／Run及Email参数归属精简已实施。机器可读记录见 [验收JSON](validation/2026-10-29-meeting-run-cleanup.json)。
