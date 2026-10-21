# AGENTS.md

Instructions for AI agents, both agents *using* OpenVariance and agents *working on* this
repository.

## Using OpenVariance for someone

**Set it up.**

- If your client supports MCP, add the server. In Claude Code:
  `claude mcp add openvariance -- uvx --from git+https://github.com/wavebyrd/openvariance openvariance mcp`
- If you can run shell commands, the CLI works too. Add `--json` to any command for structured
  output.
- Public-company data needs `OPENVARIANCE_SEC_USER_AGENT="Name email@example.com"`. Ask the user
  for their name and email. Don't invent one.

**Work the way a careful analyst would.**

1. Never state a number the tools didn't return. Don't do arithmetic on results yourself.
2. Private-company files:
   - Run `inspect_file` (or `openvariance inspect`) first.
   - Show the user every mapping suggestion that isn't high confidence and get a yes before
     importing.
   - Save the mapping (`save_mapping_to`, or `--write-mapping`) so the next import is one step.
3. If an import reports unmapped accounts or failed tie-out checks, stop and resolve them with the
   user. Don't change the data to make a check pass.
4. Variance sign convention: positive = favorable. Lead with the bridge (why), then the material
   lines (where).
5. When presenting a forecast, say which assumptions were inferred and which defaulted (the
   `assumption_sources` field), and report the check results.
6. Point users to the Excel export for anything they'll share. Every number in it is a formula
   they can audit.

**Formatting files for import.** If a file won't inspect cleanly, the most reliable fix is to
produce the standard format (`period,line,amount,department`) and confirm it with the user. Line
keys come from `describe_lines`. See [docs/importing.md](docs/importing.md) for common traps:
subtotal rows, "in thousands", negative revenue in GL exports, departments across the top.

## Working on this repository

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest            # tests, including Excel recalculation and property-based checks
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy              # strict
python scripts/make_demo_data.py   # rebuild the Demo Co. dataset after changing it
```

On Windows use `.venv\Scripts\` in place of `.venv/bin/`.

Rules:

- Money is `Decimal` (or `Fraction` inside attribution). Never use float for amounts in the engine.
- Store only leaf lines. Totals are derived from `accounts.LINES` formulas.
- Every new calculation needs a test that proves it ties or reconciles. Prefer a property-based
  test (Hypothesis) when the invariant holds for any input.
- Never force a balance with an unexplained plug. Report the gap as a failed `Check`.
- Keep the CLI and MCP tools thin. Shared behavior belongs in `workflows.py`.
- User-facing text uses sentence case and plain language. Errors say what happened and what to do.
