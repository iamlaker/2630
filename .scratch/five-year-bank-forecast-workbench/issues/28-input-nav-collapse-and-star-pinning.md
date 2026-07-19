# 28 — 输入导航折叠行为：分组恒可折叠、折叠时星标项留外

**What to build:** 调整左栏输入参数导航的折叠交互：任何分组都可折叠（不再被"相关"状态强制锁开）；分组折叠时，星标（★）指标仍保留显示在组外，非星标项收起。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] "重要参数"等所有分组都可折叠展开（当前 `renderNav` 中 `open = search || relevant || openGroups` 使含相关项的组被强制撑开，用户无法收起——relevant 只应影响默认展开，不应阻止用户手动折叠）
- [x] 分组处于折叠状态时，组内星标指标仍显示在组标题下方（"留在外面"），非星标项隐藏；展开时正常全显
- [x] 搜索时仍显示全部匹配项（现状保留）；自动展开的默认逻辑保留，但用户手动折叠后不再被相关状态重新撑开（除非新搜索）
- [x] 折叠状态持久化（沿用 currentDraft().openGroups）
- [x] Edge headless 截屏验证：重要参数可收起；星标某项后收起分组，该项仍可见
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：用户走查反馈第 1(1)(2) 条。现状：勾选星标后整组不可折叠。
- 2026-07-19 实现：新增 `navGroupOpen(key, relevant, search)`——搜索时恒开；用户未手动操作过（`stored === undefined`）时按 relevant 默认展开；手动折叠/展开后严格尊重 `openGroups` 存储值（relevant 不再锁开）。折叠时渲染 `visibleItems = items.filter(state.favorites)` 使星标项留外。组标题按钮取反当前有效状态（而非简单布尔翻转，修复"首次点击无效"）。TDD：新增 `test_navigation_collapsible_groups_and_star_pinning`；161 项测试全绿；Edge headless CDP 截屏验证：重要参数可收起、星标项折叠时留外、再点可展开。
