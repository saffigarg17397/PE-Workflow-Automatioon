# Deploying a shareable demo

The goal is a URL you can put in an email that loads instantly and shows real
output. On the Google AI Studio free tier this costs nothing end to end —
hosting and generation both.

## What replaced cost as the constraint

Model calls are free, so the thing to manage is no longer spend. Two constraints
took its place, and the second is the one that matters:

**Rate limits are shared.** The free tier allows 1,500 requests/day and 15/minute
across the whole key. Each memo is four calls. An open run button plus a crawler
in a loop exhausts the day's quota for everyone, including you.

**Free-tier prompts may be used for training.** That is fine for the synthetic
CIMs in this repo. It is emphatically not fine the first time a visitor uploads
a real one — and a PE reader looking at a CIM tool will be tempted to try exactly
that. Any instance with live runs enabled must say so on the upload form, in
plain words, before the file picker.

So the deployed instance defaults to **demo mode**: it serves memos generated
beforehand, read-only. Those memos are real pipeline output — same verified
citations, same review queue, same telemetry, nothing simulated — they just
aren't regenerated per visitor. Live runs stay available behind
`CIM_RUN_PASSWORD` for when you want to drive it in an interview.

| | Cost per visitor | Load time | Quota risk | Confidentiality |
|---|---|---|---|---|
| Demo mode (recommended) | $0 | instant | none | nothing leaves the box |
| Open live runs | $0 | 40–90s | unbounded | visitor uploads reach Google |

---

## Getting a key

aistudio.google.com → **Get API key** → *Create API key*. A Google account is
all it needs — no card, no phone verification, no billing setup. Keys start with
`AIza`. Put it in `.env` as `GEMINI_API_KEY=AIza...`.

Generating the full corpus is 20 requests against a 1,500/day allowance, so you
can regenerate freely while tuning prompts. `make seed-one` runs a single
document if you just want to see one end to end.

---

## Steps

### 1. Generate the memos locally (needs your API key)

```bash
cp .env.example .env          # add GEMINI_API_KEY
make install
make seed                     # runs all 5 CIMs, writes data/seed_runs/*.json
make eval-seeds               # scores those same runs — no API calls, free
```

`make seed` is 20 requests — about 1.3% of the daily free allowance.

`make eval-seeds` scores the artifacts you just generated rather than
regenerating them. Scoring needs no model call at all, so it is both instant
and free of quota. Use plain `make eval` only when you want a fresh generation
measured (e.g. after changing a prompt).

Check the output before committing it. `data/memos/*.md` are the rendered memos;
read one, pick a number, and confirm the page it cites really says that. The
verifier already checked every quote mechanically, so a citation that survived
to the memo should hold — but the first run on a new model is exactly when you
want to confirm that by hand rather than trust it. If extraction degraded you'll
see a red banner saying so, including a count of quotes the model offered that
were not in the document.

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
4. Leave `GEMINI_API_KEY` unset — demo mode doesn't need it.

You get `https://cim-ic-memo.onrender.com`.

> Free tier sleeps after ~15 minutes idle and takes ~30s to wake. Someone
> clicking your link cold sees a blank tab for half a minute. If that matters,
> the $7/month Starter plan removes it — worth it while you're actively sending
> the link.

**Fly.io** no longer has a permanent free tier (removed in 2024 — new accounts
get a short trial, then roughly $2–5/month). **Railway**: New Project → Deploy
from GitHub → add `CIM_DEMO_MODE=true` in Variables; it detects the Dockerfile
automatically. **Oracle Cloud Always Free** gives an always-on VM with no sleep
if the Render cold start bothers you and you don't mind administering a box.

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
GEMINI_API_KEY=AIza...
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
- **Free-tier prompts may train the model.** Anything uploaded to a live-run
  instance is sent to Google under free-tier terms. A paid key changes those
  terms; the free one does not.
- **Uploads are capped** at `CIM_MAX_UPLOAD_MB` (default 32) and streamed to a
  temp file that's deleted after the run.

## If you point this at real CIMs

Don't do it on a public URL, and not on a free-tier key at all. At minimum you'd
want a paid key (so prompts are excluded from training), auth in front of the
whole app, a private network, encryption at rest, and a data-retention review — a
CIM is confidential by definition and this app has none of that.
