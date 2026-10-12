# Feedback: Nebius Token Factory, Nebius AI Cloud, NVIDIA models and tools

This feedback comes from building Test-Repair Agent. Each item is labelled by how it was
observed:

- **[docs]**: read in the official documentation or cookbook while building
  (2026-10-12).
- **[live]**: observed while calling the service. *No live items yet: the build machine had
  no API key or Docker. Add them below after running `scripts/hello_tokenfactory.py` and the
  benchmark.*

## Nebius Token Factory

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
  moving the frequent calls to Nano worthwhile on paper. The benchmark will measure whether
  it holds.

## Add your observations here (after live runs)

- Model availability on your account (which IDs `/v1/models` returned; any substitution):
- Latency per role (from `bench/results/results.md`):
- Errors, rate limits, or retries seen (the `llm retry` log lines):
- Did Ultra follow the exact answer format? How often were corrections needed (count of
  `patch_rejected` events with `stage=validation`)?
- Did Nano return valid JSON for triage and sanity checks (`valid_json` in triage events)?
- Serverless deployment: anything that differed from the docs:
