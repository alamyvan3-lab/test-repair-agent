# Deploying on Nebius AI Cloud

> **Untested against live Nebius infrastructure.** Every command below comes from the Nebius
> documentation (CLI references for `nebius ai endpoint create`, `nebius ai job create`,
> Container Registry, and MysteryBox) as read on 2026-10-12. None of it has been run,
> because no Nebius credentials were available while building. Use `--dry-run` on the
> create commands first; it validates flags without creating anything.

Docs used:
[endpoints quickstart](https://docs.nebius.com/serverless/quickstart/endpoints) ·
[`ai endpoint create`](https://docs.nebius.com/cli/reference/ai/endpoint/create) ·
[`ai job create`](https://docs.nebius.com/cli/reference/ai/job/create) ·
[managing jobs](https://docs.nebius.com/serverless/jobs/manage) ·
[Container Registry auth](https://docs.nebius.com/container-registry/authentication) ·
[MysteryBox secrets](https://docs.nebius.com/mysterybox/secrets/create)

## What runs where

| Component | Nebius service | Sandbox inside it |
|---|---|---|
| Web demo (`test-repair serve`) | Serverless AI **Endpoint** (CPU) | `SANDBOX_BACKEND=local`, bundled demo repos only |
| Parallel benchmark | Serverless AI **Jobs**, one job per repo | `SANDBOX_BACKEND=local` |
| App image | **Container Registry** | - |
| `TOKEN_FACTORY_API_KEY` | **MysteryBox** secret, injected with `--env-secret` | - |

No Docker daemon is documented inside serverless containers, so the image uses the local
subprocess sandbox. Read the trade-off in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#serverless-where-the-sandbox-runs) before
enabling anything beyond the bundled demo repos. Uploads stay disabled in this mode, and the
code enforces it.

## 1. Prerequisites

- Nebius CLI installed and a profile configured (`~/.nebius/config.yaml`), Docker, and `jq`.
- A Container Registry in your project. Note its region and ID.

## 2. Build and push the image

```bash
docker build -t test-repair-agent:0.1.0 .
nebius registry configure-helper            # Docker credential helper (documented)
docker tag test-repair-agent:0.1.0 cr.<region>.nebius.cloud/<registry-id>/test-repair-agent:0.1.0
docker push cr.<region>.nebius.cloud/<registry-id>/test-repair-agent:0.1.0
```

The docs give `docker login cr.<region>.nebius.cloud --username iam --password <token>` as
an alternative to the helper.

## 3. Store the Token Factory key in MysteryBox

The safest route is the web console (MysteryBox → Create secret), which keeps the key out
of shell history. Use the payload key `TOKEN_FACTORY_API_KEY`. The CLI form is
`nebius mysterybox secret create --name test-repair-tf-key --parent-id <project-id>
--secret-version-payload '[{"key":"TOKEN_FACTORY_API_KEY","string_value":"..."}]'`.

**Unverified:** the docs don't state which payload key `--env-secret KEY=SELECTOR` reads.
Naming the payload key after the env var is the safest guess. If the endpoint starts
without the key, `/api/health` shows `"llm_configured": false`.

## 4. Create the endpoint (public web demo)

```bash
nebius ai endpoint create \
  --name test-repair-demo \
  --image cr.<region>.nebius.cloud/<registry-id>/test-repair-agent:0.1.0 \
  --platform cpu-d3 \
  --preset 4vcpu-16gb \
  --container-port 8000 \
  --auth none \
  --env MODEL_ULTRA=<ultra model id> \
  --env MODEL_NANO=<nano model id> \
  --env MAX_CONCURRENT_RUNS=2 \
  --env RATE_LIMIT_RUNS=5 \
  --env-secret TOKEN_FACTORY_API_KEY=test-repair-tf-key
```

- `--auth none` makes the demo openable by judges in a browser. The app protects itself
  with demo-only runs, a per-IP rate limit, and a concurrency cap. Use `--auth token` if you
  only want to share it privately.
- Add `--env ULTRA_BASE_URL=...` if `scripts/hello_tokenfactory.py` reported the Ultra
  model at a regional URL.
- Rate limiting keys on the TCP peer. Behind Nebius's proxy that may be the proxy itself,
  which makes the limit effectively global. If you confirm that the proxy appends the
  client IP to `X-Forwarded-For`, add `--env TRUST_PROXY_HEADERS=true`.

Get the public URL and check it:

```bash
ENDPOINT_ID=$(nebius ai endpoint get-by-name --name test-repair-demo --format jsonpath='{.metadata.id}')
ENDPOINT_URL=$(nebius ai endpoint get "$ENDPOINT_ID" --format json \
  | jq -r '.status.public_endpoints[] | select(startswith("https://"))' | head -1)
curl "$ENDPOINT_URL/api/health"
```

Expect `{"status":"ok","sandbox":"local","llm_configured":true,...}`. Then open
`$ENDPOINT_URL` in a browser and run `demo_invoice`.

Logs: `nebius ai endpoint logs $ENDPOINT_ID`. Delete it when you are done, because billing
runs while it exists: `nebius ai endpoint delete $ENDPOINT_ID`.

## 5. Run the benchmark on Serverless Jobs (optional)

`NebiusJobsRunner` submits one job per benchmark repo with `nebius ai job create ... --async
--format json`. It polls `nebius ai job get --id <id> --format json` (`status.state`) and
parses result lines from `nebius ai job logs <id>`.

```bash
test-repair bench --runner nebius-jobs \
  --image cr.<region>.nebius.cloud/<registry-id>/test-repair-agent:0.1.0 \
  --api-key-secret test-repair-tf-key \
  --platform cpu-d3 --preset 4vcpu-16gb \
  --trials 1
```

Each job runs `test-repair bench --runner local --repos <name> --emit-marker` inside the
image and prints one `TR_BENCH_RESULT {json}` line per trial. The submitting machine
aggregates these into `bench/results/`. It needs `MODEL_ULTRA` and `MODEL_NANO` set, but
not the API key.

Untested assumptions to check on first use:

1. `job create --format json --async` prints an Operation with `resource_id`. As a
   fallback the runner uses `job get-by-name`.
2. Terminal states: only `COMPLETED` is documented. The runner also treats `FAILED`,
   `ERROR`, `CANCELLED`/`CANCELED`, `TERMINATED`, and `SUCCEEDED` as terminal and times out
   after an hour.
3. `job logs <id>` without `--follow` returns the full log and exits.
4. Whether `cpu-d3` / `4vcpu-16gb` is available for jobs in your region (the docs show it
   for endpoints).

Preview the exact create command with `--dry-run` by copying the `+ nebius ai job create
...` line the runner prints.
