You are extracting a structured deal profile from a Confidential Information Memorandum for a private equity screening workflow.

Extract only what the document actually states. This is the governing rule and it overrides any instinct to produce a complete-looking record:

- If a field is not stated in the document, leave it null or empty. Do not infer, estimate, or compute it.
- Do not carry a figure over from a similar-sounding line item. Revenue is not gross profit; reported EBITDA is not adjusted EBITDA.
- All monetary amounts are in USD millions. If the document presents a figure in thousands or units, convert it and keep the millions convention.
- Percentages are numbers without the % sign (34.6, not "34.6%").

Specific guidance on the harder fields:

**financials** — one entry per fiscal year presented in the historical financials table. Include both `reported_ebitda` and `adjusted_ebitda` when both are shown; these are different numbers and the difference matters. Do not include projected or forecast years.

**addbacks** — every line in the adjusted EBITDA bridge. For each:
  - `years_recurring`: how many of the historical fiscal years this adjustment appears in. The bridge table's "Periods Presented" column tells you this. An adjustment shown for FY2022-FY2024 has years_recurring = 3.
  - `is_labelled_one_time`: true if the description calls it one-time, non-recurring, or similar language.
  These two fields together are how a reviewer detects an adjustment that is labelled one-time but recurs every year, so get them right.

**top_customer_pct / top_five_customer_pct** — read these from the customer table, not from any narrative claim about diversification elsewhere in the document. If the executive summary and the customer table disagree, the table is the extraction source.

**management** — `staying_post_close` is true, false, or null. Set it false only when the document says the person intends to leave or transition out. Absence of a statement is null, not true.

**days_sales_outstanding** — one value per fiscal year, in the same year order as `financials`.

**acquisitions_completed** — the number of acquisitions the company has completed to date, if stated.

Return the structured profile.
