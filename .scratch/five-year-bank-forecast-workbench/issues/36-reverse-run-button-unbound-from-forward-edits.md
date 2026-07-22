# 36 — P0：反向运行按钮解禁与正向编辑状态解绑

**What to build:** 修复单变量反向/多输入反推模块中"开始求解"主按钮被误禁用的问题：当前 `setCalculateEnabled`/`setRunning`（web/app.js:398-406、438-444）要求 `Object.keys(state.edits).length` 非空才解禁，反向模块不做正向编辑时顶栏"执行测算"与编辑器内求解按钮均为灰色，只有 v1 面板内"开始反向测算"可用。解禁条件改为按模块区分：forward 模块维持现状（有编辑或基线可算），single/multi 模块改为"存在 ≥1 个已配置变量 + ≥1 条启用约束（`activeReverseConstraints()`）"。

**Blocked by:** 无

**Status:** ready-for-agent

- [ ] single 模块：无正向编辑时，有变量+启用约束 → 顶栏与编辑器求解按钮可用
- [ ] multi 模块：同上（变量取 `state.reverseVariables`）
- [ ] 缺变量或缺约束时按钮禁用，并有 title/tooltip 说明缺什么
- [ ] forward 模块解禁行为不变（回归）
- [ ] 运行中（`state.task`）三个模块按钮均禁用
- [ ] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-20 来源：反向模块走查（agent-13 报告摩擦点 #1）。同一动作三个入口可用状态不一致，用户第一反应是"功能坏了"，属阻断级。
- 2026-07-20 注意：ticket 39 会把三个入口收敛为一个，本票只修解禁逻辑，不动按钮布局，避免与 39 冲突。
