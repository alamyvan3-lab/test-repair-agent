# Devpost form: copy-paste kit

Field by field, in the order the form shows them. All numbers are measured (benchmark run
2026-10-21, commit `bf4c7b7`; example run in [example_run.md](example_run.md)).

---

## About the project (paste everything between the lines)

---

## Inspiration

A failing test is the most precise bug report a codebase will ever give you, yet turning
it into a fix is still manual: read the traceback, find the file, patch, rerun, repeat. LLMs
can write patches, but one guessed patch is often wrong. Worse, an agent left alone can
"fix" a red suite by editing the tests. I wanted an agent that closes the loop: it runs its
own patches, learns from failures, and is structurally unable to cheat.

## What it does

**Suture** takes a Python repository with failing pytest tests and returns a minimal patch,
a plain-language explanation, and a full event log.

1. Runs the suite in a locked-down sandbox and parses every failure.
2. Localizes the fault with **spectrum-based fault localization**. A tiny coverage plugin
   records which lines each test executes, and each line is scored with the Ochiai
   coefficient:

$$\text{Ochiai}(s) = \frac{e_f(s)}{\sqrt{F \cdot \left(e_f(s) + e_p(s)\right)}}$$

   Here \\(e_f\\) and \\(e_p\\) count the failing and passing tests that run line \\(s\\),
   and \\(F\\) is the number of failing tests. On the demo repo it ranked the exact buggy
   line first.
3. **Nemotron Nano** condenses raw pytest output into a compact JSON triage.
4. **Nemotron Ultra** writes a diagnosis and a unified diff: three candidates **in
   parallel** on Token Factory.
5. Every diff is validated before it touches anything. It must stay inside the repo, leave
   test and config files alone, contain no test-gaming code, change at most 60 lines,
   compile, and pass `git apply --check`. Rejections go back to Ultra as precise errors.
6. Nano reviews each patch for special-casing. Candidates that are the same program merge
   into votes, and the distinct ones are tested in parallel sandboxes.
7. The best patch is applied and the suite reruns. Regressions are reverted, the history
   feeds the next attempt, and a winning patch is minimized to what the tests need.

A web UI streams every step live, with the model, tokens and latency per call. A replay
mode plays back recorded real runs.

## How I built it

- **Models:** NVIDIA `Nemotron-3-Ultra-550b-a55b` (hard reasoning: diagnosis and patches)
  and `NVIDIA-Nemotron-3-Nano-30B-A3B` (frequent, cheap calls: triage and patch review).
  Both are served by **Nebius Token Factory** through its OpenAI-compatible API with the
  stock `openai` SDK. The client honors `Retry-After` on 429s and uses Token Factory's JSON
  mode for Nano.
- **Sandbox:** a fresh Docker container per test run, with no network, a read-only root,
  memory/CPU/PID limits, all capabilities dropped, a non-root user, and a host-enforced
  timeout. The repo goes in as a tar on stdin, so there are no bind mounts.
- **Agent:** about 4,500 lines of plain Python (FastAPI, pydantic, pytest, standard
  library), with no agent framework. Techniques come from the literature: Ochiai SBFL
  (Abreu et al.; used by AutoCodeRover), AST-normalized patch voting and test-based
  selection (Agentless), and 1-minimal patch reduction (Zeller's delta debugging).
- **Evaluation:** a 15-repo benchmark with one seeded bug each (off-by-one, wrong operator,
  mutable default, a bug spanning two functions, a fix that needs a second file, …). It
  runs the full agent and a single-shot baseline, and every number is written by the
  benchmark itself. There are 179 tests in total, including guardrail tests, a
  prompt-injection test, and an end-to-end SSE test.
- **Nebius services:** the web demo is packaged for **Nebius Serverless Endpoints**, and
  the benchmark can fan out over **Nebius Serverless Jobs**, with the key held in
  **MysteryBox**.

## Results (real Nemotron calls)

| | Pass rate | Cost (15 repos) | Wall time |
|---|---|---|---|
| Suture (full agent) | **15/15** | $0.23 | 336 s |
| Single Ultra call, no loop | 14/15 | $0.08 | 129 s |

- Nano made **more** calls than Ultra (67 against 57) but cost **$0.008 against $0.217**,
  under 4% of the model spend, at **1.7 s/call against 8.3 s**. Its triage made the text
  Ultra reads **75% smaller**.
- The repo the baseline missed shows why the loop exists. All three Ultra candidates
  agreed on the same wrong fix. The tests caught it, and Suture finished the fix on attempt
  2. Agreement is not correctness; executing the tests is.
- The demo repo goes red to green in 13.7 s for about one cent.

## Challenges I ran into

- **LLM diffs are messy.** Line numbers are unreliable, so hunks are located by content,
  re-rendered as clean diffs, and `git apply`-checked. My first benchmark run then found a
  subtler habit: Nemotron Ultra pads hunks with a blank line *past the end of the file*.
  That caused 18 of 22 rejected diffs and dragged the baseline to 10/15. Trimming blank
  edge context fixed it (the baseline went to 14/15). I kept the first run in the repo
  instead of hiding it.
- **Test-gaming.** Research such as ImpossibleBench shows models editing tests to pass.
  Suture rejects test edits, test-environment detection, stack inspection and mocks in
  source code before any model review. A repo with a "delete the tests" comment planted in
  its source is part of the test suite.
- **No Docker in serverless containers.** I designed a documented subprocess fallback and
  am honest about its weaker isolation: it's for trusted demo repos only, and uploads are
  refused in that mode.
- **Windows details:** CRLF checkouts broke patches, and cp1252 garbled pytest's `±`.
  Both are fixed and covered by tests.

## What I learned

- A strong model fixes simple bugs in one call. The agent's value is **verification**:
  executing every patch, catching confident-but-wrong fixes, and refusing to cheat.
- Splitting work across two model sizes really pays off. The small model handles the
  frequent calls for a few percent of the cost.
- Measure everything, and publish the embarrassing first run too.

## What's next

Ablations per technique, harder multi-file bugs, more trials for variance, and
repositories with third-party dependencies.

---

## Built with (tags; 21 of 25)

```
python, nvidia-nemotron, nemotron-3-ultra, nemotron-3-nano, nebius-token-factory,
openai-api, fastapi, uvicorn, pydantic, pytest, docker, git, javascript, html5, css3,
server-sent-events, github-actions, github-pages, nebius-serverless, nebius-mysterybox,
spectrum-based-fault-localization
```

Only keep `nebius-serverless` and `nebius-mysterybox` if you actually deploy the live demo
on Nebius (see [deploy/README.md](../deploy/README.md)). Otherwise drop them; they would
overstate what ran.

## "Try it out" links

| Link | URL | Before submitting |
|---|---|---|
| Code | https://github.com/alamyvan3-lab/test-repair-agent | Make the repo **public** |
| Replay demo (real recorded run) | https://alamyvan3-lab.github.io/test-repair-agent/?replay=demo_invoice-20261021-110309.jsonl&play | Public repo, then Settings → Pages → Source: GitHub Actions |
| Live demo (optional) | your Nebius endpoint URL | Deploy per [deploy/README.md](../deploy/README.md) |

## Image gallery (upload in this order; all PNG, 3000x2000 = 3:2, each under 2 MB)

| # | File | Caption |
|---|---|---|
| 1 | [assets/suture-thumbnail.png](assets/suture-thumbnail.png) | Suture: minimal, verified fixes for failing tests, using NVIDIA Nemotron on Nebius Token Factory. |
| 2 | [assets/gallery/02-live-run.png](assets/gallery/02-live-run.png) | A real recorded run in the web UI: tests red, the isolation rerun, and fault localization pointing at the buggy line. |
| 3 | [assets/gallery/03-nano-and-ultra.png](assets/gallery/03-nano-and-ultra.png) | Nemotron Nano triages the failure, then Nemotron Ultra writes candidate patches in parallel, each with tokens and latency shown. |
| 4 | [assets/gallery/04-green-and-result.png](assets/gallery/04-green-and-result.png) | Tests green, all three candidates agree (3 votes), and the plain-language explanation and per-model usage. |
| 5 | [assets/gallery/05-how-it-works.png](assets/gallery/05-how-it-works.png) | How it works: localize, triage, patch in parallel, validate, vote and verify, minimize, and retry on failure. |
| 6 | [assets/gallery/06-results.png](assets/gallery/06-results.png) | Measured on 15 repos with real Nemotron calls: 15/15 against 14/15 for one Ultra call; Nano at 4% of the model spend. |
| 7 | [assets/gallery/07-guardrails.png](assets/gallery/07-guardrails.png) | Guardrails: it fixes the code, never the tests, and every rule is covered by an automated test. |

Images 2-4 are screenshots of the real web UI replaying the recorded real run (no
mock-ups). Images 5-7 are rendered by [assets/render_cards.py](assets/render_cards.py);
the results card reads `bench/results/results.json` directly.

## Demo video URL

YouTube or Vimeo, **public or unlisted with no password**. Only the first **3 minutes** are
reviewed. Script: [DEMO_SCRIPT.md](DEMO_SCRIPT.md). Title, description and chapters:
[SUBMISSION.md](SUBMISSION.md) section 3.
