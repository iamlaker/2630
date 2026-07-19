# Five-Year Bank Forecasting

This context defines the business language used to operate template-backed bank forecasting, rule maintenance, calculations, and scenarios.

## Language

**Activity Template**:
The template version that is authoritative for all newly initiated calculations, rule-set maintenance, and scenarios. The activity template is currently `2026-2030年盈利测算表0717-模板.xlsx`; older templates remain historical only.
_Avoid_: Current file, latest Excel, default workbook

**Historical Template**:
A previously authoritative template retained only to explain or reproduce historical rules, calculations, and scenarios. It must not receive newly initiated calculations after replacement.
_Avoid_: Old file, backup template

**Rule Set**:
A template-specific collection of input mappings and adjustment configurations. A rule set belongs to one template fingerprint and cannot become active for another fingerprint without migration and validation.
_Avoid_: Mapping table, cell configuration

**Base Year**:
The 2025 actual-value year used as the reference point for displaying and calculating forecast changes. It is read-only in the workbench and is not a forecast adjustment year.
_Avoid_: Forecast year, editable year

**Forecast Years**:
The editable 2026–2030 years used by forward scenarios and reverse calculations.
_Avoid_: Base year

**Five-Year Change**:
The absolute change from the 2025 Base Year value to the 2030 Forecast Year value.

**Compound Growth Rate (CAGR)**:
The compound annual growth rate from 2025 through 2030, calculated across five periods.

**Year-Bound Constraint**:
A reverse-calculation condition attached to one indicator and exactly one year. Its relation is equality (`=`), lower bound (`≥`), or upper bound (`≤`), and it may be hard or soft.

**Reverse Variable**:
An input indicator whose permitted range is searched by a reverse calculation to satisfy its constraints. Its configured value is a search boundary or starting point, not a fixed result.
_Avoid_: Target output, fixed input

**Reverse Constraint**:
A required or preferred condition placed on an input or calculated output during reverse calculation.
_Avoid_: Output edit, direct output adjustment

**Calculation Draft**:
The unsaved set of input edits, reverse variables, and constraints being prepared in one calculation module.
_Avoid_: Scenario, calculated result
