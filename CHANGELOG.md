# Changelog

## 0.1.0 (2026-10-18)

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
