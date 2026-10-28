# 模型退休第三批：Conversation session 唯一归属

状态：候选实现、当前数据副本验收和独立SPEC/QUALITY最终复审通过；标准门禁/发布待办。
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

尚需最新主线正常合并、标准完整gate/
quiet/备份发布及生产UI/schema/原生结果读回，然后删除本批已验证备份和明确临时产物。
第四批Feedback及第五批剩余历史闭包未完成。最终结构化证据见
[本批JSON](validation/2026-10-28-model-retirement-batch3.json)。
