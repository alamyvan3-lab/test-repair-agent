# Architecture

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Every setting comes from an env var (`Settings`, `ServerSettings`, per-role `RoleModel`). |
| `llm.py` | `TokenFactoryClient`: the OpenAI-compatible client with retries, backoff, per-call accounting and logging, and `<think>` stripping. `ChatModel` protocol for fakes. |
| `prompts.py` | All prompt text and builders. Untrusted data is wrapped in tags, and closing tags inside it are neutralized. |
| `pytest_parser.py` | Parses `pytest -q --tb=short -rfE` output into failing test IDs, messages, tracebacks, `file:line` frames, and counts. Handles Windows paths, collection errors, and truncated output. |
| `localize.py` | Ranks non-test source files by evidence and returns the top K with size-capped content. |
| `patcher.py` | Extracts the single fenced diff, parses it, validates it, relocates hunks by content, re-renders a standard diff, and runs `git apply --check` and then `git apply`. |
| `workspace.py` | Temporary git-tracked copy of the target repo: baseline commit, commit per kept attempt, revert, cumulative diff. |
| `sandbox.py` | `DockerSandbox` (default) and `LocalSandbox` (fallback). Bounded output capture and host-side timeout. |
| `agent.py` | The repair loop and the `RunResult`. |
| `events.py` | `Event` model, redaction, `EventLog`, JSONL sink. |
| `report.py` | `report.md` rendering. |
| `benchmark.py` | Benchmark tasks, `LocalRunner`, `NebiusJobsRunner`, aggregation, `results.md`, README block. |
| `cli.py` / `server.py` | `test-repair run|bench|serve`; FastAPI + SSE. |

## The loop

1. Copy the repo into a temp dir, `git init`, and commit a baseline.
2. Run the tests in the sandbox. If green, the run is a SUCCESS. pytest exit codes 3, 4,
   and 5 (internal or usage error, no tests) give ERROR.
3. For each attempt up to `MAX_ATTEMPTS`:
   1. **Nano triage**: raw output (clipped to `MAX_TRIAGE_CHARS`, head and tail) becomes
      JSON with failing tests, error class, key lines, and a summary.
   2. **Localize**: rank suspect files (below).
   3. **Ultra**: the prompt holds the compact parsed failures, the triage summary, the
      suspect files, and up to 3 earlier attempts (diff, outcome, resulting failures).
      Ultra answers with `DIAGNOSIS:` and one fenced diff.
   4. **Validate**. On any `PatchError`, the exact message goes back to Ultra in the same
      conversation, up to `MAX_CORRECTIONS` times. No test run is spent.
   5. **Nano sanity check** on the validated diff. A `special_case` verdict is sent back as
      a correction (it shares the correction budget).
   6. **Apply** and rerun the tests. Compare with the previous run:
      - all green: commit, SUCCESS;
      - regressed (fewer passing, more failing, or a new timeout): revert and record it;
      - same failure signature (no effect): revert and record it;
      - improved or failures changed: commit (keep) and continue from the new state.
4. When attempts run out the status is EXHAUSTED. A token or time budget breach gives
   ABORTED_GUARDRAIL. Model, sandbox, or git failures give ERROR.
5. Write `repair.patch` (cumulative `git diff` against the baseline), `report.md`, and
   `events.jsonl`. Everything is redacted.

### Localization scoring

- Traceback frame in a non-test repo file: 3.0 × (1 + depth/len) per frame. Deeper frames
  are closer to the error.
- Defines a name that a failing test file imports (`from pkg import name`, so re-exports
  resolve to the defining file): 2.0. Imported module file: 2.0, or 0.5 for a package
  `__init__.py`.
- Defines a function referenced as `name(` in failure output: 1.5.
- Imported by any file already suspected: 1.0. This is what pulls in the second file for
  cross-file bugs.
- No evidence at all (e.g. a timeout with no traceback): all source files in path order.

## Serverless: where the sandbox runs

Nebius Serverless AI Jobs and Endpoints run one container image. The `nebius ai job create`
and `nebius ai endpoint create` CLI references document no privileged mode, Docker socket,
or Docker-in-Docker option, so this design assumes no Docker daemon is available inside
them. That has not been verified on live infrastructure.

**Fallback used:** the app image sets `SANDBOX_BACKEND=local`. Each test run happens in a
throwaway copy, as a subprocess with:

- a scrubbed environment: no `TOKEN_FACTORY_API_KEY` and a temp `HOME`;
- a host-enforced wall-clock timeout that kills the process group;
- POSIX rlimits on address space, file size, CPU, and core dumps, set by a tiny exec wrapper
  rather than `preexec_fn`, which is unsafe in a threaded server;
- network isolation via `unshare -rn` when the kernel allows unprivileged user namespaces.
  It is probed at startup and often not allowed in containers.

**Trade-off, stated plainly:** the serverless container is the outer isolation boundary.
Test code runs as the same OS user as the server, so hostile code could read the server
process environment through `/proc` and might have network access. The public demo
therefore runs **only bundled demo repos** (trusted code). Uploads are refused whenever the
backend is not Docker, even with `ALLOW_UPLOADS=true`.

**Alternatives considered:** (a) run only the LLM-side logic on Nebius and keep the Docker
sandbox on a machine with Docker. This is the right choice for untrusted code, but it splits
the system across two hosts. (b) gVisor, firejail, or nsjail in the image: these need
privileges or kernel features the docs do not promise.

## Decisions

Reasonable calls made without asking, as the brief allowed.

1. **Extra modules** beyond the suggested layout: `workspace.py` (git working copy),
   `report.py` (report rendering), and `benchmark.py` (the runner logic lives in the package
   so `test-repair bench`, the Nebius job entry point, and the tests share it;
   `bench/run_bench.py` is a thin wrapper). Also `tests/helpers.py` (fake LLM).
2. **The repo goes into the container as a tar on stdin**, not a bind mount. This works the
   same with Windows host paths and when the agent itself runs in a container talking to
   the host Docker socket. Inside, it is extracted to the tmpfs, so tests can write files
   while the root filesystem stays read-only.
3. **Hunks are located by content, not by line numbers.** Model line numbers and counts are
   unreliable. The patcher finds each hunk's old lines in the current file (exact match
   first, then ignoring trailing whitespace) and applies it in memory. It then re-renders a
   standard 3-line-context diff with `difflib`. Context comes from the real file. `git
   apply` needs trailing context unless a hunk is at end of file, so model-written
   asymmetric hunks fail without this step. The result still goes through
   `git apply --check`.
4. **Regression rule:** a drop in the passed count, or (with equal passes) a rise in
   failures and errors, or a new timeout. **"No effect" patches** (identical failure
   signature) are also reverted so dead edits do not pile up. Patches that change the
   failure set without regressing are kept, because two-function bugs often need two steps.
5. **Sanity check placement:** after `git apply --check` and before applying. This is
   equivalent to the brief's order and saves a revert. Only `special_case` blocks;
   `unrelated` and unparseable reviews are logged but do not block, so the small model
   cannot veto correct fixes.
6. **Corrections and attempts:** validation and sanity rejections share the per-attempt
   correction budget (2). An attempt whose diff never validates still consumes an attempt,
   but no test run.
7. **Role names** in code are `ultra` and `nano`. The Nano role can be served by a Super
   model through `MODEL_NANO`.
8. **Per-role base URLs** (`ULTRA_BASE_URL`, `NANO_BASE_URL`). Nebius's Nemotron cookbook
   uses `api.tokenfactory.us-central1.nebius.com` while the quickstart uses
   `api.tokenfactory.nebius.com`.
9. **Reasoning controls are not assumed.** The per-model cookbook pages document no thinking
   switch. `*_EXTRA_BODY` passes through any extra request fields, and `<think>` blocks are
   stripped from responses.
10. **Cost** comes from `PRICE_*` env vars, which default to the public catalog prices, and
    is reported as n/a when they are unset. It is not computed from `/v1/models?verbose`
    pricing because the units there are unverified.
11. **pytest output is parsed as text** (`-q --tb=short -rfE`), not junitxml. A single
    stdout stream comes out of the read-only sandbox, `-rfE` gives the test IDs, and the
    parser is tested on Linux and Windows formats.
12. **Protected files** go slightly beyond the brief: `test/` directories, `setup.py`,
    `noxfile.py`, and `.coveragerc`. Deleting, renaming, or changing file modes is
    rejected outright.
13. **Uploads require the Docker sandbox.** This is enforced in code, not just documented.
14. **Proxy headers:** `TRUST_PROXY_HEADERS=true` uses the last `X-Forwarded-For` entry
    (the one the nearest proxy appended). It is off by default, in which case the rate
    limit keys on the TCP peer.
15. **SSE** replays each run's in-memory event list, polled every 200 ms. Only the last
    50 runs are kept, which bounds memory.
16. **Single-shot baseline:** the same Ultra prompt and localization, with no triage, no
    corrections, no sanity check, and one test run.
17. **Lint scope:** `bench/repos` and `examples` are excluded from ruff because they
    contain intentional bugs (e.g. a mutable default argument).
18. **Python versions:** 3.11+ supported. The images use 3.12, and development and tests
    ran on 3.14.
