# 模型退休第三批：Conversation session 唯一归属

状态：第三批已完成标准部署、生产读回、精确归属备份及临时目录清理。
来源为实际已发布44c13bc91的Store28.1/Email54，目标Store28.2/Email54。

## 数据与调用合同

删除conversations的codex_session_id/codex_session_contract_hash两项payload键及其
两个VIRTUAL列。权威值只在conversation_runtime_sessions按conversation_id+route_name
保存。48表/463对象不变，1234→1232列；目标compact schema摘要
8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a。
普通ConversationRecord只承载元数据；session列表使用现有OAuth关联组装当前session DTO，
HTML/API原有URL及响应identity/detail_url不变。Attempt缺失自身session时直接读现有
canonical OAuth owner。Run/Attempt的原生session事实不动。

普通metadata upsert去掉session参数。清理旧getter/setter/clear与双写及其无用开关。
Consumer观察到session时统一保存已有任务的普通会话元数据，不按路线分支；canonical
会话持久化规则不变。QUALITY发现若只删除旧OAuth分支，WeChat非worker来源会话可能
缺metadata而被列表JOIN遗漏；该案例先RED再GREEN，最终复审单列，不隐藏这次发现。
CLI reset删除canonical OAuth槽，保留其他路线；原有全路线clear及route-specific CAS不变。
现行功能测试改用metadata+canonical写入，不为测试恢复旧API。

## 显式正反迁移

只允许准确28.1/54来源。迁移前逐行检查非空镜像ID/hash与同一canonical OAuth槽一致，
缺失/非空冲突则拒绝；不是按标题、整数碰撞或相似文本关联。使用TEMP复制state的
四个原生存储列及ID，只有conversations payload移除两键；其他payload字节不变。
重建准确目标state并保留原索引/FK/当前sqlite_sequence全部类型与水位，失败事务回滚。

inverse从当前canonical编码leaf重建完整旧镜像，不另外保存旧空hash；这是Derek已确认
的回退语义。缺canonical的普通元数据行恢复NULL session与空hash，不造假会话。
当前constructor只接收当前声明；前序frame和必要历史predecessor使用冻结声明，不混入
运行时fallback。源28.1和目标28.2准确catalog逐项验证。

## 副本和测试证据

生产派生副本49张原生表（含sequence）逐事实/类型/身份hash通过forward、新Task/Run/
Config/Action/替身receipt、新canonical会话写入、inverse、真实44c13bc91旧代码冷启动
读回/写入，再reforward及当前代码冷启动写入。三组新增会话均可跨周期读回；原有
canonical session payload逐字节一致，原始行保留事实无变动。CLI和网络调用被替身
验收子进程拒绝，没有生产测试任务或对外发送。旧代码是git archive准确源码，QUALITY独立比对全部463个app文件，0缺失/0不同。

定向分组234 Consumer、546 Store/Task/runtime、717 worker/CLI、216 State upsert/bulk、
147 State storage及根代理41 release/selector/artifact/native-feedback用例通过；分组
有重叠，不求和宣称独立case总数。根代理schema中断回滚、冲突/缺owner拒绝、typed
codec/water、旧constructor拒绝和当前选择器测试保留。现行feedback receipt测试改为
最新真实paired来源，保留原有业务事实/回执断言，不再用退休的旧Signal来源造数。

三项直接turn测试失败已在44c13bc91旧代码逐项重现，源于原夹具使用未绑定Developer的
裸hash；测试改用当前已有会话合同，保持compact→完整重试、同session、原生Claude及
跨路线断言，并增加裸hash不匹配断言。原三项及完整60项turn文件通过，相关45项通过。
没有改变session恢复、compact选择、Audit或授权政策。QUALITY复审通过元数据修复，
无Important/Critical问题。

## 标准发布、生产读回与清理

正常合并日报修复e20e61c91后17项受影响测试通过，准确安装
`af24bc48595c0f3eb0edd80d9730f10fb37a45c6`。完整强制gate、默认quiet、验证备份、
paired迁移/重启/健康流程通过，09:47:41Z账本succeeded，operation
`deploy-1e77f697-9fdb-4c30-a83a-792ecf3b349c`；PID71305/health200/buildstamp一致。
实际463项完整SQL/FK[]与目标逐项相等，两镜像payload键/生成列均确认物理消失。

状态/Attention/History1、2页及done筛选/Attempt16220/canonical会话列表API均200。
488条权威OAuth会话可列出；实际UI session列表、History21-40 page2和完成筛选、
既有native session详情、All approved actions verified及receipt20893可读。
部署前后的历史失败身份集合无增减；summary failed26/Attention0，processing按真实
业务变化。未解决反馈健康项仍degraded1，native delivery401/不可用0；本批不代替
业务反馈处理，没有生产测试任务或验收外发。

daily替换代次inode831847485/mtime1793180909597007210/514969600字节在目录锁下
重新验证CEOB、integrity_check ok、精确schema/FK[]及本批operation，无打开引用后
用精确代次护栏删除并确认不存在。明确临时根3578文件/716702531字节（副本、旧代码、
测试目录）确认不存在，验收tab已关闭；生产库和原生trajectory保留。
第四批使用另一任务专属临时Git worktree继续开发，不纳入本批已完成清理。

第四批Feedback及第五批剩余历史闭包未完成，最终结构化证据见
[本批JSON](validation/2026-10-28-model-retirement-batch3.json)。
