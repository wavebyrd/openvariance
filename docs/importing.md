# Importing private-company data

OpenVariance reads the files finance teams actually have: exports from QuickBooks, Xero and
NetSuite, budget spreadsheets, and general ledger or trial balance dumps. The import has three
steps. Each one shows its work.

## 1. Inspect

```bash
openvariance inspect "P&L Sept.xlsx"
```

Inspection reads the file without importing anything and reports:

- **Layout**
  - *wide:* accounts down the side, periods across the top
  - *long:* one row per account and period, optionally with department or debit/credit columns
  - *by department:* departments or classes across the top, for one period you name with
    `--period 2026-09`
- **Header row and periods.** Recognized period formats include `Jan 2026`, `September 2026`,
  `Sep-26`, `2026-09`, `09/2026`, `Q3 2026`, `2026 Q3`, and real Excel dates.
- **Units.** "In thousands", `($000s)`, "in millions" and similar notes are applied to every
  amount.
- **Skipped rows**, with the reason for each: titles, blank rows, section headers, totals.
- **Totals verified.** "Total Payroll Expenses" is checked against the rows in its section,
  including nested sections. A total that doesn't match produces a warning (often a sign of hidden
  rows).
- **Unlabeled subtotals.** A row that exactly equals the sum of every row in its section is treated
  as a subtotal, with a note saying so.
- **The total column** is checked against each row's periods.
- **Number formats** such as `(1,234.50)`, `1,234.50-`, `$ 99`, `12.00 CR` and dashes for zero.

For XLSX files, the sheet with the most numbers is used unless you pass `--sheet`. Old `.xls`
files need to be saved as `.xlsx` first.

## 2. Map accounts

Every account gets a suggested standard line, a confidence and a reason. Strongest first:

1. **Section:** an account under "Cost of Goods Sold" is cost of sales (high). An account under
   "Other Expense" reduces other income (high).
2. **Name keywords:** "Salaries", "Wages", "Bonus" are payroll (high). "Hosting" and "AWS" are
   cost of sales (medium, because some companies treat hosting as opex).
3. **Account number ranges:** 4000s revenue, 5000s cost of sales, 6000–7000s operating expenses,
   8000s other (low).

Special targets:

- `-revenue`: contra accounts such as returns and discounts are subtracted from revenue.
- `-other_income`: non-operating expenses.
- `ignore`: memo or statistical rows.

```bash
openvariance inspect "P&L Sept.xlsx" --write-mapping map.json   # review and edit, then reuse
```

A mapping file is plain JSON keyed by the account label exactly as it appears in the source:

```json
{
  "openvariance_mapping": 1,
  "sign_convention": "auto",
  "accounts": {
    "5010 Hosting and Infrastructure": "cogs",
    "6200 Software Subscriptions": "software",
    "4900 Sales Returns": "-revenue"
  }
}
```

By default only **high**-confidence suggestions are applied automatically (`--accept high`). Any
account that is still unmapped stops the import and gets listed. Nothing is guessed silently.

## 3. Tie out

After mapping, OpenVariance compares its result with the totals in the source file: Total Income,
Total Cost of Goods Sold, Gross Profit, Net Operating Income and Net Income. Each comparison is a
check with the difference shown. A failed check stops the command-line import, and agents are told
to resolve it with you rather than work around it.

**Sign conventions** are detected automatically:

- If revenue comes in negative while expenses are positive, as in GL and trial balance exports
  (debit − credit), income lines are flipped.
- If revenue is positive and expenses are negative, expense lines are flipped.

Each flip is recorded in the import notes. Set `"sign_convention": "natural"` in the mapping file
to turn detection off.

## The standard format

Anything already in this long format loads directly with no mapping:

```csv
period,line,amount,department
2026-09,revenue,4753442.00,
2026-09,payroll,832500.00,Engineering
```

`line` is a key from the standard chart of accounts. Run `describe_lines` from an agent, or see
`src/openvariance/accounts.py`.

## Driver files

Driver bridges need operating drivers in long format:

```csv
period,driver,key,value
2026-09,units,Pro,4280
2026-09,price,Pro,499
2026-09,unit_cost,Pro,95.40
2026-09,headcount,Engineering,50
2026-09,cost_per_head,Engineering,16650
2026-09,software,,303200
```

`units`, `price` and `unit_cost` are keyed by product. `headcount` (FTEs for the period) and
`cost_per_head` are keyed by department. Any P&L line key, such as `software` or `marketing`,
records fixed spend.
