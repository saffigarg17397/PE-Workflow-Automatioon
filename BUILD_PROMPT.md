# Build Prompt — CIM-to-IC-Memo Automation

Paste everything below the line into Claude Code (or Cursor / any agentic coding tool) in an empty repo.
It is written to be executed in one pass, then iterated on.

---

## ROLE

You are building a production-quality demo application for a private equity investment team. The user is an MBA candidate with a background shipping production ML, applying for AI-build roles at middle-market PE firms (services rollups). This repo is a portfolio artifact: it will be linked in outreach emails and reviewed by investment professionals and technical interviewers.

Optimize for four things, in this order:

1. **Credible to a PE professional.** The output must look like something a deal team would actually use. Every extracted number traceable to a source page. No hallucinated financials.
2. **Credible to an engineer.** Typed schemas, tests, an eval harness, cost instrumentation, clean module boundaries. Not a notebook, not a single 800-line file.
3. **Runnable in under 5 minutes** by someone who clones the repo and has an API key. `make demo` should go from zero to a rendered memo.
4. **Explainable in a 30-minute interview.** Every architectural decision should have a one-sentence justification the author can give out loud.

Do NOT build a generic "chat with your PDF" app. That is the thing this must not be mistaken for.

---

## THE PROBLEM BEING SOLVED

In middle-market PE — especially services rollups doing many small add-on acquisitions — the diligence cost per deal does not scale down. A $3M-revenue bolt-on absorbs nearly the same analyst, legal, and QoE lift as a $30M platform. That fixed cost is what caps add-on velocity.

The highest-volume, lowest-judgment work in that funnel is: read an 80-page CIM → extract the financial profile → normalize adjusted EBITDA → flag the obvious red flags → draft the screening memo. That is days of analyst time on deals that mostly die.

This tool automates that first pass so a human analyst starts from a drafted, sourced memo instead of a blank page.

**Framing to preserve throughout: this tool does not make investment decisions. It produces a sourced first draft and flags what a human must verify.** Every design choice should reinforce that.

---

## WHAT TO BUILD

A FastAPI service plus a thin web UI implementing this pipeline:

```
Upload CIM (PDF)
   ↓
[1] INGEST     — parse, page-index, cache
   ↓
[2] EXTRACT    — typed deal profile w/ page citations + confidence
   ↓
[3] ANALYZE    — derived metrics, red-flag detection, thesis-fit score
   ↓
[4] DRAFT      — IC screening memo in house format, every figure cited
   ↓
[5] REVIEW     — human-in-the-loop queue for low-confidence fields
   ↓
Export (Markdown / DOCX)
```

### Non-negotiable features

These five are what separate this from a wrapper. Do not drop any of them, and do not treat them as stretch goals.

**1. Provenance on every number.**
Every extracted figure carries the source page number and the verbatim quote it came from. The rendered memo shows these as inline citations; clicking one jumps to that page in the source viewer. Use the Claude API's native PDF citations (`citations: {"enabled": true}` on the document block) — the response returns `page_location` with `start_page_number` / `end_page_number` and `cited_text` per claim. Do not hand-roll this with regex.

A number without a citation must never render in the memo. If extraction can't cite it, the field is null and goes to the review queue.

**2. Confidence + human-in-the-loop.**
Every extracted field carries a confidence enum (`high` / `medium` / `low`). Anything below `high`, or anything null-but-expected, lands in a review queue in the UI with the source page shown beside it. A memo built on unreviewed low-confidence fields renders those figures visibly flagged, not silently.

**3. An eval harness.**
`evals/` contains ground-truth labels for the synthetic CIMs and a runner that scores extraction accuracy field-by-field (exact match for numerics with a tolerance band, set F1 for list fields like red flags). Output a scorecard table to stdout and `evals/results/`. This is the single strongest "AI practitioner, not prompt tinkerer" signal in the repo — make it real and make the README show its output.

**4. Cost and latency instrumentation.**
Every pipeline run records per-stage token counts (input / output / cache-read / cache-write), latency, and computed USD cost. Surface "this memo cost $0.83 and took 47s" in the UI and in the run record. PE is a unit-economics business; showing you instrumented yours is the point.

**5. Configurable investment thesis.**
`config/thesis/*.yaml` defines screening criteria (target revenue band, EBITDA margin floor, end markets, customer-concentration ceiling, recurring-revenue preference, geography, deal-breakers). Ship at least two: `services_rollup.yaml` and `software_buyout.yaml`. The fit score is computed against the loaded thesis, and the memo names which thesis it was screened under. This is what makes the tool generalize across firms instead of hard-coding one firm's box.

---

## TECH STACK

- **Python 3.11+**, `uv` or `pip` + `requirements.txt` (pick one, be consistent)
- **FastAPI** + Pydantic v2 for the API and all schemas
- **Anthropic Python SDK** (`anthropic`) — see API requirements below
- **PDF**: `pypdf` for page indexing and text fallback; send the PDF itself to the API as a document block for extraction (Claude reads PDFs natively — don't pre-OCR)
- **Storage**: SQLite via SQLAlchemy. No external DB dependency for the demo.
- **Frontend**: server-rendered Jinja2 + HTMX + Tailwind via CDN. No build step, no npm. The UI must be clean and legible but must not become the bulk of the work.
- **Testing**: `pytest`. Mock the API in unit tests; the eval harness is the only thing that makes real calls.
- **DOCX export**: `python-docx`.

Do not add Celery, Redis, Postgres, Docker Compose orchestration, or a React SPA. A reviewer cloning this must be able to run it with `pip install -r requirements.txt && make demo`.

---

## CLAUDE API REQUIREMENTS

These are current as of the model generation in use. Follow them exactly — do not substitute patterns from memory.

```python
import anthropic
client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
```

- **Model**: `claude-opus-5` as the default for every stage. Put it in config so it's swappable, but do not silently downgrade stages to a cheaper model to save cost — use `effort` as the cost lever instead (below).
- **Thinking**: `thinking={"type": "adaptive"}`. Do NOT use `budget_tokens` — it is removed and returns a 400.
- **Effort**: `output_config={"effort": "..."}` — `low` for the ingest/classification pass, `high` for extraction and memo drafting. Make this per-stage configurable; the effort sweep is a legitimate cost/quality finding to write up in the README.
- **Sampling params**: do NOT pass `temperature`, `top_p`, or `top_k`. They are removed and return a 400. Steer with prompting.
- **Structured extraction**: use `client.messages.parse(...)` with `output_format=YourPydanticModel`. Read the validated result off `response.parsed_output`. Do not prompt for JSON and parse it yourself, and do not use assistant-turn prefills (they return a 400).
- **PDF input**: base64 `document` content block with `"citations": {"enabled": True}`. Note citations are incompatible with `output_config.format` — so run the **cited extraction pass** and the **structured-schema pass** as separate calls, and join them on field identity. Design the schema so this join is clean; document the tradeoff in a code comment.
- **Prompt caching**: put `cache_control: {"type": "ephemeral"}` on the CIM document block. The same document is hit by multiple pipeline stages, so this is a large real saving — and `usage.cache_read_input_tokens` in your cost telemetry is what proves it works. Verify it's actually hitting; a zero cache-read across repeated runs means something is invalidating the prefix.
- **Streaming**: any call with `max_tokens` above ~16000 must stream (`client.messages.stream(...)`, then `.get_final_message()`). The memo-drafting call will need this.
- **Cost**: use `client.messages.count_tokens(...)` for pre-flight estimates and the response `usage` object for actuals. Do not use `tiktoken` — it is the wrong tokenizer and will be materially wrong.
- **Errors**: catch the SDK's typed exceptions in a most-specific-first chain (`NotFoundError` → `RateLimitError` → `APIStatusError` → `APIConnectionError`). Never string-match error messages. The SDK already retries 429/5xx — don't add a second retry layer on top.
- **Refusals**: check `response.stop_reason == "refusal"` before reading `response.content`. Unlikely on this content, but the pipeline should degrade to a flagged field rather than crash on an index error.

---

## SYNTHETIC DATA

Build `scripts/generate_cims.py` that produces realistic CIM PDFs. This is a real deliverable, not a fixture — the demo's credibility rests on it.

Generate **5 companies**, all shaped like middle-market tech-enabled-services rollup targets (this makes the demo land with the intended audience):

| # | Profile | Planted issues |
|---|---|---|
| 1 | Regional HVAC services, $18M rev | Clean — the control case, should score well |
| 2 | Dental practice group, $9M rev | Severe customer/payor concentration; declining organic growth masked by acquisitions |
| 3 | Managed IT services (MSP), $24M rev | Aggressive EBITDA addbacks (owner comp, "one-time" costs recurring 3 years running) |
| 4 | Commercial landscaping, $6M rev | Working-capital deterioration; DSO creeping; seasonality understated |
| 5 | Accounting/bookkeeping outsourcer, $12M rev | Key-person dependency; contracts lack change-of-control clauses |

Each CIM must include the sections a real one has: executive summary, company overview, end markets, service lines, management team, historical financials (3 years P&L + adjusted EBITDA bridge), customer detail, growth strategy, transaction overview. 25–40 pages. Prose should read like a banker wrote it — promotional, hedged in the right places, burying the bad news in a footnote.

**The planted issues are the eval ground truth.** `evals/ground_truth/*.yaml` records, per company, every extractable financial field and every red flag that should be detected. Generator and ground truth must be produced together so they can never drift.

Include one adversarial case: a red flag that is only visible by cross-referencing two sections (e.g. the customer table on p.19 contradicts the concentration claim in the executive summary on p.3). If the tool catches that, it's demonstrating something a keyword search cannot.

---

## RED FLAG DETECTION

Hybrid, not pure LLM. Implement both and label each finding by source:

- **Deterministic rules** (`analysis/rules.py`): thresholds on extracted structured data — customer concentration above X%, addbacks above Y% of reported EBITDA, revenue CAGR below floor, margin compression across periods, DSO trend, working-capital swing. These are cheap, explainable, and never hallucinate.
- **LLM-identified** (`analysis/llm_flags.py`): qualitative issues the rules can't reach — key-person risk, contract structure, competitive positioning, disclosure gaps, internal inconsistencies between sections.

Every flag carries: severity (`high`/`medium`/`low`), category, the evidence (quote + page), and its detection source (`rule` vs `model`). The memo separates them. A reviewer should be able to see at a glance which findings are arithmetic and which are judgment.

---

## IC MEMO OUTPUT

Fixed house format. Every figure inline-cited to a source page.

```
1. Recommendation          — Advance / Pass / More info, with the one-line reason
2. Business overview       — what it does, end markets, service lines
3. Financial summary       — 3yr P&L, adjusted EBITDA bridge, key ratios
4. Thesis fit              — scored against the loaded thesis, criterion by criterion
5. Key risks               — red flags, ranked by severity
6. Diligence priorities    — what a human must verify before proceeding
7. Sources & confidence    — extraction confidence, unreviewed fields, what the model could not find
```

Section 7 is not optional and not an appendix afterthought. A diligence tool that is honest about what it doesn't know is more trustworthy than one that isn't, and reviewers will notice.

---

## PROJECT STRUCTURE

```
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── config.py            # settings, model config, per-stage effort
│   ├── models/              # Pydantic schemas — the typed contract
│   │   ├── deal.py          # DealProfile, Financials, Customer, Management
│   │   ├── flags.py         # RedFlag, Severity, DetectionSource
│   │   ├── citation.py      # Citation, Confidence
│   │   └── memo.py          # Memo sections
│   ├── pipeline/
│   │   ├── ingest.py
│   │   ├── extract.py       # structured pass + cited pass, joined
│   │   ├── analyze.py
│   │   ├── draft.py
│   │   └── orchestrator.py  # stage sequencing, telemetry, error handling
│   ├── analysis/
│   │   ├── rules.py
│   │   ├── llm_flags.py
│   │   └── scoring.py       # thesis fit
│   ├── llm/
│   │   ├── client.py        # SDK wrapper: caching, retries, telemetry
│   │   ├── prompts/         # prompts as versioned files, not inline strings
│   │   └── telemetry.py     # token/cost/latency accounting
│   ├── storage/             # SQLAlchemy models + session
│   ├── export/              # markdown.py, docx.py
│   └── web/                 # templates/, static/
├── config/thesis/
│   ├── services_rollup.yaml
│   └── software_buyout.yaml
├── scripts/generate_cims.py
├── data/sample_cims/        # generated output, committed
├── evals/
│   ├── ground_truth/
│   ├── run_evals.py
│   └── results/
├── tests/
├── .gitlab-ci.yml
├── Makefile
├── requirements.txt
├── .env.example
└── README.md
```

Keep prompts in `app/llm/prompts/` as files, loaded at runtime. Prompts are the highest-churn artifact in the system and belong in version control where their diffs are readable — say so in the README.

---

## CI (GITLAB)

`.gitlab-ci.yml` with three stages:

```yaml
stages: [lint, test, eval]
```

- `lint` — `ruff check` + `ruff format --check` + `mypy app/`
- `test` — `pytest` with coverage, API fully mocked, no key required
- `eval` — the eval harness against real API calls; **manual trigger only** (`when: manual`), gated on `ANTHROPIC_API_KEY` being present as a masked CI variable. It must not run on every push and must not fail the pipeline when the key is absent.

Never commit a `.env`. `.env.example` lists required variables with dummy values. Add a `.gitignore` covering `.env`, `__pycache__`, `*.db`, `evals/results/*.json`.

---

## README

The README is a deliverable, not documentation. It is what a PE professional skims before deciding whether the author is worth 30 minutes. Include:

1. **One-paragraph problem statement** — the diligence-unit-economics framing above, in the author's voice
2. **Screenshot or animated GIF** of a rendered memo with visible citations
3. **Architecture diagram** — the pipeline, as a Mermaid block
4. **The eval scorecard**, with real numbers from an actual run
5. **Cost per memo**, measured — with the effort-level sweep if you ran one
6. **"What this doesn't do"** — an explicit limitations section: doesn't replace diligence, doesn't handle scanned/image-only PDFs, synthetic data only, not tuned on real deal outcomes. Stating limits plainly reads as judgment, not weakness, to this audience.
7. **Quickstart** — clone → install → key → `make demo`

---

## BUILD ORDER

Work in this sequence and commit at each step. Do not build everything and commit once.

1. Repo skeleton, `requirements.txt`, `Makefile`, `.gitignore`, `.env.example`
2. Pydantic schemas — the typed contract for everything downstream, so it goes first
3. `scripts/generate_cims.py` + the 5 CIMs + ground truth (must exist before extraction can be tested)
4. `app/llm/client.py` — SDK wrapper with caching and telemetry
5. Ingest + extraction, with citations, against CIM #1
6. Eval harness — get a baseline score **before** tuning anything. Record it.
7. Analysis: rules, then LLM flags, then thesis scoring
8. Memo drafting + Markdown export
9. FastAPI routes + UI + review queue
10. DOCX export, CI config, README

At step 6, write the baseline score down. At the end, report both baseline and final in the README. A tuning delta you can point at is more persuasive than a single final number.

---

## STANDARDS

- Type-annotate everything. `mypy app/` must pass.
- Never construct a financial figure the model didn't extract with a citation. If it's not sourced, it's `None` and it goes to review.
- Log every LLM call: stage, model, effort, tokens, cost, latency, cache-hit status.
- Tests must not require an API key. Mock at the client boundary.
- Comment the *why*, not the *what* — particularly the citations/structured-outputs split, and the rule-vs-LLM detection boundary.
- Commit messages: conventional commits, one logical change each.

When you finish a step, say what you built, what you verified, and what you're doing next. If something in this spec turns out to be wrong or impossible against the actual API, say so and propose the fix rather than silently working around it.
