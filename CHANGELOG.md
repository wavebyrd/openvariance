# Changelog

## 0.1.0 (unreleased)

First release.

- Line-by-line variance analysis with favorable-signed variances, absolute-base percentages,
  materiality flags and department drill-down
- Shapley driver attribution (volume, mix, price, unit cost, headcount, pay rate, fixed spend)
  with exact arithmetic and largest-remainder rounding, so bridges reconcile to the cent
- Linked three-statement forecast with base, upside and downside scenarios, inferred assumptions
  with sources, and balance and cash checks every period
- SEC EDGAR loader: ranked us-gaap tags, restatement handling, discrete quarters, derived Q4,
  balance sheet tied to filed totals
- Importer for wide, long and by-department layouts, with unit detection, subtotal verification,
  confidence-rated account mapping and tie-out checks
- Excel exports with live formulas, verified by independent recalculation in the tests
- Self-contained HTML dashboard
- CLI with `--json` output, and a local MCP server for AI agents
- Demo Co. sample dataset and messy example exports

Fixes found in end-to-end testing before release:

- CI created no virtual environment before installing, so every job failed
- "Office Supplies & Misc" mapped to rent and facilities; explicit supplies, postage and misc
  accounts now map to other operating expenses
- MCP tool errors now reach the agent as plain-English messages instead of a generic failure
- Forecasts refuse data without a balance sheet instead of assuming zero balances
- Excel: KPI tiles no longer show `####`, the waterfall chart plots its hidden helper columns,
  check cells read 0.00, and the scenario selector rejects names that aren't scenarios
