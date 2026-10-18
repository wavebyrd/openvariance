---
name: openvariance
description: FP&A variance analysis with OpenVariance. Use when someone asks how actual results compare with a budget, forecast or prior period, why profit or costs moved, for a budget vs actual report, to import a P&L export or budget spreadsheet, for a three-statement forecast, or for a public company's quarterly financials from SEC filings.
---

# OpenVariance

Use the OpenVariance MCP tools if they're connected. Otherwise use the CLI
(`uvx --from git+https://github.com/wavebyrd/openvariance openvariance <command> --json`).

## Workflow

1. **Get the data.**
   - Demo: `load_demo`.
   - Files: `inspect_file` each one. Show the user any suggestion that isn't high confidence as a
     short table (account → line, reason) and ask them to confirm. Then call `import_file` with the
     confirmed `mapping` and `save_mapping_to`.
   - Public company: `load_sec_company`. Ask the user for the name and email SEC requires if
     `OPENVARIANCE_SEC_USER_AGENT` isn't set.
2. **Stop on problems.** Unmapped accounts or failed tie-out checks: explain them in plain words and
   resolve them with the user.
3. **Analyze.** Call `run_variance` for one period or `ytd=true`. Lead with the headline and the
   bridge (why), then the material lines (where). Quote numbers exactly as returned.
4. **Look ahead (optional).** Call `run_forecast`. Say which assumptions were inferred and which
   defaulted, and confirm the checks passed.
5. **Deliver.** Use `export_dashboard` to share and `export_excel` for anything people will audit
   or edit. Give the user the file paths.

## Conventions

- Positive variance = favorable. F/U flags make it explicit.
- Var % = variance / |baseline|. "n/m" means the baseline is zero.
- Never compute, round or estimate figures yourself. The tools return every number you need.
