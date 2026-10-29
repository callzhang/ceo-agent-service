# 模型退休第一批：无 DDL 清理验收

状态：2026-10-28 第一批已标准部署、生产读回与明确归属清理完成。后续四批未完成。

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

## 标准部署与生产读回

完整target gate通过后，标准安静等待/备份/切换/健康流程安装
`c5f39aa8e18d7bceb1b460108374dd296671cab2`，operation为
`deploy-35275d26-718b-4c5b-bf8c-b5385616956b`，07:53:55Z账本succeeded；
PID82581/health200，前端stamp等于安装commit的frontend tree。
没有使用skip-quiet-wait，没有额外生产测试任务或验收发送。

独立只读复核完整476对象SQL数组/FK[]与基线完全相等；status/Attention/History
第一页、第二页、done筛选、既有Attempt16220与session列表均200。370个会话可列出。
History的19条failed身份集合没有增减；status summary前后均pending5/processing0/
failed28/attention0，指标口径不同，不把28与19混为同一列表。

实际浏览器完成History page2（21-40）及done筛选（22877条）、正式Task列表
（403条）和Task862来源详情、Attempt16220 completed/All approved actions verified/
receipt20893、Consumer与Audit分离详情、原生session394条事件。验收标签已关闭。
状态页仍degraded：未解决反馈有1个健康项；native delivery coverage390/不可用0。
本批不修改该反馈业务状态，不把health200宣称为所有业务问题已解决。

## 清理

标准升级备份在重启后被服务07:55的已验证日备份替代清理。独立核对新日备份
包含同一成功operation/目标c5f39、完整性ok、CEOB完成标记、schema/FK一致、
无打开引用；持目录锁并复核inode831033648/mtime1793174119600742034后删除
该514969600字节精确代次，确认路径不存在。没有删除其他代次或生产库。

自有`/private/tmp/ceo-model-retirement.qWbIiJ`无打开引用，1300个普通文件/
64652191字节已删除；标准gate成功克隆已由部署器清理，路径缺失。只保留简洁
验收和第二批无业务数据合同。原生trajectory保留。额外的仓内pyc时间戳批量清理
被自动审批以并发归属不能证明拒绝；该额外范围未执行、未绕过，窄清理正常完成。
本批仅代码/模型入口简化，不声明数据库容量缩减。
