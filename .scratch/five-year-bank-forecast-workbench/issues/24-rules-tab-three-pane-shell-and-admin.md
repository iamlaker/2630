# 24 — 规则页签复用三栏外壳并支持页签内管理员维护

**What to build:** 规则集维护页签复用工作台三栏外壳，不再隐藏三栏换全宽 iframe；embedded 模式提供管理员登录/会话能力，管理员可在第四页签内完成维护操作，普通用户保持只读。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 规则页签复用三栏外壳布局（ticket 20 验收项；demo 中亦有"规则集维护在正式方案中复用三栏外壳"的提示）
- [x] embedded 模式下管理员可登录并执行维护操作，不必另开 `/rules.html`
- [x] 普通用户只读行为不变；规则修改/扫描/发布/停用 POST 仍要求管理员会话
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (a)（ticket20-L3/L9 未达成）。现状：第四页签隐藏三栏换全宽 iframe，embedded 模式隐藏登录。
- 2026-07-19 实现完成：保留同源 iframe（侵入最小），规则页签不再隐藏三栏——`rulesPane` 改为 `.pane` 并显式落在工作台网格中栏（`grid-column: 3`），左右栏、栏宽拖拽与外壳风格保持可用；中栏工具条承载"规则维护"标题与原展示项保存按钮。窄屏（≤760px）下规则内容归入"卡片"移动页签，左/右栏仍可按移动页签切换。
- embedded 模式新增页签内管理员登录：未登录时 `rules.html` 顶部显示紧凑登录条并保持 `embedded-readonly` 只读；登录成功（或新增 `GET /api/admin/session` 检测到既有会话）即移除只读类，确认/配置/扫描/发布/停用与审计操作全部可用。`web/rules.html` 的"返回测算台"在 embedded 下隐藏，避免整站嵌套导航。
- 服务端：`workbench.py` 将请求处理器提取为模块级 `build_handler(service, static, admin_token)` 工厂（`serve()` 行为不变），新增只读端点 `GET /api/admin/session` 返回 `{"admin": bool}`；规则修改/扫描/发布/停用及 `/api/display-defaults` POST 的管理员守卫未改动。新增 `tests/test_admin_http.py`（5 项）以 stub service 起真实 HTTP 服务，覆盖会话查询、登录拒绝/发卡、未授权 403 及管理员维护通路。
- 验证：`python -m unittest discover -s tests` 149 项全绿（原 144 + 新 5）；`node --check web/app.js`、`node --check web/rules.js` 通过；stub 模式下静态资源 `/`、`/rules.html?embedded=1`、`/rules.js`、`/rules-embedded.css`、`/style.css`、`/app.js` 与 `/api/admin/session` 均 200。未做真实浏览器人工验收（无浏览器工具），三栏布局与登录条建议人工过目。
