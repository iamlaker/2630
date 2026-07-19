# 22 — 指标导航：组头计数、五种状态点与自动展开补全

**What to build:** 补齐输入/输出两侧导航的状态表达：组头显示可见/总数/相关计数；状态点五种并列（已选、已修改、约束、已有结果、异常）；输出导航补齐状态点；自动展开覆盖管理员默认项与活跃反向约束所在分组。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 组头显示可见/总数/相关计数，不再只显示"N 项"（PRD L323）
- [x] 输入导航状态点五种并列：已选、已修改、约束、已有结果、异常（PRD L325-331；ticket 17）
- [x] 输出导航同样显示状态点（当前完全无状态点）
- [x] 自动展开逻辑覆盖管理员默认项与活跃反向约束所在分组
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (a)。现状：`renderNav` 只显示"N 项"，仅已选/已修改/约束三种点。
- 2026-07-19 实现完成：两侧组头统一为"可见/总数 项 · N 相关"（可见=过滤后条数，总数=组内全部指标，相关=星标/已修改/已选/管理员默认/活跃约束任命中一）；输入导航五种状态点并列，输出导航补齐状态点。判定全部基于已有真实数据，未改 API：已选=当前模块卡片/输出勾选；已修改=模块草稿 edits；约束=已配置反向约束或变量（输入侧补充了变量判定）；已有结果=最近一次 trust.status==="valid" 且该输入在返回的 edited_values 中（输出侧另要求行内有数值）；异常=规则 rejected/unsupported，或当前草稿中启用的约束在最近反向结果中 hit===false（输出侧另覆盖有效计算但行无数值）。自动展开新增管理员默认项（display_defaults.inputs/outputs）与活跃（enabled!==false）反向约束两种触发。输出侧按 PRD L333 不打"已修改"点（输出只算不改）。测算/反向完成、约束增删启停、输出勾选、模块切换后两侧导航即时重渲染；状态点带 title 提示，图例同步为五项。tests/test_workbench.py 新增 test_navigation_group_counts_and_five_status_dots。141 项测试全绿，node --check 通过。
