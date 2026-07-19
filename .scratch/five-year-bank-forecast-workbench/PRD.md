# Five-Year Bank Forecast Workbench PRD

## Problem Statement

The user needs a browser-based workbench for forecasting bank-wide indicators over the next five years. The current Excel template under `D:\workspace\2026-2030\模版` is the authoritative calculation model. The system must let users adjust input assumptions, run forward calculations, perform constrained reverse calculations, compare scenarios, and export results while preserving traceability back to template versions and rule versions.

The first implementation must run on Windows as a local browser workbench and later deploy to an Ubuntu server.

## Current Template Source

- Activity template file: `D:\workspace\2026-2030\模版\2026-2030年盈利测算表0717-模板.xlsx`
- Activity template fingerprint (SHA-256): `A27DF7CB03878EA11779E82D1C7ECA3B45ABF227E6BDDD6D269D77B7D62FBDEE`
- Previous 0717 tool template fingerprint: `5C259FC6C7788D58C00FC7B498CEA81058B3B0C4B8F9152BABBE713FC6C7595B`; retained as historical template version 2 with publication `0b473b8a-44d5-40e0-a957-ed151116fd44`
- Historical template file: `D:\workspace\2026-2030\模版\2026-2030年盈利测算表0716-模板.xlsx`; retained for historical traceability only and not used for newly initiated calculations
- Confirmed rules from 0716 are not directly reusable against 0717. They are migration candidates only and must be re-scanned, then marked reusable, changed, or historical-only after confirmation.
- Main UI source sheet: `汇总展示表`
- Core year columns: D-H, representing 2026-2030
- Baseline scenario: the values currently stored in the template
- Main input groups: important parameters, scale assumptions, price assumptions, intermediary business assumptions
- Main output group: financial results
- Special case: consolidated total assets can be treated as an input item

## Product Scope

The product has four major modules:

1. Rule set confirmation and maintenance
2. Forward single-scenario calculation
3. Constrained reverse calculation
4. Multi-scenario comparison based on saved forward and reverse calculation results

The first delivery should include all three modules at a usable level, with clear phasing inside reverse calculation:

- Reverse calculation v1: fix several constraints, select one target indicator as the variable, and solve how far it must move to reach the required condition.
- Reverse calculation v2: support one target with multiple input indicators adjusted together by priority.
- Reverse calculation v3: if performance allows, support multiple fixed constraints and multiple linked indicators.

## User Experience

The app should be a browser-based local workbench for Windows first, with the same architecture deployable to Ubuntu later.

The product includes an administrator-facing rule set confirmation and maintenance module. It is the operational bridge between automatic rule discovery and the calculation workbench: administrators review candidate source cells and formula traces, complete adjustment configuration, create immutable rule versions, and activate a usable rule set for a template version.

The interface should expose the active 0717 template by default and keep historical templates behind a read-only switch for traceability. Historical templates must not appear as editable workspaces.

The first screen should be the usable workbench, not a landing page. The layout should be control-first:

- Left side: grouped parameter tree based on `汇总展示表`, with search, favorites, "adjusted only", and "pending rule confirmation" filters
- Center: selected input indicator editor with a 2026-2030 horizontal control that supports per-year and five-year linked adjustment
- Right side: result cards, reverse calculation result cards, scenario status, and validation status
- Detail area: expandable full indicator details, calculation logs, rule trace, and cycle convergence details

Result presentation should use cards for core indicators. Full details remain searchable and expandable. Core result cards should include items such as profit, operating income, net interest margin or net interest income, total assets, ROE/RAROC, capital adequacy, RWA, LCR, and NSFR.

## Input Adjustment Rules

Each input item needs a rule record instead of hard-coded behavior. The rule set must describe:

- Display item identity from `汇总展示表`
- Indicator group and display name
- Year mapping for 2026-2030
- Display unit
- Adjustment mode
- Minimum step
- Allowed range
- Five-year linkage strategy
- Source cell or source cells to write
- Formula trace from display cell to source cell
- Rule confidence
- Confirmation status
- Template fingerprint and rule version

Adjustment mode is configured by indicator type:

- Scale indicators can use absolute value or percentage adjustment
- Price, rate, and ratio indicators can use basis point, percentage point, or configured relative adjustment
- Intermediary business and financial indicators use their configured mode

User-facing adjustment ultimately produces target display values for each year. The backend uses confirmed rules to write changes into underlying source cells.

## Rule Discovery And Maintenance

When a template is imported, the system scans `汇总展示表` input items and formula dependencies to generate candidate source-cell rules.

Rule maintenance flow:

1. Upload or select a template version.
2. Compute template fingerprint.
3. Scan `汇总展示表` and formula dependency chains.
4. Generate candidate rules with source cells, formula trace, and confidence.
5. Auto-reuse existing rules when the template fingerprint and item identity match.
6. For a newly active template, migrate prior-template confirmed rules as candidates only, then mark exact matches as reusable, changed rules as pending confirmation, and removed rules as historical-only.
7. Mark changed or uncertain rules as pending confirmation.
8. A user with permission confirms or edits pending rules.
9. The administrator reviews rule set completeness and validation results.
10. The administrator activates a usable rule set version for the template.
11. Only rules from the active rule set become available for calculation.

When a template changes row names, groups, or positions, indicator identity should be treated as business-semantic rather than positional. Confirmed rules may be reused only after explicit manual mapping; renamed or newly introduced indicators become new rules, and removed indicators remain historical-only.

If an input item cannot be traced to a unique source cell, the system must not auto-write it. It should show candidate source cells, formula chains, and confidence, then wait for user confirmation.

### Rule Set Confirmation And Maintenance Module

The first delivery must include a dedicated administrator-facing module for operating rule sets rather than relying on backend calls or direct database edits.

The module must support:

- Selecting a template version and viewing its rule set summary, including total, confirmed, pending, changed, rejected, unsupported, and configuration-incomplete counts
- Searching and filtering rules by indicator group, indicator name, confirmation state, confidence, diagnostic type, and configuration completeness
- Reviewing each 2026-2030 display cell, candidate source cells, complete formula trace, confidence, and diagnostics
- Confirming one or more source cells, manually correcting source mappings, rejecting unsuitable candidates, and recording a reason where appropriate
- Maintaining display unit, adjustment mode, minimum step, allowed range, and five-year linkage strategy
- Comparing the current rule version with its previous version and preserving immutable history
- Re-scanning changed templates and clearly showing reused, changed, new, and no-longer-matched rules
- Validating rule-set completeness before activation and preventing activation when required input rules are unresolved or configuration is incomplete
- Activating one rule set version per template version for use by forward and reverse calculations
- Showing audit history for discovery, confirmation, editing, rejection, validation, activation, and deactivation

Rule set activation is configuration publication, not a multi-person approval workflow. The module may support bulk confirmation only when every selected rule is unambiguous and configuration-complete; ambiguous rules still require explicit review.

## Calculation Engine

The system should use the Excel workbook as the calculation model.

Implementation strategy:

- Windows local version: use Excel COM as the primary baseline calculation engine.
- Linux deployment: use a calculation engine adapter, likely LibreOffice or a commercial engine.
- Both engines must expose one unified backend interface.
- Baseline reconciliation tests must compare returned values with the original Excel template.
- Ubuntu engine must pass the same regression samples within the configured tolerance before production use.

Each calculation should copy the template or use an isolated workbook instance, write confirmed source-cell inputs, run the cycle convergence process, recalculate, then read output values from `汇总展示表`.

## Cycle Convergence

The template currently has two iterative copy-paste cycles:

- In `2026-2030年盈利测算表`, copy values from `N154:W155` to `N160:W161`
- In `板块`, copy values from `H131:Q131` to `H132:Q132`

The calculation is valid only when every source cell and pasted cell difference is within `0.1`.

The engine must:

- Run iterative copy/recalculate steps
- Stop only when all cycle differences are within tolerance
- Record iteration count and final differences
- Return a `cycle_not_converged` validation state if it cannot converge within a configured maximum iteration count

## Scenario Model

Each scenario stores adjustments relative to the baseline scenario rather than a full workbook.

Scenario records should include:

- Scenario name
- Scenario type: baseline, optimistic, pessimistic, custom, reverse-calculation result, comparison snapshot
- Template version
- Rule version
- Input adjustments
- Calculation result snapshot
- Validation state
- Creator and timestamps

Existing scenarios created from 0716 remain historical-only after template replacement. They are readable for traceability and comparison history, but they must not be auto-migrated into editable 0717 scenarios.

Initial support should include multiple named scenarios, copy, rename, delete, and compare. Future support should add scenario sets, such as macro scenario x asset growth scenario x pricing scenario.

## Reverse Calculation

Reverse calculation supports optional constraints on input and output indicators.

Constraint types:

- Hard constraint: must be satisfied
- Soft constraint: preferred target; returned result should show deviation if it cannot be fully satisfied

Versioned roadmap:

- v1: fix several constraints and choose one variable indicator; solve the value needed to meet target conditions
- v2: support one target with several input indicators adjusted together by priority
- v3: if performance supports it, solve multiple fixed constraints and multiple linked indicators

Reverse calculation output should include:

- Whether a feasible solution was found
- Required value or adjustment for selected variable indicators
- Constraint hit/miss status
- Soft-constraint deviation
- Calculation validation state
- A save-as-scenario action

## Performance Requirements

Use tiered response expectations:

- Forward single-scenario adjustment: return in 1-3 seconds when possible
- Reverse calculation: allow 5-15 seconds
- Any operation over 3 seconds must show progress and allow cancellation
- Multi-scenario comparison and batch calculations can run asynchronously

The system should avoid tracing formulas on every slider drag. Confirmed rules should be cached and reused during calculation.

## Validation States

Every calculation must return a trust state:

- Valid
- Pending rule confirmation
- Cycle not converged
- Engine difference
- Calculation failed

The right-side validation panel should show the reason and provide clickable details such as the indicator, source cell, formula chain, template version, rule version, cycle iteration details, and engine comparison.

## Permissions And Audit

First version uses lightweight roles plus full operation logging.

Roles:

- Admin: manage templates, rule confirmation, rule edits, and user-visible configuration
- User: create and edit scenarios, run calculations, run reverse calculations, compare scenarios, and export results

Audit log must record:

- Template uploads
- Rule confirmations and edits
- Rule set validation, activation, and deactivation
- Input adjustments
- Forward calculations
- Reverse calculations
- Scenario save/copy/delete actions
- Exports

Each log entry should include operator, timestamp, template version, rule version, scenario, before/after values where relevant, and validation state.

## Export

First version should support Excel exports:

- Current scenario result Excel
- Reverse calculation result Excel
- Multi-scenario comparison Excel

PDF or image report export can be a later enhancement.

## Storage

First Windows version should use SQLite plus a local file directory:

- SQLite stores metadata: templates, rules, scenarios, calculations, users, roles, and audit logs
- File storage keeps uploaded template originals, calculation workbook copies, and exported files

The data access layer should avoid binding domain records directly to local file paths so that Ubuntu deployment can later move to PostgreSQL plus file or object storage.

## Technical Decisions

- Build as a browser workbench with backend service and frontend UI.
- Backend owns workbook execution, rule discovery, scenario persistence, reverse calculation, validation, and export generation.
- Frontend owns parameter search/tree, year controls, result cards, validation panel, scenario management, and comparison views.
- Rule sets are versioned and reusable across template versions when stable.
- Forward and reverse calculations consume only the active rule set for the selected template version.
- Calculation engines are accessed through an adapter interface so Windows Excel COM and Linux calculation engines can be swapped behind a common contract.

## Acceptance Criteria

- User can upload or select a template and see detected input/output indicators from `汇总展示表`.
- User can confirm candidate source-cell rules for input items.
- Admin can use a dedicated rule set module to search, review, confirm, edit, reject, and version rules without direct database access.
- Admin can complete adjustment configuration, validate a rule set, and activate one usable rule set version for a template.
- The calculation workbench only uses rules from the active rule set and clearly reports unresolved rule-set conditions.
- User can create a scenario from the baseline template.
- User can adjust an input item for one year or all five years.
- The backend writes the adjustment to confirmed source cells, recalculates the workbook, handles the two cycle convergence ranges, and returns updated output values.
- The UI displays core output cards and detailed indicator values.
- The UI shows validation status and details for pending rules, convergence failures, engine differences, and calculation errors.
- User can run reverse calculation v1 with fixed constraints and one selected variable indicator.
- User can save reverse calculation output as a scenario.
- User can compare multiple saved scenarios.
- User can export scenario results and comparison results to Excel.
- Admin and user roles exist at a lightweight level.
- Operations are recorded in an audit log.
- Forward calculation target response is 1-3 seconds for normal cases; slower operations show progress and can be cancelled.

## Out Of Scope For First Delivery

- Full enterprise identity integration
- Approval workflow
- User-customizable dashboard layout
- PDF/image report export
- Fully automatic source-cell guessing for ambiguous rules
- Full multi-variable global optimization unless performance supports it after v1/v2 are stable
- Rewriting the Excel model manually in Python or another service model

## Workbench Interaction Redesign (2026-07-19)

This section supersedes earlier UI and interaction requirements wherever they conflict. It refines the existing calculation capabilities; it does not change the Excel workbook's authority as the calculation model.

### Unified Module Structure

The workbench has four parallel top-level tabs:

1. Forward single-scenario calculation
2. Single-variable reverse calculation
3. Multi-input reverse calculation
4. Rule-set maintenance

The tabs share the selected activity template, scenario, and calculation engine. Each calculation tab retains its own indicator selection, card order, input edits, variables, and constraints when the user switches tabs. Rule-set maintenance uses the same workbench shell but does not inherit calculation drafts. Users may view rule-set information; only administrators may edit, confirm, activate, or deactivate rules.

### Three-Pane Workspace

Each calculation tab uses a resizable left, center, and right workspace. Users can drag both separators, double-click a separator to restore the tested default layout, and retain widths independently per module in the current browser. Minimum pane widths must preserve readable indicator names, one complete center card, and the output table. Narrow screens switch to a single-pane drawer layout. The final default proportions must be chosen after testing actual content density rather than fixed in advance.

### Indicator Navigation And Preferences

The left input navigation and right output navigation are grouped by workbook business groups. Important indicators may appear individually; larger groups such as scale, price, intermediary income, capital, profit, and risk are collapsed by default.

Administrators configure the globally visible default input and output indicators. Users may star indicators as personal favorites; favorites are stored in the current browser and shared across workbench modules. Current visibility, selected cards, and card order are stored separately for each module.

Groups automatically expand when they contain an administrator default, a user favorite, a search match, an edited input, or an active reverse constraint. Group headers show visible, total, and relevant status counts. Output navigation uses grouped expansion and scrolling rather than mechanical pagination.

Indicator states use small, low-saturation theme-aligned labels or markers that may appear together:

- Selected in the center workspace
- Input value edited
- Reverse variable or constraint configured
- Valid calculated result available
- Rule error, constraint conflict, calculation failure, or infeasible result

An output used as a reverse target is described as constrained, not edited, because outputs are calculated rather than directly changed.

### Center Card Workspace

Selected indicators appear as cards in the center pane. Each small screen holds at most six cards; selecting more automatically creates another numbered screen. Users can switch screens with tabs or arrows, drag cards to reorder them, remove ordinary selections, and restore card values to baseline. Cards containing unsaved edits, variables, or constraints must not disappear without confirmation.

Forward input cards show five vertical controls for 2026–2030. Each control represents the absolute target value, while also showing its baseline value and difference from baseline. Slider range, step, unit, and allowed linkage strategies come from the active rule set. Supported linkage strategies remain independent, same value, same delta, and baseline ratio. Dragging updates the visible value continuously; releasing commits the draft change. Users can enter an exact value by keyboard.

Reverse calculation uses two visually distinct card types, differentiated by a small low-saturation color strip or label rather than a large colored background:

- Variable card: configures the permitted search range and initial value. It does not fix the final solution value.
- Constraint card: configures indicator, year, relation, target value, and hard/soft behavior.

Single-variable reverse calculation permits one variable card and multiple constraint cards. Multi-input reverse calculation permits multiple variable and constraint cards.

### Reverse Constraints And Search Priority

Every reverse constraint is bound to exactly one forecast year, with 2030 selected by default. Relations are equality (`=`), lower bound (`≥`), and upper bound (`≤`). The same indicator may have constraints for multiple years. Unspecified years have no implicit constraint.

In multi-input reverse calculation, priority controls adjustment order and cost. Priority 1 variables are used first; lower-priority variables are enabled only when higher-priority variables cannot satisfy the constraints. Multiple variables at the same priority may be combined in one search.

Feasible solutions are ranked in this order:

1. Satisfy every hard constraint.
2. Minimize total soft-target deviation.
3. Minimize the number of changed input variables.
4. Minimize total deviation from baseline values.
5. Prefer adjustment through higher-priority variables.

The result explains which variables were enabled, their adjustment magnitudes, why they were used, and a concise search summary. Reverse calculations are always started manually.

### Calculation Trigger Modes

Forward calculation defaults to warm Excel COM with automatic calculation. Automatic mode is available only while the warm worker is healthy. Slider release or completed keyboard input starts a 500 ms debounce period; additional edits reset the timer and are combined into one calculation.

If a calculation is already running, intermediate drafts are not queued. The current run completes and only the newest draft is calculated next. The right pane retains the last valid result while newer edits are pending.

Warm automatic failure first degrades to warm manual mode and allows a manual retry. If that retry fails, the workbench degrades to cold COM manual mode. The UI shows the active mode and degradation reason and offers an explicit warm-worker health recheck. It must not silently switch back to automatic mode during active work.

### Workbook Column Contract And Results

The summary sheet uses the following authoritative column contract:

- C: 2025 base-year actual value
- D–H: 2026–2030 forecast values
- I: five-year change
- J: compound growth rate (CAGR)

Import validates both these positions and their header text. A mismatch blocks import as a template-structure error. The frontend must not recalculate columns I or J. It displays the workbook values and leaves a cell blank when the corresponding workbook cell is blank.

The right result pane lists selected output indicators in a table with indicator name, unit, 2025, 2026, 2027, 2028, 2029, 2030, five-year change, and CAGR. It uses horizontal scrolling when required. The result presentation distinguishes yearly result values, workbook-provided five-year change and CAGR, change from the active baseline scenario, and reverse-constraint state.

Display formatting is:

- Percentage below 10%: `x.xx%`
- Percentage from 10% through 100%: `xx.x%`
- Percentage above 100%: `xxx%`
- Amount in 亿元: integer with no decimal places
- Other configured units: rule-set display precision
- Other unconfigured numeric values: two decimal places

Negative signs are retained. Five-year change uses its workbook-provided unit and CAGR uses percentage formatting when present. Excel exports retain original workbook precision; display formatting does not round stored or exported values.

### Draft Protection

Switching template or scenario with an unsaved input edit, variable range, or constraint opens a three-way choice: save as a scenario and switch, discard and switch, or cancel. A successfully calculated but unnamed result remains an unsaved draft. Browser refresh restores the most recent local draft and labels it as restored and unsaved. Unsaved rule-set edits have a separate warning and never mix with calculation drafts.

### Redesign Acceptance Criteria

- All four modules are accessible as parallel tabs in one workbench shell.
- Module switching preserves shared template/scenario/engine context and module-specific drafts.
- Input and output navigation are grouped, collapsible, searchable, and reflect edits and constraints with low-saturation multi-state labels.
- More than six selected indicators automatically create additional center screens without losing state.
- Forward cards provide five vertical absolute-value controls with baseline comparisons and rule-driven limits.
- Forward calculation starts in healthy warm automatic mode, debounces for 500 ms, calculates only the newest draft, and follows the specified degradation path.
- Reverse variable cards and constraint cards are immediately distinguishable and enforce the single-variable or multi-input module rules.
- Reverse constraints bind to one year and support `=`, `≥`, and `≤` with hard or soft behavior.
- The result table reads C–J exactly, leaves blank I/J cells blank, and applies display-only precision rules.
- Pane widths and per-module view preferences survive browser reload.
- Scenario, template, refresh, and rule-edit transitions protect unsaved work.

### Prototype Verdict (2026-07-19)

The balanced three-pane Variant A is the selected visual direction.

- Forward cards offer two user-selectable layouts. The default places years in vertical rows with horizontally moving sliders; the alternative places years across the card with vertically moving sliders.
- The chosen card layout is a view preference and does not change calculation semantics or draft values.
- Every result-table column is resizable from its header edge.
- Default result widths prioritize showing all C–J values without truncating numeric data. The indicator-name column is intentionally narrower and may ellipsize long names while exposing the full name on hover.
- Horizontal result scrolling remains a fallback for narrow panes or user-expanded columns.

### Implementation Status (2026-07-19)

Tickets 16–20 are implemented and moved to `ready-for-human`. The production `/` entry now uses the balanced Variant A unified four-module shell; the throwaway prototype remains only as a historical design artifact and is not referenced by the production entry.

- Shared template, scenario, and engine context plus module-specific drafts, pane widths, card layout, result column widths, favorites, and mobile-pane choice persist in the current browser.
- Forward calculation uses warm automatic mode with 500 ms debounce, newest-draft coalescing, and visible warm-manual/cold-manual degradation.
- The Excel adapter reads C–J and passes workbook-provided 2025, forecast years, cumulative change, and compound-growth values without frontend recalculation. The activity template uses blank/merged C and J headers, so validation requires exact D–H years, change semantics in I, and validates C/J semantics when nonblank.
- Single-variable and multi-input reverse calculation use separate manual modules and retain variables, constraints, priorities, search results, scenario save, and export behavior.
- Multi-input reverse search combines variables at the same priority within the evaluation budget and ranks candidates by hard constraints, soft deviation, changed-variable count, baseline deviation, then priority cost.
- Rule maintenance is embedded as the fourth tab. Read APIs are available to ordinary users; mutation, rescan, activation, and deactivation remain administrator-only.
- Known limitation: an already-imported catalog created before the C–J extension retains blank 2025/I/J fields until the activity template is re-imported or force-rescanned. No values are fabricated while that historical catalog remains active.
