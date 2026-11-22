# Held-Out Experiment Completion Plan

**Status:** Planning only. No experiment was started for this report.

## Verified state

The pinned held-out plan contains 900 unique observations for model
`openai/gpt-oss-120b`, prompts A, B-k3, B-k5, B-k8, and C, with runs 1–3.
The plan digest is
`16121fec106ce4b819f3093bedaba2d00eb83153cb71964759e6252864319b21`.
The pinned test snapshot SHA-256 is
`6ed1d877c461a3e4d6252a52f882f515337ef95a3fc9816426de2f62921f13a4`.

The isolated results store currently has **71 successful**, **0 failed**, and
**829 pending** observations. No stored key is outside the plan. Preserve the
71 successes; ordinary resume skips existing keys and duplicate insertion is
rejected.

The held-out retry log has 75 existing events in the legacy four-field schema.
The current writer adds extended diagnostics to future events, and the loader
accepts both schemas. Do not rewrite or migrate the existing retry history.

## Token budget and expected duration

The 71 successful observations recorded 161,597 input and 17,151 output tokens,
for **178,748 total tokens**. This gives an observed mean of:

`178,748 / 71 = 2,517.577 tokens per successful observation`

Assuming that mean holds for the remaining work:

`829 × 2,517.577 ≈ 2,087,072 expected remaining tokens`

At a hard cap of **180,000 tokens per UTC day**, that is **11.59 cap-days**,
or approximately **12 daily execution windows**. At the observed mean, 71
observations use 178,748 tokens; a 72nd average-sized observation would exceed
the cap. A nominal schedule is 71 observations per window for 11 windows
(781 observations), followed by 48 in the twelfth. Actual daily counts will
vary with token usage; the runner's guard estimates the next call and stops
before an overrun.

The user's Groq Console screenshot verifies a provider TPD limit of
**200,000 tokens/day** for `openai/gpt-oss-120b`. The project configuration
default of **280,000** in `src/config.py` is not the provider quota. The
approved process-local safety cap of **180,000 tokens/day** is 20,000 below
the verified provider limit. The safe local artifacts do not establish numeric
RPM, TPM, or RPD limits. The previous run recorded 75 HTTP 429 responses among
146 attempts, with a maximum logged wait of 1,441 seconds and a retry-event
span of about 2.61 hours. Consequently, 12 is an estimate of token-budget
windows, **not a guaranteed calendar completion date**. Rate-limit backoff may
extend elapsed time.

All 178,748 recorded tokens are dated **2026-11-15 UTC**. The next daily
window begins at **2026-11-16 00:00 UTC (05:30 IST)**. On the current UTC day,
the runner estimates the next call at 2,518 tokens; 178,748 + 2,518 exceeds
180,000, so no additional call should be made before that reset.

## Safe resume procedure

1. Wait until the next UTC day before resuming. Do not change the token cap to
   compensate for rate limits.
2. Verify the pinned plan and dataset snapshot using the runner's safe plan
   validation, then confirm the current store still contains only expected
   keys and preserve all successful results.
3. Run **one process at a time** using the command below. The process-local
   assignment sets `config.DAILY_TOKEN_LIMIT` to exactly 180,000 before the
   runner is imported; it does not change the global config default.
4. Confirm the runner's prompt when ready. Do not add `--no-daily-limit`,
   `--retry-failed-key`, or any other retry/limit bypass. The normal resume
   path skips stored observations and honors persisted retry deadlines,
   backoff, and retry limits.
5. Let the daily guard stop the process before an estimated overrun. Keep the
   results and retry log in the dedicated held-out directory. Repeat the same
   command only after a new UTC day begins.
6. Count persisted results against all 900 expected keys after each session.
   Run held-out evaluation only after the complete planned grid is persisted
   and validated.

```bash
cd /Users/tusharpandey/Documents/ClaimFast
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'from src import config; config.DAILY_TOKEN_LIMIT = 180_000; from src.run_experiment import main; raise SystemExit(main(["--split", "test", "--prompts", "A", "B-k3", "B-k5", "B-k8", "C", "--runs", "3"]))'
```

This command intentionally omits `--yes`, so the runner still requests
confirmation. It also omits retry and daily-limit bypass flags. The run remains
sequential and uses the pinned model, prompts, plan, and isolated held-out
stores.

## Scope and limitations

This estimate uses actual token counts from the 71 stored successes; it does
not assume token counts for unrun observations are known. Future calls may use
more or fewer tokens. Recent 429s do not identify a specific RPM, TPM, TPD, or
RPD bucket, so no more precise throughput estimate is supported. No billing
settings, model, prompts, dataset, plan, code, or experiment data are changed
by this plan.
