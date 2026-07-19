# 30 — 参数卡片：每页卡片数按可用宽度自适应

**What to build:** 取消卡片网格"每页固定 6 张"的限制（`draft.page * 6`），每页卡片数按中栏实际宽度/卡片最小宽度动态计算，高分辨率宽屏下自然容纳更多卡片；窗口尺寸变化时重算；分页与页码指示保留。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 每页卡片数由 cardGrid 可用宽度 ÷ 卡片最小宽度（含纵向布局的高度约束）决定，不再硬编码 6
- [x] resize、模块切换、面板拖拽（左/右栏宽度变化）后重算并保持当前页内卡片尽量可见（当前页码越界时收敛）
- [x] 页数指示（"屏 1"等）随动态数量更新
- [x] Edge headless 截屏验证：1440 宽与 2560 宽（用 Emulation 设 2560×1080）下每页卡片数不同且布局不破
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：用户走查反馈第 2 条。
- 2026-07-19 实现：新增 `cardsPerPage()`（按 cardGrid 可用宽/高 ÷ 卡尺寸动态计算列×行），替换 5 处 `* 6` 硬编码；`.card-grid` 改 `repeat(auto-fill, minmax(240px, 340px))` + `grid-auto-rows`；`ResizeObserver`（150ms 防抖）在窗口/面板拖拽后重算并重绘，页码越界自动收敛。TDD：新增 `test_card_grid_adaptive_page_size`；162 项测试全绿；CDP 截屏验证 1440×900（每页 4 张）与 2560×1080（每页 6 张）分页与布局正常。
