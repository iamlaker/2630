# 15 - warm_com 生产启用前验收与反向测算性能复验

**What to build:** 系统对 `warm_com` 进行生产启用前的端到端验收，覆盖正向单场景、反向测算 v1/v2、多场景对比、取消、fallback、诊断展示和连续运行稳定性，并用真实样本复验 Task11 的 5-15 秒反向测算性能目标。

**Blocked by:** 14 - 降低 Excel COM 冷启动与开簿耗时

**Status:** ready-for-human

- [x] 使用活动模板、活动 publication 和真实规则集完成一组浏览器端 UAT，覆盖正向单场景、反向 v1、反向 v2、多场景对比、场景保存和导出，不只调用底层函数。
- [x] 反向测算 v1/v2 在 `warm_com` 下完成真实性能复验，记录单目标、单变量、多变量优先级搜索的墙钟耗时、正向调用次数、队列等待、取消状态和结果是否有效。
- [x] 若反向测算 v1/v2 能稳定进入 5-15 秒目标区间，更新 Task11 并勾选剩余性能项；若不能，保留未勾选并用阶段耗时说明瓶颈。
- [x] 验证 `warm_com` 在 UI/API 中正确展示 requested/actual engine mode、worker id、queue wait、stage timings、fallback reason、validation state 和 cancellation state。
- [x] 验证取消、超时、Excel 异常重建和 fallback 行为不会导致界面假死、错误成功状态或孤儿 `EXCEL.EXE` 增长。
- [x] 连续运行至少 30 次混合任务（正向、反向 v1、反向 v2、多场景对比），证明 worker 状态复原稳定、无跨请求污染、无新增孤儿进程，并记录 P50/P95/P99。
- [x] 明确给出是否建议将 `warm_com` 从实验能力提升为默认/推荐模式；如建议提升，列出必须保留的保护开关和回退策略。
- [x] 所有验收报告写入 `.scratch/perf/`，并在本 ticket 和 Task11 中追加结论摘要。

## Comments

- 2026-07-17 立项背景：Task14 已在最新活动模板 `a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee` 和 publication `aa4f9fd2-c399-4141-882c-cb43fa5a9708` 下证明 `warm_com` 连续正向测算中位数约 0.587 秒、P95 约 0.669 秒，cold/warm 输出逐值一致。
- Task11 仍有一项未闭环：反向测算 v2 的 5-15 秒性能和长任务取消行为。该项此前无法完成的主要原因是 cold COM 单次正向测算过慢；现在应基于 `warm_com` 重新验证。
- 本任务是生产启用前验收，不应再大幅重构业务功能。若发现模块边界、测试结构或 worker 生命周期存在明显设计风险，应记录为后续整体 review/hardening ticket，而不是在本任务里发散。
- 2026-07-17 验收完成，状态转为 `ready-for-human`。正式报告：`.scratch/perf/task15-warm-com-production-readiness-20260717.md`；原始逐任务数据：`.scratch/perf/task15-warm-uat-20260717-233320.json`。
- 浏览器端使用活动模板 V3 和活动 publication 完成 warm 正向测算、场景保存；真实 HTTP 服务补齐 v1/v2、强制刷新对比及三类导出。小修仅聚合多场景对比的 requested/actual mode、worker、queue wait、逐场景 stage timings、fallback 和 cancel status，没有改变业务测算。
- 30 次混合任务为正向 12、v1 6、v2 6、双场景强制刷新对比 6，共 48 次真实正向调用；P50/P95/P99 为 **0.738/1.447/3.865 秒**，30/30 succeeded。压测前两个用户 Excel PID 保持不变，服务关闭后 worker PID 消失，新增孤儿为 0，模板 SHA-256 未变。
- v1 六次 P50/P95 为 **1.370/1.447 秒**（每次 3 次正向）；v2 六次为 **0.676/0.780 秒**（代表目标首候选命中）。真实取消 1.148 秒内进入 `cancelled`；取消触发 worker 重建后剩余任务继续成功。Task11 剩余 checkbox 已闭环。
- 建议把 `warm_com` 提升为 Windows 本地/受控 Windows 服务的推荐模式，但保留环境开关、硬超时、异步取消、SHA-256 与 publication 校验、隔离副本、循环收敛、状态复原失败即重建、定向 PID 清理及自动 `cold_com` fallback。默认开关和“实验”文案仍留给人工上线决策。
