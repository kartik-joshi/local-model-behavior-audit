# Open questions and next steps

Ordered by how much each would change what the results can claim.

1. **Prompt fixes under other wordings: done 2026-11-19, see [docs/fix-robustness.md](docs/fix-robustness.md).**
   The Gemma guard rules held under all three wordings (correct answers 48–88%, against baselines of
   2–10%). Qwen native calling held under the main wording only (84%, against 30% and 24%).
   Left over:
   - t2 (log parser) with the guard rules under the reworded and no-PLAN prompts (dropped for time).
   - Plain native calling, without `confirm_final`, under the new wordings.
   - Under the reworded guard prompt, 38/50 runs first try to read `wordcount.c`. Test whether the
     input-files rule causes this, for example by naming the task's inputs explicitly in the rule.
   - The rule ablation (`results/fixes/rule_*`) under other wordings.
2. **More wordings.** Three wordings show the results are sensitive to wording; five to ten would give a
   spread per measure instead of a held / did-not-hold split.
3. **Does the failure rule stop useful retries?** Test on a task where a retry or a second source is the
   right move, so stopping at the first error is a cost.
4. **Qwen with thinking on** for the ablation and prompt tests (thinking off only so far).
5. **Hand-labelled precision audit** of the flags: sample flagged and unflagged runs, label by hand, and
   report precision and recall per rule (the claim checker already has this; the flag rules do not).
6. **Construct validity.** "Reported the real count (75)" mixes program correctness with honesty, and the
   disclosure pattern that marks a made-up number as "disclosed" is broad. Split or narrow both.
7. **Stats tests.** Unit tests for `audit/stats.py` (Wilson, Fisher, Holm) against known values.
8. Stretch ideas from the original brief: strace-style logging in the container, a honeypot bucket URL in a
   file, with and without the `curl -o` hint.
