# Home Finance — Roadmap

## Decisions in effect

| Topic | Decision |
|---|---|
| Auth | None for now — app is internal/home-only |
| Categories | YAML config file (`config/categories.yaml`); restart to retune; not stored in DB |
| LLM stack | LangChain + LiteLLM (OpenAI-compatible endpoints) |
| LiteLLM | docker-compose sidecar |
| Deployment | docker-compose: migrate init container, app container, worker container, LiteLLM sidecar |
| Microservices | Not planned for release 1 |

---

## Iteration 1 — Walking skeleton with real classification

Goal: a working end-to-end slice that validates the full stack (Reflex, import pipeline, LangChain/LiteLLM classification) before layering features on top.

### Infrastructure & scaffold
- [ ] Reflex project structure (`ui/`, `domain/`, `services/`, `storage/`, `llm/`, `jobs/`, `tests/`)
- [ ] SQLAlchemy models + Alembic migrations (Account, ImportBatch, SourceObservation, Transaction, Classification, TransferLink, Job, AuditEvent)
- [ ] docker-compose: migrate init container (runs `alembic upgrade head`), Reflex app, background worker, LiteLLM sidecar
- [ ] `config/categories.yaml` — initial taxonomy with model guidance and examples
- [ ] Synthetic fixture data for tests (never use `sample-data/` in committed tests)

### CSV import
- [ ] Finnish-format CSV parser: semicolon delimiter, decimal commas, DD.MM.YYYY dates, UTF-8+BOM, padded fields, `))))` suffix stripping, null balances on pending rows
- [ ] Handle `Executed`, `Pending`, `Rejected`, `Deleted` status values
- [ ] Import flow: upload → parse → validate → preview → commit (atomic)
- [ ] Basic duplicate detection (exact match on account + date + amount + normalized description)
- [ ] Store original file bytes, file hash, row order, and parser version

### Transaction table
- [ ] Paginated, filterable transaction list (date, account, category, type, status)
- [ ] Transaction detail view (source row, current classification)

### Classification
- [ ] LangChain + LiteLLM integration: structured output (type, category ID, merchant, rationale, ambiguity flag)
- [ ] Categories loaded from `config/categories.yaml` at startup; injected into classification prompt
- [ ] Background job: enqueue classification after import commit
- [ ] Manual category correction on a transaction; correction persists after reimport and model reruns
- [ ] Classification precedence: manual override → rules → LLM suggestion → unknown

### Basic conversational answer
- [ ] Single-turn question → LangChain tool call → calculated answer with transaction drill-down link
- [ ] Tools in scope for this iteration: `summarize_period`, `find_transactions`
- [ ] Mocked tool responses acceptable for layout/UX validation; real tools preferred

### Tests
- [ ] CSV parser: decimal commas, date formats, BOM, padded fields, null balance, all status values
- [ ] Import idempotency: reimport of identical file produces zero new executed transactions (A02)
- [ ] Manual override survives reimport and model rerun (A11)

---

## Iteration 2 — Import robustness

- [ ] Overlap and duplicate review queue: ambiguous rows shown with evidence, not silently merged
- [ ] Multiplicity preservation: two equal purchases on the same date both retained (A04)
- [ ] Missing-balance ambiguity disclosed, not silently resolved (A05)
- [ ] Pending reservations: stored separately, not counted as executed spending
- [ ] Pending snapshot: display latest confirmed snapshot with import date
- [ ] Balance validation: prior balance + amount = subsequent balance where coverage is established (A01)
- [ ] Import history page
- [ ] Concurrent import safety: no duplicate ledger insertion (A18)

---

## Iteration 3 — Classification & transfer matching

- [ ] Classification rule engine: match conditions, priority, outputs, versioning
- [ ] Merchant normalization and canonical merchant mapping
- [ ] LLM classification batching and caching (by description + taxonomy version)
- [ ] Transfer matching: suggest pairs across accounts (equal + opposite amounts, dates within configurable window)
- [ ] Transfer confirmation UI: one-to-one links only; unpaired transfers visible separately (A08)
- [ ] Internal transfer excluded from spending even before counterpart is imported
- [ ] Audit history: classification source, rule/model version, timestamps, correction history
- [ ] Review queue UI: ambiguous duplicates, classification questions, transfer suggestions
- [ ] Gross expenses and refunds reported separately; net spending = expenses − refunds (A12)

---

## Iteration 4 — Reporting & budgets

- [ ] Reporting engine: deterministic functions shared by UI and conversational tools
- [ ] Monthly spending by category (expenses − refunds; unknowns shown separately)
- [ ] Contributions, inflows, internal transfers, net account movement
- [ ] Bank-reported balances with observation dates and staleness indicators
- [ ] Month-to-month and custom-period comparisons (absolute + percentage; null when baseline is zero) (A13, A14)
- [ ] Category budgets: monthly allocations, actual vs budget, remaining, overspend
- [ ] Overview page: period + account selectors, four summary metrics, category table, drill-down links
- [ ] Coverage warnings: partial months labeled; missing data never treated as zero

---

## Iteration 5 — Conversational analysis

- [ ] Full multi-turn LangChain sessions: conversation history, retry logic, memory management
- [ ] Model routing via LiteLLM: smaller model for classification, capable model for complex analysis
- [ ] All structured tools: `summarize_period`, `compare_periods`, `find_transactions`, `analyze_category_trend`, `inspect_budgets`, `inspect_transfers`
- [ ] Server-side argument validation and result size limits on all tools
- [ ] Pagination and aggregation for broad queries (no arbitrary truncation)
- [ ] Ask page: suggested questions, tool progress, concise explanations, clickable evidence
- [ ] Every monetary claim derived from a tool result; facts distinguished from hypotheses (A15, A17)

---

## Iteration 6 — Operations & hardening

- [ ] Data export (transactions, rules, classifications, provenance)
- [ ] SQLite backup and documented restore procedure
- [ ] Environment example file (no secrets committed)
- [ ] Performance measurement: overview and aggregate queries < 1 s on 100k transactions
- [ ] LiteLLM key management: server-side only, never in DB export or client bundle
- [ ] Upload validation: size limits, content checks, path traversal prevention
- [ ] Job retry limits, durable job states; provider failures must not lose data (A16, A19)
- [ ] README: setup, model configuration, import behaviour, backup, known limits

---

## Release 2 — Reserves & forecasting

- [ ] Virtual reserve pots with opening allocations
- [ ] Reserve entries: allocation, consumption, adjustment, optional transaction link
- [ ] Accrual balance = actual balance − assigned pots − unallocated (A20)
- [ ] Recurring payment detection with user confirmation
- [ ] Cash and reserve forecasts: deterministic, reproducible assumption snapshots (A21)
- [ ] Seasonal comparisons and year-over-year analysis
- [ ] `forecast_cash_balance` and `inspect_reserves` conversational tools
- [ ] Reserves and Forecast pages

---

## Category taxonomy (initial — `config/categories.yaml`)

Seed set based on the spec and observed sample data:

| ID | Name | Notes |
|---|---|---|
| `groceries` | Groceries | Supermarkets, convenience stores |
| `restaurants_cafes` | Restaurants & cafes | Includes fast food and takeaway |
| `children_activities` | Children / Activities | Sports clubs, activity payments |
| `children_clothing` | Children / Clothing | Separate budget line from adult clothing |
| `clothing` | Clothing | Adult clothing and accessories |
| `health_wellness` | Health & wellness | Pharmacies, beauty, hairdressing |
| `housing_maintenance` | Housing / Maintenance | Monthly housing company charges |
| `electricity_energy` | Electricity / Energy | Consumption charges (Pohjois-Karjalan Sähkö, Helen) |
| `electricity_network` | Electricity / Network | Grid distribution (Caruna, Elenia) |
| `insurance` | Insurance | Home, car, life, pet |
| `transport` | Transport | Fuel, parking, car equipment |
| `leisure_entertainment` | Leisure & entertainment | Events, holidays, hobbies |
| `household_purchases` | Household purchases | Hardware, home goods (Clas Ohlson, Tokmanni) |
| `subscriptions` | Subscriptions | Recurring digital services |
| `fees_charges` | Fees & charges | Bank fees, ATM fees, service charges |
| `other_review` | Other / Review | Model-flagged uncertain transactions |
