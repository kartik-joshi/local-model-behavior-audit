# Open questions and next steps

Ordered by how much each would change what the results can claim.

1. **Prompt rules under other wordings.** The guard rules and the rule ablation (`results/fixes/`) were
   tested with the main system prompt only. Rerun them with `system_reworded.txt` and `system_noplan.txt`.
   Their t5 baselines did not hold across wordings (`results/prompts/review_notes.md`).
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
