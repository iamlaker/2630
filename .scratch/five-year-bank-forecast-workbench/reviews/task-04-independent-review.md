# 任务 04 独立审查：规则集确认与维护工作台

审查日期：2026-07-17。范围是当前工作区的全量实现；`git rev-parse --show-toplevel`、`git status --short`、`git log -5 --oneline` 均返回 `not a git repository`，因此无法进行固定比较点或 commit-diff 审查。本报告分别完成 Standards 和 Spec 两轴审查。

审查遵守只读限制：真实数据库通过 SQLite `mode=ro` 打开；未执行迁移、初始化、扫描、`VACUUM`、写入或启动生产服务器。唯一项目内写入物是本报告。临时复现仅在系统临时目录创建数据库，进程退出后已删除。

## 关键结论

1. **首要卡顿根因**是不可变规则版本把稳定且巨大的公式图（同时含候选 `reference_paths` 的重复链）重新 JSON 序列化到每个 `input_rules.payload_json`；不是列表页本身。
2. 采样时主库为 **3,996,299,264 bytes**；`payload_json` 合计 **3,542,761,142 bytes**，且 `freelist_count=0`，增长是活动数据而非大量可回收空页。
3. 两个逻辑规则占全部规则正文的 **99.34%**；其中一个有 7 条记录但只有 3 个不同版本，已出现重复版本数据。
4. `rule_summary_index` 使真实列表摘要查询约 **1.13 ms**，但只绕开 `/api/rules` 的正文读取；不降低写入量，`initialize`、`calculate` 和完整历史仍读全部 payload。
5. 一次网页“确认并保存”实际生成两个完整版本；临时复现中一个 17.42 MB payload 的确认使数据库增加约 18.80 MB，每次配置编辑再增加约 18.80 MB。
6. 审计当前已排除公式链，真实审计仅约 263 KB，**不是**本次 3.54 GB 正文膨胀的主因；review cache 是额外副本（约 2.18 MB），但非当前主因。
7. 活动规则集不是冻结发布快照，且激活一个模板会停用所有模板；这会使已发布计算静默消费后续编辑，属于正确性阻塞。
8. 任务 04 不能标记为 `ready-for-human`；应暂停任务 05，先修复存储/发布语义和可审查性，再让管理员继续确认规则。

## Standards Findings

### P0 — 稳定公式图被每个规则版本完整复制，造成持续且可操作触发的数据库膨胀

- **文件和行号：** `input_rules.py:214-265`、`input_rules.py:183-206`、`input_rules.py:292-299`。
- **代码证据：** 发现结果把完整 `formula_dependency_chain` 放进 payload（:260）；候选又把每条 `reference_path` 保存在 `candidate_source_cells`（:214-225），与公式链重叠。`_new_version` 浅拷贝旧 payload（:293-296），`_save` 仍对整个对象 `json.dumps` 后插入（:185-191）；每版还写一份 review cache（:200-206）。
- **用户影响：** 仅修改步长、范围或状态也会复制数十至数百 MB 的稳定发现数据。规则确认越多、编辑越多，数据库和历史读取越不可用；这是当前“特别卡”的首要根因。
- **复现/测量：** 临时库构造五年三叉深链：首次发现 payload 17,418,102 bytes，DB 48 KB → 18,837,504 bytes；确认后 37,642,240 bytes；每次配置编辑各增加 18,796,544 bytes。五版本的全历史 JSON 为 87,091,521 bytes，读取 2,223.83 ms。
- **最小修复建议：** 将候选、完整公式图、诊断拆为按内容哈希去重的不可变 `discovery_snapshot`；轻量规则版本仅保存 `snapshot_id`、状态、来源确认映射和配置。候选不再把同一链同时嵌入 `reference_paths` 与 `formula_dependency_chain`。
- **轴：** Standards。

### P0 — “确认并保存”在一次用户动作中创建两个完整版本

- **文件和行号：** `workbench.py:105-130`。
- **代码证据：** `confirm` 先调用 `confirm_rule`（:125-129），随后总会以新 `rule_id` 调用 `edit_rule`（:130）。两次都进入 `_new_version`/`_save`。
- **用户影响：** 每次确认至少复制两份巨型正文，并产生中间的“已确认但配置未完成”版本；这直接加剧 P0 膨胀和版本噪声。
- **复现/测量：** 临时复现的单次 `confirm_rule` 已复制一份 17.42 MB payload；网页路径还额外调用一次 `edit_rule`，故确认并保存至少多写两份正文及两条审计事件。
- **最小修复建议：** 新增单一 `confirm_and_configure` 命令，在同一事务只创建一个新版本和一条字段级审计事件。
- **轴：** Standards / Spec。

### P0 — 活动规则集不是发布快照，激活后编辑会绕过完整性校验并改变计算

- **文件和行号：** `input_rules.py:137-140`、`input_rules.py:393-403`、`workbench.py:177-205`、`workbench.py:218-224`。
- **代码证据：** `active_rule_sets` 只保存模板标识和 `active` 标志，没有规则集版本或 logical-rule→rule-version 清单。激活后 `calculate` 只检查 active 位（:181），随后 `_latest_rules()` 读取当前最新版本（:183、:218-224）。
- **用户影响：** 发布后任何规则编辑、拒绝或配置不完整都会静默进入后续测算，而不会重新校验、重新发布或留下准确的消费版本；历史追溯和“仅消费活动规则集”均失效。
- **复现方法：** 在临时库使完整规则集激活；随后编辑任一逻辑规则到配置不完整或拒绝状态；调用另一个规则的计算。当前代码仍按最新规则读取而非按激活时的快照读取。
- **最小修复建议：** 新增不可变 `rule_set_publications` 与 `rule_set_members(publication_id, logical_rule_id, rule_id)`；激活时事务性校验并写入成员快照，测算只按 publication 成员读取。任何编辑只形成草稿规则版本，必须重新发布。
- **轴：** Standards / Spec。

### P0 — 版本写入与发布在 `ThreadingHTTPServer` 下不具备事务原子性，真实库已出现重复版本

- **文件和行号：** `input_rules.py:121-124`、`input_rules.py:126-152`、`input_rules.py:183-191`、`input_rules.py:292-299`、`input_rules.py:393-402`、`workbench.py:267-325`。
- **代码证据：** 一个 `check_same_thread=False` 连接被线程 HTTP 服务共享；`_get`、`_next_version`、`_save`、审计分别执行并多次 commit。表没有 `(logical_rule_id, rule_version)` 唯一约束，未配置 WAL/busy timeout，也未用将版本、索引、审计、发布包裹起来的事务。
- **用户影响：** 并发编辑可产生同版本、丢失修改或 `database is locked`；激活和编辑可交错。真实库的数据已不满足“每个逻辑规则版本号唯一”。
- **复现/测量：** 只读真实库发现 `logical_rule_id=18188453-23d7-477a-b7ab-3dee7d04f1c2` 的 version 2 有 3 行、version 3 有 3 行（相同创建时间），共 7 行但只有 3 个不同版本。摘要最新查询因此返回 84 行而非 82 个逻辑规则。
- **最小修复建议：** 每请求独立 SQLite 连接，或完整服务锁；使用 `BEGIN IMMEDIATE` 将版本、摘要、审计和发布合并为一个事务；增加唯一约束并在迁移中先处理重复版本；启用 WAL 与合理 `busy_timeout`（不能替代原子事务）。
- **轴：** Standards / Spec。

### P1 — 摘要索引跨模板计算“最新版本”时漏掉模板限定，且重复行未被去重

- **文件和行号：** `input_rules.py:320-331`。
- **代码证据：** 子查询仅以 `newer.logical_rule_id = current.logical_rule_id` 求 `MAX(rule_version)`，没有限定 `newer.template_version_id = current.template_version_id`，也没有唯一约束保证一个逻辑规则只有一个当前版本。
- **用户影响：** 同一逻辑规则复用于另一模板后，旧模板的列表、完整性和激活判断可漏项；重复版本会令总览计数错误。
- **复现/测量：** 真实库 `rule_summary_index` 与 `input_rules` 没有 orphan/字段不一致，但 82 个逻辑规则对应 84 条“最新”摘要，正是重复 version 2/3 造成。
- **最小修复建议：** 查询必须同时按模板版本限定；发布后列表应查询明确的 publication 成员，不应靠相关子查询猜测“最新”。
- **轴：** Standards。

### P1 — 详情页宣称审查公式链，却永久截断为每年 20 条

- **文件和行号：** `input_rules.py:200-206`、`input_rules.py:355-364`、`web/rules.js:8`。
- **代码证据：** `_cache_rule_review` 固定 `chain_limit=20`；详情命中 cache 后只返回截断内容；前端直接以“逐年公式链”展示 JSON，没有 `truncated` 标识或加载更多。
- **用户影响：** 复杂规则无法完成 PRD 要求的完整链审查，管理员会误以为 20 条即全部证据。
- **复现/测量：** 真实最大详情 cache 响应约 146,101 bytes、2.04 ms，说明它不是完整正文（真实最大单版本正文为 373,421,053 bytes）。
- **最小修复建议：** 首屏只返回摘要和链计数；另设分页/游标的按需完整链端点，返回明确 `truncated` 状态。新快照结构使该读取不必回读整条规则版本。
- **轴：** Standards / Spec。

### P1 — 管理操作没有服务端角色边界，审计 actor 可由客户端伪造

- **文件和行号：** `web/rules.js:9-10`、`workbench.py:105-130`、`workbench.py:308-322`。
- **代码证据：** 前端固定发送 `actor:'admin'`；所有 POST 接受该字段并执行确认、拒绝、编辑、激活/停用，未认证、授权或来源保护。
- **用户影响：** 任意可访问本地端口的进程可修改规则集、发布或伪造审计操作人，违反轻量角色和完整操作日志要求。
- **复现方法：** 对任一 `/api/rules/<id>` 或激活端点提交 JSON body 中的任意 `actor`，服务端会记录该值。
- **最小修复建议：** 在服务端建立会话/令牌与 Admin 角色决策；忽略客户端 actor，并为本地部署至少配置绑定地址与随机认证令牌。
- **轴：** Standards / Spec。

### P1 — 激活错误地让所有模板全局互斥

- **文件和行号：** `input_rules.py:398-399`。
- **代码证据：** 激活任一模板执行 `UPDATE active_rule_sets SET active = 0 WHERE active = 1`，不限当前 `template_version_id`。
- **用户影响：** 激活模板 B 会停用模板 A，违反“每个模板版本最多有一个活动规则集”（每模板最多一个，不是系统全局最多一个）。
- **复现方法：** 用临时库为两个模板各建立完整规则集，依次激活；第一个模板 active 位会被清零。
- **最小修复建议：** 发布记录按 template version 约束，在同一模板内原子切换；不同模板的发布可并存。
- **轴：** Standards / Spec。

### P2 — 工作台和历史路径仍隐式读取巨大正文；GET 还可能写 cache

- **文件和行号：** `workbench.py:63-85`、`workbench.py:177-224`、`input_rules.py:312-318`、`input_rules.py:355-360`、`input_rules.py:366-384`。
- **代码证据：** `initialize()`、`calculate()` 都经 `_latest_rules()` 调 `list_rules()`，后者 `SELECT payload_json` 并解析全部历史。详情 cache miss 会在 GET 中写入并 commit；审计使用无投影、无分页的 `SELECT *`。
- **用户影响：** `rule_summary_index` 仅改善规则维护列表，无法保护任务 05 的启动和测算；历史、审计随规则版本数增长而劣化，读请求还会和写事务竞争。
- **复现/测量：** 真实库最大逻辑规则的 `get_rule_history_summaries` 仅返回 1,117 bytes 仍耗 2,215.62 ms（访问巨大行）；临时库完整历史 87 MB/2.22 s。真实 `list_latest_rule_summaries` 为 1.13 ms，证明索引仅解决该单一路径。
- **最小修复建议：** 导航和测算读取轻量摘要/活动发布成员；按 rule ID 只加载所需配置；历史和审计使用投影、分页和上限；GET 不写缓存。
- **轴：** Standards。

### P2 — COM 生命周期不适用于线程请求，关闭错误被吞掉

- **文件和行号：** `workbench.py:267-325`、`forecast_engine.py:121-125`、`forecast_engine.py:204-218`、`forecast_engine.py:341-350`。
- **代码证据：** `ThreadingHTTPServer` 请求线程直接创建 `DispatchEx`，未显式 `CoInitialize`/`CoUninitialize`；`close()` 不是分段 finally，外层吞掉所有 close 异常。
- **用户影响：** 可能出现线程 COM 初始化错误、残留 Excel 进程或临时工作簿，且无可诊断日志。
- **复现方法：** 在临时存储配置下并发调用计算；观察 COM 初始化/退出和临时文件清理。现有常规测试未覆盖线程 COM。
- **最小修复建议：** 使用专用单线程 COM worker，或严格在每个工作线程初始化/卸载 COM；将 workbook/excel/temp-file 清理分别放入 finally，并记录失败。
- **轴：** Standards。

### P2 — 测试隔离掩盖 SQLite 规模和真实 HTTP/COM 行为

- **文件和行号：** `tests/test_input_rules.py:151-175`、`tests/test_workbench.py:39-67`、`tests/test_workbench.py:123-188`、`tests/test_forward_calculation.py:46-62`、`tests/test_template_import.py:88-107`。
- **代码证据：** “重用重对象”测试 monkeypatch `_save`，只验证 Python 对象 identity，生产路径却会 JSON 序列化。工作台测试使用 `FakeRules`/内存引擎；COM 测试分别只测试模板导入或硬编码 `ConfirmedInputRule`，未覆盖任务 04 发布闭环。
- **用户影响：** 测试通过不能证明文件增长、响应大小、版本唯一性、HTTP 序列、发布快照或线程 COM 正确。
- **最小修复建议：** 增加临时 SQLite 大链回归、真实临时 HTTP 序列、并发发布和 COM worker 隔离测试；禁止这些测试指向 `.workbench`。
- **轴：** Standards。

## Spec Findings

| 任务 04 / PRD 验收项 | 结论 | 证据与差距 |
| --- | --- | --- |
| 模板规则集总览、状态数量 | 已实现但有缺陷 | `workbench.py:88-96` 能统计五种状态并筛选；未独立返回 total/configuration-incomplete，且重复摘要使数量错误。 |
| 搜索/按分组、状态、置信度、诊断、配置筛选 | 已实现且基本正确 | `workbench.py:88-96`、`web/rules.html:5` 覆盖所需筛选。 |
| 逐年候选、公式链、置信度、诊断审查 | 部分实现 | 候选和诊断已呈现，但 `input_rules.py:200-206` 将完整链截至 20 条。 |
| 确认、手工修正、拒绝、配置维护 | 已实现但有缺陷 | 确认/编辑和配置存在；`input_rules.py:309-310`、`workbench.py:110-130`、`web/rules.js:9` 无拒绝理由字段；确认又双写版本。 |
| 不可变版本、前后差异、历史、审计 | 已实现但有缺陷 | 版本 append-only 意图存在，但重复版本破坏唯一性；`workbench.py:153-160` 固定 `version_diff={}`；历史未返回 actor/updated_at，而 `web/rules.js:8` 试图显示；审计仅查当前 rule ID，无法连续展示旧版本审计。 |
| 模板重扫描的复用/变化/新增/失配呈现 | 部分实现 | `input_rules.py:208-269` 有后端发现、复用和 changed 逻辑；`workbench.py:98-103` 固定最后模板，`web/rules.html:3-5` 无模板选择或重扫描入口。 |
| 完整性校验并阻止激活 | 部分实现 | 当前最新摘要和目录缺失检查存在（`workbench.py:132-148`）；但重复/跨模板最新查询和非快照发布使结果不可靠，`rule_set_status` 也不记录校验审计。 |
| 每模板最多一个活动集；激活/切换/停用审计 | 已实现但有缺陷 | 激活/停用有审计，但全局停用其它模板（`input_rules.py:398-409`），且活动集未绑定版本快照。 |
| 正向测算只消费活动集的已确认规则 | 部分实现，被代码问题阻塞 | active 位检查存在（`workbench.py:181-205`），但实际消费“当前最新”，不是发布时的成员版本。 |
| 至少一条真实规则确认、配置、激活、Excel COM 闭环 | 仅等待管理员人工确认 | 任务备注明确 82 条尚未解决、未激活；现有 COM 测试不是 RuleService→发布→测算闭环。管理员尚未审完规则本身不是 bug，但 P0/P1 缺陷使继续人工确认会放大问题。 |
| 原始模板保持不变 | 已实现且有测试支持 | `tests/test_template_import.py:25-43` 验证输入模板 hash；隔离副本路径见 `forecast_engine.py:204-218`。 |

## 数据库分析

### 只读采样

真实库：`.workbench/rules.sqlite3`。采样过程中未读取或输出公式链 JSON 内容。

| 项目 | 结果 |
| --- | ---: |
| 文件大小 | 3,996,299,264 bytes |
| page size / page count | 4,096 / 975,659 |
| freelist count | 0 |
| journal mode / auto vacuum | `delete` / 0 |
| `input_rules` / 逻辑规则 | 102 / 82 |
| 平均 / 最大版本数 | 1.244 / 7 |
| `rule_audit_log` | 184 |
| `rule_review_cache` / `rule_summary_index` | 102 / 102 |
| `payload_json` 合计 / 平均 / 最大 | 3,542,761,142 / 34,732,952 / 373,421,053 bytes |
| audit before+after 合计 | 263,443 bytes |
| review cache 合计 / 最大 | 2,176,404 / 149,606 bytes |
| summary diagnostics 合计 | 14,214 bytes |

SQLite 未编译 `dbstat` 虚表，无法按 B-tree 精确归属所有页；但正文总量已为文件的约 88.6%，而 `freelist_count=0`。因此没有证据支持“主要是已释放未回收页面”，相反有直接证据表明主要是活动的 `input_rules.payload_json`。

最大占用（仅列标识与大小）：

- `logical_rule_id=18188453-23d7-477a-b7ab-3dee7d04f1c2`：7 行、3 个不同版本、合计 2,613,947,094 bytes、单行最大 373,421,053 bytes；version 2 和 version 3 各重复 3 次。
- `logical_rule_id=3f7e4fbf-1e0d-411f-a6d1-d55778b31ff4`：3 行、合计 905,038,229 bytes、单行最大 301,679,481 bytes。
- 上述两条合计 3,518,985,323 bytes，即全部 payload 的约 99.34%。

审计按操作的最大总量是 `rule_discovered` 与 `rule_candidate_generated`，各约 103 KB；确认与编辑合计也仅数十 KB。它们已经在 `input_rules.py:168-181` 排除了链和候选，故审计当前不是主膨胀来源。review cache 保存截断链，仍是冗余副本，但规模远小于正文。

### 查询、页面与增长路径

| 操作 | 实测 / 代码结论 | 瓶颈 |
| --- | --- | --- |
| 规则列表摘要 | 真实库 `list_latest_rule_summaries(1)` 1.13 ms，JSON 38,090 bytes | 摘要索引有效；仍因重复版本返回 84 而非 82 条。 |
| 规则集状态 | 0.65 ms，JSON 35,085 bytes | 返回未解决规则摘要；本身非正文瓶颈。 |
| 单条详情 | 最大 review cache 2.04 ms，146,101 bytes | 被截断到 20 条链，不是完整审查。 |
| 历史摘要 | 最大规则 2,215.62 ms，JSON 1,117 bytes | 表行仍带巨大 BLOB，索引非覆盖查询导致访问溢出页；完整历史会更糟。 |
| 审计 | 全模板 1.48 ms，368,026 bytes | 当前量可接受，但 API 无分页且 `SELECT *`，会随使用增长。 |
| 完整性校验 | 0.65 ms | 基于摘要很快，但受重复/跨模板和非快照语义影响。 |
| 页面首屏 | 静态代码固定 3 请求：`/api/rule-admin` → `/api/rules` → `/api/rule-set`（`web/rules.js:4-5`） | 无详情自动请求；筛选每次 input 都触发上述后两请求，未 debounce。 |
| 页面详情 | 点击后 1 次 `/api/rules/<id>`（`web/rules.js:7-8`） | 当前响应小是因为不完整链；不能当作完整详情性能验收。 |

`rule_summary_index` 与正文记录的 rule ID/版本字段没有 orphan 或字段不一致，但“最新”逻辑不正确：82 个 logical rule 期望 82 项，实际查询返回 84 项。它只加快列表查询，未解决存储根因、版本异常、工作台全量 payload 或历史读取。

## 阻塞分类

### 代码缺陷

- P0：稳定大图版本复制、确认双版本、并发版本非原子、活动集无冻结快照、全局停用其它模板。
- P1：完整链截断、版本差异为空、历史/审计不连续、跨模板摘要查询、拒绝理由缺失、模板选择/重扫描入口缺失、无 Admin 鉴权。
- P2：工作台仍读所有正文、无分页审计/GET 写 cache、COM 生命周期和测试隔离不足。

### 数据迁移问题

- 真实库已有 102 行版本、7 行/3 版本的异常逻辑规则，以及约 3.54 GB 正文。不能在旧模型上继续人工编辑。
- 迁移必须保留原库不可修改的备份，验证引用及语义，不能靠 `VACUUM` 或删除数据掩盖增长。

### 产品/交互问题

- 详情没有说明链已截断，管理员无法判定审查证据是否完整。
- 首屏虽不取详情，但筛选无 debounce；总览缺少 total/configuration-incomplete 独立数量。
- 管理员页面不能选择模板或触发受控重扫描，拒绝不能填写理由。

### 管理员人工处理事项

- 82 条规则尚待逐项确认和补齐配置；真实规则集未激活，真实 RuleService→激活→Excel COM 正向测算闭环尚未完成。
- 这是产品规定的人工作业，不是“82 条”本身的缺陷；但必须在 P0/P1 修复和迁移完成后继续，避免继续复制 GB 级正文或发布不可靠规则集。

### 非任务 04 范围

- 任务 05 的完整工作台 UI/业务验收本身不在本次实现范围；但其依赖活动规则集，且现有 `initialize/calculate` 的正文读取问题必须在任务 05 继续前修复。
- 反向计算、多场景和导出不在任务 04 审查结论内。

## 最小修复顺序

### P0：数据完整性、无限增长和不可用性

1. **定义发布与发现数据模型。** 在 `input_rules.py` 以 `discovery_snapshots`（`snapshot_id`、内容哈希、候选/完整链/诊断）承载稳定发现数据；规则版本表仅保存状态、配置、已确认映射、`snapshot_id` 和元数据。`rule_summary_index` 只保留列表字段。
2. **一次命令、一次事务。** 合并确认与配置为一次规则版本创建；为 `(logical_rule_id, rule_version)` 加唯一约束；每请求独立连接或服务锁，以 `BEGIN IMMEDIATE` 原子保存版本、摘要、审计。
3. **冻结活动发布。** 新增 publication 和成员表，激活时写 immutable 成员清单；在 template version 范围内原子切换，测算只消费该清单。编辑不改变已发布集。
4. **迁移现有库。** 先停写并制作带 SHA-256 的原库备份；在新数据库构建 schema，按旧 `rule_id` 读取 payload（流式，不复制整个 DB），对候选/链 canonical JSON 求 hash，写一次 snapshot，写轻量版本和字段级 audit；异常重复 version 记录迁移决策表，保留所有旧 `rule_id` 映射，不能静默丢弃。
5. **验证与回滚。** 对每版本比较轻量语义（身份、状态、配置、确认映射、模板/版本）及 snapshot hash；比较每模板最新规则数、未解决数、审计事件数；抽样/全量验证公式链 hash。新库通过临时只读验证后，以原子文件替换或配置切换启用；保留原库和可切回配置，禁止覆盖备份。

### P1：任务 04 验收阻塞

1. 将列表、完整性、工作台导航改为摘要/发布成员查询；计算按需加载单条轻量规则与 snapshot，消除 `list_rules()` 全正文路径。
2. 用按需分页完整链 API 替代截断 cache；明确链总数和截断状态。实现历史前后字段差异，并按 logical rule 汇集跨版本审计。
3. 完成模板选择、受控重新扫描入口、复用/变化/新增/失配展示、拒绝理由、总览 total/configuration-incomplete 和筛选 debounce。
4. 建立最小 Admin 服务端鉴权，服务端生成 actor；校验工作表/单元格地址与模板范围。
5. 采用专用 COM worker 或线程初始化/清理；然后以临时存储和真实模板完成一条真实规则确认→发布→COM 测算闭环，确认原始模板 SHA-256 不变。

### P2：后续优化

1. 审计字段投影、分页/上限和按逻辑规则索引；GET 不再填充持久化 cache。
2. 使用 WAL、busy timeout、连接生命周期指标和请求耗时/响应字节日志；这些是 P0 事务正确性之后的并发优化。
3. 对复杂公式图考虑压缩或二进制编码，但仅在 snapshot 去重后按测量决定，避免在 JSON 复制模型上增加复杂度。

## 建议测试

- 临时 SQLite 大链：发现、一次确认、十次配置编辑，断言每次只增加轻量版本大小，snapshot 行数不增长；断言 payload/cache/audit 上限和 page 增长上限。
- 同一逻辑规则并发编辑、激活与编辑交错：断言唯一版本号、无丢失更新、事务失败可重试。
- 跨两个模板发布：各自活动 publication 可并存；编辑草稿不改变已发布计算；重新发布才改变消费版本。
- 迁移 fixture：包含重复 version、超大链、旧审计；断言旧→新 ID 映射、语义/hash/计数相等，并能回滚到原库。
- 完整链分页：首屏只返回摘要，翻页可拼回完整链，管理员可见总数/截断状态。
- 真实临时 HTTP 测试：断言首屏请求数为 3、无自动详情请求、筛选经过 debounce、列表和详情响应字节预算、历史/审计分页。
- 权限测试：未授权和伪造 actor 的确认/编辑/激活均被拒绝，审计 actor 由服务端提供。
- 临时存储下的 COM worker 测试：顺序/并发调用均能关闭 Excel 与临时副本；真实模板 hash 不变。普通测试不得复用生产 `.workbench`。

## 最终判断

- **任务 04 当前不能标记为 `ready-for-human`。** 除未完成的管理员确认外，存在 P0 数据膨胀、版本完整性和活动发布语义错误，以及 P1 完整审查/维护能力缺口。
- **应暂停任务 05。** 它依赖任务 04 的活动规则集，而当前实现既不能稳定发布，也会在初始化/测算时重新读取巨大正文。
- **最优先修复：** 用去重 discovery snapshot + 轻量规则版本替代全 payload 复制，同时用事务化 immutable publication 修复活动集语义；随后迁移、验证并可回滚地替换 3.99 GB 现有库。
- **客观完成标准：** （1）任何配置编辑不复制公式图，增长与字段大小近似而非链大小线性相关；（2）版本唯一、并发测试通过；（3）每模板可并存一个冻结活动发布，测算严格消费其成员；（4）管理员可分页审完整链、比较版本、填写拒绝理由并重扫描；（5）临时 HTTP/SQLite 性能和真实一条规则发布→COM 闭环通过，原模板 hash 不变；（6）迁移语义/hash/计数全量验证且具备备份回滚。
