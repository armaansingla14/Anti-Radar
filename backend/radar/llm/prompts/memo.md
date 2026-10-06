Compress the Anti Brief (JSON) into a concise one-page Anti Memo for an investment discussion.

Requirements:
- Use only claims present in the brief. Keep each claim's source_ids (they refer to the sources listed); never add ids that are not listed.
- Keep epistemic labels honest; do not upgrade INFERENCE or MODEL_OPINION to FACT.
- 1-4 claims per section; empty list if the brief has nothing.
- one_liner: one neutral sentence on what they build.
- what_others_may_be_missing: the non-consensus angle, labelled MODEL_OPINION unless directly evidenced.
- risks must include the strongest bear case.
- questions_to_answer: 4-6 questions.
