# 32 — 工作台启动器（进程管理 UI）

**What to build:** 一个 Tkinter 桌面启动器（`launcher.py`，仅标准库）：枚举所有 workbench.py 进程（PID/端口/启动时间/命令行），显示每个实例服务的模板版本与 warm 健康；可终止选中/全部进程；可按配置的端口与管理员令牌启动、重启实例；端口多监听冲突红色告警；一键打开工作台、复制令牌、重扫模板；端口与令牌配置持久化。附 `启动器.bat`（pythonw 无窗启动）。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 进程列表：PowerShell CIM 枚举 + netstat 监听映射 + HTTP 探测（/api/workbench 模板版本、/api/warm-health 健康）
- [x] 终止选中/全部（taskkill /T 含 warm worker 子进程树）
- [x] 启动/重启：新控制台启动 `workbench.py --port --admin-token`，重启先清同端口残留再启动
- [x] 同端口多监听红色高亮与状态栏告警（今天踩过的坑）
- [x] 配置持久化 `.workbench/launcher.json`；`--self-test` 无窗自检（枚举并打印实例后退出）
- [x] `python -m py_compile launcher.py` 通过；`python launcher.py --self-test` 能列出当前实例

## Comments

- 2026-07-19 来源：同端口多进程残留导致 invalid admin token 与"重启不生效"的实际事故。
- 2026-07-19 实现：`launcher.py`（Tkinter，仅标准库）+ `启动器.bat`。`--self-test` 实测列出 PID 27324 / 8765 / V3 活动模板 / warm 正常 / 令牌 abcd1234；py_compile 通过。
