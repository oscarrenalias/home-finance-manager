# Home Finance Application Specification

Version: 0.2  
Date: 4 October 2026  
Status: Working draft for collaborative editing and implementation

## 1. Purpose

Build a private web application for understanding household finances from manually imported bank CSV exports. Store historical transactions, classify spending, distinguish transfers from expenses, compare periods, and support conversational analysis backed by exact database calculations.

The application covers the accounts imported by the user. It must never present these accounts as a complete picture of household income or spending unless all relevant accounts are included.

The LLM interprets descriptions and questions and explains results. Application code and database queries calculate all financial figures.

## 2. Implementation defaults

These are proposed defaults, not existing user constraints. Implement them unless this specification is revised.

| Area | Default |
| --- | --- |
| Deployment | Private, self-hosted, single application instance |
| Users | One household and one authenticated user initially |
| Backend | Python services invoked through Reflex event handlers; no separate API required initially |
| Database | SQLite on local persistent storage, with migrations |
| Data access | SQLAlchemy and Alembic; keep models compatible with PostgreSQL |
| Frontend | Reflex UI authored in Python, using its generated frontend |
| Currency | EUR only in the first release |
| Dates | Bank dates stored as calendar dates; reporting timezone Europe/Helsinki |
| Money | Signed integer cents; no floating-point financial arithmetic |
| Models | Provider adapter with configurable model names and server-side credentials |
| Background work | Persistent job table and one worker; no external queue initially |
| Distribution | Container configuration, persistent volumes, documented backup and restore |

Do not introduce microservices, a vector database, autonomous agents, or an unrestricted model execution environment.

## 3. Accounts and financial semantics

Initial accounts:

- **Common account:** household contributions and payments for shared expenses, including electricity, children's activities and clothing, and regular or occasional household costs.
- **Accrual account:** reserves for future costs. Most activity consists of transfers between this account and the common account.

An account has an editable name and role. Support additional accounts without changing the schema.

### 3.1 Transaction types

Transaction type is separate from spending category:

- `expense`: payment to an external party.
- `refund`: repayment of an expense; offsets the relevant spending category.
- `internal_transfer`: movement between tracked accounts.
- `contribution`: money supplied to the household pool from outside the tracked accounts.
- `income`: other identified external income, such as interest.
- `external_transfer`: identified movement outside the tracked scope that is not an expense or contribution.
- `unknown`: insufficient information to determine the type.

Never infer that every credit is income or every debit is spending. Never silently omit unknown transactions: show their amounts separately.

### 3.2 Reporting definitions

- Account cash flow includes all executed credits and debits on that account.
- Combined cash flow is the sum across selected accounts. A fully included internal transfer contributes zero net cash flow.
- Spending equals expense outflows minus refunds, grouped by category. Internal transfers, contributions, income, and external transfers are excluded.
- Report gross expenses and refunds separately as well as net spending.
- Spending comparisons use executed transactions only by default.
- Pending reservations appear separately as provisional commitments and are not added to booked spending or the reported bank balance.
- Stored balance observations represent bank-reported balances; do not treat the sum of imported amounts as an absolute balance without an opening balance and complete coverage.
- Partial months and missing import coverage must be visible in every relevant report.
- If only one side of an internal transfer is in the selected accounts, show it as a transfer crossing the selected scope rather than as consumption.

### 3.3 Reserve example

Monthly electricity allocation: EUR 250. Summer bill: EUR 30. Transfer to accrual: EUR 220.

Expected reporting: common-account outflow EUR 250; household spending EUR 30; reserve increase EUR 220. A later EUR 200 transfer back does not create income. A EUR 450 winter bill creates EUR 450 of spending.

An allocation is planning information, not a bank transaction. Moving money between accounts does not by itself create or consume a budget allocation.

## 4. Release scope

### First release

1. Account management and CSV import with preview.
2. Reliable overlapping-import handling and an ambiguity review queue.
3. Executed and pending transaction separation.
4. Categories, merchant rules, LLM suggestions, and manual corrections.
5. Internal-transfer suggestions and confirmation.
6. Searchable transaction table and transaction details.
7. Monthly overview, category totals, and period comparisons.
8. Conversational read-only analysis through structured tools.
9. Basic monthly category budgets and variance reporting.
10. Import history, data export, backup instructions, and audit history.

### Second release

- Virtual reserve pots and opening allocations.
- Recurring payment detection with user confirmation.
- Cash and reserve forecasts with explicit assumptions and scenarios.
- Seasonal comparisons and year-over-year analysis.

### Later features

- Charts generated from reporting datasets.
- Receipt imports and transaction splits.
- Multiple household users.
- Optional constrained SQL for advanced ad-hoc analysis.
- Additional currencies and bank export formats.

Direct bank integration, payments, investment advice, and automatic financial actions are outside scope.

## 5. CSV import

### 5.1 Supported format

```csv
"Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
"30.09.2026";"Food and daily purchases   ";"Grocery stores and supermarkets   ";"K-Market";"-2,55";"722,00";"Executed";"No"
"29.09.2026";"Other expenses   ";"Transfers to own accounts   ";"HOUSEHOLD MEMBER";"-150,00";"724,55";"Executed";"No"
"02.10.2026";"   ";"   ";"Reservation";"-28,98";"";"Pending";"No"
```

Use a real CSV parser with semicolon delimiter and quoted-field handling. Accept UTF-8 including BOM. If decoding fails, offer an explicit encoding choice rather than silently corrupting text. Trim padded category fields; preserve original text alongside normalized descriptions. Parse decimal commas exactly and store empty balance as null. Validate headers, dates, amounts, status values, and row lengths. Preserve the bank's reconciliation field as metadata; it is not the app's review state.

The user selects an account and confirms the export's coverage dates. Default coverage dates from executed row dates, but explain that transactions alone cannot establish coverage on dates with no activity. Allow complete-export versus partial/subset-import selection. Absence from a later file does not automatically delete executed transactions.

### 5.2 Import lifecycle

Upload -> parse -> validate -> preview -> resolve blocking errors -> commit ledger changes atomically -> enqueue classification -> display summary.

Preview includes source account, coverage, executed and pending counts, new rows, existing rows, ambiguous rows, and errors with row numbers. LLM availability must not block import. Failed commits leave no partially committed ledger changes. Retrying a completed import must not insert duplicates.

Preserve original file bytes, file hash, parser version, original row values, and row order. Store files outside public web directories. An identical file hash on the same account is an already-imported file. The same file on a different account requires a warning and explicit confirmation because the sample contains no account identifier.

### 5.3 Overlap and duplicate matching

The format has no stable bank transaction ID. Perfect identity inference is impossible for indistinguishable records. The app must expose uncertainty instead of claiming perfect deduplication.

Use account, executed date, signed amount, conservatively normalized source text, bank balance when present, and neighboring row sequence as matching evidence. Bank categories and user classifications must not determine identity. Preserve multiplicity: two otherwise identical purchases in one export are two transactions.

For each overlapping region, match new rows to existing records one-to-one. Strong exact matches can be reused automatically. Where balances are missing and rows are indistinguishable, compare counts within confirmed complete coverage and surrounding matches; uncertain cases require review. Do not create a database uniqueness constraint on date, description, and amount. Do not ask the LLM to deduplicate financial records.

If a likely existing transaction has changed metadata, show a proposed source revision. Preserve previous observations and manual classifications. Changes to amount or date require review unless a future bank adapter supplies a stable identifier.

Serialize imports for the same account. Recompute matches at commit time if ledger state changed after preview. Use an import idempotency token and transactional checks.

### 5.4 Pending reservations

Store pending records as import observations separate from executed ledger transactions. Display the latest confirmed complete pending snapshot, with its import date. With partial imports, retain observations and mark freshness uncertain.

Do not match reservations to booked records solely by amount. Possible matches may be suggested for review but never counted twice. Pending records disappearing from a later complete snapshot become no longer observed, not confirmed executed. Pending-only exports must be importable.

### 5.5 Balance validation

For consecutive executed observations whose order and coverage are established, check that prior balance plus signed amount equals subsequent balance. Account for reverse chronological row order. If same-day ordering is ambiguous, report that the check is inconclusive. Never rearrange records or fabricate transactions merely to make balances agree.

## 6. Classification and corrections

Maintain a controlled, editable category taxonomy. Initial suggestions: groceries, utilities/electricity, utilities/other, housing, children/activities, children/clothing, transport, household purchases, leisure, subscriptions, insurance, health, fees, and other expenses. Categories apply to expenses and refunds; transaction type handles transfers and contributions.

Classification precedence:

1. Transaction-level manual override.
2. User-confirmed rules, ordered by explicit priority and specificity.
3. Previously confirmed merchant mapping when unambiguous.
4. Validated LLM suggestion.
5. Unknown / needs review.

Normalize merchant descriptions conservatively; cosmetic suffixes such as `))))` can be removed for display and merchant matching, while original descriptions remain intact.

The LLM returns structured JSON containing transaction type, category ID, merchant suggestion, rationale, and ambiguity flag. Validate IDs and allowed values. A model's self-reported confidence is not a calibrated probability. Auto-accept only low-risk merchant/category suggestions under a configurable policy; transfers and ambiguous person-to-person payments require confirmation or an explicit saved rule.

Send only necessary description and bank category hints by default. Include amounts or dates only when needed. Batch unfamiliar descriptions and cache using description, taxonomy version, and relevant context. Never propagate a classification across conflicting confirmed examples.

Correction UI offers: change this transaction, or create/update a rule for future matching transactions. Reclassification of historical transactions requires a preview and confirmation. Manual overrides always survive new imports and model reruns. Keep source, rule/model version, timestamps, and correction history.

## 7. Transfer handling

Suggest candidate pairs across different tracked accounts when amounts are equal and opposite, dates fall within a configurable window (default three days), and descriptions or confirmed counterparty rules support the match. Equal amounts and nearby dates alone are insufficient for automatic confirmation.

Enforce one-to-one links initially. A transaction cannot belong to multiple active pairs. Pairing creates a relationship, not replacement transactions. Preserve both account entries. Unpaired transfers can be manually marked and remain visible in reconciliation. Never create a missing counterpart automatically.

Transactions marked as transfers must be excluded from spending even if their counterpart has not yet been imported. Show the unpaired state separately. Undoing a match restores the previous classification and is audited.

## 8. Logical data model

Use generated stable IDs, foreign keys, migrations, and created/updated timestamps. The following are logical entities; implementation may combine tables where integrity and traceability remain clear.

| Entity | Required information |
| --- | --- |
| Account | Name, role, currency, active flag |
| Import batch | Account, filename, hash, coverage, completeness declaration, state, parser version, counts, idempotency token |
| Source observation | Batch, row number, original fields, parsed values, executed transaction link or pending state |
| Transaction | Account, date, amount cents, original/display descriptions, currency, source balance observation, active classification |
| Classification | Transaction, type, category, merchant, source, rule/model version, review state, rationale |
| Category | Name, optional parent, active flag |
| Merchant | Canonical name and aliases |
| Rule | Match conditions, priority, outputs, version, enabled flag |
| Transfer link | Two transactions, evidence, suggested/confirmed state, confirmation metadata |
| Budget | Category, month/effective period, amount cents |
| Audit event | Entity, action, prior/new values, actor, timestamp |
| Job | Kind, state, retry count, inputs, error summary |
| Conversation | Messages, tool calls, result references, reporting assumptions |
| Reserve pot — release 2 | Name, assigned account, opening allocation, balance derived from entries |
| Reserve entry — release 2 | Pot, date, signed amount, allocation/consumption/adjustment, optional transaction reference |
| Forecast assumption — release 2 | Contributions, recurring costs, seasonal values, scenario overrides, effective dates |

Index transactions by account/date, date/category, type/date, and merchant/date. Index file hashes by account and transaction source links. Money APIs return integer cents and currency; formatting happens at display boundaries.

## 9. Reporting engine

Expose reusable deterministic reporting functions consumed by both UI and conversational tools. Each result includes filters, date range, selected accounts, data coverage, unresolved counts and amounts, calculation definitions, and drill-down references.

First-release reports:

- Monthly spending by category, including refunds and unknown outflows separately.
- Contributions, other inflows, external outflows, internal transfers, and net account movement.
- Bank-reported latest balances with observation dates and staleness.
- Month-to-month and custom-period comparisons with absolute and percentage changes.
- Category budget versus actual, remaining budget, and overspend.
- Largest transactions and merchants contributing to a spending change.

When the baseline is zero, percentage change is null with an explanation. Do not compare a partial current month to a full prior month without labeling it; offer matched day-of-month comparisons. Do not treat missing data as zero activity. Questions about overspending must distinguish budget variance from simply spending more than last month.

## 10. Conversational analysis

The assistant can analyze data but cannot modify the ledger, classifications, budgets, rules, or reserve allocations through chat in the first release. It may recommend a correction and link to the relevant UI.

Tools:

| Tool | Inputs | Output |
| --- | --- | --- |
| `summarize_period` | Dates, account IDs | Spending, cash flow, coverage, unresolved items |
| `compare_periods` | Two ranges, account IDs, category filters | Totals, deltas, category and merchant contributors |
| `find_transactions` | Dates, text, category, type, amount range, account IDs, cursor | Paginated records and total count |
| `analyze_category_trend` | Category IDs, dates, monthly grouping, account IDs | Monthly values and coverage |
| `inspect_budgets` | Month, category filters | Budget, actual, variance |
| `inspect_transfers` | Dates, account IDs, match state | Matched and unpaired transfer records |
| `forecast_cash_balance` — release 2 | Horizon, scenario, account IDs | Monthly cash forecast and assumptions |
| `inspect_reserves` — release 2 | Date, pot IDs | Assigned reserves, changes, unallocated funds |

Validate all arguments on the server. Limit result size, tool-call count, time, and model budget. Broad transaction queries use pagination and aggregation, not arbitrary truncation presented as complete totals. Treat transaction descriptions as untrusted data, never instructions. No arbitrary SQL, shell, network browsing, or code execution tool.

The assistant may make multiple tool calls to answer a question. Maintain follow-up context such as selected period and accounts. Ask a brief clarification if materially different interpretations cannot be resolved from context; otherwise state the assumption.

Every monetary claim must derive from a tool result or an explicit user-provided assumption. Responses distinguish facts, hypotheses, and projections. Attach result/transaction links to supporting figures. Do not invent the reason for a purchase or imply receipt-level knowledge from a merchant name.

Example supported questions:

- Where did money go last month?
- Why did September cost more than August?
- How much did we spend on children's activities this year?
- Which grocery merchants account for the increase?
- Did we exceed our electricity budget?
- Which transfers are still unpaired?
- Which months typically have the highest spending? (Label insufficient history.)

## 11. Web interface

Navigation: Overview, Transactions, Import, Review, Budgets, Ask, Settings. Add Reserves and Forecast in release 2.

- **Overview:** period and account selectors; spending, contributions, account movement, latest balances, budget variance; category table; coverage and pending indicators. Every total supports drill-down.
- **Transactions:** filters, sorting, pagination, merchant/category/type fields, review status. Detail view includes source rows, classification history, and transfer links.
- **Import:** account selection, upload, coverage declaration, preview, commit, progress, summary, and import history.
- **Review:** ambiguous duplicates, classification questions, changed source observations, and transfer suggestions. Explain evidence for each decision.
- **Budgets:** editable category allocations by month, actual amounts, and variance.
- **Ask:** conversation with suggested questions, tool progress, concise explanations, and clickable evidence.
- **Settings:** accounts, categories, merchant rules, model configuration status, export, and backup guidance.

Use EUR formatting and day-month-year dates. Show empty, loading, unavailable-model, failed-import, and incomplete-data states. Support desktop and mobile widths and keyboard-accessible controls. Tables suffice initially; charts are optional later.

## 12. Reserve pots and forecasting — release 2

Reserve entries track earmarking independently of physical transfers. A reserve's opening allocation and monthly provision increase its assigned balance; linked bill consumption decreases it. A transfer between accounts changes cash location only. Prevent double consumption when a bill is linked again or edited.

Display actual account balance, assigned pot total, unallocated amount, and any underfunding separately. Require opening allocations; do not infer historic pot assignments from transfer amounts alone. Negative pot balances are allowed as explicit underfunding.

Forecast inputs: confirmed recurring bills, expected contributions, variable-category baselines, electricity seasonality, user-entered upcoming costs, and opening cash/reserves. Forecasting is deterministic and stores a reproducible snapshot of assumptions.

Use recent complete-month averages for variable costs initially. Seasonal profiles require at least two reasonably complete annual cycles; otherwise use editable monthly estimates. Show low/base/high expense scenarios with explicit ranges rather than fabricated statistical confidence. Include planned transfers to identify account-level liquidity shortfalls, without treating them as household expenditure.

Backtest forecasts against subsequent actuals when enough history exists. LLM explanations may describe forecast drivers; they must not invent amounts or replace the forecasting calculation.

## 13. Security and operations

- Require authentication for all financial UI and API routes. Local-only development may use an explicitly configured development bypass bound to loopback; production must fail closed without authentication configuration.
- Keep LLM keys server-side and outside the database exports and client bundle.
- Validate upload size and content; prevent filename/path traversal and spreadsheet-formula injection in exported CSV.
- Do not log raw files, full transaction descriptions, credentials, or model request bodies by default.
- Keep raw files and the database on persistent storage with restricted permissions. Document encrypted storage/backups as deployment options.
- Minimize data sent to external model providers and expose a clear setting to disable model calls. Import, correction, and predefined reporting must still work.
- Use retry limits and durable job states. Provider failures must not lose data or overwrite confirmed values.
- Provide data export and a documented restore procedure. Use a consistent SQLite backup mechanism rather than copying an active database file blindly.

Target normal overview and aggregate queries below one second on a representative 100,000-transaction local dataset, excluding LLM latency. Report the test environment. Imports show progress and must not freeze the UI.

## 14. Acceptance criteria

| ID | Scenario | Required result |
| --- | --- | --- |
| A01 | Import the supplied CSV shape | Dates, decimal commas, whitespace, null balances, and statuses parse correctly |
| A02 | Reimport an identical file for the same account | Zero new executed transactions |
| A03 | Import overlapping complete extracts | Existing executed rows are reused; genuinely new rows are inserted once |
| A04 | Two equal purchases on the same date | Both retained; later overlapping import preserves their multiplicity |
| A05 | Missing balances and indistinguishable overlap | Ambiguity shown; no silent destructive merge |
| A06 | Pending reservation disappears and a similar executed payment appears | Executed payment counted once; no assumed identity based only on amount |
| A07 | Pending-only export | Imports without requiring an executed-date range |
| A08 | EUR 220 transfer between common and accrual accounts | Account movements visible; combined spending unchanged |
| A09 | Summer electricity example | Common outflow EUR 250; spending EUR 30; transfer EUR 220 |
| A10 | Person-to-person description with insufficient context | Review requested; no invented purpose |
| A11 | Manual classification followed by reimport/model rerun | Manual override retained |
| A12 | EUR 100 expense and EUR 20 refund | Gross expense EUR 100; refund EUR 20; net spending EUR 80 |
| A13 | Partial month compared with full month | Coverage warning and comparison basis visible |
| A14 | Zero previous-period spending | No division error or infinite percentage |
| A15 | Ask why spending increased | Exact comparison and contributing records support the explanation |
| A16 | Model provider unavailable | Import and deterministic reports work; jobs can retry |
| A17 | Description contains malicious instructions | Treated as transaction text; no instructions executed |
| A18 | Concurrent import or retry | No duplicate ledger insertion or partial commit |
| A19 | Export and restore backup | Transactions, rules, classifications, and provenance survive |
| A20 | Reserve allocation EUR 250, bill EUR 30, transfer EUR 220 | Pot grows EUR 220 once; physical transfer adds no allocation — release 2 |
| A21 | Forecast generated twice with identical inputs | Identical numerical results and recorded assumptions — release 2 |

## 15. Delivery plan for the implementing model

1. Create project structure, migrations, account model, and CSV parser. Add realistic synthetic fixtures.
2. Implement source preservation, import preview/commit, duplicate review, pending snapshots, and balance checks. Verify A01–A07 and A18.
3. Implement classification rules, manual overrides, transfer matching, audit history, and review UI. Verify A08–A12.
4. Implement reporting engine, budgets, overview, and transaction drill-down. Verify A13–A14 and numerical consistency across UI/API.
5. Add provider adapter, structured classification, background jobs, and conversational tools. Use mocked providers for repeatable tests. Verify A15–A17.
6. Add authentication, container setup, export/backup/restore instructions, operational checks, and performance measurement. Verify A19.
7. Implement release 2 separately after the first release is usable and reviewed.

Deliver runnable source, migration scripts, meaningful tests for financial/import invariants, synthetic demonstration data, environment example without secrets, and a README covering setup, model configuration, import behavior, backup, and known limits.

Do not use real bank data in committed fixtures. Do not deliver only a UI mockup. Complete each slice end to end, with deterministic results and inspectable evidence, before adding the next.

## 16. Decisions to revisit

The following do not block the first implementation; use the stated defaults until changed:

- SQLite versus PostgreSQL: SQLite initially.
- Hosting environment and authentication provider: self-hosted private deployment with configurable authentication; select concrete production integration during setup.
- Model provider and model: configurable adapter; no hard-coded model name.
- Final category taxonomy and merchant rules: editable in the application.
- Existing opening balances and reserve allocations: user-entered, never inferred without evidence.
- Whether contribution sources need named household members: optional metadata initially.
- Whether exports always cover all transactions: explicit confirmation per import.

## 17. Technical recommendations

### 17.1 Framework choice

Recommend **Reflex** for the first implementation. The user's preference is Python and a shared language for frontend and backend. This application requires multiple pages, imports with review steps, transaction corrections, budgets, and conversational analysis; a general application interface is a better fit than a dashboard-only design.

Reflex provides Python-authored components, state, and event handlers with a generated React frontend and Python server. This is a single authoring language, not a Python-only runtime: frontend build dependencies and browser/server communication still need deployment configuration. Use the framework-supported build process; do not maintain generated frontend code manually.

| Option | Appropriate use | Trade-off for this application |
| --- | --- | --- |
| Reflex — recommended | Python-authored multipage application with interactive workflows | Requires learning its state/event model and accepting its frontend abstraction |
| Streamlit | Fast implementation of a compact personal analysis tool with tables and chat | Rerun-based execution needs careful handling as review workflows and UI state grow |
| Plotly Dash | Dashboard-oriented analysis with sophisticated charts and grids | Callback-driven development is less natural for the full import, correction, and conversation workflow |
| FastAPI plus React/Vite | Maximum UI control and a separately consumable API | Adds TypeScript, frontend tooling, API contracts, and two application layers to maintain |

Streamlit is a viable alternative, including for a lasting personal app, if a straightforward analysis interface becomes the priority. Choose Dash if interactive dashboards become the main product. Reconsider a separate SPA if required UI components prove awkward in Reflex or external API consumers become a concrete requirement.

### 17.2 Recommended components

| Layer | Recommendation | Responsibility |
| --- | --- | --- |
| UI and application events | Reflex | Pages, components, UI state, validated user actions, streamed progress |
| Business logic | Ordinary Python modules | Parsing, identity matching, classification precedence, transfers, reporting, forecasts |
| Validation | Pydantic | Import commands, reporting arguments, tool contracts, model response schemas |
| Persistence | SQLAlchemy and Alembic | Database sessions, models, constraints, explicit schema migrations |
| Database | SQLite initially | Local durable records; consider PostgreSQL when concurrency or deployment needs justify it |
| CSV parsing | Python standard-library CSV parser and Decimal | Exact parsing before conversion to integer cents |
| LLM integration | Provider SDK behind a small adapter | Structured classification, streaming conversation, tool calls, limits and retries |
| Durable jobs | Database job table and separate Python worker | Restart-safe classification and longer processing tasks |
| Charts later | Plotly | Figures built from deterministic reporting datasets; verify Reflex integration before adoption |
| Tests | pytest | Financial invariants, import identity, services, provider-contract tests |
| Packaging | Docker and persistent storage | Repeatable build, application and worker processes, backup and restore |

Use pinned compatible dependency versions and a lockfile. Configure the database location, provider credentials, model names, and operational limits through server-side settings. A separate FastAPI service, Redis, Celery, LangChain, or a vector store is not required for the initial application.

### 17.3 Separation of UI and financial logic

Keep Reflex state limited to presentation concerns: selected accounts, filters, page cursors, import preview identifiers, review selection, and chat progress. Persist ledger data, import decisions, jobs, rules, budgets, and conversation history in the database. Browser reconnection or server restart must not lose committed financial state.

Event handlers validate a command, authorize it, invoke a service, and update displayed state. They must not contain the financial calculation or transaction-matching implementation. Reporting tools and predefined UI reports call the same Python services so figures remain consistent.

Suggested module boundaries:

- `ui`: Reflex pages, components, state, and event handlers.
- `domain`: money, transaction semantics, matching rules, reporting definitions, and forecasts.
- `services`: application operations coordinating domain logic and persistence.
- `storage`: SQLAlchemy models, repositories, session management, and migrations.
- `llm`: provider adapter, structured schemas, prompts, and tool dispatch.
- `jobs`: durable job acquisition, execution, retries, and recovery.
- `tests`: synthetic fixtures and behavioral tests.

Domain modules must be testable without importing Reflex. Use short-lived database sessions per operation; never keep an open session in UI state or across an LLM network call. Enforce authorization at the service/event boundary, including framework event and upload endpoints; client state identifiers are not authentication credentials.

### 17.4 Background work and deployment

Reflex background events support responsive interactions but do not provide durable job execution. Use them for transient progress and streaming where appropriate. Persist classification jobs before execution and use the worker for tasks that must survive a process restart. Save conversation/tool results progressively when recovery matters.

The worker should claim jobs transactionally, record a lease and attempt count, retry with bounded backoff, and recover expired leases. Make job effects idempotent. Use short SQLite write transactions and configure an appropriate busy timeout; application and worker access the database on the same host, not through a network filesystem.

Deploy the generated frontend, backend, and worker using the supported Reflex production workflow. Configure reverse-proxy routing for browser/server communication, including WebSocket upgrades. Document persistent volumes for the database and raw imports. Do not assume a static frontend deployment alone is sufficient.

### 17.5 Initial framework validation

Before implementing every screen, complete one runnable slice:

1. Upload a synthetic CSV and show a validated preview.
2. Commit the import and show a paginated, filtered transaction table.
3. Correct one category and confirm it persists after refresh.
4. Ask a question through a mocked model and return a calculated answer with a transaction drill-down link.
5. Verify progress display, connection recovery, and layout at desktop and mobile widths.

This slice validates Reflex against the application's most important interactions. If a component requires a custom JavaScript wrapper, document the scope and maintenance cost before broad adoption. A framework change must preserve the ordinary Python domain/services and database model.

### 17.6 Reference documentation

- [Reflex architecture](https://reflex.dev/docs/advanced-onboarding/how-reflex-works/)
- [Reflex background events](https://reflex.dev/docs/events/background-events/)
- [Streamlit execution flow](https://docs.streamlit.io/develop/api-reference/execution-flow)
- [Streamlit chat elements](https://docs.streamlit.io/develop/api-reference/chat)
- [Dash AG Grid](https://dash.plotly.com/dash-ag-grid)
- [Dash background callbacks](https://dash.plotly.com/background-callbacks)

These links support framework capabilities; the recommended application structure and framework fit are design judgments for this project.
