You are locating the source passages for specific financial facts in a Confidential Information Memorandum, so a private equity analyst can verify each figure against the document.

The document is given to you as plain text, split into pages. Each page begins with a marker line reading `=== PAGE n ===`. That marker is how you know which page a passage is on.

For each field listed below, find the passage that states it and produce **one line**, in exactly this format:

```
FIELD: <field_name> | VALUE: <the value as stated> | PAGE: <n> | QUOTE: <verbatim text from that page>
```

If the document does not state a field:

```
FIELD: <field_name> | VALUE: NOT_FOUND
```

Output only these lines. No preamble, no commentary, no summary at the end.

## The quote is checked

Your quote is matched against the actual text of the page you name. This matching is done in code, not by a model, and it is not lenient about invention:

- **Copy the text exactly as it appears.** Do not paraphrase, reword, correct spelling, expand abbreviations, or fix formatting.
- **Do not join text from two places.** If the label and the number are far apart, quote the fragment that contains the number.
- **Give the page where the quoted words physically appear.** If a table spans two pages, cite the page holding the row you quoted.
- **Keep it to one or two sentences**, or a single table row. Long quotes spanning a page break will fail the check.
- If the fact comes from a table, quote the row — for example `FY2024 Revenue 24.3 22.1 19.8` — including the numbers as they are laid out.

A quote that cannot be found on the page you name is discarded, and the field is then reported to the analyst as unverified. A field marked NOT_FOUND is a correct and useful answer when the document is silent. An invented quote is the one outcome worse than either, because it costs a reviewer their trust in every other line you produced.

## Fields to locate

- `company_name`
- `headquarters`
- `year_founded`
- `employees`
- `recurring_revenue_pct` — the stated share of revenue that is recurring or contracted
- `latest_revenue` — revenue for the most recent fiscal year presented
- `latest_adjusted_ebitda` — adjusted EBITDA for the most recent fiscal year
- `latest_reported_ebitda` — reported (unadjusted) EBITDA for the most recent fiscal year
- `top_customer_pct` — the largest single customer as a share of revenue, from the customer table
- `top_five_customer_pct` — the top five customers combined, from the customer table
- `customer_count` — number of customers or client relationships served
- `asking_price` — the enterprise value or asking price
- `asking_multiple` — the implied multiple of adjusted EBITDA
- `acquisitions_completed` — number of acquisitions completed to date
- `latest_dso` — days sales outstanding for the most recent fiscal year

## Example

```
FIELD: latest_revenue | VALUE: 24.3 | PAGE: 11 | QUOTE: Total revenue grew to $24.3 million in FY2024
FIELD: asking_multiple | VALUE: NOT_FOUND
```
