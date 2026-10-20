# How LedgerStitch uses AI

AI in LedgerStitch is **optional**. First-run setup offers **local AI**: a model that runs on
your own computer (or your own server), downloaded only after your explicit OK. Nothing leaves
your machine. Its job is narrow: look at a merchant name such as `NETFLIX.COM` or `ZELLE TO <PERSON>` and suggest a
category, a lane (Business / Personal / Transfer / Income / Review) and an IRS
Schedule C line. Everything else, including parsing statements, de-duplicating,
matching transfers, totals and exports, runs on your computer without AI.

## The order LedgerStitch tries things

1. **Your rules.** Rules you wrote, plus the ones LedgerStitch learns when you
   correct a transaction and tick "remember for this merchant". The longest
   matching pattern wins, and a rule limited to one account beats a global
   rule. Rules never touch transactions you approved or edited yourself, and
   they leave duplicates and matched card payments or transfers alone.
2. **Built-in keyword rules.** About 280 well-known merchant keywords (Adobe, Kroger,
   Shell, USPS and so on). The longest keyword wins, so `AMAZON WEB SERVICES` is
   Cloud & Hosting and not Shopping. Keywords match on word boundaries, so `AWS`
   does not match `LAWSON`. These guesses get confidence 0.70.
3. **AI, only if you turned it on.** LedgerStitch sends only the merchants that
   steps 1 and 2 couldn't place, or placed with low confidence.
4. **You.** Anything with confidence below 0.75, or in the Review lane, goes to
   the Review queue. Nothing reaches your CPA report as "Business" unless a rule,
   a confident suggestion or you put it there.

## Exactly what the AI receives

When AI is on, each request to the model contains only:

| Sent | Example |
|---|---|
| Fixed instructions (the same for every user) | "You categorize bank and credit-card merchant descriptors…" |
| The category list: name, default lane, Schedule C code | `{"name": "Office Supplies", "lane": "business", "schedule_c": "L18"}` |
| The Schedule C line names (public IRS form text) | `"L18": "Office expense"` |
| Your **business profile**, but only if you wrote one in Settings | `restaurant owner in Dallas` |
| **Redacted merchant descriptors**, de-duplicated and sorted alphabetically | `"SQ *BLUE BOTTLE"`, `"ZELLE TO <PERSON>"` |

What is **never** included: amounts, dates, balances, totals, how many times you
shopped somewhere, account or card numbers, account names, your name, addresses,
phone numbers, emails, reference or confirmation numbers, the raw statement line,
or your vault name.

How descriptors are cleaned before they reach the model:

1. LedgerStitch's redactor (`ledgerstitch/security/redact.py`) removes card masks,
   any run of 4 or more digits, emails, phone numbers, addresses and ZIP codes,
   ACH id fields and trace numbers. It also replaces people's names after
   Zelle, Venmo, Cash App or "transfer to/from" with `<PERSON>`.
2. A second safety pass (`scrub` in `ledgerstitch/ai/categorizer.py`) removes
   anything left that looks like an amount (`$45.10`, `12.99`), a date
   (`07/04`), a store number (`#0423`), any run of 3 or more digits, and leftover
   labels such as `REF`.
3. Descriptors are de-duplicated and sorted, so the request does not show how
   often you visited a merchant or in what order.

If the redactor can't load for any reason, the model receives **nothing**.

**Preview it yourself.** Settings → "Preview what AI sees" shows the exact list
of descriptors that the next run would use.

The AI's answers are also checked locally. An unknown lane becomes Review, an
unknown Schedule C code is dropped, and answers for descriptors that weren't
sent are ignored. The AI may suggest new categories, but it can add at most 10
per run, and a suggestion that only differs from an existing name in case or
punctuation is folded into the existing category. Which descriptor was used for
which transaction is kept in memory during the run and is never saved.

## Where the AI runs

| Setting | Where it runs | Model | Notes |
|---|---|---|---|
| **Ollama (local)** | **Your computer** (`localhost:11434`), or the `ollama` service in Docker | `glm-4.7-flash:latest` | Fully offline. 19 GB, about 24 GB RAM. After your explicit OK, first-run setup installs Ollama (desktop) and downloads the model in the background. |
| Off | n/a | n/a | Rules and keywords only. Everything else waits in Review. |

LedgerStitch has no servers of its own, so nothing passes through us. Paid add-ons can plug
other AI options into the same place through the plugin API (docs/PLUGINS.md). Whatever is
plugged in receives exactly the redacted list above, and its answers go through the same local
checks. See {{SITE_URL}} for what the add-ons offer.

## Local AI (Community)

Local AI uses **one model, GLM-4.7-Flash** (`glm-4.7-flash:latest`, 19 GB), running in
[Ollama](https://ollama.com) on your computer, or in the `ollama` service next to LedgerStitch
when you self-host with Docker. There is no model picker.

**Nothing downloads without your explicit OK.** Step 2 of first-run setup shows a **Local AI**
card with two status chips (Ollama reachable / model installed) and a **Set up local AI** button.
It opens a consent dialog, nothing pre-ticked, that shows in icons:

- what is downloaded (the model from ollama.com, 19 GB; on desktop also Ollama itself if missing),
- where it runs (this computer / this server),
- what it sees (only merchant names, never amounts or account numbers),
- what it needs (25 GB of disk and 24 GB of RAM), next to the free disk and total memory measured
  on this machine,
- that it can be removed later in **Settings → Privacy & AI**.

**Agree and set up** records your consent in `app.json` (`local_ai_consent`: time, model, Ollama
address; no secrets) and starts one background job (`POST /api/setup/local-ai`). **Not now** does
nothing. The API refuses a download without a recorded consent for this model.

If the machine has less than 25 GB free or less than 24 GB of RAM, the dialog shows a warning and
the button turns into **Download anyway**, which needs a second click. In Docker, Ollama's API
doesn't report free disk, so LedgerStitch measures its own data volume instead (named volumes on
one Docker host share a disk) and says *approx.*; memory comes from `/proc/meminfo`, which inside a
container is the Docker VM's memory.

The job:
1. installs or starts Ollama if needed (desktop only; same allowlist and signature checks as
   before; on Linux you run the official command yourself). In server mode nothing is ever
   installed: if the Ollama service isn't reachable, setup shows `docker compose up -d ollama`;
2. pulls the model through Ollama's `/api/pull` stream, with progress in bytes and percent.

The wizard doesn't wait. It goes on to vault creation and into the app. A pill in the top bar
("Local AI: downloading 47%") and **Settings → Privacy & AI → Local AI** show progress. When the
model is ready you get a toast, and categorization switches to it: `ai_enabled=true`,
`provider=ollama`, `model=glm-4.7-flash:latest`. This happens once per workspace, and only while AI
is still off. A provider you chose yourself, or turning AI off later, is never overridden.

**Resumable.** The request is kept in `app.json` (`local_ai_requested`) until the model is
present. If the app (or the container) restarts mid-download, the job is queued again at launch,
only if the consent is still recorded, and Ollama resumes the partial pull itself.

**Settings → Privacy & AI → Local AI** shows the status, model, size and memory need, and:
- **Remove model** calls Ollama's `DELETE /api/delete` and turns local AI off in the open workspace;
- **Withdraw consent** stops a running download, forgets the request and the consent, and turns
  local AI off. A model that already finished downloading stays until you remove it.

### Docker: the Ollama service

`docker/docker-compose.yml` runs Ollama as its own service (`ollama/ollama`, pinned), with the
models on the `ollama-models` volume. It is **not published to the host**: only LedgerStitch reaches
it, over the compose network. LedgerStitch gets `LEDGERSTITCH_OLLAMA_URL=http://ollama:11434`.

Local AI normally talks only to loopback addresses (`127.0.0.1`, `localhost`, `::1`). In server
mode, exactly the URL in `LEDGERSTITCH_OLLAMA_URL` is allowed as well, because the operator set it,
not a web page. An Ollama address typed into Settings is still loopback-only (or exactly that URL).
LedgerStitch never gets the Docker socket, so it can't start, stop or install containers.

### Which local model, and why

`RECOMMENDED_MODEL` in `ledgerstitch/ai/local_ollama.py` is the single source of truth. Setup,
the categorizer default, the API (`/api/status.recommended_local_model`) and the Settings hint all
read it. It is **`glm-4.7-flash:latest`** (GLM-4.7-Flash; the same digest as `:q4_K_M`).

| | GLM-4.7-Flash |
|---|---|
| Size | 19 GB download (Q4_K_M) |
| Memory | about 24 GB RAM recommended (19 GB of weights plus context and the OS) |
| Architecture | 30B-class mixture of experts, about 3B parameters active per token |
| Thinking | thinking-capable; LedgerStitch turns it off for categorization |

Why this model: a mixture of experts carries the knowledge of a ~30B model (more small merchants,
processor prefixes such as `SQ *` and `TST*`) while running at about the speed of a ~3B model,
because only a few experts run per token. The previous default, Qwen 3.5 2B (1.9 GB), was chosen
for its size; GLM-4.7-Flash trades disk and memory for knowledge.

The trade-off is size: 19 GB of disk and about 24 GB of RAM. On a smaller machine it can be very
slow or fail to load, which is why the consent dialog warns below 25 GB of disk or 24 GB of RAM.
`glm-5.3-flash` exists only as a cloud model in Ollama, so it isn't an option for local AI.

**Docker on macOS is CPU-only.** Docker Desktop can't pass the Apple GPU to containers, so in Docker
on a Mac the model runs on the CPU, which is much slower than on a GPU. Give
Docker at least 24 GB of memory (Docker Desktop → Settings → Resources). On Linux with an NVIDIA GPU,
uncomment the GPU block in the compose file. The desktop app uses Ollama's native GPU support.

**Thinking off.** A merchant label needs no chain of thought, so requests to thinking-capable
families (`glm`, `qwen3`, …; `THINKING_FAMILIES`) carry `think: false`. If an Ollama build rejects
the flag, LedgerStitch retries once without it. If a model still puts its JSON in the `thinking`
field, it is read from there.

**For development and tests only:** `LEDGERSTITCH_LOCAL_MODEL=<tag>` replaces the model (for
example a tiny one for an end-to-end check). It isn't shown in the UI and isn't meant for
production.

This is a product decision, not a benchmark result. Re-check the choice with
`ledgerstitch benchmark --ai --model <tag>` (docs/BENCHMARK.md) before changing the constant.

## What it costs

Local AI is free. It uses your own computer's power and disk. Cost in time depends on
**unique merchants**, not transactions: 3,000 transactions from a year of statements usually
come down to roughly 200–600 unique merchants, and only the ones your rules and the keyword
list can't handle go to the model. Re-running categorization only sends merchants that are still
unresolved, and every correction you make with "remember" means that merchant never needs AI
again.

## Limits, honestly

- The AI sees only a merchant name, so it **guesses typical use**. It cannot
  know whether a particular Costco trip was for your business. That is why
  merchants commonly used both ways (Amazon, Costco, gas, restaurants, rideshare,
  Zelle) go to Review unless your business profile makes the answer clear.
- Suggestions are not tax advice. Your CPA sees the source of every category
  (rule, keyword, AI or you) in the export.
