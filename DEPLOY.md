# Deploying a shareable demo

The goal is a URL you can put in an email that loads instantly, shows real
output, and cannot cost you money.

## The one thing to get right

A hosted instance holds your `ANTHROPIC_API_KEY` server-side. If the run button
is open to the internet, **every visitor spends your credits** — and a crawler
hitting it in a loop spends a lot of them.

So the deployed instance runs in **demo mode**: it serves memos you generated
beforehand, read-only. Those memos are real pipeline output — same citations,
same review queue, same telemetry, nothing simulated — they just aren't
regenerated per visitor. Live runs stay available behind `CIM_RUN_PASSWORD` for
when you want to drive it in an interview.

| | Cost per visitor | Load time | Abuse risk |
|---|---|---|---|
| Demo mode (recommended) | $0 | instant | none |
| Open live runs | ~$0.50 × visitors | 40–90s | unbounded |

---

## Doing this without spending anything

Hosting is free outright — Render's free plan runs the container, and demo mode
means the deployed instance never holds an API key or makes a call.

Generation is the only step that consumes model tokens, and it can be covered by
free credit rather than a purchase:

* **Anthropic's new-account grant — $5, no card.** Console → Plans & Billing →
  *Claim free credits*, gated on SMS verification (a real mobile, not VoIP).
  Nothing in this repo changes; the key works as-is. Five memos cost $2–3, so
  the grant covers the whole corpus with change left over.
* **Google Cloud's $300 / 90-day trial**, spent on Claude via Vertex AI, if the
  Anthropic grant is unavailable. This needs a code change — `AnthropicVertex`
  in place of `Anthropic` in `app/llm/client.py` — and it is worth confirming
  that the model id and the citations + structured-output features this pipeline
  depends on are available on Vertex before committing to it. Not the first
  choice.

To spend as little of that as possible:

```bash
make seed-one                       # one document, ~$0.50, still a real demo
CIM_MODEL=claude-sonnet-5 make seed  # whole corpus at roughly half the cost
```

`make seed-one` is the floor. A single memo with working citations, a populated
review queue and a real red flag demonstrates everything the tool claims; the
other four documents make the *eval* meaningful, not the demo.

What cannot be free is generating the memos at all. The seed files have to be
genuine pipeline output — a demo of a provenance system that ships invented
numbers is self-refuting, and any reader who checks one citation finds out.

---

## Steps

### 1. Generate the memos locally (needs your API key)

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY
make install
make seed                     # runs all 5 CIMs, writes data/seed_runs/*.json
make eval-seeds               # scores those same runs — no API calls, free
```

`make seed` is the only step that costs money — roughly $2–3 for five memos, once.
`make seed-one` does a single document for ~$0.50 if you are working off the
free grant.

`make eval-seeds` scores the artifacts you just generated rather than
regenerating them. Scoring needs no model call, so running the corpus twice
would double the cost for no extra signal. Use plain `make eval` only when you
want a fresh generation measured (e.g. after changing a prompt).

Check the output before committing it. `data/memos/*.md` are the rendered memos;
read one and confirm the citations point at real pages and the numbers match the
source CIM. If extraction degraded you'll see a red banner saying so — fix that
before shipping, since the whole point of the demo is provenance.

### 2. Paste the eval numbers into the README

```bash
make eval                     # scorecard across all 5 documents
```

Replace the placeholder table in the README's Results section with the real
output. This is the single most persuasive thing in the repo for a technical
reader — a measured accuracy number beats any amount of description.

### 3. Commit the seeds

```bash
git add data/seed_runs README.md
git commit -m "Add seed runs and eval results"
git push
```

### 4. Deploy

**Render** (free tier, simplest — `render.yaml` is already in the repo):

1. Push to GitHub.
2. render.com → New → Blueprint → pick the repo. It reads `render.yaml`.
3. Deploy. `CIM_DEMO_MODE=true` is already set in the blueprint.
4. Leave `ANTHROPIC_API_KEY` unset — demo mode doesn't need it.

You get `https://cim-ic-memo.onrender.com`.

> Free tier sleeps after ~15 minutes idle and takes ~30s to wake. Someone
> clicking your link cold sees a blank tab for half a minute. If that matters,
> the $7/month Starter plan removes it — worth it while you're actively sending
> the link.

**Fly.io** (no sleep on the free allowance):

```bash
fly launch --no-deploy          # detects the Dockerfile
fly secrets set CIM_DEMO_MODE=true
fly deploy
```

**Railway**: New Project → Deploy from GitHub → add `CIM_DEMO_MODE=true` in
Variables. Detects the Dockerfile automatically.

### 5. Check it

```
https://your-url/                      # populated list, no run button
https://your-url/memo/<run_id>         # full memo with [p.N] citations
https://your-url/review/<run_id>       # review queue
https://your-url/healthz               # "ok"
```

Open a memo and hover a citation marker — the source quote should appear. That
is the thing worth showing.

---

## Live runs in an interview

```bash
# in the host's dashboard
CIM_RUN_PASSWORD=<something>
ANTHROPIC_API_KEY=sk-ant-...
```

The run form reappears with a password field. You can drive a live run while
sharing your screen; visitors without the password still can't.

Unset both when you're done.

---

## Known limits of the hosted instance

- **Runs don't persist.** SQLite lives on an ephemeral filesystem, so a live run
  made in an interview disappears on restart. Seeds reload every boot, so the
  demo is always populated. Add a persistent disk if you want live runs to stick.
- **Single worker.** Runs execute in a background thread, not a job queue. Fine
  for one person at a time, which is the actual use case.
- **No auth on memo URLs.** Anyone with a run id can read that memo. The content
  is synthetic, so this is deliberate — don't point a public instance at real
  deal documents.
- **Uploads are capped** at `CIM_MAX_UPLOAD_MB` (default 32) and streamed to a
  temp file that's deleted after the run.

## If you point this at real CIMs

Don't do it on a public URL. At minimum you'd want auth in front of the whole
app, a private network, encryption at rest, and a data-retention review — a CIM
is confidential by definition and this app has none of that.
