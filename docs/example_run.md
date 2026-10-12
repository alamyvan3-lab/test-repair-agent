# Example run

**Not recorded yet.** This file must come from a real red-to-green run with real Token
Factory calls. No API key or Docker was available while the project was built, so none has
been run.

To record it (this overwrites this file):

```bash
docker build -t test-repair-sandbox:latest sandbox/
test-repair run examples/demo_invoice --save-md docs/example_run.md
```

The output will contain the command, the streamed progress log (with role, model, tokens,
and latency for every model call), and the generated `report.md`: status, explanation,
attempts table, per-role usage, and the final patch.
