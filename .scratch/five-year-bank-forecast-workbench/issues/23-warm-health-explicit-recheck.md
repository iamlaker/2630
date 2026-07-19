# 23 — warm 健康显式复查

**What to build:** `#workerHealth` 支持启动时健康检查与点击显式复查，自动模式以健康为前提；既有降级路径行为不变（PRD L370）。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 启动时即发起 warm 健康检查，不再恒显示"热启动待检查"
- [x] 点击健康指示触发显式复查并更新状态文案（PRD L370"显式 warm 健康复查"）
- [x] 自动模式以健康检查为前提（当前未检查即假设可用）
- [x] 降级时改文案的现有行为保留
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (a)。现状：app.js 仅在降级时改 `#workerHealth` 文案，无启动检查与点击复查。
- 2026-07-19 实现：后端新增 `WorkbenchService.warm_worker_recheck()`（经 `_get_warm_worker()` 主动确保/重建 worker 后返回 health；无 fingerprint 时返回 `healthy: False` + 原因，不抛错）并暴露 `GET /api/warm-health`（ThreadingHTTPServer 下不阻塞其他请求）；既有 `warm_worker_health()` 被动接口保持不动，API 兼容。前端 `state.warmHealthy`（null/true/false）三态：启动即发 `recheckWorkerHealth()`（待检查→检查中→正常/不可用），点击 `#workerHealth` 显式复查；`scheduleAutomaticCalculation` 与"自动"模式按钮均以 `warmHealthy === true` 为前提（按钮点击会先触发复查，失败则拒绝切换并提示）；复查失败且当前为自动模式时降级为手动并提示，不静默恢复自动（PRD L370）；`degradeForwardMode` 文案行为保留，仅补充置 `warmHealthy = false`。样式：`style.css` `.health` 增加 `cursor: pointer; user-select: none`，`index.html` 指示元素加 `title` 提示。验证：`python -m unittest discover -s tests` 144 项全绿（新增 3 项：`test_warm_recheck_starts_worker_once_and_reports_health`、`test_warm_recheck_replaces_unhealthy_worker`、`test_warm_recheck_without_fingerprint_reports_unavailable`）；`node --check web/app.js` 通过。未做真机 Excel COM 联调（服务层已用 fake worker 覆盖）。
