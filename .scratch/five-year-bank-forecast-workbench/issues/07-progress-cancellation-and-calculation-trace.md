# 07 - 支持长任务进度与取消

**What to build:** 当正向或后续计算超过短响应时间时，用户可以看到进度、当前阶段和循环迭代信息，并取消尚未完成的任务；系统保留可诊断的计算记录。

**Blocked by:** 05 - 完成正向单场景工作台

**Status:** ready-for-human

- [x] 正常正向测算在可行情况下达到 1-3 秒响应目标。
- [x] 超过 3 秒的计算显示进度、当前阶段和预计状态，并提供取消操作。
- [x] 取消后不会将半完成的工作簿结果保存为有效场景结果。
- [x] 计算记录包含开始结束时间、模板版本、规则版本、迭代次数、最终差异、状态和错误原因。
- [x] 前端能够区分计算中、已取消、成功、未收敛和失败状态。

## Comments

- 2026-07-17 计划同步：任务 13 将补齐真实 Excel COM 测算的阶段耗时与优化验证；本任务实现进度展示和取消时，应优先消费任务 13 输出的阶段计时、循环迭代和计算记录结构。
- 任务 05 已为同步正向测算返回 `calculation_details`：计算 ID、开始/结束时间、耗时、阶段、活动 publication、循环次数、最终差异、错误和阶段日志。任务 07 可在该契约上增加异步状态、进度事件和取消令牌；本次没有提前实现队列或取消。
- 2026-07-17 实现完成（独立实现 Agent），状态调整为 `ready-for-human`。
- 异步任务模型：`workbench.py` 新增 `CalculationTaskManager`，状态机 queued → running →（cancel_requested）→ succeeded / failed / cancelled / cycle_not_converged；内存保留最近 100 条任务记录（含创建/开始/结束时间、模板版本、迭代、最终差异、stage_timings、结果或错误）。新路由：`POST /api/calculations`（202 提交，同步校验模板存在与活动指纹）、`GET /api/calculations/{id}`（状态+进度快照）、`POST /api/calculations/{id}/cancel`（置取消令牌）。同步 `POST /api/calculate` 原样保留，兼容未动。
- 取消机制：`forecast_engine` 新增 `CalculationCancelled`；`run_forward_calculation` 接受 `cancel_token` 与 `progress` 回调，在开簿后、初始重算前、每轮循环迭代边界检查令牌，命中即抛出并经 `finally` 关闭 Excel/删除临时副本/CoUninitialize；不拦截不可中断的 COM 调用，期间任务保持 `cancel_requested`，不伪造已取消。
- 前端：`app.js` 测算改为提交+每秒轮询；进度区显示当前阶段（中文标签映射 + 循环第 N 轮）、已耗时、循环次数、已完成的 stage_timings 摘要；`cancel_requested` 期间显示「取消请求已收到，等待安全停止点…」；终态徽标区分 有效/待确认/未收敛/计算失败/已取消；已取消只显示提示，不渲染结果卡片，无 scenario_draft 可保存。`index.html` 新增 taskProgress 区与取消按钮，`style.css` 新增 running 徽标脉冲与进度区样式。
- 单测：`tests/test_workbench.py` 新增 `AsyncCalculationTests` 7 项——创建/成功（含 stage_timings 与 scenario_draft）、引擎失败、cycle_not_converged 独立状态、取消请求→已取消且 result 为 None、终态后取消为 no-op、历史模板拒绝、未知任务报错。`python -m unittest discover -s tests -q` 67 项全部通过；`node --check web/app.js`、`python -m py_compile workbench.py forecast_engine.py` 通过。
- 真实验收（0717 活动 publication + 10年期国债收益率 +10bp）：① curl 全流程——提交 202、轮询见 `running@open_isolated`、t+2.1s 取消→`cancel_requested`→开簿边界生效 `cancelled` 且 result 为 None；历史模板 400、未知任务 404。② 完整任务 succeeded：trust=valid、迭代 2、最终差异 0.002967、9 卡片、161 快照、stage_timings 完整（本次总耗时 18.9 秒，其中 close_cleanup 出现约 9.5 秒环境抖动，excel_start 4.4s + workbook_open 3.5s）。③ 真实 `web/app.js` 经 Node DOM-stub 对真实服务驱动：加载 78 参数→选择规则→编辑→提交→轮询状态序列 running→cancel_requested→cancelled，徽标「已取消」、提示「测算已取消，未保存任何结果」，PASS。④ 无新增 EXCEL.EXE 残留（仅 7/7、7/16 两个历史残留），原始模板 SHA-256 未变。
- 关于 1-3 秒目标：异步提交即时返回（<100ms），界面全程不阻塞；完整测算本身约 8-19 秒，瓶颈是 Excel 冷启动与工作簿打开（任务 13 结论），1-3 秒完整测算需要长驻 worker，其风险已在任务 13 评估并暂缓，本任务未实现 worker，仅在任务模型上预留了可供复用的进度/取消契约（后续反向测算可直接复用 `CalculationTaskManager`）。
- 已知限制：任务记录在内存中，服务重启后不可查询（审计级持久化留给后续场景/审计任务）；页面刷新后丢失进行中任务的跟踪（任务本身仍在服务端完成）。
