# Public seed v1 data card

`public_seed_v1.jsonl` contains 12 synthetic instruction/response examples authored
for Marven's original PR #66. They contain no intentionally included private user
history. They are retained unchanged so this revision's pipeline changes can be
compared without also changing the seed corpus.

Purpose: validate formatting, completion masking, and the training path. This is
not a production personality corpus or a validated safety curriculum. The targets
include meta-language such as "I should"; reviewed conversational data should also
show Marven actually answering naturally, not merely reciting policies.

Coverage: collaboration, memory abstention, supersession, evidence trust, retention,
untrusted tool content, retrieval relevance, frustration, JSON, implementation
choices, and limits of inference from tone. Twelve examples do not establish
adequate coverage or balance across those categories.

Before a serious adapter run:

1. Add a few hundred varied, human-reviewed examples with a clear source/license.
2. Balance useful positive action with boundary handling and uncertainty.
3. Include ordinary technical work and conversation so the model does not learn
   to respond to everything with a policy explanation.
4. Group paraphrases and source episodes together; keep validation and an unseen
   final test set independent. Inspect semantic overlap manually.
5. Review factual correctness, privacy, tone, and unwanted anthropomorphism.
6. Keep evolving personal facts in canonical memory, not model weights.

A count target alone is not a quality threshold. These public examples and the
public smoke cases are visible development assets; neither is a hidden benchmark.
