# Meeting 结果写入与旧 Run 入口清理验收

本轮无 DDL。Meeting 结果更新从补默认行再 UPSERT 改为一次显式 `(task_id,result_kind)` UPSERT；插入保留原默认值，更新只修改调用方传入的字段。事务、生命周期、外部执行、审查、授权、重试和回执语义不变。

删除无运行时调用的 `app/logical_run_storage.py`。保留当前 Run owner 测试，退休旧来源 writer 专用断言；验收脚本通过 `runs.id` 与 `run_provenance` 查询 Meeting 详情，不再依赖旧容器列、固定 Project54 或固定40条历史记录。显式历史声明／迁移工具保持原有范围。

## 本地证据

- Meeting 回归先在旧实现观察2项失败（每次更新实际写2条SQL）；修复后 Meeting 及新增回归157项通过，29.69秒。
- 原生 Run 与验收脚本测试8项通过，5.96秒。Ruff、差异检查及独立 SPEC／QUALITY 审查通过。新增两份回归及当前 Run owner 测试纳入标准门禁。
- 当前生产派生副本514,969,600bytes，quick_check=ok。更新后的脚本读取13个API：History两页、Meeting筛选、Attention、队列、任务与待办、Task详情、Meeting Run400681、Attempt16397及Email预览／原文不可用。持久值逐表fingerprint前后一致；邮件来源为替身，未启动lifespan、worker或真实发送。
- 发布前生产安装676770955；完整sqlite_schema清单464项（含SQLite内部声明），本文的固定排序／编码SHA256为9188be45ccf5967c560fef01b1e13462d30673d58c7884575dc31fd0835b1690，FK异常为空。发布后将使用同一算法逐项比较；不与此前采用其他筛选规则的463项摘要混用。

## 发布与清理

待标准精确提交部署、生产读回及本轮产物清理；目前仅本地及副本验证通过，不表示已上线。机器可读记录见 [验收JSON](validation/2026-10-29-meeting-run-cleanup.json)。
