# Email 搜索实施计划

用户已确认：筛选栏右侧放搜索框，窄屏换行；搜索本地保存的发件人、主题和正文，300ms 防抖，URL 保留 q，改变关键词回第一页，保留筛选和每页数量。清空恢复列表；错误与零结果分开。普通文本包含匹配，不支持高级语法或联网搜索。

**Architecture:** 现有列表 API 增加 q；Store 在分页前对发件人、主题、保存文本执行参数化文本匹配，count 与列表使用同一条件。所有筛选包括 unsubscribe 都应用搜索。

**Tech Stack:** SQLite、FastAPI、React、Vitest。

实施验证：前端 496 passed / 2 skipped；Email API 157 passed；TypeScript/Vite 构建、Ruff、diff check 通过。新增 API/UI 测试先观察到缺搜索导致失败再实现。对真实 DB 只读验证大小写、中文、无结果，1440/600px 截图无横向溢出。正文匹配复用可见正文投影，避免 CSS/HTML 源码误命中；casefold 支持 Unicode。未合并、未发布。

- [ ] 在 tests/test_email_web_api.py 写跨页、字段、中文、通配符字面量、筛选测试；执行并确认失败。
- [ ] 在 app/email_store.py 添加 q 默认空字符串，用 instr(lower(coalesce(field,'')),lower(?)) 参数化匹配；在 app/web_api/email.py 传递 q，空白视为无搜索。
- [ ] 在 frontend/src/pages/EmailPage.test.tsx 写 URL 初始化、搜索重置页码、筛选保留、清空、零结果和错误测试；确认失败。
- [ ] 在 frontend/src/pages/email/EmailList.tsx 加搜索输入、300ms 防抖、中文输入组合保护、清除和 URL 同步；frontend/src/api/console.ts 类型增加 q。
- [ ] 运行完整前端、Email API 测试、构建与 diff 检查，检查宽窄屏搜索布局。更新文档并提交；不自动合并或发布。
