# 文档写入核验与续跑恢复

## 范围与根因

执行用户批准的文档写入核验与续跑恢复计划，只修改共用文档结果交接。基线参见 `scheduled-continuation-closure-verification.md`：52 次已写入但核验失败，11 组出现 24 次额外同内容覆盖。不改 Audit、prompt、session、重试次数或 DWS 核验规则。

固定历史样本来自 Consumer396024 原生 JSONL 的零起第1137行写入、第1140行错误、第1144行读取、第1147行读回；提交正文 SHA 为 `8c7f34b360b3914999a1b0f0a515da80a5f845f287d11b0baa3ee7af312e06b0`。同样正文在1474、1592、2049、2211行重复提交。没有执行生产写入或任务重放。

使用本机 DWS v1.0.65 对应源码 `92c4f8c355c749ec470c68f998d862a00d7d1709` 的原生比较函数离线复现：预期和实际各801个结构token，51处不同；50处为表格/单元格右对齐丢失，另1处为文本空格差异。原始Markdown的右对齐分隔符在Markdown读回中变成无对齐分隔符；下方完整JSONML复核证实实际文档仍保留右对齐，不能把该导出差异说成文档格式丢失。此证据不能支持忽略差异或宣称内容完全一致，也不能证明所有52次失败均只有相同原因。匿名双列表格控制验证：完全匹配通过，右对齐丢失及数值改变均拒绝。未修改或发布 DWS。

DWS 的 `docVerificationError` 生成 `doc.operation.v1/status=partial_success`、目标、已执行步骤、失败阶段及不可重试信息。CEO 服务 `run_json` 只保留格式化短错误与message/server_key，丢失其余字段；`_write_document_artifact` 在异常时没有返回结构化结果。历史错误正文不能恢复已丢失字段；此处的原生结构由准确版本源码与固定子进程返回测试验证，不伪称为原始完整错误回执。

## 最小改动

- `DwsError` 在内存中携带原生 `provider_error`，保持现有格式化显示与其他调用方行为。
- 文档工具对对应 `doc.create/doc.update` 的原生部分执行错误，返回 `ok=false/status=partial_success/verified=false`，保留目标、正文SHA、原错误和可用provider结果，`readback=null`。不自动读写、不生成成功读回回执。
- provider写入已返回后，目标目录解析或服务读回失败也保留已有结果；创建回执已给出nodeId时先保留其身份。写前校验、权限拒绝及提交未知仍抛出原错误。正常成功路径不变。
- 不因该结果给 Audit 制造成功文档回执；现有投影要求readback为对象，本次失败返回null。

## 验证与发布

新增回归先失败：初始7例缺失字段/结果；独立审查发现创建后目录失败缺口，新增4例先失败并修复。首轮受影响测试403通过，最终补测结果及发布读回在下方追加。没有重跑全量测试或模型任务。

生产验收终点仍待：上线后首个自然相关运行的真实失败/成功结果及是否继续发生同机制重复覆盖。没有自然样本时只能声明已部署、生产行为待回读，不能据本地通过关闭全部目标。

本地最终验证：初轮403项通过；创建后恢复修复后的文档/角色定向测试65项通过；新增回归最终14项通过（含不生成成功Audit文档回执的既有投影检查）。Ruff与diff检查通过。独立审查唯一发现已修复，复核无剩余问题。后续未重跑无变化的DWS客户端与编排测试。

## 生产发布读回（2026-10-24 04:06 UTC）

修复提交 `3638ea4a7` 经 `9cd3394d9` 合入 main；随后另一会话的正式部署将生产更新至 `d960fb9e6f7870ab036454cb8436a14cf802dc67`，祖先检查确认包含本修复。部署回执 `deploy-fe288dd7-4691-45fa-98fc-c8dbe64c5438` 为 succeeded、error为空、installed_commit与生产一致，完成时间04:05:14 UTC。本会话发现并发部署后撤下自己仍等待仓库锁的进程，未启动第二次发布、未强制中断任务。

生产工作树干净，launchd PID由93450变为22671。healthz、console status、Attention、History均HTTP200；Attention6项，History37884项。dispatcher在途均0，scheduled排队5、Meeting排队4；Reply失败12/Attempts失败13/Meeting失败1/Meeting Memory失败4与部署前相同，未将既有失败误报为本次回归。Email provider恢复处理（pending225/processing1）。这些是运行健康与恢复读回，不证明文档业务结果。

04:06 UTC只读查询：部署完成后新增Consumer运行0项，没有可用于该文档失败机制验收的自然样本。当前状态：**修复已部署；生产行为待自然样本回读，尚未确认24次额外覆盖问题在真实任务中消失。** 后续只检查首个自然相关运行的完整轨迹与文档结果；不人工重放历史任务。临时DWS源码、业务样本及诊断token文件已清理，未持久保存业务正文。

## 后续自然运行与JSONML复核（2026-10-24）

生产已前进至 `01761730b`，继续包含服务结果交接修复。部署后3次Consumer运行398422、398433、398583均已完成，对应两份原生JSONL共267行（同session修订不重复累计）。逐行检查全部工具调用，未发现`consumer_document_write`；这些自然运行不能替代文档失败恢复验收。会话 `01a16c1a-40c8-7cd3-b805-14e66e59a94a` 的182行文本SHA为 `4408f0b79686be808d43fe064c2d8cb1cacf113896fd292891179bf3fdae9194`，会话 `01a16c51-6456-7d72-85ac-b83ed7a17315` 的85行文本SHA为 `67c7620d8c3d8b09b4194da427ddedae7d0e2a0ed6981cd75c48cf8c67f2c48b`（按splitlines再以换行连接，不含末尾换行）。

**纠正先前解释：**历史第1484行完整JSONML读回表明对齐仍在。三张表分别5行×6列、4行×6列、19行×7列；表头右对齐列（零起）分别[4]、[4]、[1,5]，共47个单元格段落保留`jc=right`，与提交Markdown要求的列一致。第1484行原生记录SHA为 `c0c6b2fc06170a062de25456200c95fdb689e8223a0c6375e80e30baaa9b06c3`。第1095行较早完整JSONML亦有同样结构。

因此，这份样本的50处对齐token差异反映Markdown读回省略属性，而非已证明写入丢失对齐。DWS `executeVerifiedDocContentMutation` 对Markdown写入只用`get_document_content(format=markdown)`核验，无法从该输出区分真实对齐缺失与导出省略。剩余1处文本空格差异还需要独立核对；不能由47处对齐存在推断整篇已完全匹配。

已核对DWS上游main，仍采用相同Markdown核验路径；没有找到已发布的对应修复。后续DWS修复必须以完整JSONML核对Markdown无法表达回来的属性，并保留真正对齐缺失、正文数值变化、正文缺失的失败检测；禁止简单忽略对齐token或把本样本改判成功。本轮未改DWS、未切换生产文档写入格式、未重放报告。服务交接已上线，实际重复覆盖是否收敛仍待首个相关自然样本。

## DWS核验修复与本机安装（2026-10-24 06:25 UTC）

用户明确要求修复DWS、用完整JSONML核对对齐，并按现有计划验证上线。本次变更在DWS所属仓库完成：上游候选提交 `4e79e33a`，PR [DingTalk-Real-AI/dingtalk-workspace-cli#1551](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pull/1551)；本机安装以原v1.0.65的 `92c4f8c3` 为基线，仅带入同一修复，发布提交为 `87ddde4130bd3aa7080b7528f2c2324f5e6c2112`（已推送callzhang fork的 `codex/doc-jsonml-alignment-v1.0.65`）。上游PR尚未合入；这是本机源码修复版，不冒充官方新版本，原生源码安装的version输出为dev。

机制：Markdown包含显式表格对齐时，每个既有核验轮次读取Markdown与完整JSONML，核对表/行/列数量及每个目标单元格段落的jc属性；只有JSONML证明对齐正确，才恢复比较AST中的对齐token，继续执行原有渲染等价及结构/内容比较。缺失或错误对齐、缺失正文、数值变化、不完整/损坏/片段JSONML、读回失败均不能成功。没有增加写入重试；compact回执保留JSONML对齐证据摘要，不返回第二份完整正文。

原始样本剩余的1处空格差异确认为结构指纹将转义文本AST片段的边界误当作空格；现改为先拼接原片段再归一化实际空白，显式换行和真实词间空格保留。匿名失败回归已证明该缺陷。原始完整样本离线核验由失败变为通过；不存在生产写入或任务重放。

验证：上游main基线和安装用v1.0.65基线的完整文档模块测试均通过；安装分支包含114项顶层测试及子场景，go vet通过。新增先失败的用例覆盖导出省略对齐仍成功、真实对齐缺失/错误仍失败、数值和正文变化、JSONML缺失/损坏/非root/不完整、多个表格、追加、创建、一次写入、JSONML读取失败、实体转义和空正文覆盖。独立审查发现的空正文及HTML实体两项退化均以失败回归复现并修复，复核无剩余问题。

通过仓库原生 `make build` 和 `scripts/install.sh` 的源码安装路径安装到 `/Users/derek/.local/bin/dws`（`DWS_NO_SKILLS=1`，保留原有5份Skill备份）；runtime payload校验、签名校验通过。安装文件与构建产物一致，SHA-256为 `ec9a6d35f01cbc13ac5ae0fcc8608b282b807e2d135643c3c3963dcf669cf92d`。原文件SHA为 `1d832e4fe77a8746e3546de026ffd3d21b0eaddcfcdea7f2027543989af83dd8`，备份及校验回执保存在本机 `Library/Application Support/ceo-agent-service/backups/dws-doc-jsonml-87ddde41/`；未修改CEO服务生产checkout或Skill正文。

安装后使用该DWS二进制只读fetch原目标，exit0，完整JSONML为3张表、47处右对齐；响应SHA为 `72a275cfecce5f4935c21268fdb9e1e64f3628627e3f2630283e1cea8dc400f2`，不保存正文。已检查实际进程环境，服务主进程37209及worker37212/37213/37214均将dws解析到已更新的文件；DWS为逐次启动CLI，新调用加载新二进制，无须为本次CLI安装重启CEO Python服务。healthz、status、Attention和History均HTTP200，Attention10项、History37920项；这些是观测值，不宣称既有错误已清零或其增长与本次变更有关。

截至安装后查询，新增Consumer自然运行0项：**DWS修复已在本机运行路径上线，原始离线样本通过，实际自然文档写入是否停止重复覆盖仍待业务轨迹验收。** 不人工触发报告制造样本，不将只读fetch成功当作写入行为已验证。
