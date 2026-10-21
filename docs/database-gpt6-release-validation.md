# GPT-6 数据库副本与发布验收

本记录对应 Store `2026-10-19.11`／Email53 数据库重构，承接[九字段退休验收](business-model-legacy-fields-root-acceptance.md)。Derek 在 2026-10-20 明确选择先以真实 GPT-6 在副本重跑完整链路，不新增事实核对工具，副本与 schema inverse 通过后再按标准流程部署。

## 范围与来源

- 隔离 worktree：`codex/database-gpt6-release`，数据库重构基础提交 `892af8b15`，整合 upstream `d812e0439` 的合并提交 `1b49b0573`。开发主目录中的其他未提交工作未改动。
- 新鲜生产来源：运行提交 `2cb011cc8f54af4bff127179fde731aa719ab4a3`，SQLite readonly backup，Store19.8／Email53，486,514,688 字节，SHA-256 `3a450d4797ee97ea4d8b50b7ceb482b53a63d30cd8840c39d9876df5c790a8ab`。
- 原完整性检查 `ok`；原 FK finding 为 `runs` ROWID 190042 引用 `tasks` 的一条既有问题，两向保留该 finding，不宣称原库没有 FK finding。
- 正式发布入口在完整副本上正迁至19.11，保留事实使用源侧列名、SQL 类型、ROWID、真实 sequence、父子引用及 FK finding 独立比较，不使用模型结果作为 schema oracle。
- 九项已批准控制／评估字段永久退休；schema inverse 只补中性默认值。provenance、必要业务事实、完整 changes_json、follow_up_question、import_task_id 和回执继续保留。

## GPT-6 型号与原生来源

首次按生产 `.env` 已保存的设置使用 `gpt-6-luna/high`，ChatGPT 登录通道返回400，尚未产生候选或执行任何发送。独立 `gpt-6-sol` 探针同样返回400。已有 API 路线的 Luna 探针返回429，未将这些调用计为语义验收。

应 Derek 要求重新通过原生 `codex app-server` 的 `model/list` 读取实时清单：当前 ChatGPT 通道的 GPT-6 只有 `gpt-6-astra`，且标为默认；API `/models` 列出的 Luna／Sol 不表示它们能在 ChatGPT 通道使用。 进一步只读核对：生产进程 Settings 当前有效值仍为 `gpt-5.6-luna/high`，与文件中已保存的 `gpt-6-luna` 不同；`load_env_file()` 在新进程导入配置时读取该文件，launchd 未另设模型覆盖。不能把文件改动说成当前进程已使用新型号，也不能假设重启后有效型号不变。最终验收明确固定单一路线 `codex_oauth`、`gpt-6-astra/high`，不自动切换到旧模型。

脚本 [validate_business_model_gpt6_copy.py](../scripts/validate_business_model_gpt6_copy.py) 在不同进程分别执行 prepare／select／finish。真实 Consumer／Audit 只读取本任务合成 artifact 和现有系统动作契约；执行端使用现有 InMemoryProvider，对外真实发送次数必须为0。每次调用的实际型号及 effort 从原生 trajectory 的限定行范围读取，原轨迹仍只保存在 Codex 自己的空间，验收记录只保存定位和元数据。

保留原用例完整标记 `NATIVE_SELECTION_9178ab3793`、三个 units `23/27/50` 和旧的“完整标记及100在发送正文中”断言；条数3是额外明确标注的新覆盖，允许既有 `(via agent)` 附注。没有新增事实核对工具、Audit 策略或业务字段。旧5.6 Luna两次漏抄失败仍是历史FAIL，不被本次结果改写。

## 合并兼容与检查

上游新 Meeting Attention 查询仍用历史 Run namespace，整合到C2后返回空链接。其测试先因退休 writer 失败；使用当前 claim_logical_run／create_logical_run 建立相同fixture后，精确复现空链接与 `/meeting-attempts/2` 不等。运行代码改用现有 logical_run_projection 后，6项链接检查通过。没有新增路由或兼容分支。

- 后端首次119项 scoped检查：118通过，唯一失败为上述旧测试writer；修正后重跑全部6项Attention链接检查通过，重叠项不相加。
- 前端4个相关测试文件107项通过，TypeScript／Vite构建通过。
- 新验收脚本及链接改动 Ruff通过；SPEC审查已通过。迁移与回退完成后，独立 GPT-6 Astra／max QUALITY 审查通过数据库代码、原生副本与当前事实往返验证；生产重启配置选择仍单列待确认。

## 当前验收状态

原生完整链路、当前事实 inverse／真实生产旧代码冷读／reforward 以及最终部署状态，见同目录 validation/2026-10-20-database-gpt6-copy.json。记录未明确通过的阶段不能由本页面或迁移成功推断为已完成。

## 最终副本结果与待发布项

真实 GPT-6 Astra／high Consumer、Audit 各一次成功；两者原生轨迹均为上述实际配置、同一完整 artifact hash，shell调用0。公开选择 handler 返回303并持久化 execute 选择；另一个进程的真实 SystemExecutor／Worker 完成 Task。内存替身发送1次、核验1次；完整标记、条数3、总和100均正确，原11张普通表已有行不变。结果、动作尝试和发送记录都指向同一回执 owner，没有新增模型调用。验收脚本在正常 needs_human artifact 清理后的收尾假设已修复：hash提前计算，后续只从原生工具读取结果复核；同一次成功运行恢复metadata后继续执行，没有重跑模型或复原来源文件。

完整当前数据往返用494.85秒通过：inverse恢复真实19.8 schema SQL及marker原时钟，九字段只回填中性值；原2cb生产代码冷启动读取975个旧Project、3,327个旧Todo、5,468个旧Update以及本次新增已完成Task、Consumer/Audit Run、选择、执行和同一回执；全部typed事实、SQLite序列和原FK finding保留。旧构造器冷读不改变事实，reforward恢复目标SQL并保持同一结果。

页占用比较仅表示完整19.8→19.11组合迁移：主文件仍486,514,688字节；空闲页从11增至1,103，可复用约4.27MiB。未执行VACUUM，不能把它描述为主文件已变小，亦不能将组合节省全归于九字段退休。

**尚未部署。** 标准部署将创建新进程，生产文件中已保存但该OAuth通道不支持的 `gpt-6-luna` 可能生效。目前等待Derek确认是否同步使用已验收的 `gpt-6-astra/high`；没有擅自切型号、回写旧设置、触发重启或修改生产DB。完成该选择后，按标准 `python -m app.deploy --target-commit <已推送的完整main SHA>` 发布，并核对新PID、healthz、队列、Attention、History及实际schema；这些上线结果不能由本次副本PASS推断。
