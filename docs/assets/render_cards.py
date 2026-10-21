"""Render the Devpost gallery cards (3:2 PNG) with headless Edge or Chrome.

Usage: python docs/assets/render_cards.py [path-to-msedge-or-chrome]
Numbers on the results card come from bench/results/results.json, never typed by hand.
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
ROOT = ASSETS.parents[1]
SRC = ASSETS / "src"
OUT = ASSETS / "gallery"
DEFAULT_BROWSER = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

STYLE = """
:root { --text:#f0f3f6; --muted:#9aa7b6; --dim:#6b7787; --green:#76b900; --ok:#7ee787;
  --bad:#ff7b72; --ultra:#c7adff; --ultra-bg:#2b2140; --nano:#7fd6e0; --nano-bg:#15303a;
  --card:#151b23; --line:#2a323d; }
* { box-sizing:border-box; margin:0; padding:0; }
html, body { width:1500px; height:1000px; overflow:hidden; }
body { font-family:"Segoe UI", system-ui, sans-serif; color:var(--text); position:relative;
  background:
    radial-gradient(circle at 1px 1px, rgba(255,255,255,0.05) 1px, transparent 0) 0 0 / 28px 28px,
    radial-gradient(ellipse at 15% 5%, #1b2a3a 0%, transparent 55%),
    radial-gradient(ellipse at 95% 95%, rgba(118,185,0,0.12) 0%, transparent 50%),
    linear-gradient(160deg, #121a24, #090d13); }
.head { position:absolute; left:84px; top:64px; right:84px; }
.kicker { font-size:24px; font-weight:700; letter-spacing:3px; color:var(--green);
  text-transform:uppercase; }
h1 { font-size:58px; font-weight:700; letter-spacing:-1px; margin-top:8px; }
.sub { font-size:25px; color:var(--muted); margin-top:10px; }
.chip { display:inline-block; font-size:19px; font-weight:700; padding:3px 12px; border-radius:999px; }
.chip.ultra { background:var(--ultra-bg); color:var(--ultra); }
.chip.nano { background:var(--nano-bg); color:var(--nano); }
.foot { position:absolute; left:84px; right:84px; bottom:40px; font-size:19px; color:var(--dim); }
"""


def page(body: str, extra_css: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{STYLE}{extra_css}'
            f"</style></head><body>{body}</body></html>")


def how_it_works() -> str:
    steps = [
        ("1", "Run tests", "fresh locked-down sandbox; parse every failure", ""),
        ("2", "Localize", "SBFL: lines run by failing tests, scored with Ochiai", ""),
        ("3", "Triage", "raw pytest output to compact JSON", "nano"),
        ("4", "Patch &times;3", "diagnosis + unified diff, three candidates in parallel", "ultra"),
        ("5", "Validate", "paths, no test edits, no test-gaming, compiles, git apply --check",
         "nano"),
        ("6", "Vote &amp; verify", "same-AST candidates merge; distinct ones run in parallel sandboxes",
         ""),
        ("7", "Minimize", "drop any change the tests do not need", ""),
    ]
    cards = []
    for num, title, text, role in steps:
        chip = {"ultra": '<span class="chip ultra">ULTRA</span>',
                "nano": '<span class="chip nano">NANO</span>'}.get(role, "")
        if num == "5":
            chip += ' <span class="note">+ Nano review</span>'
            chip = chip.replace('<span class="chip nano">NANO</span> ', "")
        cards.append(f'<div class="step"><div class="num">{num}</div><div class="t">{title}</div>'
                     f'<div class="d">{text}</div><div class="r">{chip}</div></div>')
    css = """
    .row { position:absolute; left:84px; right:84px; top:300px; display:grid;
      grid-template-columns:repeat(7, 1fr); gap:14px; }
    .step { background:var(--card); border:2px solid var(--line); border-radius:18px;
      padding:20px 16px; height:330px; position:relative; }
    .num { width:44px; height:44px; border-radius:50%; background:#1e2a1a; color:var(--green);
      font-size:24px; font-weight:700; display:flex; align-items:center; justify-content:center; }
    .t { font-size:27px; font-weight:700; margin-top:16px; line-height:1.15; }
    .d { font-size:19px; color:var(--muted); margin-top:10px; line-height:1.35; }
    .r { position:absolute; left:16px; bottom:18px; }
    .note { font-size:17px; color:var(--nano); font-weight:600; }
    .loop { position:absolute; left:84px; right:84px; top:668px; height:120px; }
    .loop svg { width:100%; height:100%; }
    .looptext { position:absolute; left:0; right:0; top:782px; text-align:center; font-size:24px;
      color:#d6dde5; }
    .looptext b { color:var(--bad); } .looptext i { color:var(--ok); font-style:normal; }
    """
    body = (
        '<div class="head"><div class="kicker">How Suture works</div>'
        "<h1>Localize, patch in parallel, verify, minimize</h1>"
        '<div class="sub">NVIDIA Nemotron Ultra for the hard reasoning, Nano for the frequent '
        "cheap calls, both on Nebius Token Factory.</div></div>"
        f'<div class="row">{"".join(cards)}</div>'
        '<div class="loop"><svg viewBox="0 0 1332 120" preserveAspectRatio="none">'
        '<path d="M1240 10 C 1240 90, 1180 100, 1100 100 L 232 100 C 150 100, 92 90, 92 10" '
        'fill="none" stroke="#d9a441" stroke-width="4" stroke-dasharray="10 10"/>'
        '<polygon points="80,22 92,2 104,22" fill="#d9a441"/></svg></div>'
        '<div class="looptext"><b>Still red or regressed?</b> Revert, feed the history back to '
        "Ultra, retry (up to 5 attempts). <i>Green?</i> Ship the patch + explanation.</div>"
        '<div class="foot">Every step is a streamed event with model, tokens, and latency. '
        "Plain Python + FastAPI, no agent framework.</div>"
    )
    return page(body, css)


def results() -> str:
    data = json.loads((ROOT / "bench" / "results" / "results.json").read_text(encoding="utf-8"))
    agent, single = data["aggregate"]["agent"], data["aggregate"]["single_shot"]
    ultra, nano = agent["roles"]["ultra"], agent["roles"]["nano"]
    share = nano["cost_usd"] / agent["total_cost_usd"] * 100
    squeeze = (1 - agent["triage_summary_chars"] / agent["triage_input_chars"]) * 100
    date = data["meta"]["finished_at"][:10]

    def bar(label: str, wins: int, runs: int, color: str) -> str:
        pct = wins / runs * 100
        return (f'<div class="bar"><div class="bl">{html.escape(label)}</div>'
                f'<div class="track"><div class="fill" style="width:{pct:.1f}%;background:{color}">'
                f"</div></div><div class=\"bv\">{wins}/{runs}</div></div>")

    css = """
    .bars { position:absolute; left:84px; top:330px; width:820px; }
    .bar { display:grid; grid-template-columns:270px 1fr 110px; align-items:center; gap:18px;
      margin-bottom:34px; }
    .bl { font-size:26px; font-weight:600; color:#d6dde5; }
    .track { height:46px; background:#1a2129; border-radius:12px; overflow:hidden; }
    .fill { height:100%; border-radius:12px; }
    .bv { font-size:40px; font-weight:700; }
    .caption { font-size:21px; color:var(--muted); width:820px; line-height:1.45; }
    .stats { position:absolute; right:84px; top:310px; width:470px; display:grid; gap:24px; }
    .stat { background:var(--card); border:2px solid var(--line); border-radius:18px;
      padding:18px 22px; }
    .stat b { font-size:44px; display:block; letter-spacing:-1px; }
    .stat span { font-size:20px; color:var(--muted); }
    .roles { position:absolute; left:84px; top:640px; width:820px; border-collapse:collapse;
      font-size:22px; }
    .roles th { text-align:left; color:var(--dim); font-weight:600; padding:8px 10px;
      border-bottom:2px solid var(--line); }
    .roles td { padding:12px 10px; border-bottom:1px solid var(--line); }
    .roles td.n, .roles th.n { text-align:right; font-variant-numeric:tabular-nums; }
    """
    body = (
        '<div class="head"><div class="kicker">Measured, not estimated</div>'
        "<h1>15 seeded-bug repos, real Nemotron calls</h1>"
        f'<div class="sub">Benchmark run {date} · 1 trial per repo · numbers written by '
        "bench/run_bench.py</div></div>"
        '<div class="bars">'
        + bar("Suture (full agent)", agent["successes"], agent["runs"], "var(--green)")
        + bar("One Ultra call, no loop", single["successes"], single["runs"], "#4a5868")
        + '<div class="caption">The repo the baseline missed: all three Ultra candidates agreed on '
        "the same wrong fix. The tests caught it, and Suture fixed it on attempt 2.</div></div>"
        '<table class="roles"><tr><th>Agent mode</th><th class="n">Calls</th>'
        '<th class="n">Mean latency</th><th class="n">Cost</th></tr>'
        f'<tr><td><span class="chip ultra">ULTRA</span> Nemotron-3-Ultra</td>'
        f'<td class="n">{ultra["calls"]}</td><td class="n">{ultra["mean_latency_per_call_s"]:.1f} s</td>'
        f'<td class="n">${ultra["cost_usd"]:.3f}</td></tr>'
        f'<tr><td><span class="chip nano">NANO</span> Nemotron-3-Nano</td>'
        f'<td class="n">{nano["calls"]}</td><td class="n">{nano["mean_latency_per_call_s"]:.1f} s</td>'
        f'<td class="n">${nano["cost_usd"]:.3f}</td></tr></table>'
        '<div class="stats">'
        f'<div class="stat"><b>{share:.0f}%</b><span>of model spend went to Nano, which made '
        "the most calls</span></div>"
        f'<div class="stat"><b>{squeeze:.0f}% smaller</b><span>pytest output after Nano triage, '
        "before Ultra reads it</span></div>"
        f'<div class="stat"><b>${agent["total_cost_usd"]:.2f}</b><span>to run the full agent on '
        "all 15 repos on Token Factory</span></div></div>"
        '<div class="foot">A first run, before a patcher fix for how Nemotron pads diffs, is kept '
        "in bench/results/history/.</div>"
    )
    return page(body, css)


def guardrails() -> str:
    tiles = [
        ("Locked-down sandbox", ("Fresh container per run: no network, read-only root, CPU / memory "
         "/ PID limits, all capabilities dropped, non-root, hard timeout.")),
        ("Tests are off-limits", ("Any diff touching tests/, test_*.py, conftest.py, or pytest / "
         "tox / pyproject config is rejected before it is applied.")),
        ("No test-gaming", ("Code that reads PYTEST_CURRENT_TEST, inspects the call stack, or "
         "mocks in source is blocked, then Nano reviews every patch.")),
        ("Injection-resistant", ("Code and test output are data, never instructions. A demo repo "
         "hides \u201cdelete the tests\u201d in its source; nothing obeys it.")),
        ("Budgets", ("Max attempts, corrections, tokens, and wall time. Runs end cleanly with a "
         "clear status instead of looping.")),
        ("Secrets stay put", ("The Token Factory key stays server-side, is redacted from events "
         "and reports, and never enters the sandbox.")),
    ]
    cells = "".join(f'<div class="tile"><div class="tt">{html.escape(t)}</div>'
                    f'<div class="td">{html.escape(d)}</div></div>' for t, d in tiles)
    css = """
    .grid { position:absolute; left:84px; right:84px; top:300px; display:grid;
      grid-template-columns:repeat(3, 1fr); gap:20px; }
    .tile { background:var(--card); border:2px solid var(--line); border-radius:18px;
      padding:28px 28px; height:300px; border-top:6px solid var(--green); }
    .tt { font-size:30px; font-weight:700; }
    .td { font-size:22px; color:var(--muted); margin-top:12px; line-height:1.45; }
    """
    body = (
        '<div class="head"><div class="kicker">Guardrails</div>'
        "<h1>Fixes the code. Never the tests.</h1>"
        '<div class="sub">Every rule below is covered by an automated test in the repository.'
        "</div></div>"
        f'<div class="grid">{cells}</div>'
    )
    return page(body, css)


def main() -> int:
    browser = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BROWSER
    SRC.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    for name, make in (("05-how-it-works", how_it_works), ("06-results", results),
                       ("07-guardrails", guardrails)):
        src = SRC / f"{name}.html"
        src.write_text(make(), encoding="utf-8")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=2", "--window-size=1500,1000",
                        f"--screenshot={OUT / (name + '.png')}", src.as_uri()],
                       check=True, capture_output=True)
        print("rendered", OUT / f"{name}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
