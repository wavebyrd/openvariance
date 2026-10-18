# Using OpenVariance from an AI agent

OpenVariance ships a local MCP server. The agent app starts it on demand with `openvariance mcp`
and talks to it over stdio. Nothing is hosted, and there is no account or API key.

## Setup

**Claude Code**

```bash
claude mcp add openvariance -- uvx --from git+https://github.com/wavebyrd/openvariance openvariance mcp
```

**Claude Desktop, Cursor, and other MCP clients.** Add this to the client's MCP configuration:

```json
{
  "mcpServers": {
    "openvariance": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wavebyrd/openvariance", "openvariance", "mcp"],
      "env": { "OPENVARIANCE_SEC_USER_AGENT": "Your Name you@example.com" }
    }
  }
}
```

The `env` entry is only needed for public-company data. SEC asks every automated client to send
a name and email.

**Agents that can run shell commands** can skip MCP and use the CLI with `--json` on any command.
Agents can also set themselves up by reading [AGENTS.md](../AGENTS.md) or [llms.txt](../llms.txt).

## Tools

| Tool | What it does |
|---|---|
| `load_demo` | Loads Demo Co. as `actual`, `budget` and `history`, with driver data |
| `inspect_file` | Layout, rows, skipped totals, warnings and mapping suggestions, without importing |
| `import_file` | Maps and imports a file as a named dataset; reports unmapped accounts and tie-out checks |
| `load_sec_company` | Quarterly statements for a US public company from SEC EDGAR |
| `list_datasets` | What's loaded in this session |
| `run_variance` | Line-by-line variance, materiality, department detail, reconciling bridge, summary |
| `run_forecast` | Three-statement forecast, base/upside/downside, with assumption sources and checks |
| `check_balance_sheet` | Assets = liabilities + equity, every period |
| `export_excel` | Formula-driven workbook for the last variance or forecast |
| `export_dashboard` | Self-contained HTML dashboard for the last variance |
| `describe_lines` | The standard chart of accounts, for mappings |

## Rules the server gives agents

The server sends these instructions when it connects:

1. Every number reported must come from a tool result. The agent never calculates or estimates
   figures itself.
2. For private-company files, inspect first, show the user anything that isn't a high-confidence
   mapping, confirm, then import. Save the confirmed mapping for next month.
3. Unmapped accounts or failed checks stop the work until they're resolved with the user.
4. Lead variance answers with the bridge, which explains why the headline moved.
5. Positive variance is favorable. The F/U flags say which.
6. Mention check results when presenting a forecast or bridge.

## Example conversation

> **You:** Here's our September P&L from QuickBooks and the FY26 budget. How did we do?
>
> **Agent:** calls `inspect_file` on both files. Two budget accounts are medium confidence, so it
> asks: "Should 'Other G&A' map to other operating expenses?"
>
> **You:** Yes.
>
> **Agent:** calls `import_file` for each file, which saves the mapping and passes the tie-out
> checks, then `run_variance`, then `export_dashboard`, and replies: "Operating income was $1.00M,
> $188K (15.8%) below budget. The biggest drivers were software (−$90.0K) and Enterprise pricing
> (−$79.4K) …"

## Privacy

The server reads files on your machine and never uploads them anywhere itself. The numbers it
returns to your agent become part of your conversation with your AI provider, the same as pasting
them into a chat. Use an AI setup your organization has approved for financial data.
