# Submission kit

Copy-paste text and the exact order of steps for the four required items. Anything in
`<angle brackets>` is yours to fill in. Status of each item is tracked in
[SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md).

## Critical path

Everything that shows the project working needs a Token Factory API key. Docker is **not**
on the critical path: the bundled demo repos are trusted code, so you can record real runs
with `SANDBOX_BACKEND=local`, and GitHub Actions runs the Docker sandbox tests for you.

| # | Step | Command / place | Produces |
|---|---|---|---|
| 1 | Put the key in `.env` (copy `.env.example`) | editor | - |
| 2 | Verify models and setup | `python scripts/hello_tokenfactory.py`, then `test-repair doctor` | correct `MODEL_ULTRA` / `MODEL_NANO` |
| 3 | First real red-to-green run (add `--sandbox local` if Docker is not installed) | `test-repair run examples/demo_invoice --out runs/demo --save-md docs/example_run.md` | `docs/example_run.md` |
| 4 | Publish it for the replay demo | `test-repair publish-recording runs/demo --title "Invoice coupon bug"` | `web/recordings/*.jsonl` |
| 5 | Benchmark with real numbers | `test-repair bench --readme README.md` (with `--set SANDBOX_BACKEND=local` if no Docker) | `bench/results/`, README table |
| 6 | Push the repo public | see section 4 below | repo URL; CI and Pages run |
| 7 | Live demo on Nebius | [deploy/README.md](../deploy/README.md); then put its URL in `web/recordings/index.json` → `live_url` | live demo URL |
| 8 | Record and upload the video | [DEMO_SCRIPT.md](DEMO_SCRIPT.md), section 3 below | YouTube URL |
| 9 | Submit the form | sections 1-4 below | - |

## 1. Project description

Paste the body of [PROJECT_DESCRIPTION.md](PROJECT_DESCRIPTION.md) (what it is, why I built
it, how it works; about 400 words). If the form also asks for a one-line tagline:

> A coding agent that fixes failing Python test suites with NVIDIA Nemotron on Nebius Token
> Factory: fault localization, parallel patches, sandboxed verification, no test-gaming.

**Track:** Coding and Agentic Engineering.

## 2. Working demo URL

- **Primary:** the live web app on a Nebius Serverless Endpoint,
  `<https://... from deploy/README.md step 4>`. It runs real Nemotron calls on the bundled
  demo repos. An endpoint bills while it exists, so keep it up through judging and then
  delete it.
- **Backup (always on, free):** the GitHub Pages replay, `https://<github-user>.github.io/test-repair-agent/`.
  It plays back the recorded event logs of real runs and is labelled as a replay on the page.
  One-time setup: repository Settings → Pages → Source: **GitHub Actions**. The `pages`
  workflow publishes `web/` on every push that changes it.

Paste the primary URL if the endpoint is up for the judging period. Otherwise paste the
replay URL and say in the description that it is a replay of real runs.

## 3. Demo video (≤ 3:00, public on YouTube)

Script with timestamps and narration: [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

Recording: on Windows, Xbox Game Bar (`Win+Alt+R`) or OBS Studio, at 1080p. Record a **live**
run if you can, since it shows real model latency, and trim the waits in editing. If a live
run misbehaves on the day, record the replay page (`?replay`, 1× speed) of a real run
instead and say "this is a replay of a recorded run" in the narration. If you record with
`SANDBOX_BACKEND=local`, don't say "Docker container" in the narration; say "a sandboxed
subprocess".

Upload with visibility **Public**, not Unlisted, since the rules ask for a public video.

**Title:**
> Test-Repair Agent: fixing failing tests with NVIDIA Nemotron on Nebius Token Factory

**Description:**
> Test-Repair Agent diagnoses failing pytest suites, writes a minimal patch, verifies it in a
> sandbox, and iterates until the tests pass. NVIDIA Nemotron Ultra diagnoses the fault and
> writes candidate patches in parallel. Nemotron Nano triages test output and reviews patches
> for test-gaming. Both are served by Nebius Token Factory's OpenAI-compatible API.
>
> Code (Apache-2.0): <repo URL>
> Live demo: <demo URL>
>
> 0:00 The problem
> 0:20 Live run: fault localization, Nano triage, parallel Ultra patches, ranking, green tests
> 1:50 Benchmark results and Token Factory latency / tokens
> 2:30 Architecture and repository

## 4. Public code repository

- **Name:** `test-repair-agent`
- **About / description:** `Agent that repairs failing pytest suites with NVIDIA Nemotron on Nebius Token Factory, verified in a sandbox.`
- **Topics:** `nvidia-nemotron`, `nebius`, `token-factory`, `llm-agents`, `program-repair`,
  `pytest`, `fault-localization`, `hackathon`
- **License at the top:** GitHub detects the Apache-2.0 `LICENSE` file and shows it in the
  About box. The README's first lines also name it, with a badge.

Before making anything public, check that no secret is tracked. Both commands should print
nothing:

```bash
git ls-files .env
```
```bash
git log --all --oneline -- .env
```

Create an **empty** repository on github.com (no README, license, or .gitignore, since
the repo has them), then push:

```bash
git remote add origin https://github.com/<github-user>/test-repair-agent.git
```
```bash
git push -u origin main
```

After the push, Actions runs `ci` (lint and every test on Python 3.11-3.13, including the
Docker sandbox tests) and `pages` (the replay site, once Pages is enabled). Optional: add
the repository secret `TOKEN_FACTORY_API_KEY` and the variables `MODEL_ULTRA` and
`MODEL_NANO`, then run `ci` manually with "live" ticked to get live tests and benchmark
results as a build artifact.

**README highlights the judges ask for, and where they are:**

| Ask | README section |
|---|---|
| Setup instructions and how to run | Quickstart |
| How NVIDIA Nemotron is used | How Nemotron is used |
| Where Token Factory accelerated the workflow | Where Token Factory helped (benchmark table filled by `bench --readme`) |
| Other Nebius tools and services | Other Nebius services used |
