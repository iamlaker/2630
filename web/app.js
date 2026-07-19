const persisted = WorkbenchState.load();
const state = {
  data: null,
  selected: null,
  module: persisted.activeModule,
  persisted,
  edits: persisted.drafts[persisted.activeModule]?.edits || {},
  task: null,
  taskKind: null,
  scenarios: [],
  comparisonSelection: {},
  comparisonBaseline: null,
  comparison: null,
  reverseResult: null,
  reverseConstraints: persisted.drafts[persisted.activeModule]?.constraints || [],
  reverseVariables: persisted.drafts[persisted.activeModule]?.variables || [],
  favorites: persisted.favorites,
  autoTimer: null,
  newestDraftPending: false,
};
const $ = (id) => document.getElementById(id),
  years = [2026, 2027, 2028, 2029, 2030];
async function load() {
  const response = await fetch("/api/workbench");
  state.data = await response.json();
  if (!response.ok) throw Error(state.data.error || "工作台加载失败");
  $("templateMeta").textContent =
    `活动模板 V${state.data.template.version} · ${state.data.template.fingerprint.slice(0, 10)}`;
  state.persisted.shared.templateVersionId = state.data.template.id;
  $("engineMode").value = state.persisted.shared.engineMode || "warm_com";
  if (!state.persisted.restored) {
    state.persisted.drafts.forward.selected = [...(state.data.display_defaults?.inputs || [])];
    WorkbenchState.MODULES.filter((module) => module !== "rules").forEach((module) => {
      state.persisted.drafts[module].outputSelection = [...(state.data.display_defaults?.outputs || [])];
    });
  }
  const groups = [...new Set(state.data.parameters.map((x) => x.group))];
  $("group").innerHTML =
    '<option value="">全部分组</option>' +
    groups.map((x) => `<option>${x}</option>`).join("");
  const blocked = !state.data.rule_set.active;
  $("workspaceNotice").hidden = !blocked;
  $("workspaceNotice").textContent = blocked
    ? "0717 活动模板尚未发布规则集。当前仅可查看基准值；请先在规则集维护中完成确认与激活。"
    : "";
  setCalculateEnabled();
  renderNav();
  renderTrust(state.data);
  renderCards(state.data.core_results);
  renderDetails(state.data.result_rows || state.data.details);
  renderOutputNavigation();
  renderTrace(state.data.calculation_details);
  renderReverseMetrics();
  initializeUnifiedWorkbench();
}
function renderNav() {
  const search = $("search").value.toLowerCase(),
    group = $("group").value,
    onlyFav = $("favorites").checked,
    onlyAdjusted = $("adjusted").checked,
    onlyPending = $("pending").checked;
  const list = state.data.parameters.filter(
    (x) =>
      (!search || x.name.toLowerCase().includes(search)) &&
      (!group || x.group === group) &&
      (!onlyFav || state.favorites[x.id]) &&
      (!onlyAdjusted || state.edits[x.id]) &&
      (!onlyPending ||
        ["pending_confirmation", "changed", "rejected", "unsupported"].includes(
          x.rule_status,
        )),
  );
  $("adjustedCount").textContent =
    `${Object.keys(state.edits).length} 项已调整`;
  const grouped = {};
  list.forEach((x) => (grouped[x.group] ??= []).push(x));
  $("parameterTree").innerHTML =
    Object.entries(grouped)
      .map(
        ([name, items]) => {
          const key = `input:${name}`;
          const relevant = items.some((item) => state.favorites[item.id] || state.edits[item.id] || currentDraft().selected.includes(item.id));
          const open = Boolean(search || relevant || currentDraft().openGroups[key]);
          return `<section class="nav-group"><button class="group-title" data-nav-group="${key}"><span>${open ? "−" : "+"}</span>${name}<small>${items.length} 项</small></button>${open ? items.map((item) => `<div class="parameter ${state.selected?.id === item.id ? "active" : ""}" data-id="${item.id}"><span class="star ${state.favorites[item.id] ? "on" : ""}" data-star="${item.id}">${state.favorites[item.id] ? "★" : "☆"}</span><span>${item.name}</span><span class="state-dots">${currentDraft().selected.includes(item.id) ? '<i class="dot selected"></i>' : ""}${state.edits[item.id] ? '<i class="dot edited"></i>' : ""}${state.reverseConstraints.some((x) => x.indicator_id === item.id) ? '<i class="dot constraint"></i>' : ""}</span><small>${item.rule_status === "confirmed" ? "已发布" : "待确认"}</small></div>`).join("") : ""}</section>`;
        },
      )
      .join("") || '<div class="empty">没有匹配指标</div>';
  document
    .querySelectorAll(".parameter")
    .forEach((el) => (el.onclick = () => select(el.dataset.id)));
  document.querySelectorAll("[data-star]").forEach(
    (el) =>
      (el.onclick = (event) => {
        event.stopPropagation();
        state.favorites[el.dataset.star] = !state.favorites[el.dataset.star];
        persistWorkbench();
        renderNav();
      }),
  );
  document.querySelectorAll("[data-nav-group]").forEach((button) => (button.onclick = () => {
    const key = button.dataset.navGroup;
    currentDraft().openGroups[key] = !currentDraft().openGroups[key];
    persistWorkbench();
    renderNav();
  }));
}
function canEdit(item) {
  return Boolean(
    state.data?.rule_set?.active &&
    item?.rule &&
    item.rule.confirmation_status === "confirmed" &&
    !item.rule.configuration_pending,
  );
}
function select(id) {
  state.selected = state.data.parameters.find((x) => x.id === id);
  $("editorEmpty").hidden = true;
  $("editor").hidden = false;
  $("editorGroup").textContent = state.selected.group;
  $("editorName").textContent = state.selected.name;
  $("editorSummary").textContent =
    `${state.selected.unit || "未知单位"} · 原始定位 ${state.selected.location}`;
  $("editorRule").textContent = state.selected.rule
    ? `${state.selected.rule.confirmation_status} · ${state.selected.rule.linkage_strategy || "未配置"}`
    : "活动发布中无此规则";
  $("editorVersion").textContent = state.selected.rule
    ? `规则 V${state.selected.rule.rule_version}`
    : "";
  $("linkage").value = state.selected.rule?.linkage_strategy || "independent";
  $("linkage").disabled = !canEdit(state.selected);
  $("editMode").textContent = state.selected.rule
    ? `${state.selected.rule.adjustment_mode} · 步长 ${state.selected.rule.minimum_step ?? "未设"} · 范围 ${state.selected.rule.allowed_range?.join("—") || "未设"}`
    : "配置待确认";
  renderYears();
  $("ruleDetails").textContent = JSON.stringify(
    state.selected.rule
      ? {
          rule_id: state.selected.rule.rule_id,
          rule_version: state.selected.rule.rule_version,
          publication_id: state.data.rule_set.publication_id,
          confirmed_source_cells: state.selected.rule.confirmed_source_cells,
          configuration: {
            display_unit: state.selected.rule.display_unit,
            adjustment_mode: state.selected.rule.adjustment_mode,
            minimum_step: state.selected.rule.minimum_step,
            allowed_range: state.selected.rule.allowed_range,
            linkage_strategy: state.selected.rule.linkage_strategy,
          },
        }
      : { status: state.selected.rule_status },
    null,
    2,
  );
  renderNav();
  const draft = currentDraft();
  if (!draft.selected.includes(id)) draft.selected.push(id);
  draft.cardOrder = [...draft.selected];
  draft.page = Math.floor(draft.selected.indexOf(id) / 6);
  persistWorkbench();
  renderCardPages();
  renderCardGrid();
}
function renderYears() {
  const item = state.selected,
    values = state.edits[item.id] || item.baseline,
    editable = canEdit(item),
    range = item.rule?.allowed_range,
    step = item.rule?.minimum_step ?? "any";
  $("years").innerHTML = years
    .map(
      (year) =>
        `<div class="year ${editable ? "" : "locked"}"><label><span>${year}</span><span>${item.unit || ""}</span></label><input type="number" step="${step}" data-year="${year}" value="${values[year] ?? ""}" ${editable ? "" : "disabled"}>${range ? `<input type="range" step="${step}" data-range="${year}" value="${values[year] ?? 0}" min="${range[0]}" max="${range[1]}" ${editable ? "" : "disabled"}>` : ""}<div class="baseline">基准 ${item.baseline[year]}</div><div class="delta">变化 ${((values[year] ?? item.baseline[year]) - item.baseline[year]).toFixed(4)}</div><button data-reset="${year}" ${editable ? "" : "disabled"}>恢复基准</button></div>`,
    )
    .join("");
  $("years").className = `years layout-${currentDraft().cardLayout}`;
  document
    .querySelectorAll("[data-year]")
    .forEach(
      (el) =>
        (el.oninput = (event) =>
          changeYear(+el.dataset.year, event.target.value)),
    );
  document
    .querySelectorAll("[data-range]")
    .forEach(
      (el) =>
        (el.oninput = (event) =>
          changeYear(+el.dataset.range, event.target.value)),
    );
  document
    .querySelectorAll("[data-reset]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          changeYear(+el.dataset.reset, item.baseline[el.dataset.reset])),
    );
}
function changeYear(year, raw) {
  const value = Number(raw),
    item = state.selected;
  if (!Number.isFinite(value) || !canEdit(item)) return;
  const range = item.rule.allowed_range;
  if (range && (value < range[0] || value > range[1])) return;
  const linkage = $("linkage").value,
    base = { ...(state.edits[item.id] || item.baseline) };
  if (linkage === "independent") base[year] = value;
  else if (linkage === "same_value") years.forEach((y) => (base[y] = value));
  else if (linkage === "same_delta")
    years.forEach(
      (y) =>
        (base[y] =
          Number(item.baseline[y]) + value - Number(item.baseline[year])),
    );
  else if (linkage === "baseline_ratio" && Number(item.baseline[year]) !== 0)
    years.forEach(
      (y) =>
        (base[y] =
          Number(item.baseline[y]) * (value / Number(item.baseline[year]))),
    );
  state.edits[item.id] = base;
  renderYears();
  renderNav();
  setCalculateEnabled();
  currentDraft().edits = state.edits;
  persistWorkbench();
  updateDraftStatus();
  scheduleAutomaticCalculation();
}
function payload() {
  return Object.entries(state.edits).map(([id, values]) => {
    const item = state.data.parameters.find((x) => x.id === id);
    return { rule_id: item.rule.rule_id, indicator_id: id, values };
  });
}
function setCalculateEnabled() {
  const enabled = Boolean(
    state.data?.rule_set?.active && Object.keys(state.edits).length,
  );
  $("calculate").disabled = !enabled;
  $("calculateTop").disabled = !enabled;
}
const STATUS_LABELS = {
  valid: "有效",
  reverse_no_feasible: "无可行解",
  pending_rule_confirmation: "待确认",
  cycle_not_converged: "未收敛",
  engine_difference: "引擎差异",
  calculation_failed: "计算失败",
  cancelled: "已取消",
};
const TERMINAL = ["succeeded", "failed", "cancelled", "cycle_not_converged"];
const STAGE_LABELS = {
  open_isolated: "复制模板并启动 Excel",
  baseline_summary_read: "读取基准汇总",
  write_input: "写入输入假设",
  initial_recalculate: "初始重算",
  result_summary_read: "读取结果汇总",
};
function stageLabel(stage) {
  if (!stage) return "排队中";
  if (stage.startsWith("cycle_iteration_"))
    return `循环迭代 第 ${stage.split("_").pop()} 轮`;
  if (stage.startsWith("comparison_scenario_"))
    return `对比场景 ${stage.split("_").pop()}`;
  if (stage.startsWith("reverse_v2_search_"))
    return `v2 优先级搜索 第 ${stage.split("_").pop()} 次`;
  if (stage.startsWith("reverse_search_"))
    return `v1 单变量搜索 第 ${stage.split("_").pop()} 次`;
  return STAGE_LABELS[stage] || stage;
}
function setRunning(running) {
  ["calculate", "calculateTop"].forEach((id) => {
    $(id).disabled =
      running ||
      !state.data?.rule_set?.active ||
      !Object.keys(state.edits).length;
  });
  $("taskProgress").hidden = !running;
}
async function calculate() {
  if (
    !state.data.rule_set.active ||
    !Object.keys(state.edits).length ||
    state.task
  )
    return;
  $("calculate").disabled = true;
  $("calculateTop").disabled = true;
  setRunning(true);
  state.taskKind = "calculation";
  try {
    const response = await fetch("/api/calculations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_version_id: state.data.template.id,
        adjustments: payload(),
        engine_mode: $("engineMode").value,
      }),
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "测算提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
    degradeForwardMode(error.message);
  }
}
async function poll() {
  if (!state.task) return;
  let task;
  try {
    const response = await fetch(`/api/calculations/${state.task}`);
    task = await response.json();
    if (!response.ok) throw Error(task.error || "状态查询失败");
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
    return;
  }
  renderTaskProgress(task);
  if (!TERMINAL.includes(task.status)) {
    setTimeout(poll, 1000);
    return;
  }
  const kind = state.taskKind;
  state.task = null;
  state.taskKind = null;
  setRunning(false);
  if (task.status === "succeeded") {
    const data = task.result;
    if (kind === "comparison") {
      state.comparison = data;
      renderComparison(data);
    } else if (kind === "reverse") {
      state.reverseResult = data;
      state.data.scenario_draft = data.scenario_draft;
      $("exportReverse").disabled = !data.scenario_draft;
      renderTrust(data);
      renderTrace(data.calculation_details);
      $("cards").innerHTML =
        (data.variables || [data.variable])
          .map(
            (x) =>
              `<article class="card"><h3>${x.indicator_name} · P${x.priority || 1}</h3><div class="metric">${x.suggested_value ?? x.required_value}</div><div class="years-mini">调整 ${x.adjustment} · ${x.hit_boundary ? "触及边界" : "范围内"}</div></article>`,
          )
          .join("") +
        `<article class="card"><h3>搜索摘要</h3><div class="metric">${data.feasible ? "可行" : "无解"}</div><div class="years-mini">${data.search_count}/${data.calculation_details.max_evaluations || data.search_count} 次 · 软偏差 ${data.soft_deviation}</div></article>` +
        data.constraints
          .map(
            (x) =>
              `<article class="card"><h3>${x.indicator_name}</h3><div class="metric">${x.hit ? "命中" : "未命中"}</div><div class="years-mini">实际 ${x.actual ?? "—"} · 偏差 ${x.deviation}</div></article>`,
          )
          .join("");
    } else {
      state.data = { ...state.data, ...data };
      renderTrust(data);
      renderCards(data.core_results);
      state.data.result_rows = data.result_rows || state.data.result_rows;
      renderDetails(state.data.result_rows || data.details);
      renderTrace(data.calculation_details);
      currentDraft().calculatedUnsaved = true;
      persistWorkbench(); updateDraftStatus();
    }
  } else if (task.status === "cycle_not_converged" && task.result) {
    renderTrust(task.result);
    renderTrace(task.result.calculation_details);
  } else if (task.status === "cancelled") {
    renderTrust({
      trust: { status: "cancelled", reason: "测算已取消，未保存任何结果" },
    });
  } else {
    const reason = task.error || task.result?.trust?.reason || "测算失败";
    renderTrust(
      task.result?.trust
        ? task.result
        : { trust: { status: "calculation_failed", reason, error: reason } },
    );
    if (kind === "calculation") degradeForwardMode(reason);
  }
  if (kind === "scenario") loadScenarios();
  if (kind === "calculation" && state.newestDraftPending) {
    state.newestDraftPending = false;
    setTimeout(calculate, 0);
  }
}

function degradeForwardMode(reason) {
  if (state.module !== "forward") return;
  state.persisted.shared.degradationReason = reason;
  if (state.persisted.shared.forwardMode === "auto") {
    state.persisted.shared.forwardMode = "manual";
    $("workerHealth").textContent = "● warm 自动已降级为手动";
  } else if ($("engineMode").value === "warm_com") {
    $("engineMode").value = "cold_com";
    state.persisted.shared.engineMode = "cold_com";
    $("workerHealth").textContent = "● warm 手动失败，已切换 cold";
  }
  $("workerHealth").classList.add("failed");
  $("workspaceNotice").hidden = false;
  $("workspaceNotice").textContent = `计算模式已降级：${reason}`;
  document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item.dataset.mode === state.persisted.shared.forwardMode));
  persistWorkbench();
}
async function cancelCalculation() {
  if (!state.task) return;
  $("cancelCalc").disabled = true;
  try {
    await fetch(`/api/calculations/${state.task}/cancel`, { method: "POST" });
  } finally {
    $("cancelCalc").disabled = false;
  }
}
async function showEngineValidation() {
  const response = await fetch("/api/engine-validation"), data = await response.json();
  if (!response.ok) return alert(data.error || "尚无引擎验证报告");
  renderTrust({
    ...data,
    trust: { status: data.validation_state, reason: data.reason, rule_version: data.publication_id },
  });
}
function renderTaskProgress(task) {
  const waiting = task.status === "cancel_requested";
  $("statusBadge").textContent = waiting ? "取消中…" : "测算中…";
  $("statusBadge").className = "badge running";
  const comparison = task.stage_timings || {};
  const timings = Object.entries(comparison)
    .filter(([, values]) => Array.isArray(values))
    .map(
      ([key, values]) =>
        `${key}: ${values.map((x) => Math.round(x)).join("/")}ms`,
    )
    .join(" · ");
  $("taskStage").textContent = waiting
    ? "取消请求已收到，等待安全停止点…"
    : `当前阶段：${stageLabel(task.current_stage)}`;
  const batch = comparison.total
    ? ` · ${comparison.completed || 0}/${comparison.total} · ${comparison.current_scenario || ""}`
    : "";
  const search = comparison.search_count
    ? ` · 搜索 ${comparison.search_count}/${comparison.max_evaluations || "?"}`
    : "";
  const engine = ` · ${task.engine_mode || "cold_com"}${task.worker_id ? ` · worker ${task.worker_id.slice(0, 8)}` : ""} · 排队 ${Math.round(task.queue_wait_ms || 0)}ms · 取消 ${task.cancel_status || "not_requested"}`;
  $("taskMeta").innerHTML =
    `已耗时 ${(task.elapsed_ms / 1000).toFixed(1)} 秒 · 循环 ${task.iterations || 0} 次${engine}${batch}${search}${timings ? `<br><small>${timings}</small>` : ""}`;
}
function renderTrust(data) {
  const t = data.trust || {},
    failed = [
      "calculation_failed",
      "engine_difference",
      "cycle_not_converged",
    ].includes(t.status);
  $("statusBadge").textContent = STATUS_LABELS[t.status] || "未计算";
  $("statusBadge").className =
    `badge ${t.status === "valid" ? "valid" : failed ? "failed" : "pending"}`;
  $("trust").className =
    `trust ${t.status === "valid" ? "valid" : failed ? "failed" : ""}`;
  const differences = data.engine_validation?.differences || data.differences || [];
  const differenceDetails = differences.length
    ? `<ul>${differences.slice(0, 20).map((item) => `<li>${item.indicator}${item.year ? ` · ${item.year}` : ""}: ${item.reference_value} → ${item.candidate_value}（${item.reference_engine} ${item.reference_version || "?"} / ${item.candidate_engine} ${item.candidate_version || "?"}）</li>`).join("")}</ul>`
    : "";
  $("trust").innerHTML =
    `<strong>${t.reason || data.reason || "尚未计算"}</strong><small>循环 ${t.iterations ?? 0} 次 · 最大差异 ${t.final_difference ?? "—"}<br>活动发布 ${t.rule_version || data.publication_id || state.data?.rule_set?.publication_id || "未发布"} · ${t.error || "详情可展开查看"}</small>${differenceDetails}`;
}
function renderCards(cards = []) {
  $("cards").innerHTML =
    cards
      .map(
        (card) =>
          `<article class="card"><h3>${card.name}</h3><div class="metric">${card.values?.[2030] ?? "—"}</div><div class="years-mini">${years.map((y) => `${y} ${card.values?.[y] ?? "—"}`).join(" · ")}</div></article>`,
      )
      .join("") || '<div class="empty">尚未识别到核心结果</div>';
}
function renderDetails(details = []) {
  const query = $("detailSearch").value.toLowerCase();
  const rows = details.filter((x) => !query || x.name.toLowerCase().includes(query));
  const columns = ["name", "2025", "2026", "2027", "2028", "2029", "2030", "five_year_change", "cagr"];
  const labels = ["指标", "2025", "2026", "2027", "2028", "2029", "2030", "五年变化", "CAGR"];
  const widths = currentDraft().columnWidths;
  $("details").innerHTML = `<div class="result-scroll"><table class="result-table"><colgroup>${widths.map((width) => `<col style="width:${width}px">`).join("")}</colgroup><thead><tr>${labels.map((label, index) => `<th>${label}<span class="col-resizer" data-col="${index}"></span></th>`).join("")}</tr></thead><tbody>${rows.map((row) => resultRow(row, columns)).join("")}</tbody></table></div>`;
  setupColumnResize();
}

function resultRow(row, columns) {
  const constrained = state.reverseConstraints.some((x) => x.indicator_name === row.name);
  const changed = resultRowChanged(row);
  const classes = [constrained ? "constraint" : "", changed ? "changed" : ""].filter(Boolean).join(" ");
  const markers = `${constrained ? " · 已设约束" : ""}${changed ? " · 较基准变化" : ""}`;
  const cells = columns.slice(1).map((column) => {
    const value = row.values?.[column];
    const cls = resultValueClass(value, column);
    return `<td${cls ? ` class="${cls}"` : ""}>${formatResultValue(value, column === "cagr" ? "%" : row.unit, row.precision)}</td>`;
  }).join("");
  return `<tr${classes ? ` class="${classes}"` : ""}><td title="${row.name}"><strong>${row.name}</strong><small>${row.unit || ""} · ${row.group || ""}${markers}</small></td>${cells}</tr>`;
}

function resultRowChanged(row) {
  const baseline = row.baseline_values;
  if (!baseline) return false;
  return ["2025", "2026", "2027", "2028", "2029", "2030", "five_year_change", "cagr"].some((key) => {
    const current = Number(row.values?.[key]), before = Number(baseline[key]);
    return Number.isFinite(current) && Number.isFinite(before) && Math.abs(current - before) > 1e-9 * Math.max(1, Math.abs(current), Math.abs(before));
  });
}

function resultValueClass(value, column) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (number < 0) return "negative";
  return column === "five_year_change" ? "positive" : "";
}

function formatResultValue(value, unit, precision) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (unit === "%") {
    const percentage = number * 100;
    const digits = Math.abs(percentage) < 10 ? 2 : Math.abs(percentage) <= 100 ? 1 : 0;
    return `${percentage.toFixed(digits)}%`;
  }
  if (unit === "亿元") return Math.round(number).toLocaleString("zh-CN");
  if (Number.isFinite(precision)) return number.toFixed(precision);
  return number.toFixed(2);
}

function rulePrecision(item) {
  const step = item?.rule?.minimum_step;
  if (typeof step !== "number" || !Number.isFinite(step) || step <= 0) return undefined;
  const text = step.toFixed(10).replace(/0+$/, "").replace(/\.$/, "");
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

function setupColumnResize() {
  document.querySelectorAll("[data-col]").forEach((handle) => (handle.onmousedown = (event) => {
    event.preventDefault();
    const index = Number(handle.dataset.col), start = event.clientX, initial = currentDraft().columnWidths[index];
    const move = (next) => {
      currentDraft().columnWidths[index] = Math.max(index === 0 ? 82 : 52, initial + next.clientX - start);
      const column = document.querySelectorAll(".result-table col")[index];
      if (column) column.style.width = `${currentDraft().columnWidths[index]}px`;
    };
    const up = () => {
      removeEventListener("mousemove", move); removeEventListener("mouseup", up); persistWorkbench();
    };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
  }));
}
function renderTrace(details) {
  $("calculationTrace").innerHTML = details
    ? `<dl><dt>计算编号</dt><dd>${details.calculation_id || "—"}</dd><dt>阶段</dt><dd>${details.stage}</dd><dt>耗时</dt><dd>${details.duration_ms ?? "—"} ms</dd><dt>引擎</dt><dd>${details.engine_mode || "—"}${details.worker_id ? ` / ${details.worker_id}` : ""}</dd><dt>排队/取消</dt><dd>${details.queue_wait_ms ?? 0} ms / ${details.cancel_status || "not_requested"}</dd><dt>循环</dt><dd>${details.iterations ?? 0} 次</dd></dl><ol>${(details.log || []).map((line) => `<li>${line}</li>`).join("")}</ol>`
    : "尚未执行测算";
}
function setupReverse() {
  const host = $("ruleDetails").parentElement,
    b = document.createElement("details");
  b.className = "reverse";
  b.innerHTML =
    '<summary>单变量反向测算 v1</summary><p>当前指标作为变量；配置初始值、范围和约束后手动搜索。</p><div class="reverse-add"><select id="reverseMetric"></select><select id="reverseYear">' +
    years
      .map((y) => `<option ${y === 2030 ? "selected" : ""}>${y}</option>`)
      .join("") +
    '</select><input id="reverseInitial" type="number" placeholder="变量初始值"><select id="reverseKind"><option value="min">≥</option><option value="max">≤</option><option value="target">=</option></select><input id="reverseValue" type="number" placeholder="约束值"><select id="reverseHard"><option value="true">硬约束</option><option value="false">软目标</option></select><button id="addConstraint">添加</button></div><div id="reverseConstraints"></div><button class="primary" id="runReverse">开始反向测算</button>';
  host.parentElement.insertBefore(b, host);
  const v2 = document.createElement("details");
  v2.className = "reverse";
  v2.innerHTML =
    '<summary>多输入优先级反推 v2</summary><p>选择当前 confirmed 输入加入变量，按优先级逐项搜索；最多 15 次正向测算。</p><button id="addReverseVariable">加入当前输入</button><div id="reverseVariables"></div><button class="primary" id="runReverseV2">开始 v2 反推</button>';
  host.parentElement.insertBefore(v2, host);
  $("addConstraint").onclick = addReverseConstraint;
  $("runReverse").onclick = runReverse;
  $("addReverseVariable").onclick = addReverseVariable;
  $("runReverseV2").onclick = runReverseV2;
  renderReverseMetrics();
  renderReverseConstraints();
  renderReverseVariables();
}
function renderReverseMetrics() {
  if (!$("reverseMetric") || !state.data) return;
  const items = [
    ...state.data.parameters.map((x) => ({
      id: x.id,
      name: x.name,
      type: "input",
    })),
    ...state.data.details
      .filter((x) => x.classification === "output")
      .map((x) => ({ id: "", name: x.name, type: "output" })),
  ];
  $("reverseMetric").innerHTML = items
    .map(
      (x) =>
        `<option value="${x.type}|${x.id}|${x.name}">${x.type === "input" ? "输入" : "输出"} · ${x.name}</option>`,
    )
    .join("");
  if (state.selected) $("reverseInitial").value = currentDraft().singleInitial ?? state.selected.baseline["2030"] ?? "";
}
function addReverseConstraint() {
  const value = Number($("reverseValue").value);
  if (!Number.isFinite(value)) return alert("请输入约束值");
  const [indicator_type, indicator_id, indicator_name] =
    $("reverseMetric").value.split("|");
  state.reverseConstraints.push({
    indicator_type,
    indicator_id,
    indicator_name,
    year: $("reverseYear").value,
    kind: $("reverseKind").value,
    value,
    hard: $("reverseHard").value === "true",
    enabled: true,
  });
  syncReverseDraft();
  renderReverseConstraints();
}
function renderReverseConstraints() {
  $("reverseConstraints").innerHTML =
    state.reverseConstraints
      .map(
        (x, i) =>
          `<label class="reverse-row"><input type="checkbox" data-enable="${i}" ${x.enabled ? "checked" : ""}><span>${x.indicator_name} · ${x.year} · ${x.kind === "min" ? "≥" : x.kind === "max" ? "≤" : "="} ${x.value} · ${x.hard ? "硬" : "软"}</span><button data-remove="${i}">删除</button></label>`,
      )
      .join("") || "<small>尚未添加约束</small>";
  document
    .querySelectorAll("[data-enable]")
    .forEach(
      (x) =>
      (x.onchange = () =>
          ((state.reverseConstraints[+x.dataset.enable].enabled = x.checked), syncReverseDraft())),
    );
  document.querySelectorAll("[data-remove]").forEach(
    (x) =>
      (x.onclick = () => {
        state.reverseConstraints.splice(+x.dataset.remove, 1);
        syncReverseDraft();
        renderReverseConstraints();
      }),
  );
}
async function runReverse() {
  if (
    !state.selected?.rule ||
    !state.reverseConstraints.some((x) => x.enabled) ||
    state.task
  )
    return alert("请选择 confirmed 输入变量并启用至少一个约束");
  const range = state.selected.rule.allowed_range || [],
    initial = Number($("reverseInitial").value),
    year = $("reverseYear").value,
    body = {
      template_version_id: state.data.template.id,
      variable: {
        rule_id: state.selected.rule.rule_id,
        indicator_id: state.selected.id,
        year,
        initial: Number.isFinite(initial) ? initial : state.selected.baseline[year],
        lower: range[0],
        upper: range[1],
      },
      adjustments: payload(),
      constraints: state.reverseConstraints,
      max_evaluations: 25,
      engine_mode: $("engineMode").value,
    };
  currentDraft().singleInitial = body.variable.initial;
  persistWorkbench();
  setRunning(true);
  state.taskKind = "reverse";
  try {
    const response = await fetch("/api/reverse-calculations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      task = await response.json();
    if (!response.ok) throw Error(task.error || "反向测算提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
  }
}
function addReverseVariable() {
  if (!canEdit(state.selected)) return alert("请选择 confirmed 输入指标");
  if (state.reverseVariables.some((x) => x.indicator_id === state.selected.id))
    return alert("该变量已添加");
  const range = state.selected.rule.allowed_range || [],
    year = "2030";
  state.reverseVariables.push({
    rule_id: state.selected.rule.rule_id,
    indicator_id: state.selected.id,
    indicator_name: state.selected.name,
    priority: state.reverseVariables.length + 1,
    year,
    initial: state.selected.baseline[year],
    lower: range[0] ?? state.selected.baseline[year],
    upper: range[1] ?? state.selected.baseline[year],
    step: state.selected.rule.minimum_step,
    linkage_strategy: state.selected.rule.linkage_strategy || "independent",
  });
  syncReverseDraft();
  renderReverseVariables();
}
function renderReverseVariables() {
  if (!$("reverseVariables")) return;
  $("reverseVariables").innerHTML =
    state.reverseVariables
      .map(
        (x, i) =>
          `<div class="reverse-variable"><strong>${x.indicator_name}</strong><label>优先级<input type="number" min="1" data-v2="priority" data-index="${i}" value="${x.priority}"></label><label>年度<select data-v2="year" data-index="${i}">${years.map((year) => `<option ${String(year) === x.year ? "selected" : ""}>${year}</option>`).join("")}</select></label><label>初始值<input type="number" data-v2="initial" data-index="${i}" value="${x.initial ?? ""}"></label><label>下限<input type="number" data-v2="lower" data-index="${i}" value="${x.lower}"></label><label>上限<input type="number" data-v2="upper" data-index="${i}" value="${x.upper}"></label><label>步长<input type="number" min="0" data-v2="step" data-index="${i}" value="${x.step ?? ""}"></label><label>联动<select data-v2="linkage_strategy" data-index="${i}"><option value="independent">独立</option><option value="same_delta">同幅</option><option value="same_value">同值</option><option value="baseline_ratio">同比例</option></select></label><button data-remove-v2="${i}">删除</button></div>`,
      )
      .join("") || "<small>尚未添加可调变量</small>";
  document.querySelectorAll("[data-v2]").forEach((element) => {
    const item = state.reverseVariables[+element.dataset.index];
    if (element.dataset.v2 === "linkage_strategy") element.value = item.linkage_strategy;
    element.onchange = () => {
      const field = element.dataset.v2;
      item[field] = ["priority", "initial", "lower", "upper", "step"].includes(field)
        ? Number(element.value)
        : element.value;
      syncReverseDraft();
    };
  });
  document.querySelectorAll("[data-remove-v2]").forEach(
    (element) =>
      (element.onclick = () => {
        state.reverseVariables.splice(+element.dataset.removeV2, 1);
        syncReverseDraft();
        renderReverseVariables();
      }),
  );
}
async function runReverseV2() {
  if (!state.reverseVariables.length || !state.reverseConstraints.some((x) => x.enabled) || state.task)
    return alert("请添加可调变量并启用至少一个目标约束");
  if (state.reverseVariables.some((x) => !Number.isFinite(x.initial) || !Number.isFinite(x.lower) || !Number.isFinite(x.upper) || x.initial < x.lower || x.initial > x.upper || x.lower > x.upper || !Number.isFinite(x.priority)))
    return alert("请检查变量优先级和允许范围");
  setRunning(true);
  state.taskKind = "reverse";
  try {
    const response = await fetch("/api/reverse-calculations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_version_id: state.data.template.id,
          variables: state.reverseVariables,
          adjustments: payload(),
          constraints: state.reverseConstraints,
          max_evaluations: 15,
          engine_mode: $("engineMode").value,
        }),
      }),
      task = await response.json();
    if (!response.ok) throw Error(task.error || "v2 反推提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({ trust: { status: "calculation_failed", reason: error.message, error: error.message } });
  }
}

function currentDraft(module = state.module) {
  return state.persisted.drafts[module];
}

function persistWorkbench() {
  state.persisted.activeModule = state.module;
  state.persisted.favorites = state.favorites;
  state.persisted.shared.engineMode = $("engineMode").value;
  WorkbenchState.save(state.persisted);
}

function syncReverseDraft() {
  currentDraft().constraints = state.reverseConstraints;
  currentDraft().variables = state.reverseVariables;
  persistWorkbench();
  updateDraftStatus();
}

function loadModuleDraft(module) {
  const draft = currentDraft(module);
  state.edits = draft.edits || {};
  state.reverseConstraints = draft.constraints || [];
  state.reverseVariables = draft.variables || [];
  const selectedId = draft.selected[draft.page * 6] || draft.selected[0];
  if (selectedId && state.data.parameters.some((item) => item.id === selectedId)) select(selectedId);
  else {
    state.selected = null;
    $("editor").hidden = true;
    $("editorEmpty").hidden = false;
  }
  renderNav();
  renderReverseConstraints();
  renderReverseVariables();
  setCalculateEnabled();
  renderCardPages();
}

function switchModule(module) {
  if (!WorkbenchState.MODULES.includes(module)) return;
  currentDraft().edits = state.edits;
  syncReverseDraft();
  state.module = module;
  state.persisted.activeModule = module;
  document.querySelectorAll("[data-module]").forEach((button) => button.classList.toggle("active", button.dataset.module === module));
  const rules = module === "rules";
  $("rulesPane").hidden = !rules;
  ["leftPane", "centerPane", "rightPane"].forEach((id) => ($(id).hidden = rules));
  document.querySelectorAll(".pane-resizer").forEach((item) => (item.hidden = rules));
  $("forwardMode").hidden = module !== "forward";
  $("calculateTop").hidden = rules;
  $("layoutToggle").hidden = module !== "forward";
  $("linkage").hidden = module !== "forward";
  $("leftTitle").textContent = module === "forward" ? "输入参数" : module === "single" ? "变量与约束" : module === "multi" ? "多输入变量与约束" : "规则导航";
  $("centerTitle").textContent = module === "forward" ? "年度参数卡片" : module === "single" ? "单变量求解画布" : module === "multi" ? "多输入求解画布" : "规则维护";
  document.documentElement.style.setProperty("--left-pane", `${state.persisted.paneWidths[module].left}px`);
  document.documentElement.style.setProperty("--right-pane", `${state.persisted.paneWidths[module].right}px`);
  if (!rules) loadModuleDraft(module);
  updateReverseVisibility();
  updateDraftStatus();
  persistWorkbench();
}

function updateReverseVisibility() {
  document.querySelectorAll("details.reverse").forEach((item, index) => {
    item.hidden = state.module === "forward" || state.module === "rules" || (state.module === "single" ? index !== 0 : index !== 1);
    item.open = !item.hidden;
  });
  $("calculate").textContent = state.module === "forward" ? "执行测算" : state.module === "single" ? "开始单变量求解" : "开始多输入求解";
  $("calculate").onclick = state.module === "forward" ? calculate : state.module === "single" ? runReverse : runReverseV2;
  $("calculateTop").onclick = $("calculate").onclick;
}

function renderCardPages() {
  const draft = currentDraft();
  const count = Math.max(1, Math.ceil(draft.selected.length / 6));
  draft.page = Math.min(draft.page, count - 1);
  $("cardPages").innerHTML = Array.from({ length: count }, (_, index) => `<button class="page-dot ${index === draft.page ? "active" : ""}" data-page="${index}">屏 ${index + 1}</button>`).join("");
  document.querySelectorAll("[data-page]").forEach((button) => (button.onclick = () => {
    draft.page = Number(button.dataset.page);
    const id = draft.selected[draft.page * 6];
    if (id) select(id);
    persistWorkbench();
    renderCardGrid();
  }));
}

function renderCardGrid() {
  const draft = currentDraft();
  const ids = draft.selected.slice(draft.page * 6, draft.page * 6 + 6);
  $("cardGrid").hidden = !ids.length;
  $("editorEmpty").hidden = Boolean(ids.length);
  $("editor").hidden = true;
  $("cardGrid").innerHTML = ids.map((id) => {
    const item = state.data.parameters.find((parameter) => parameter.id === id);
    if (!item) return "";
    const values = state.edits[id] || item.baseline;
    const variable = state.reverseVariables.find((entry) => entry.indicator_id === id);
    const constraint = state.reverseConstraints.find((entry) => entry.indicator_id === id || entry.indicator_name === item.name);
    const kind = state.module === "forward" ? "" : variable ? "variable" : constraint ? "constraint" : "";
    const precision = rulePrecision(item);
    const body = state.module === "forward"
      ? `<div class="work-card-years">${years.map((year) => `<div class="work-card-year"><span>${year}</span><span>${formatResultValue(values[year], item.unit, precision)}</span><small>Δ ${formatResultValue(Number(values[year]) - Number(item.baseline[year]), item.unit, precision)}</small></div>`).join("")}</div>`
      : variable
        ? `<p>初始值 ${formatResultValue(variable.initial ?? item.baseline[variable.year], item.unit, precision)}</p><p>范围 ${variable.lower} — ${variable.upper}</p><p>优先级 ${variable.priority}</p>`
        : constraint
          ? `<p>${constraint.year} ${constraint.kind === "min" ? "≥" : constraint.kind === "max" ? "≤" : "="} ${constraint.value}</p><p>${constraint.hard ? "硬约束" : "软目标"}</p>`
          : "<p>选择后可添加为变量或约束</p>";
    return `<article class="work-card ${kind} ${state.selected?.id === id ? "selected" : ""}" draggable="true" data-card="${id}"><div class="work-card-head"><div><h3>${item.name}</h3><small>${kind === "variable" ? "变量" : kind === "constraint" ? "约束" : item.group}</small></div><span class="spacer"></span><button data-open-card="${id}">编辑</button><button data-remove-card="${id}">×</button></div><div class="work-card-body">${body}</div><div class="work-card-actions"><button data-move="${id}|-1">前移</button><button data-move="${id}|1">后移</button><button data-reset-card="${id}">恢复基准</button></div></article>`;
  }).join("");
  document.querySelectorAll("[data-open-card]").forEach((button) => (button.onclick = () => {
    select(button.dataset.openCard);
    $("cardGrid").hidden = true;
    $("editor").hidden = false;
  }));
  document.querySelectorAll("[data-remove-card]").forEach((button) => (button.onclick = () => removeCard(button.dataset.removeCard)));
  document.querySelectorAll("[data-reset-card]").forEach((button) => (button.onclick = () => {
    delete state.edits[button.dataset.resetCard];
    currentDraft().edits = state.edits;
    persistWorkbench(); renderCardGrid(); renderNav(); updateDraftStatus();
  }));
  document.querySelectorAll("[data-move]").forEach((button) => (button.onclick = () => {
    const [id, direction] = button.dataset.move.split("|");
    moveCard(id, Number(direction));
  }));
  document.querySelectorAll("[data-card]").forEach((card) => {
    card.ondragstart = (event) => event.dataTransfer.setData("text/plain", card.dataset.card);
    card.ondragover = (event) => event.preventDefault();
    card.ondrop = (event) => {
      event.preventDefault();
      const source = event.dataTransfer.getData("text/plain"), target = card.dataset.card;
      const sourceIndex = draft.selected.indexOf(source), targetIndex = draft.selected.indexOf(target);
      if (sourceIndex >= 0 && targetIndex >= 0) {
        draft.selected.splice(sourceIndex, 1); draft.selected.splice(targetIndex, 0, source);
        draft.cardOrder = [...draft.selected]; persistWorkbench(); renderCardGrid();
      }
    };
  });
}

function removeCard(id) {
  const dirty = Boolean(state.edits[id] || state.reverseVariables.some((item) => item.indicator_id === id) || state.reverseConstraints.some((item) => item.indicator_id === id));
  if (dirty && !confirm("该卡片包含未保存编辑、变量或约束。确认移除？")) return;
  const draft = currentDraft();
  draft.selected = draft.selected.filter((item) => item !== id);
  draft.cardOrder = [...draft.selected];
  delete state.edits[id];
  state.reverseVariables = state.reverseVariables.filter((item) => item.indicator_id !== id);
  state.reverseConstraints = state.reverseConstraints.filter((item) => item.indicator_id !== id);
  syncReverseDraft(); renderCardPages(); renderCardGrid(); renderNav();
}

function moveCard(id, direction) {
  const draft = currentDraft(), index = draft.selected.indexOf(id), next = index + direction;
  if (index < 0 || next < 0 || next >= draft.selected.length) return;
  [draft.selected[index], draft.selected[next]] = [draft.selected[next], draft.selected[index]];
  draft.cardOrder = [...draft.selected]; persistWorkbench(); renderCardGrid();
}

function updateDraftStatus() {
  const dirty = WorkbenchState.isDirty(currentDraft()) || currentDraft().calculatedUnsaved;
  $("draftStatus").textContent = dirty ? `● ${state.persisted.restored ? "已恢复的" : ""}未保存草稿` : "";
}

function renderOutputNavigation() {
  const query = ($("outputSearch")?.value || "").toLowerCase();
  const rows = state.data?.result_rows || [];
  const groups = {};
  rows.filter((row) => !query || row.name.toLowerCase().includes(query)).forEach((row) => (groups[row.group || "未分组"] ??= []).push(row));
  $("outputGroups").innerHTML = Object.entries(groups).map(([group, items]) => {
    const key = `output:${group}`, relevant = items.some((item) => currentDraft().outputSelection.includes(item.id));
    const open = Boolean(query || relevant || currentDraft().openGroups[key]);
    return `<section class="output-group"><button data-output-group="${key}">${open ? "−" : "+"} ${group}<span>${items.length}</span></button>${open ? `<div class="output-metrics">${items.map((item) => `<label><input type="checkbox" data-output="${item.id}" ${currentDraft().outputSelection.includes(item.id) ? "checked" : ""}> ${item.name}</label>`).join("")}</div>` : ""}</section>`;
  }).join("");
  document.querySelectorAll("[data-output-group]").forEach((button) => (button.onclick = () => {
    currentDraft().openGroups[button.dataset.outputGroup] = !currentDraft().openGroups[button.dataset.outputGroup]; persistWorkbench(); renderOutputNavigation();
  }));
  document.querySelectorAll("[data-output]").forEach((input) => (input.onchange = () => {
    const id = input.dataset.output;
    currentDraft().outputSelection = input.checked ? [...new Set([...currentDraft().outputSelection, id])] : currentDraft().outputSelection.filter((item) => item !== id);
    persistWorkbench(); renderDetails(filteredResultRows());
  }));
}

function filteredResultRows() {
  const rows = state.data?.result_rows || [];
  return currentDraft().outputSelection.length ? rows.filter((row) => currentDraft().outputSelection.includes(row.id)) : rows;
}

async function protectDraftBeforeSwitch() {
  if (!(WorkbenchState.isDirty(currentDraft()) || currentDraft().calculatedUnsaved)) return true;
  const choice = prompt("当前存在未保存草稿。输入 save 保存为场景并切换，discard 丢弃并切换，或 cancel 取消。", "cancel");
  if (choice === "save") {
    $("scenarioName").value ||= `未命名草稿 ${new Date().toLocaleString("zh-CN")}`;
    return await saveScenario();
  }
  if (choice === "discard") {
    currentDraft().edits = {}; currentDraft().variables = []; currentDraft().constraints = []; currentDraft().calculatedUnsaved = false;
    state.edits = {}; state.reverseVariables = []; state.reverseConstraints = []; persistWorkbench();
    return true;
  }
  return false;
}

function scheduleAutomaticCalculation() {
  if (state.module !== "forward" || state.persisted.shared.forwardMode !== "auto" || $("engineMode").value !== "warm_com") return;
  clearTimeout(state.autoTimer);
  state.autoTimer = setTimeout(() => {
    if (state.task) state.newestDraftPending = true;
    else calculate();
  }, 500);
}

function setupPaneResize() {
  document.querySelectorAll("[data-resize]").forEach((handle) => {
    handle.ondblclick = () => {
      WorkbenchState.resetPanes(state.persisted, state.module);
      switchModule(state.module);
    };
    handle.onmousedown = (event) => {
      event.preventDefault();
      const side = handle.dataset.resize;
      const start = event.clientX;
      const widths = state.persisted.paneWidths[state.module];
      const initial = widths[side];
      const move = (next) => {
        widths[side] = side === "left" ? Math.max(190, Math.min(420, initial + next.clientX - start)) : Math.max(300, Math.min(720, initial - next.clientX + start));
        document.documentElement.style.setProperty(side === "left" ? "--left-pane" : "--right-pane", `${widths[side]}px`);
      };
      const up = () => {
        removeEventListener("mousemove", move);
        removeEventListener("mouseup", up);
        persistWorkbench();
      };
      addEventListener("mousemove", move);
      addEventListener("mouseup", up);
    };
  });
}

function initializeUnifiedWorkbench() {
  document.querySelectorAll("[data-module]").forEach((button) => (button.onclick = () => switchModule(button.dataset.module)));
  document.querySelectorAll("[data-mobile-pane]").forEach((button) => (button.onclick = () => {
    state.persisted.mobilePane = button.dataset.mobilePane;
    $("workspace").dataset.mobilePane = button.dataset.mobilePane;
    document.querySelectorAll("[data-mobile-pane]").forEach((item) => item.classList.toggle("active", item.dataset.mobilePane === button.dataset.mobilePane));
    persistWorkbench();
  }));
  document.querySelectorAll("[data-layout]").forEach((button) => (button.onclick = () => {
    currentDraft().cardLayout = button.dataset.layout;
    document.querySelectorAll("[data-layout]").forEach((item) => item.classList.toggle("active", item === button));
    if (state.selected) renderYears();
    persistWorkbench();
  }));
  document.querySelectorAll("[data-mode]").forEach((button) => (button.onclick = () => {
    state.persisted.shared.forwardMode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item === button));
    persistWorkbench();
  }));
  $("engineMode").onchange = () => {
    state.persisted.shared.engineMode = $("engineMode").value;
    $("engineMeta").textContent = `Excel COM / ${$("engineMode").value.replace("_com", "")}`;
    persistWorkbench();
  };
  $("saveDisplayDefaults").onclick = async () => {
    const response = await fetch("/api/display-defaults", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({inputs: state.persisted.drafts.forward.selected, outputs: currentDraft("forward").outputSelection})});
    const data = await response.json();
    if (!response.ok) return alert(data.error || "需要管理员登录");
    alert("全局默认展示项已保存");
  };
  $("workspace").dataset.mobilePane = state.persisted.mobilePane;
  setupPaneResize();
  switchModule(state.module);
}
const SCENARIO_TYPE_LABELS = {
  baseline: "基准",
  optimistic: "乐观",
  pessimistic: "悲观",
  custom: "自定义",
  reverse_result: "反向测算",
};
const CORE_CARD_ALIASES = {
  利润: ["归母净利润", "净利润", "利润"],
  营业收入: ["营业收入", "营业净收入", "营收"],
  净息差: ["净息差", "净利息收入", "利息净收入"],
  总资产: ["并表口径总资产", "资产总额", "总资产"],
  "ROE / RAROC": ["roe", "净资产收益率", "raroc"],
  资本充足率: ["资本充足率", "核心一级资本充足率"],
  RWA: ["风险加权资产", "rwa"],
  LCR: ["流动性覆盖率", "lcr"],
  NSFR: ["净稳定资金比例", "nsfr"],
};
function cardsFromSnapshot(snapshot) {
  return Object.entries(CORE_CARD_ALIASES)
    .map(([label, aliases]) => {
      const match = Object.keys(snapshot).find((name) =>
        aliases.some((alias) => name.toLowerCase().includes(alias)),
      );
      return match
        ? { name: label, source_name: match, values: snapshot[match] }
        : null;
    })
    .filter(Boolean);
}
async function loadScenarios() {
  try {
    const response = await fetch("/api/scenarios");
    const data = await response.json();
    state.scenarios = data.scenarios || [];
    renderScenarios();
    const restoredScenario = state.persisted.shared.scenarioId;
    if (restoredScenario && !state.scenarioRestored && state.scenarios.some((item) => item.scenario_id === restoredScenario)) {
      state.scenarioRestored = true;
      await restoreScenarioContext(restoredScenario);
    }
  } catch {
    /* 场景服务不可用时不阻塞工作台 */
  }
}

async function restoreScenarioContext(id) {
  const response = await fetch(`/api/scenarios/${id}`), scenario = await response.json();
  if (!response.ok) return;
  $("scenarioMeta").textContent = scenario.name;
  state.edits = {};
  Object.entries(scenario.input_adjustments || {}).forEach(([key, values]) => {
    const item = state.data.parameters.find((parameter) => parameter.id === key);
    if (item && canEdit(item)) state.edits[key] = { ...values };
  });
  currentDraft().edits = state.edits;
  currentDraft().calculatedUnsaved = false;
  if (scenario.calculation_result_snapshot) {
    renderCards(cardsFromSnapshot(scenario.calculation_result_snapshot));
  }
  persistWorkbench(); renderNav(); renderCardGrid(); updateDraftStatus();
}
function renderScenarios() {
  $("scenarioList").innerHTML =
    (state.scenarios || [])
      .map(
        (sc) =>
          `<div class="scenario"><div class="scenario-head"><label class="scenario-select"><input type="checkbox" data-compare="${sc.scenario_id}" ${state.comparisonSelection[sc.scenario_id] ? "checked" : ""}><strong>${sc.name}</strong></label><span class="badge ${sc.validation_state === "valid" ? "valid" : "pending"}">${sc.validation_state || "未计算"}</span></div><small>${SCENARIO_TYPE_LABELS[sc.scenario_type] || sc.scenario_type} · ${sc.adjustment_count} 项调整 · ${(sc.updated_at || "").slice(0, 19).replace("T", " ")}${sc.read_only ? " · 历史只读" : ""}</small><div class="scenario-actions"><label><input type="radio" name="comparisonBaseline" data-baseline="${sc.scenario_id}" ${state.comparisonBaseline === sc.scenario_id ? "checked" : ""}> 基准</label><button data-open="${sc.scenario_id}">打开</button><button data-copy="${sc.scenario_id}">复制</button><button data-rename="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>重命名</button><button data-recalc="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>重算</button><button data-delete="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>删除</button></div></div>`,
      )
      .join("") || '<div class="empty">尚无命名场景</div>';
  document.querySelectorAll("[data-compare]").forEach(
    (el) =>
      (el.onchange = () => {
        state.comparisonSelection[el.dataset.compare] = el.checked;
        if (el.checked && !state.comparisonBaseline)
          state.comparisonBaseline = el.dataset.compare;
        renderScenarios();
      }),
  );
  document.querySelectorAll("[data-baseline]").forEach(
    (el) =>
      (el.onchange = () => {
        state.comparisonBaseline = el.dataset.baseline;
        state.comparisonSelection[el.dataset.baseline] = true;
        renderScenarios();
      }),
  );
  document
    .querySelectorAll("[data-open]")
    .forEach((el) => (el.onclick = () => openScenario(el.dataset.open)));
  document
    .querySelectorAll("[data-copy]")
    .forEach((el) => (el.onclick = () => copyScenario(el.dataset.copy)));
  document
    .querySelectorAll("[data-rename]")
    .forEach((el) => (el.onclick = () => renameScenario(el.dataset.rename)));
  document
    .querySelectorAll("[data-recalc]")
    .forEach(
      (el) => (el.onclick = () => recalculateScenario(el.dataset.recalc)),
    );
  document
    .querySelectorAll("[data-delete]")
    .forEach((el) => (el.onclick = () => deleteScenario(el.dataset.delete)));
}
async function startComparison() {
  const scenario_ids = Object.keys(state.comparisonSelection).filter(
    (id) => state.comparisonSelection[id],
  );
  if (scenario_ids.length < 2) return alert("请至少选择两个场景");
  if (state.task) return alert("已有测算在进行中");
  setRunning(true);
  state.taskKind = "comparison";
  try {
    const response = await fetch("/api/comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_ids,
        baseline_scenario_id: state.comparisonBaseline || scenario_ids[0],
        force_refresh: $("comparisonForce").checked,
        engine_mode: $("engineMode").value,
      }),
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "对比提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    alert(error.message);
  }
}
function renderComparison(data) {
  $("exportComparison").disabled = false;
  renderTrust(data);
  $("cards").innerHTML =
    data.core_results
      .map(
        (card) =>
          `<article class="card comparison-card"><h3>${card.name}</h3>${card.scenarios.map((sc) => `<div class="scenario-value ${sc.values ? "" : "comparison-failure"}"><strong>${sc.name}</strong>${sc.values ? `${sc.values["2030"] ?? "—"} <small>Δ ${sc.differences?.["2030"] ?? "—"}</small>` : "无有效结果"}</div>`).join("")}</article>`,
      )
      .join("") || '<div class="empty">没有可对比的核心指标</div>';
  $("comparisonGroup").innerHTML =
    '<option value="">全部分组</option>' +
    [...new Set(data.details.map((x) => x.group))]
      .map((x) => `<option>${x}</option>`)
      .join("");
  $("comparisonScenario").innerHTML =
    '<option value="">全部场景</option>' +
    data.scenarios
      .map((x) => `<option value="${x.scenario_id}">${x.name}</option>`)
      .join("");
  renderComparisonDetails();
  renderTrace(data.calculation_details);
}
async function exportExcel(kind, payload) {
  if (!payload) return alert("当前没有可导出的结果");
  const response = await fetch(`/api/exports/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "导出失败");
  window.location.href = data.download_url;
}
function exportCurrentScenario() {
  const draft = state.data?.scenario_draft;
  if (!draft?.calculation_result_snapshot)
    return alert("请先完成正向测算");
  exportExcel("scenario", {
    ...state.data,
    metadata: {
      ...draft,
      template_fingerprint: state.data.template.fingerprint,
      scenario_id: draft.scenario_id || "current",
      calculation_time: state.data.calculation_details?.finished_at,
    },
  });
}
function exportReverseResult() {
  const result = state.reverseResult;
  if (!result) return alert("请先完成反向测算");
  exportExcel("reverse", {
    ...result,
    metadata: {
      ...result.scenario_draft,
      template_fingerprint: state.data.template.fingerprint,
      scenario_id: result.scenario_draft?.scenario_id || `reverse:${result.calculation_details.calculation_id}`,
      calculation_time: result.calculation_details.finished_at,
    },
  });
}
function renderComparisonDetails() {
  if (!state.comparison) return;
  const query = $("detailSearch").value.toLowerCase(),
    year = $("comparisonYear").value,
    group = $("comparisonGroup").value,
    scenario = $("comparisonScenario").value;
  $("details").innerHTML = state.comparison.details
    .filter(
      (metric) =>
        (!query || metric.name.toLowerCase().includes(query)) &&
        (!group || metric.group === group),
    )
    .map(
      (metric) =>
        `<div class="comparison-detail"><strong>${metric.name} <small>· ${metric.group}</small></strong>${metric.scenarios
          .filter((sc) => !scenario || sc.scenario_id === scenario)
          .map((sc) => {
            const visibleYears = year ? [year] : years.map(String);
            return `<div class="comparison-detail-row"><span>${sc.name}</span><span>${sc.values ? visibleYears.map((item) => `${item} ${sc.values[item] ?? "—"} (Δ ${sc.differences?.[item] ?? "—"})`).join(" · ") : "无有效结果"}</span></div>`;
          })
          .join("")}</div>`,
    )
    .join("");
}
async function saveScenario() {
  const name = $("scenarioName").value.trim();
  if (!name) { alert("请输入场景名称"); return false; }
  const draft = state.data?.scenario_draft || {};
  const response = await fetch("/api/scenarios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      scenario_type: $("scenarioType").value,
      ...draft,
    }),
  });
  const data = await response.json();
  if (!response.ok) { alert(data.error || "保存失败"); return false; }
  state.persisted.shared.scenarioId = data.scenario_id;
  currentDraft().calculatedUnsaved = false;
  $("scenarioMeta").textContent = data.name || name;
  persistWorkbench(); updateDraftStatus();
  $("scenarioName").value = "";
  loadScenarios();
  return true;
}
async function openScenario(id) {
  if (!(await protectDraftBeforeSwitch())) return;
  const response = await fetch(`/api/scenarios/${id}`);
  const sc = await response.json();
  if (!response.ok) return alert(sc.error || "打开失败");
  state.persisted.shared.scenarioId = id;
  $("scenarioMeta").textContent = sc.name;
  state.edits = {};
  Object.entries(sc.input_adjustments || {}).forEach(([key, values]) => {
    const item = state.data.parameters.find((x) => x.id === key);
    if (item && canEdit(item)) state.edits[key] = { ...values };
  });
  if (sc.read_only && Object.keys(sc.input_adjustments || {}).length)
    alert("历史模板场景只读：调整值仅供参考，不能重算");
  renderNav();
  if (state.selected) renderYears();
  setCalculateEnabled();
  currentDraft().calculatedUnsaved = false;
  currentDraft().edits = state.edits;
  persistWorkbench(); updateDraftStatus();
  if (sc.calculation_result_snapshot) {
    renderCards(cardsFromSnapshot(sc.calculation_result_snapshot));
    renderDetails(
      state.data.details.map((d) =>
        sc.calculation_result_snapshot[d.name]
          ? { ...d, values: sc.calculation_result_snapshot[d.name] }
          : d,
      ),
    );
  }
  renderTrust({
    trust: {
      status: sc.validation_state || "pending_rule_confirmation",
      reason: `已恢复场景「${sc.name}」${sc.calculation_result_snapshot ? "的最近结果" : "（尚未计算）"}`,
      rule_version: sc.rule_publication_id,
      iterations: 0,
    },
  });
}
async function copyScenario(id) {
  const response = await fetch(`/api/scenarios/${id}/copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "复制失败");
  loadScenarios();
}
async function renameScenario(id) {
  const name = prompt("新的场景名称");
  if (!name || !name.trim()) return;
  const response = await fetch(`/api/scenarios/${id}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "重命名失败");
  loadScenarios();
}
async function deleteScenario(id) {
  if (!confirm("确认删除该场景？")) return;
  const response = await fetch(`/api/scenarios/${id}`, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "删除失败");
  loadScenarios();
}
async function recalculateScenario(id) {
  if (state.task) return alert("已有测算在进行中");
  setRunning(true);
  state.taskKind = "scenario";
  try {
    const response = await fetch(`/api/scenarios/${id}/recalculate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "重算提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    alert(error.message);
  }
}
setupReverse();
["search", "group", "favorites", "adjusted", "pending"].forEach(
  (id) => ($(id).oninput = renderNav),
);
$("linkage").onchange = renderYears;
$("calculate").onclick = calculate;
$("calculateTop").onclick = calculate;
$("cancelCalc").onclick = cancelCalculation;
$("engineValidation").onclick = showEngineValidation;
$("saveScenario").onclick = saveScenario;
$("startComparison").onclick = startComparison;
$("exportScenario").onclick = exportCurrentScenario;
$("exportReverse").onclick = exportReverseResult;
$("exportComparison").onclick = () => exportExcel("comparison", state.comparison);
$("resetOne").onclick = () => {
  if (state.selected) {
    delete state.edits[state.selected.id];
    renderYears();
    renderNav();
    setCalculateEnabled();
  }
};
$("detailSearch").oninput = () =>
  state.comparison
    ? renderComparisonDetails()
    : renderDetails(filteredResultRows());
$("outputSearch").oninput = renderOutputNavigation;
["comparisonYear", "comparisonGroup", "comparisonScenario"].forEach(
  (id) => ($(id).oninput = renderComparisonDetails),
);
load()
  .then(loadScenarios)
  .catch((error) => {
    $("workspaceNotice").hidden = false;
    $("workspaceNotice").textContent = error.message;
    $("calculate").disabled = true;
    $("calculateTop").disabled = true;
  });
