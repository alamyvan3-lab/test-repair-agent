# Submission checklist

Copy-paste text for the form and the ordered critical path are in
[SUBMISSION.md](SUBMISSION.md).

## Required items and where they live

| Requirement | Where | Status |
|---|---|---|
| Uses NVIDIA Nemotron via Nebius Token Factory (OpenAI-compatible API) | `src/test_repair/llm.py`, `.env.example` | Implemented; **live calls not yet run** |
| Model split: Ultra for diagnosis/patch, Nano/Super for triage + sanity, env-configurable | `MODEL_ULTRA`, `MODEL_NANO` in `.env.example`; `src/test_repair/agent.py` | Implemented |
| Model IDs from a live `/v1/models` listing | `scripts/hello_tokenfactory.py` | **To do: run it with your key and update `.env.example` if the IDs differ** |
| Public repo with Apache-2.0 `LICENSE` at the root, named at the top of the README | `LICENSE`, `README.md` line 1-4 | Done |
| README: setup and run instructions | `README.md` Quickstart | Done |
| README: how Nemotron is used | `README.md` "How Nemotron is used" | Done (numbers pending a bench run) |
| README: where Token Factory accelerated the workflow, with measured numbers | `README.md` BENCH block | **To do: `test-repair bench --readme README.md`** |
| README: every other Nebius service used | `README.md` "Other Nebius services used" | Done (marked untested) |
| Working demo: web UI at a public URL | `Dockerfile`, `deploy/README.md` (live); `.github/workflows/pages.yml` + `web/recordings/` (replay backup) | **To do: deploy and paste the URL; publish a real recording for the replay site** |
| `FEEDBACK.md` | `FEEDBACK.md` | Seeded; add your live observations |
| Project description | `docs/PROJECT_DESCRIPTION.md` | Done |
| Demo video script (≤ 3 min) | `docs/DEMO_SCRIPT.md` | Done; fill the numbers from results.md |
| Example red-to-green run with real calls | `docs/example_run.md` | **To do: `test-repair run examples/demo_invoice --save-md docs/example_run.md`** |
| Benchmark with real results and a single-shot baseline | `bench/results/results.md` | **To do: run the benchmark** |
| Unit and guardrail tests pass | `pytest` | 155 passed, 22 skipped (Docker/live) on the build machine and in a fresh clone; CI runs the Docker ones |

## Only you can do these

0. **Fastest way to verify the Docker sandbox without installing Docker:** push the repo to
   GitHub. The `ci` workflow runs lint and every test on Python 3.11 to 3.13 with the
   sandbox image built, so the Docker tests run there. To run live tests and the benchmark
   in CI, add a repository secret `TOKEN_FACTORY_API_KEY` and variables `MODEL_ULTRA` /
   `MODEL_NANO`. Then start the workflow manually with "live" ticked; the benchmark results
   are uploaded as an artifact.
1. **Get the prerequisites working:** install Docker (on Windows: `wsl --install` in an
   admin PowerShell, reboot, install Docker Desktop) and add `TOKEN_FACTORY_API_KEY` to
   `.env`. `test-repair doctor` shows what is still missing.
2. **Verify the models:** `python scripts/hello_tokenfactory.py`. If Ultra or Nano is not
   available to your account, set the closest Nemotron model it suggests and note the
   substitution in `README.md` ("How Nemotron is used") and `FEEDBACK.md`.
3. **Run the live checks:**
   ```bash
   docker build -t test-repair-sandbox:latest sandbox/
   pytest                      # Docker tests now run instead of skipping
   pytest -m live -s           # real Nemotron calls
   test-repair run examples/demo_invoice --save-md docs/example_run.md
   test-repair bench --readme README.md
   ```
4. **Deploy the web demo** to Nebius Serverless Endpoints ([deploy/README.md](../deploy/README.md))
   and paste the public URL into the README and the submission form.
5. **Record and upload the video** (YouTube), following `docs/DEMO_SCRIPT.md`.
6. **Push the repo public** (GitHub). Check that `.env` is not tracked: `git ls-files .env`
   should print nothing.
7. **Submit:** pick the **Coding and Agentic Engineering** track. Paste the project
   description, repo URL, demo URL, and video URL, and attach or link `FEEDBACK.md`.
8. Update the "Verification status" table in the README after the live runs.
