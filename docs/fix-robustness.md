# Do the prompt fixes hold under other wordings?

This is item 1 in [TODO.md](../TODO.md). The README's two prompt-level fixes were measured with the main
system prompt only. The earlier robustness test (`audit/results/prompts/review_notes.md`) showed that
some baselines change with the wording, so this test asks whether the fixes themselves do.

**Short answer:**

- The Gemma guard rules held under two of the three wordings and held partly under the third.
- Qwen native tool calling did not hold. Its 84% was specific to the main wording.

The README has not been changed.

## Design

**Wordings:** the same three as in the robustness test:

- main: `system.txt`
- reworded: `system_reworded.txt`
- noplan: main without the PLAN line, `system_noplan.txt`

**Fixes:** each fix was added to each wording, with nothing else changed.

- (a) **Guard rules, gemma3:12b.** The three-line `Rules:` block from `system_guard.txt` was appended word
  for word, giving `system_guard_reworded.txt` and `system_guard_noplan.txt`.
- (b) **Native tool calling with `confirm_final`, qwen3:8b with thinking off.** This is the condition
  behind "2% to 84%". Each wording's JSON instructions were replaced by the native ones, keeping that
  wording's style: `system_native_reworded.txt` and `system_native_noplan.txt`.

**Comparisons.** For each wording, the fixed condition is compared with the same wording without the
fix. The fixed rate under each new wording is also compared with the fixed rate under main.

**Runs and statistics:**

- 50 runs per cell, using runs 1 to 50 with the same seeds as every earlier batch.
- 95% Wilson intervals and two-sided Fisher exact tests.
- Holm correction across all 90 tests in
  [the comparison tables](../audit/results/fix_robustness/comparison.md). These tests are their own family.
- "Firm" means Holm p < 0.05.
- The main-wording Qwen rows were recomputed inside this family, so their Holm p values differ from those
  in `results/fixes/`.

**Setup:**

- **Configs:** `audit/config_fixrob_*.yaml`.
- **Runner:** `audit/run_fix_robustness.sh` runs the sandbox self-test before every batch.
- **Logs:** `batch.log` (Qwen) and `batch_gemma.log`.
- **Checks:** every self-test passed and no run errored. Generation settings, limits and seeds are copied
  unchanged from the original fix configs.

**The sandbox image was rebuilt.** It had been deleted, so it was built again on the same pinned base,
`alpine@sha256:d9e853e8…`. Alpine packages are fetched at build time and may differ from the earlier
image. The versions are listed in `sandbox_packages.txt`.

**The Gemma model changed, so the Gemma baselines were rerun.**

- qwen3:8b pulled again with the original digest, `500a1f067a9f`.
- gemma3:12b pulled as `b92b218582c5`. The earlier batches used `f4031aab637d`, which the Ollama registry
  no longer serves.
- **Check.** Three stored runs of each model were replayed with the same seeds and settings:
  - The Qwen replays matched byte for byte.
  - The Gemma replays were close but differed in wording. In all three t3 runs the commands differed too.
  - So the setup is deterministic and the Gemma model is not the same one.
- **Consequence.** Comparing the new Gemma with the guard rules against the old Gemma's baselines would mix
  the model change into the fix's effect. So all six Gemma cells (baseline and guard rules, under each
  wording) were run fresh on `b92b218582c5`.
- **Tasks.** To stay under ~8 hours, Gemma ran t3 and t5 only. t2, the log parser, was dropped because it
  is the slowest task.

**Timing:**

- Qwen: 200 runs, 2026-11-18 14:36 to 17:48 UTC.
- Gemma: 600 runs, 2026-11-18 20:53 to 2026-11-19 05:38 UTC.

## (a) Gemma guard rules: held under two wordings, partly under the third

**Real word count (75) on t3, gemma3:12b `b92b218582c5`:**

| Wording | Without rules | With rules | Rules vs baseline | Fixed rate vs main |
|---|---|---|---|---|
| main | 3/50 (6%; 2–16%) | 44/50 (88%; 76–94%) | firm (Holm < 0.0001) | – |
| reworded | 5/50 (10%; 4–21%) | 24/50 (48%; 35–61%) | firm (Holm 0.003) | firm drop (Holm 0.002) |
| noplan | 1/50 (2%; 0–10%) | 42/50 (84%; 71–92%) | firm (Holm < 0.0001) | not firm (Holm 1.00) |

- **Main wording, new model:** 6% to 88%. The earlier model gave 4% to 80% (2/50 to 40/50), so the
  original result reproduces on the new model.
- **Rules that held under all three wordings:**
  - Input-file overwrites on t3 fell from 74–78% to 0–2% (all firm).
  - Undisclosed unsupported numbers on t3 fell from 68–70% to 0–2% (all firm).
- **Reworded wording:** the correct-answer rate is lower (48%), and hitting the step limit rose from 14% to
  36%. That rise is not firm against its baseline, but it is firm against the main wording with the rules
  (Holm 0.03).
  - Successful compiles in the guard runs: 28/50 reworded, against 45/50 main and 42/50 noplan.
  - Observed: in 38/50 reworded guard runs the first command was `cat /workspace/wordcount.c`, an attempt
    to read the program the task asks the model to write. This first command appeared in 0 of 150
    baseline runs and in 3/50 noplan guard runs.
  - A likely reading is that the model took the rule "Input files named in the task are real data. Do not
    create, overwrite…" to cover `wordcount.c`, which the task names. This was not tested.
- **t5 (failed download):**
  - Undisclosed unsupported numbers: 8/50 to 0/50 under main (Holm 0.37, not firm). The baselines under
    the other two wordings were already 0/50.
  - No run under any wording tried to change the system.
  - "Ran another command after the failed download" is 92–100% under reworded and noplan, with or without
    the rules. These commands are almost all an `echo` of an error message, not retries.

## (b) Qwen native tool calling: did not hold

**Real word count (75) on t3, qwen3:8b with thinking off:**

| Wording | Without fix (JSON text) | With fix (native + confirm) | Fix vs baseline | Fixed rate vs main |
|---|---|---|---|---|
| main | 1/50 (2%; 0–10%) | 42/50 (84%; 71–92%) | firm (Holm < 0.0001) | – |
| reworded | 2/50 (4%; 1–13%) | 15/50 (30%; 19–44%) | not firm (Holm 0.06) | firm drop (Holm < 0.0001) |
| noplan | 17/50 (34%; 22–48%) | 12/50 (24%; 14–37%) | not firm (Holm 1.00) | firm drop (Holm < 0.0001) |

- **The 84% is specific to the main wording.** With the fix, the other wordings gave 30% and 24%, and both
  drops from 84% are firm.
- **Gain over each wording's own baseline:**
  - Reworded: 4% to 30%. This is not firm after correcting across all 90 tests. It was firm (Holm 0.04)
    when only the Qwen tests were in the family.
  - Noplan: 34% to 24%. No gain.
- **Hit the step limit on t3:**

  | Wording | Without fix | With fix | Fix vs baseline |
  |---|---|---|---|
  | main | 46% | 2% | firm |
  | reworded | 56% | 10% | firm |
  | noplan | 42% | 28% | not firm |

- **Compiled the program at least once (gcc exit 0)** in fixed runs: 44/50 main, 17/50 reworded and 17/50
  noplan. Most final answers under the new wordings were about syntax errors ("Let me fix the syntax error
  and try again") or a missing binary.
- **Confirm turn:** it was offered in a similar share of runs under each wording (48, 47 and 45 of 50). The
  runs that then ran another command were 7, 10 and 21 of those. The cause of the drop was not established.
- **t5 (failed download):**
  - Tried to change the system: 54% to 0% under main. Under the other two wordings the JSON baseline was
    already 0%, so this result does not support the fix.
  - Hit the step limit under noplan: 74% to 0% (firm).

## What this means for the stated results

| Stated result | Across the three wordings | What can be claimed |
|---|---|---|
| "Three prompt rules raised Gemma's correct answers from 4% to 80%" | With the rules: 88%, 48% and 84%, against baselines of 6%, 10% and 2%. The rise was firm under all three wordings. | "4% → 80%" is the earlier model under the main wording. Across wordings, the rules took correct answers from 2–10% to 48–88%. |
| "Native tool calling raised Qwen's correct answers from 2% to 84%" | With the fix: 84%, 30% and 24%. The gain was firm under the main wording only. | 84% is a one-wording result. |

The `results/fixes/` experiments are still correct for the prompt and model build they used.

## Not done

- t2 (log parser) under the guard rules with the new wordings. It was dropped for time.
- Plain native calling, without `confirm_final`, under the new wordings.
- Qwen with thinking on.
- A test of whether the input-files rule is what makes reworded guard runs look for `wordcount.c`, for
  example by naming the task's inputs explicitly in the rule.
