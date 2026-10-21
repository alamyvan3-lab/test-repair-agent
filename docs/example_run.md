# Example run

Recorded 2026-10-21 14:03 GTB Daylight Time with real Token Factory calls.

```
$ test-repair run examples/demo_invoice --sandbox local --out runs/demo --save-md docs/example_run.md
[   0.0s] start      Repairing demo_invoice (agent mode, sandbox: local)
[   2.2s] tests FAIL Tests: 5 passed, 2 failed, 0 errors (1.93s)
[   3.3s] isolate    All 2 failing tests also fail when rerun on their own
[   4.5s] sbfl       Most suspicious: invoice/coupons.py:14 in coupon_value() (Ochiai 0.816)
[   7.6s] #1 triage     [nano nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 665->754 tok | 2.9s] Both tests fail because the total function returns negative values (-2.0 and -1.1) instead of the expected 0.0, showing the coupon logic incorrectly reduces the total below zero.
[   9.7s] #1 diagnose   [ultra nvidia/Nemotron-3-Ultra-550b-a55b | 1778->465 tok | 2.1s] The `coupon_value` function in `invoice/coupons.py` returns the fixed coupon amount without capping it at the purchase amount, causing the total to go negative when a fixed coupon exceeds the subtotal. The docstring promises "Never more than the amount itself" but the fixed-coupon branch ignores this. The fix caps the fixed coupon value at `amount` so the discounted base never drops below zero.
[   9.7s] #1 patch      Ultra proposed a diff
[   9.8s] #1 diagnose   [ultra nvidia/Nemotron-3-Ultra-550b-a55b | 1778->465 tok | 2.1s] The `coupon_value` function in `invoice/coupons.py` returns the full fixed coupon amount (e.g., $5) without capping it at the purchase amount. This causes the subtotal to go negative when the coupon exceeds the subtotal, and the negative base flows through tax and total. The fix caps the fixed coupon value at `amount` so the discount never exceeds the purchase amount.
[   9.8s] #1 patch      Ultra proposed a diff
[  10.5s] #1 sanity     [nano nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 576->133 tok | 0.7s] Sanity check: ok - The patch directly caps the fixed coupon value to prevent it from exceeding the purchase amount, addressing the diagnosed root cause.
[  10.5s] #1 sanity     [nano nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 578->145 tok | 0.8s] Sanity check: ok - The patch directly implements the described fix by capping the fixed coupon value at the purchase amount, addressing the diagnosed cause without special‑casing tests.
[  11.4s] #1 diagnose   [ultra nvidia/Nemotron-3-Ultra-550b-a55b | 1778->1212 tok | 3.8s] The `coupon_value` function in `invoice/coupons.py` returns the full fixed coupon amount (e.g., $5) even when the subtotal is less than that amount, causing the base to go negative before tax is applied. The function's docstring states "Never more than the amount itself" but this is only enforced for percentage coupons. The fix caps fixed coupons at the current amount so the discounted base never drops below zero.
[  11.4s] #1 patch      Ultra proposed a diff
[  12.2s] #1 sanity     [nano nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B | 586->157 tok | 0.7s] Sanity check: ok - The patch correctly caps fixed coupon amounts to prevent negative bases, directly addressing the diagnosed issue without hardcoding test specifics.
[  12.2s] #1 applied    Applied patch to invoice/coupons.py (2 changed lines)
    diff --git a/invoice/coupons.py b/invoice/coupons.py
    --- a/invoice/coupons.py
    +++ b/invoice/coupons.py
    @@ -11,4 +11,4 @@
             raise ValueError(f"unknown coupon {code!r}") from None
         if kind == "percent":
             return round(amount * value / 100, 2)
    -    return value
    +    return min(value, amount)
[  13.4s] #1 tests OK   Tests: 7 passed, 0 failed, 0 errors (1.18s)
[  13.4s] #1 rank       3 candidates, 1 distinct: chose #0 (7 passed, 0 failing, 3 vote(s))
[  13.7s] SUCCESS    SUCCESS: All tests pass after 1 attempt(s).
```

## Report: demo_invoice

- **Status:** SUCCESS (All tests pass after 1 attempt(s).)
- **Mode:** agent
- **Attempts used:** 1
- **Tests before:** 5 passed, 2 failed, 0 errors
- **Tests after:** 7 passed, 0 failed, 0 errors
- **Wall time:** 13.7s across 4 sandboxed test runs (local sandbox)

## Explanation

The `coupon_value` function in `invoice/coupons.py` returns the full fixed coupon amount (e.g., $5) without capping it at the purchase amount. This causes the subtotal to go negative when the coupon exceeds the subtotal, and the negative base flows through tax and total. The fix caps the fixed coupon value at `amount` so the discount never exceeds the purchase amount.

## Attempts

| Attempt | Ultra calls | Candidates | Rejected diffs | Applied | Tests after | Outcome |
|---|---|---|---|---|---|---|
| 1 | 3 | 3 (1 distinct) | 0 | yes (#0) | 7 passed / 0 failed / 0 errors | green |

## Localization and minimization

- **Isolation rerun:** All 2 failing tests also fail when rerun on their own
- **SBFL (Ochiai):** Most suspicious: invoice/coupons.py:14 in coupon_value() (Ochiai 0.816)

## Model usage

| Role | Model | Calls | Prompt tokens | Completion tokens | Total latency (s) | Mean latency (s) | Cost (USD) |
|---|---|---|---|---|---|---|---|
| ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` | 3 | 5334 | 2142 | 8.01 | 2.67 | 0.01176 |
| nano | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 4 | 2405 | 1189 | 5.05 | 1.26 | 0.00043 |

## Patch

```diff
diff --git a/invoice/coupons.py b/invoice/coupons.py
index b246cb2..f8c9a63 100644
--- a/invoice/coupons.py
+++ b/invoice/coupons.py
@@ -11,4 +11,4 @@ def coupon_value(code: str, amount: float) -> float:
         raise ValueError(f"unknown coupon {code!r}") from None
     if kind == "percent":
         return round(amount * value / 100, 2)
-    return value
+    return min(value, amount)
```
