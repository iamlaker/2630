# Task15 warm_com 生产启用前验收报告

## 结论

- 活动模板 SHA-256：`a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee`；活动 publication：`aa4f9fd2-c399-4141-882c-cb43fa5a9708`。
- 浏览器端完成正向调整、异步测算、场景保存；HTTP 端到端补齐反向 v1/v2、强制刷新双场景对比和场景/反向/对比三类 Excel 导出。所有请求均经过真实服务、真实规则集和真实 Excel COM worker。
- 建议将 `warm_com` 提升为 Windows 本地/受控 Windows 服务的推荐模式，但暂不删除 `cold_com`，也不建议在无进程监管的通用桌面环境中无条件设为唯一默认。
- Task11 性能与取消项可以闭环。5–15 秒是允许上限/目标窗口；本次稳定低于 5 秒属于性能优于目标：v1 六次 P50/P95 为 1.370/1.447 秒，v2 六次 P50/P95 为 0.676/0.780 秒，取消 1.148 秒内进入 `cancelled`。

## 浏览器 UAT

- 页面显示活动模板 V3、指纹前缀 `a27df7cb03` 和 publication `aa4f9fd2-c399-4141-882c-cb43fa5a9708`。
- 显式选择 `warm_com`，将「10年期国债收益率」2030 年从 0.0175 调整至 0.0185；结果为 `valid`，页面展示 worker、queue wait、stage timings、取消状态和循环收敛状态。
- 浏览器保存命名场景 `Task15-UAT-Forward` 成功；API 端到端场景 `Task15-UAT-API` 及其副本用于强制刷新对比。
- 正向、反向、对比导出均生成可下载 `.xlsx`：`scenario-d81c46f9-c180-4d08-8a68-20809c2c15df.xlsx`、`reverse-6067d76a-0e60-43f6-95af-bc20f938c83f.xlsx`、`comparison-40f55e20-4b32-4a16-bc08-ccb4fc9b89a3.xlsx`。

## 反向性能

| 路径 | 样本 | 墙钟 | 正向调用 | queue wait | validation | cancellation |
| --- | --- | ---: | ---: | ---: | --- | --- |
| v1 UAT | 单目标「归母净利润」+ 单变量「10年期国债收益率」 | 1.180s | 3 | 0.07ms | valid | not_requested |
| v2 UAT | 同一目标 + 两变量优先级（国债收益率、AC 类投资收益） | 0.712s | 1 | 0.08ms | valid | not_requested |
| v1 六次 | 同一真实样本 | P50 1.370s / P95 1.447s | 每次 3 | 近零 | 全部 valid | not_requested |
| v2 六次 | 同一真实样本 | P50 0.676s / P95 0.780s | 每次 1 | 近零 | 全部 valid | not_requested |

v2 代表样本的基线候选已满足约束，因此一次正向调用即结束；它验证了多变量优先级真实路径和无多余搜索。更紧约束的最坏预算仍受既有 `max_evaluations` 保护，不能从本样本外推为所有 v2 请求都低于一秒。

## 30 次混合稳定性

- 分布：正向 12、反向 v1 6、反向 v2 6、强制刷新双场景对比 6。
- 用户任务墙钟：P50 **0.738s**、P95 **1.447s**、P99 **3.865s**；30/30 succeeded。
- 内部共执行 48 次真实正向 Excel 测算。12 个正向输入按值轮换，反向与对比穿插执行；没有错误成功状态、validation 污染或后续请求阻塞。
- 取消测试触发 worker 定向清理/重建，后续出现新的 worker id 且剩余混合任务全部成功，证明异常生命周期可恢复。
- 压测前 Excel PID 为 10036、52656；运行中的 worker PID 32540 在服务关闭后消失。关闭后仍仅有压测前两个 PID，新增孤儿 `EXCEL.EXE` 为 **0**。未扫描或终止用户 Excel。
- 原始模板关闭后 SHA-256 仍为 `a27df7cb...`，未修改模板。

## 可观察性与故障保护

- 正向和反向结果包含 `engine_mode_requested`、实际 `engine_mode`、worker id、queue wait、stage timings、fallback reason、validation state、cancel status。
- 本任务小修了强制刷新多场景对比的诊断聚合；对比结果现包含 requested/actual mode、单一/多个 worker id、累计 queue wait、逐场景 stage timings、fallback reason 和 cancel status。
- 合作式取消在不可中断 COM 阶段保持 `cancel_requested`，阶段边界后转为 `cancelled`，不会伪造成功。真实取消最终证据见 `task15-cancellation-final.json`。
- timeout、Excel 异常重建、warm→cold fallback、只清理 worker 记录 PID 的入口由 Task14 自动化覆盖；本次全量回归再次执行。真实长跑中取消导致的 worker 重建已被后续成功任务验证。

## 推荐启用策略

推荐将 `warm_com` 作为 Windows 权威 Excel 路径的推荐模式，并保留以下保护：

1. 环境开关 `WORKBENCH_ENGINE_MODE`，可立即切回 `cold_com`。
2. `WORKBENCH_WARM_TIMEOUT_SECONDS` 硬超时与异步取消令牌。
3. warm 失败自动 `cold_com` fallback，并在 UI/API 暴露 fallback reason。
4. 原始模板 SHA-256、活动 publication、隔离副本、循环收敛和输出读取校验不得绕过。
5. 单 STA worker 串行队列；复原失败、COM 异常或超时即销毁并重建。
6. 仅清理 worker 自己记录的 automation PID，不扫描或误杀用户 Excel。
7. 保留 `cold_com` 作为手工选择和自动回退路径；上线初期持续监控 queue wait、fallback 率、重建次数和 PID。

## 证据文件

- 原始 30 次及逐任务诊断：`task15-warm-uat-20260717-233320.json`
- 最终取消状态：`task15-cancellation-final.json`
- 修复后对比诊断：`task15-comparison-diagnostics.json`
- 浏览器活动数据快照：`task15-workbench-snapshot.json`

## 后续风险

- v2 本次可行目标在首候选命中；建议后续 hardening 用多个业务目标覆盖 5、10、15 次搜索预算的尾延迟，但不阻塞本次推荐启用。
- 单 worker 设计会把并发转化为 queue wait；生产并发容量、Windows 服务账号的 Excel 桌面交互策略和长期值守重启策略仍需上线运维确认。
- UI 当前仍把 `warm_com` 标为“实验”，若采纳推荐模式，需要产品/运维确认默认开关与文案变更；本任务不擅自改默认。
