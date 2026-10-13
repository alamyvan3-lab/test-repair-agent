# Recorded runs

`index.json` lists event logs of **real** runs that the web UI can replay. Replay mode
turns on with `?replay` or when the page is served without the API, e.g. on GitHub Pages.

Add one after a real run:

```bash
test-repair run examples/demo_invoice --out runs/demo
test-repair publish-recording runs/demo --title "Invoice coupon bug"
```

`publish-recording` refuses runs that made no real Token Factory calls (test and preview
models are rejected). Events are already redacted, so the API key never appears. Set
`live_url` in `index.json` to the deployed live demo so the replay page links to it.
