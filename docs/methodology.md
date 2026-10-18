# Methodology

This page documents every calculation OpenVariance makes, so a finance reviewer can check it
without reading the code.

## 1. Data model

All amounts are stored as **natural magnitudes**: revenue, expenses, assets and liabilities are
positive numbers. Only leaf lines are stored. Totals such as gross profit, EBITDA, operating
income and total assets are always computed from their components when read, so a total can
never disagree with its parts.

Each line is a **flow** or a **stock**:

| Kind | Examples | Across several periods |
|---|---|---|
| Flow | revenue, payroll, cash from operations | summed |
| Stock | cash, receivables, debt, equity | last period's balance |

Missing data is `None`, not zero. A computed line is `None` only if every component is missing.
Reports show missing values as "—".

## 2. Variance

For each line, with *direction* = +1 for revenue and profit lines and −1 for cost lines:

```
delta    = actual − baseline
variance = delta × direction            positive = favorable
pct      = variance ÷ |baseline|        "n/m" when baseline = 0
status   = F if variance > 0, U if variance < 0 (after rounding to cents)
```

**Why the absolute baseline?** Suppose operating income was budgeted at −$100 (a loss) and came in
at −$50. The variance is +$50 (favorable). Dividing by the raw baseline would give −50%, which
reads as bad news. Dividing by |−100| gives +50%, which matches what happened.

**Materiality.** A line is flagged when |variance| ≥ the dollar threshold *or* |pct| ≥ the
percent threshold. The defaults are $10,000 and 10%.

**Department drill-down.** When a line has department detail, each department gets its own
variance row, and the line's actual and budget are the sum of its departments. In Excel, the
parent row is a `SUM` of the department rows.

## 3. Driver attribution (the bridge)

### The problem

Revenue = units × price. If units rise 10% and price rises 20%, some of the revenue increase comes
from *both changing together* (the cross term). Sequential substitution ("change volume first,
then price") gives the cross term to whichever driver goes second, so the answer depends on an
arbitrary ordering choice.

### The method: Shapley values

Each driver's contribution is its marginal effect averaged over **every order** in which the
drivers could be switched from baseline to actual:

```
φᵢ = Σ over subsets S not containing i of  |S|! (n − |S| − 1)! / n!  × [ f(S ∪ {i}) − f(S) ]
```

where `f(S)` is the metric computed with the drivers in `S` at actual values and every other
driver at baseline.

Properties:

- **Efficiency.** Σ φᵢ = f(actual) − f(baseline). The bridge always reconciles.
- **Symmetry.** Drivers with identical effects get identical credit.
- **No ordering choice.** The result is the same however the drivers are listed.

**Worked example.** Price goes from $10 to $12 and units from 100 to 110, so revenue goes from
$1,000 to $1,320.

| Order | Price effect | Volume effect |
|---|---|---|
| Price first | (12 − 10) × 100 = 200 | 12 × (110 − 100) = 120 |
| Volume first | (12 − 10) × 110 = 220 | 10 × (110 − 100) = 100 |
| **Shapley (average)** | **210** | **110** |

210 + 110 = 320, which is exactly the revenue change. With two drivers, Shapley equals the
well-known midpoint method. With more drivers, it generalizes it without arbitrary choices.

### Exactness and rounding

The attribution is computed in exact rational arithmetic (Python `Fraction`), so there is no
floating-point drift. Contributions are then rounded to cents with the **largest-remainder
method**. Each one is floored, and the cents lost to rounding go to the contributions with the
largest remainders. The rounded steps therefore add up exactly to (rounded actual − rounded
baseline), and the bridge ties to the cent.

### The driver model

```
revenue  = total units × Σ (mix shareᵢ × priceᵢ)
COGS     = total units × Σ (mix shareᵢ × unit costᵢ)
payroll  = Σ (headcount_d × cost per head_d)      headcount in FTE-months
other    = fixed spend by line (software, marketing, ...)
```

Driver groups: **Volume** (total units), **Mix** (product shares), **Price**, **Unit cost**,
**Headcount**, **Pay rate**, and one group per fixed spend line. Separating total units from mix
is what lets the bridge say "we sold more, but more of it was the cheaper plan."

For multi-period comparisons (year to date), drivers are aggregated so the model of the aggregate
equals the sum of the monthly models exactly. Units and headcount add up. Prices, unit costs and
pay rates become weighted averages (total revenue ÷ total units, and so on).

### When there are no drivers

Without driver data, the bridge is built from line-item variances: revenue, cost of sales, each
expense line. It shows *where* the change landed but not *why*, and it says so.

## 4. Forecast

A deliberately transparent, fully linked three-statement model. All rates are per period unless
labeled annual.

| Line | Formula |
|---|---|
| Revenue | prior revenue × (1 + growth) |
| Cost of sales | revenue × (1 − gross margin) |
| Operating expenses (excl. D&A) | revenue × opex % + fixed opex |
| D&A | opening PP&E × depreciation rate |
| Interest | (opening debt + opening revolver) × annual rate ÷ periods per year |
| Tax | max(pretax income, 0) × tax rate |
| Receivables | revenue × DSO ÷ days in period |
| Inventory | cost of sales × DIO ÷ days in period |
| Payables | cost of sales × DPO ÷ days in period |
| PP&E | opening PP&E + capex − D&A |
| Debt | opening debt + net borrowing |
| Equity | opening equity + net income − dividends |
| Cash from operations | net income + D&A − Δreceivables − Δinventory + Δpayables |
| Revolver | draws to keep cash at the minimum; repaid first from cash above it |
| Cash | opening cash + operating + investing + financing cash flow |

Other current assets, other assets, accrued liabilities, deferred revenue and other liabilities
are held flat.

**The revolver.** If operating and investing cash flow would push cash below the minimum cash
assumption (default 0), the model draws on a revolving credit facility for exactly the shortfall.
When cash later exceeds the minimum, the excess repays the revolver first. The forecast summary
reports the peak balance as the company's funding need, instead of showing negative cash. Interest
is charged on opening balances, which avoids a circular reference in Excel.

**Cash is not a plug.** It comes from the cash flow statement. Two checks run every period:

1. **Balance sheet:** total assets − (total liabilities + equity) = 0
2. **Cash:** opening cash + net change in cash − closing cash = 0

If the *opening* balance sheet doesn't balance (common when the history comes from partial data),
that gap is reported as a failed opening check. Later checks then measure whether the forecast
introduced any *new* gap. Nothing is quietly forced to balance.

**Starting assumptions** are inferred from recent history: growth is the compound rate over the
last four periods, margins and opex percentages come from the same window, and working-capital
days come from the latest balance sheet. Each assumption records where it came from, or that it
fell back to a stated default.

**Scenarios.** Upside and downside move four things symmetrically from the base:

- revenue growth by ±4 points a year
- gross margin by ±2 points
- opex by ∓1 point of revenue
- DSO by ∓5 days

## 5. SEC data

SEC's `companyfacts` API returns every XBRL fact a company has filed. OpenVariance turns it into
quarterly statements as follows:

1. **Ranked tags.** Each standard line has an ordered list of us-gaap tags (for example
   `Revenues`, then `RevenueFromContractWithCustomerExcludingAssessedTax`). For each quarter, the
   first tag with a value is used.
2. **Restatements.** When the same period is reported more than once, the most recently filed
   value wins.
3. **Discrete quarters.** A reported three-month value is used directly. Otherwise the quarter is
   the difference between consecutive year-to-date values with the same start date. Q4 is always
   derived this way: full year minus nine months.
4. **Labels.** Quarters are labeled by the calendar quarter of the period end, adjusted by a week
   so 52/53-week fiscal years land correctly.
5. **Lines the company doesn't itemize.** "Other operating expenses" is derived so the income
   statement ties to reported operating income. "Other assets" and "Other liabilities" are
   derived so the balance sheet ties to reported totals. Each derived value is labeled as derived.
6. **D&A.** It comes from the cash flow statement and is shown as its own line when the remaining
   operating costs are large enough to contain it.

Every value keeps its provenance (tag and method) in `SecCompany.provenance`.

## 6. Importing files

See [importing.md](importing.md). In short: totals are detected and verified, not imported;
mappings carry a confidence and a reason; and an import is only used when every account is mapped
and the result ties to the file's own totals.
