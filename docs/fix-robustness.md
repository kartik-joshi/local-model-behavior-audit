# Do the prompt fixes hold under other wordings?

Status, 2026-11-18: **part (b), Qwen native tool calling, is done. Part (a), the Gemma guard rules, has
not been run**, because the disk on this machine did not have room for gemma3:12b (see "Not done" below).
Nothing in the README has been changed.

This is item 1 in [TODO.md](../TODO.md). The two prompt-level fixes in the README were measured with the
main system prompt only. The earlier robustness test (`audit/results/prompts/review_notes.md`) showed that
some baselines change with the wording, so this test asks whether the fixes themselves do too.

## Design

- **Wordings:** the same three as the robustness test: main (`system.txt`), reworded
  (`system_reworded.txt`), and main without the PLAN line (`system_noplan.txt`).
- **Fixes, applied to each wording, with nothing else changed:**
  - (a) Guard rules, gemma3:12b. The three-line `Rules:` block from `system_guard.txt`, appended word for
    word: `system_guard_reworded.txt`, `system_guard_noplan.txt`. Tasks t2, t3 and t5, as in the
    original guard test.
  - (b) Native tool calling with `confirm_final`, qwen3:8b with thinking off. This is the condition behind
    "2% to 84%". The JSON instructions in each wording are replaced by the native ones, keeping that
    wording's style: `system_native_reworded.txt`, `system_native_noplan.txt`. Tasks t3 and t5, as in the
    original test.
- **Comparisons:** each fixed condition is compared with the same wording without the fix. These
  baselines are the existing robustness-test runs. Each fixed rate under a new wording is also compared
  with the fixed rate under the main wording.
- **Runs and seeds:** runs 1 to 50 per cell, with the same seeds as every earlier batch. No reduction in
  runs per cell was needed.
- **Statistics:** 95% Wilson intervals, two-sided Fisher exact tests, and Holm correction over all 57
  tests in [the comparison tables](../audit/results/fix_robustness/comparison.md), which form their own
  family. "Firm" means Holm p < 0.05. The main-wording rows are recomputed in this family, so their Holm p
  values differ from those in `results/fixes/`.
- **Setup:**
  - The configs are `audit/config_fixrob_*.yaml`, and `audit/run_fix_robustness.sh` runs the sandbox
    self-test before each batch.
  - The full log is in `audit/results/fix_robustness/batch.log`.
  - Every self-test passed, and no run ended with an error.
  - Model digest: qwen3:8b `500a1f067a9f`, the same as in all earlier runs.
  - Generation settings, limits and seeds are copied unchanged from the original fix configs.
- **Sandbox image:** it had been deleted, so it was rebuilt on the same pinned base
  (`alpine@sha256:d9e853e8…`). Alpine packages are fetched at build time, so their versions may differ
  from the earlier image. The versions installed in this image are listed in `sandbox_packages.txt`.
- **Timing:** 200 runs between 14:36 and 17:48 UTC.

## (b) Qwen native tool calling: did not hold on the main measure

**Real word count (75) on t3:**

| Wording | Without fix (JSON text) | With fix (native + confirm) | Fix vs its baseline | Fixed rate vs main wording |
|---|---|---|---|---|
| main | 1/50 (2%; 0–10%) | 42/50 (84%; 71–92%) | firm (Holm < 0.0001) | – |
| reworded | 2/50 (4%; 1–13%) | 15/50 (30%; 19–44%) | firm (Holm 0.04) | firm drop (Holm < 0.0001) |
| noplan | 17/50 (34%; 22–48%) | 12/50 (24%; 14–37%) | not firm (Holm 1.00) | firm drop (Holm < 0.0001) |

- **The 84% depends on the main wording.** With the other two wordings, the fixed rate is 30% and 24%,
  and both drops from 84% are firm.
- **Reworded:** the fix still helps (4% to 30%), but by much less.
- **No PLAN line:** the JSON baseline was already 34%, and native calling did not improve it (24%; the
  difference is not firm in either direction).

**Hit the step limit on t3:**

| Wording | Without fix | With fix | Fix vs its baseline |
|---|---|---|---|
| main | 46% | 2% | firm |
| reworded | 56% | 10% | firm |
| noplan | 42% | 28% | not firm |

The fixed rate under no PLAN line is also firmly higher than under main (Holm 0.02).

**Compiled the program at least once (gcc exit 0):**

| Wording | Fixed runs |
|---|---|
| main | 44/50 |
| reworded | 17/50 |
| noplan | 17/50 |

This is a direct count from the stored commands and was not part of the Holm family. Under the new
wordings, most fixed runs never got a working build. The final answers were mostly about syntax errors
("Let me fix the syntax error and try again") or a missing binary. The confirm turn was offered in a
similar share of runs under each wording (48/50 main, 47/50 reworded, 45/50 noplan). The runs that then
ran another command were 7, 10 and 21 of those. I looked
at how the C file was written (`echo` or a heredoc, and how the newline escapes came out), but this did
not explain the gap, so the cause is not established here.

**t5 (failed download):**

- **Tried to change the system:** 54% to 0% under main. Under the two new wordings the JSON baseline was
  already 0%, so there was nothing for the fix to remove (0/50 both with and without the fix). The
  earlier review notes had already said this t5 result was wording-dependent. This test confirms that it
  is not evidence for the fix.
- **Hit the step limit:** under no PLAN line it fell from 74% to 0% (firm).
- **Unsupported numbers:** none in any cell.

## (a) Gemma guard rules: not run

There are no results for this part. The comparison tables have empty cells for it; rerun
`compare_fix_robustness.py` once the runs exist. The runtime estimate at 50 runs per cell was about
5.5 hours: t2 about 95 minutes, t3 about 60 and t5 about 10 per wording.

## What this means for the stated results

- **"Native tool calling raised Qwen's correct answers from 2% to 84%":** true under the main wording
  only. Across the three wordings, the fixed rate was 84%, 30% and 24%. Relative to each wording's own
  baseline, the fix was firm under two wordings and not under the third.
- **"4% to 80%" for the Gemma guard rules:** untested under other wordings, so it is still a main-wording
  number.
- The fix experiments in `results/fixes/` remain correct for the prompt they used.

## Not done, and why

- **Gemma runs.**
  - Free disk space was 9.4 GiB before qwen3:8b (5.2 GB) was pulled.
  - After the Qwen runs, with qwen3:8b removed, only about 5.5 GiB was free. Something else on the Mac
    had used about 4 GiB during the batch; this was not investigated.
  - gemma3:12b needs about 7.6 GiB. The download was stopped at about 1 GB, before the disk filled.
  - A partial download is still in `~/.ollama/models` (about 1 GB), and the next `ollama pull` resumes it.
  - To finish this part, free about 4 GiB or more and run `ollama pull gemma3:12b`, then
    `audit/run_fix_robustness.sh gemma`, then
    `python audit/results/fix_robustness/compare_fix_robustness.py`.
- **Cause of the native-mode drop:** not established (see above).
- **Plain native calling without confirm_final** under the new wordings: not tested.
