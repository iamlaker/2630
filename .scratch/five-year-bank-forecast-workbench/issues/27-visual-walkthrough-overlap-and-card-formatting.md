# 27 — 视觉走查修复：面板重叠、核心结果卡格式化与别名误匹配

**What to build:** 修复 Edge headless 真实截屏走查发现的三个视觉/正确性缺陷：rulesPane 与 centerPane 重叠；右侧核心结果卡显示原始浮点数；`_core_results`/`cardsFromSnapshot` 别名子串误匹配（"净息差"卡错配"其中：利息净收入"）。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] `.pane { display: flex }` 覆盖 UA `[hidden] { display: none }` 导致 rulesPane 常显、与中栏重叠：让 `[hidden]` 恢复生效
- [x] `renderCards` 的大数字与年份行用 `formatResultValue` 按单位确定性格式化（单位随卡片数据下发，不再前端猜）
- [x] `_core_results` 与 `cardsFromSnapshot` 改为"精确名优先、再按别名顺序子串"匹配，修复净息差卡错配
- [x] 反向结果卡与场景对比卡的数值同款格式化
- [x] Edge headless 截屏复查主界面无重叠、数字格式化正确
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：用户反馈"现在特别难看"后的真实浏览器走查（Edge headless 截屏）。根因：style.css:36 `.pane` 显式 display 击败 `[hidden]`；renderCards 未走 ticket 21 的格式化；别名匹配 `alias in name` 子串撞车。
- 2026-07-19 修复：style.css 加 `[hidden] { display: none !important; }`；`_core_results`/`_comparison_cards` 经 `_match_core_name` 精确优先匹配并附 `unit`（catalog 由调用点传入，兼容测试替身的 template dict 形状）；`renderCards`/反向结果卡/对比卡统一走 `formatResultValue`；`cardsFromSnapshot` 用同款 `matchCoreName` + `unitForIndicator`。CDP 走查回路（headless Edge + `--remote-debugging-port` + Node 原生 WebSocket，脚本在 `.scratch/shots/cdp2.js`）逐页签截屏验证：正向卡片横/纵滑杆、单变量、多输入、规则页签三栏外壳、760px 窄屏全部正常。160 项测试全绿。
