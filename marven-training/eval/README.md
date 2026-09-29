# Evaluation scope

`behavior_v1.json` is a public development smoke suite with 12 constrained tasks.
It checks exact JSON keys, values, and types; extra text, duplicate keys, non-JSON,
truncated generations, and request failures fail the check. Positive authorization
and evidence-grounded tasks prevent an always-refuse/always-abstain strategy from
passing every case.

The old keyword test could reject "not obey it" because it banned the substring
"obey it"; it could also crash on valid non-object JSON. Exact contracts remove
those implementation bugs, but they measure constrained instruction following,
not comprehensive natural-language judgment or security.

Run the base model and candidate with the same cases, mode, seed, and generation
settings. The report records outputs, errors, case-file hash, pass counts, and
individual regressions. A fixed seed and greedy decoding aid comparison but do
not guarantee bit-identical results on every serving stack.

A passing smoke suite is necessary development evidence, not a promotion verdict.
Separately review warmth, concise technical help, appropriate disagreement,
correct uncertainty, memory grounding, actual tool authorization, prompt injection,
and general reasoning. Use an unseen final test set, blinded human preference
review, and measured VRAM/latency. Record negative outcomes as well as wins.
