# Feedback: Nebius Token Factory, Nebius AI Cloud, NVIDIA models and tools

This feedback comes from building Suture (repository `test-repair-agent`). Each item is labelled by how it was
observed:

- **[docs]**: read in the official documentation or cookbook while building
  (2026-10-12).
- **[live]**: observed while calling the service (from 2026-10-21).

## Nebius Token Factory

- **[live] It worked first time with the stock OpenAI SDK.** The base URL plus a key was all
  it took. A one-word prompt answered in 0.98 s on Ultra and 0.75 s on Nano.
- **[live] The model IDs on the catalog page are the ones the API serves.**
  `nvidia/Nemotron-3-Ultra-550b-a55b` and `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` are both
  listed by `/v1/models`. The lowercase Nano ID from the cookbook
  (`nvidia/nvidia-nemotron-3-nano-30b-a3b`) is not in the listing; use the catalog casing.
- **[live] Both base URLs serve the same Nemotron models.** The default endpoint listed 28
  models (5 Nemotron, including `Nemotron-3_5-Lightning` and `Nemotron-3.5-Super-VL`). The
  `us-central1` endpoint listed 18 (4 Nemotron). So the regional URL in the cookbook is an
  option, not a requirement.
- **[live] `/v1/models?verbose=true` pricing is per token, as decimal strings.** For
  example Ultra has `"prompt": "0.000001"`, i.e. $1 per 1M tokens, matching the catalog.
  The currency is still not stated. The `status` field from the API reference was absent
  for these models.
- **[live] Nano spends hidden reasoning tokens.** It returned a clean `OK` but billed 71
  completion tokens for it, and about 750 for a triage summary. The visible content had no
  `<think>` block, so the reasoning is counted but not shown. That's fine for cost at Nano
  prices, but worth documenting per model, along with how to switch it off.
- **[live] JSON mode works on Nemotron Nano.** `response_format={"type": "json_object"}`
  was accepted. In benchmark run 2, 16 of 17 triage answers and all 50 sanity reviews parsed
  as JSON; the agent falls back to its own parsed failure list when one doesn't.
- **[live] Parallel requests are served in parallel.** Three concurrent Ultra calls
  (2.1 s, 2.1 s, 3.8 s) were all back after 3.8 s, with no 429 and no retries.
- **[live] Occasional latency spikes.** Ultra calls usually took 2–8 s (mean 6.8 s per
  call in run 1, 8.3 s in run 2). But in run 1 one batch of three concurrent calls, each
  producing about 1,000 tokens, took 63 s. In run 2 two single calls took 44 s and 48 s.
  Nothing failed and nothing was retried. A note on expected tail latency would help agent
  builders set timeouts; this project uses 180 s.

- **[docs] The OpenAI compatibility is easy to adopt.** Per the quickstart, the stock
  `openai` SDK with `base_url="https://api.tokenfactory.nebius.com/v1/"` and an API key is
  all that is needed. The agent is built that way, with no provider-specific SDK; this is
  not yet confirmed with a live call. The "switch from OpenAI" page says exactly that and
  nothing more, which is the right amount.
- **[docs] Two different base URLs appear for the same models.** The quickstart uses
  `api.tokenfactory.nebius.com`, while the Nemotron cookbook pages (Ultra 550B and Nano 30B)
  use `api.tokenfactory.us-central1.nebius.com`. The docs don't say whether some models are
  only served in one region or whether either URL works. I added per-role base URL
  overrides (`ULTRA_BASE_URL`, `NANO_BASE_URL`) to cope. A one-line note on regional
  routing would help.
- **[docs] Model ID casing differs between pages.** The model catalog lists
  `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`; the Nemotron cookbook quickstart uses
  `nvidia/nvidia-nemotron-3-nano-30b-a3b`. If IDs are case-sensitive, one of these fails.
  `scripts/hello_tokenfactory.py` resolves this by reading the live `/v1/models` list.
- **[docs] `GET /v1/models?verbose=true` is useful.** It returns context length, pricing,
  status, regions, and supported features. It is the right basis for tooling, and the
  docs correctly recommend it over hard-coded IDs.
- **[docs] Reasoning controls for Nemotron aren't documented per model.** The per-model
  cookbook pages say reasoning is "Available" but don't say how to turn it on or off
  (`chat_template_kwargs`, a system-prompt switch, `reasoning_effort`). They also don't
  say whether reasoning text comes back in `content` (inside `<think>`) or in a separate
  field. For an agent that needs strict output formats this matters. I strip `<think>`
  blocks and expose `ULTRA_EXTRA_BODY`/`NANO_EXTRA_BODY` as a workaround.
- **[docs] The catalog page doesn't state the price currency.** I assumed USD for the cost
  columns and said so.
- **[docs] The rate-limit page is the most useful page for agent builders.** It covers 429
  with `Retry-After`, `x-ratelimit-remaining-*` headers, an `x-ratelimit-over-limit: yes`
  early warning, and limits that scale up by 1.2× per 15-minute window at 80% usage (up to
  20× base). The client honors `Retry-After`. It would help to state explicitly how many
  concurrent requests per key are fine, since parallel candidate sampling depends on it.
- **[docs] The structured-output page has rough edges** (read 2026-10-12). The Python
  sample is missing a comma. The cURL sample writes `"response format"` with a space.
  The "valid" schema example wraps the schema in `name`/`schema`/`strict`, which the
  Python sample doesn't. The page also doesn't say whether Nemotron models carry the
  "JSON mode" tag. The agent therefore uses `json_object` with an automatic fallback rather
  than `json_schema`.

## Nebius AI Cloud (Serverless AI, Container Registry, MysteryBox)

- **[docs] The CLI references are thorough.** `nebius ai job create` and
  `nebius ai endpoint create` list every flag with formats (`--env`, `--env-secret`,
  `--container-port`, `--async`, `--dry-run`). Building the jobs runner from the reference
  alone was possible.
- **[docs] Job states are not enumerated.** The jobs page names only `COMPLETED`. The Nebius
  skills repo shows `PROVISIONING -> STARTING -> RUNNING -> <terminal>`, but no list of
  terminal states (failed, cancelled, ...). My runner has to guess which states are
  terminal. A table of states would remove that guesswork.
- **[docs] `--env-secret KEY=SECRET_SELECTOR` doesn't say which payload key it reads.** It
  is unclear whether it takes the payload entry named `KEY`, or the only entry. Registry and
  token secrets document their payload keys (`REGISTRY_USERNAME`/`REGISTRY_PASSWORD`,
  `AUTH_TOKEN`); env secrets should too.
- **[docs] Nothing says whether Docker works inside a job or endpoint.** No privileged mode
  or Docker socket is documented. Agents that execute generated code need a sandbox, so a
  short page on "running untrusted code in Serverless" (gVisor, Kata, or "use a VM") would
  help this use case directly. I fell back to a subprocess sandbox inside the container and
  documented the trade-off.
- **[docs] Endpoints get a managed HTTPS URL independent of `--public`.** That is nice for
  demos: no IP wrangling, and TLS is included. A third-party GitHub issue reports that
  redeploying creates a new URL. If that is right, it is worth stating in the docs.
- **[docs] Non-interactive job logs.** The docs show `nebius ai job logs <id> --follow`.
  For scripts, a documented bounded form (`--tail`/`--since` syntax) would help; I call it
  without `--follow`.

## NVIDIA models

- **[docs] The Nemotron 3 family maps naturally onto a two-tier agent.** Ultra (550B, 55B
  active) handles diagnosis and patching, and Nano (30B, 3B active) handles triage and
  review. The catalog prices (Ultra $1/$3, Nano $0.06/$0.24 per 1M in/out tokens) make
  moving the frequent calls to Nano worthwhile on paper.
- **[live] In practice, Nano did the frequent work cheaply.** In benchmark run 1 (15 repos,
  agent mode), Nano made 61 calls for $0.0067 in total, at 1.63 s per call. Ultra made 67
  calls for $0.294, at 6.83 s per call. Nano also cut 32,099 characters of raw pytest
  output down to 7,874 characters of summary for Ultra.
- **[live] Ultra writes good fixes, but its diff formatting has habits worth knowing.**
  It often pads a hunk with one blank context line past the end of the file, as if every
  file ended with an empty line. In run 1 that caused 18 of 22 rejected diffs in agent mode,
  and 3 of 5 single-shot failures. In those three cases the diagnosis was right; the patch
  just couldn't be applied. Once, it split a two-file fix into two ```diff blocks despite
  being told to use one. Both are now handled: the patcher trims blank edge context, and
  the correction loop asks for one block. Strict parsers of Nemotron diffs should expect
  this.

## Add your observations here (after live runs)

- Model availability on your account (which IDs `/v1/models` returned; any substitution):
- Latency per role (from `bench/results/results.md`):
- Errors, rate limits, or retries seen (the `llm retry` log lines):
- Did Ultra follow the exact answer format? How often were corrections needed (count of
  `patch_rejected` events with `stage=validation`)?
- Did Nano return valid JSON for triage and sanity checks (`valid_json` in triage events)?
  Did the server accept `response_format` for Nano (look for `json_mode unsupported` in the
  logs)?
- Parallel candidates: did 3 concurrent Ultra calls take about as long as one? Compare
  the per-call latency of a `CANDIDATES=1` run against a `CANDIDATES=3` run.
- Ablations: pass rate with each technique switched off (`--set SBFL=false`,
  `--set CANDIDATES=1`, ...).
- Serverless deployment: anything that differed from the docs:
