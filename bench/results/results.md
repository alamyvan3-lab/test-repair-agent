# Benchmark results

**Not run yet.** No Token Factory API key was available while this project was built, so
there are no measured numbers. Nothing here is estimated.

To produce real results (this overwrites this file and writes `results.json`):

```bash
docker build -t test-repair-sandbox:latest sandbox/
test-repair bench --readme README.md            # agent loop + single-shot baseline, 15 repos
test-repair bench --trials 3 --readme README.md # with variance
```
