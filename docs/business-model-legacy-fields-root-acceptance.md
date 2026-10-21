# 旧 Project / Todo / Update 九字段：根任务验收

2026-10-20。九字段实现、独立 SPEC / QUALITY 审查、两套真实全量数据库副本的正迁—新增事实—schema inverse—实际旧代码冷读—再次正迁均已通过。本记录不表示生产已迁移。整批候选仍有此前两个 A5 原生内容验收 FAIL，`release_ready=false`；没有将该失败改成通过，也没有增加 Audit 策略。

## 批准的处置

永久退休 Project 的 priority、risk_level、needs_derek_attention、next_follow_up_at、follow_up_mode；Todo 的 priority、next_follow_up_at；Update 的 confidence、merge_reason。逆迁仅恢复结构并填中性值，接受旧九项值不可恢复；不另存旧值归档。其余字段按源列名逐 SQL 类型/值核对，包括完整 changes_json、follow_up_question、现有物理 import_task_id、provenance、业务事实和回执。字段解释和 51→42 完整映射见[映射](business-model-legacy-fields-mapping.md)。

## 源码与独立审查

实现基于 `8ff29625154988e4ff32f6a076ffc7595a1af120`。冻结清单 SHA-256 `a1f176780031030ca36503529643e37e6407b49fea2b3584003b258103f9785d`，补丁 SHA-256 `0cfbdc6076f9e579839762e23916c363dad755d8149b3ecdf20bf1da550d8e63`；根任务和两名独立审查者均核对 53 个变更文件、449 个 app 文件。SPEC v2 PASS，QUALITY v2 APPROVE。审查后没有修改 app/frontend/test 字节，仅补充本验收文档。

QUALITY 另在独立副本验证了 schema 已提交、最终构造器抛错的路径：随后新增当前 Task，再执行公开 rollback，恢复原 SQL 与二进制 marker 时钟，保留新增事实；重复 rollback、真实旧代码冷读和再次正迁均通过。范围测试及已在真实旧版本重现的 14 个失败基线见[实现验证](business-model-legacy-fields-validation.md)。各测试批次重叠，不累计为独立用例总数。

## 全量副本往返

固定 Python 3.12.11 / SQLite 3.53.4；源为生产只读 backup，源 SHA-256 `60ee5d5b88395235768868deda215b5b371facf931522932463a29d474f539a2`，源代码 `763caecd3ccb550aa3c2608eac6746d8ff3296d4`。975 Project、3,327 Todo、5,468 Update。目标 Store `2026-10-19.11` / Email53。

| 输入 | 验证结果 |
| --- | --- |
| 实际生产 19.8 / Email53 | 单事务组合既有 A5、C2 和九字段迁移；保留公开事实精确一致；写入合成正式 Task 和替身待办回执；当前数据逆迁回准确原声明及原 marker 时钟；实际生产 Git 版本的 Store/Email 构造器冷读全部旧 DTO 和新增事实；再次正迁精确一致 |
| 已验证的 19.10 / Email53 副本 | 同样的完整往返；冷读使用真实 `8ff2962`；两条输入路径各自独立核对，不以其中一条代替另一条 |

核对含 SQL 类型、保留值、物理 ROWID、公开身份与关系、sequence、Email 账本、原声明和原 marker 时钟。既有 A5 私有子行退休采用既定公开事实归一核对，不能声称退休子行的物理 ID 仍存在。源 FK multiset 的既有一项 `runs / 190042 / tasks / 1` 精确保留，没有宣称零 FK finding。

每条路径只新增一条明确标为 synthetic 的正式 Task（736）和一条待办 outbox 回执。替身实际调用 create、readback；provider effect 为 completed，Task 业务状态仍 open。回执 hash 在逆迁、真实旧代码冷读和再次正迁后保持一致。这不是 Consumer→Audit 真实模型链路的新通过证据；没有启动模型或真实 provider，没有对外测试发送。

两项验证脚本修正均未改变产品源码：

- 新回执完成后，现有 dispatcher 的“仅保留最新 10,000 个终态 lease”清理了一条最早旧 lease。初次过严的原行断言失败；修正 oracle 后，独立按原排序/限额算出唯一应清理 ROWID 7231，并逐值核对所有其它原行。迁移前后 State 仍要求精确一致，未豁免整个 State。
- 19.8 实际旧代码冷读最初因 JSON 文件尾部混入 slow-SQL stderr 而中断。保留首次日志，将 stdout JSON 与 stderr 分文件后，从同一个已核对的 inverse 副本继续冷读与再次正迁；没有重造通过数据库。

## 页面与 API

在已验证的 19.11 副本启动独立控制台，关闭应用 lifespan，未启动 scheduler、recovery 或 worker。使用实际构建 `app/static/workbench`。浏览器点击历史 Project 54，看到“历史记录·非正式项目”及可展开/折叠的历史事实；只读详情 API 返回 8 Todo、20 Update，九个退休键均不存在。React 页面未直接显示全部 8/20 行，故不把 API 核对说成全部行已在页面逐条点击。

正式任务页输入 `Synthetic field-retirement`，收敛到 1 条，点击 Task736，核对负责人、承诺日期和待处理状态，展开 1 条日期依据和 1 条钉钉待办记录。页面只呈现记录摘要，完整回执内容由数据库逐值/hash 核对，未声称页面显示了完整回执。测试标签已关闭，副本控制台已通过自身 uvicorn 生命周期正常退出。没有操作生产标签或真实待办。

## 容量效果

19.10→19.11 正迁后、新增测试事实前：

| 对象 | 迁移前（字节） | 迁移后（字节） |
| --- | ---: | ---: |
| Business 表及索引 | 11,751,424 | 11,194,368 |
| Actions 表及索引 | 172,855,296 | 170,668,032 |
| 数据库主文件 | 485,871,616 | 485,871,616 |
| freelist 空闲页 | 1,642,496 | 4,407,296 |

两组表及索引实际占用减少 2,744,320 字节（约 2.62 MiB）。没有 VACUUM，主文件未缩小；释放页可供后续复用，不能报告已回收对应磁盘空间。本次只批准九字段，未删除完整 changes_json 或其它保留业务事实。九项源值逻辑字节总量 1,386,821，不承诺大幅缩库。

## 未通过的独立原生验收

此前两个真实 Consumer 都读到完整合成来源标记 `NATIVE_SELECTION_9178ab3793`，最终候选却写成 `NATIVE_SELECTION_9178ab379`；两个独立 Audit 也读到了完整来源，但批准了缩短后的候选。原生 JSONL 中实际工具返回、候选和审查输入已核对，四次工具返回均有完整来源标记及 hash。数据库候选冻结、digest 绑定、选项引用、最终执行链都忠实保留被采用的候选；没有确认出本次数据库所有权机制缺陷。

因此两次验收仍为 FAIL。没有第三次重复同一 case，没有减弱断言、单例关键词补丁或新增来源正文副本。若要增加“来源事实与候选声明必须进行确定性核验”的机制，这是单独的 Audit/证据政策变更；仓库 AGENTS 明确要求单独提出并确认。标准生产发布与 PID、health、队列、Attention、History 读回尚未执行，不能称为已上线。

本记录旁的[机器可读证明](business-model-legacy-fields-root-proof.json)仅保存上述有界结果、hash、数量和容量，没有旧控制值、来源正文或 trajectory 副本。临时原始验证输入仍仅用于尚未结束的发布/阻塞判断，不作永久历史备份。
