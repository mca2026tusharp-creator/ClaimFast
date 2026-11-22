# ClaimFast prompt library

## Prompts and versions

This prompt set is version **1.2**, updated on 2026-11-13. This version makes
the date normalization, place-name-only location extraction, and urgency
threshold explicit and consistent across all five prompts. These changes
resolve mismatches identified during the development run while preserving the
existing worked examples. The prompts are static text templates; no LLM or
API was used to create or validate them. The five evaluated baseline files
remain version 1.2; the separately listed v1.3 candidate is not part of that
baseline set.

- [`prompt_a_zero_shot.md`](prompt_a_zero_shot.md) — Prompt A, zero-shot
  classification and extraction instructions without worked examples.
- [`prompt_b_few_shot_k3.md`](prompt_b_few_shot_k3.md) — Prompt B with exactly
  three development examples.
- [`prompt_b_few_shot_k5.md`](prompt_b_few_shot_k5.md) — Prompt B with exactly
  five development examples.
- [`prompt_b_few_shot_k8.md`](prompt_b_few_shot_k8.md) — Prompt B with exactly
  eight development examples.
- [`prompt_c_reasoning.md`](prompt_c_reasoning.md) — Prompt C requesting a
  concise evidence-based decision summary followed by the required JSON.
- [`prompt_c_evidence_summary_v1.3_candidate.md`](prompt_c_evidence_summary_v1.3_candidate.md) —
  a separately versioned, untested candidate; it is not part of the existing
  evaluation and does not claim to implement chain-of-thought.

Zero-shot supplies task instructions and schema without examples. Few-shot
adds labelled demonstrations so the model can follow the same rules on a new
message. Reasoning-assisted Prompt C asks for a brief, auditable summary of
evidence and decisions; it does not request private internal chain-of-thought.
The case-study rubric describes step-by-step reasoning; the existing C
prompt's evidence-summary format is a deviation, not a claim of full CoT
compliance.

## Example selection and provenance

Example identifiers are line-based labels for documentation only; dataset
records have no record-ID field. `DEV-01` refers to the first line of the
staged dev batch, and so on. Every example below contains only that record's
`original_message` and `label`; tags and notes are excluded. No test split
examples were used.

| Version | Example IDs, in prompt order | Count |
|---|---|---:|
| k=3 | DEV-01, DEV-06, DEV-17 | 3 |
| k=5 | DEV-01, DEV-06, DEV-17, DEV-10, DEV-14 | 5 |
| k=8 | DEV-01, DEV-06, DEV-17, DEV-10, DEV-14, DEV-11, DEV-03, DEV-09 | 8 |

The sets are nested: k=3 is the prefix of k=5, and k=5 is the prefix of k=8.
The k=3 set includes clear own-damage, third-party-liability, and not-a-claim
examples, covers all three urgency levels, and avoids the tagged
theft/glass-ambiguity records. The k=5 set adds clear theft and total-loss
examples. The k=8 set adds the glass-only example whose attempt is explicitly
speculative, another own-damage example, and another third-party-liability
example.

Because there are six claim types, k=3 and k=5 cannot cover every class.
k=3 includes `own_damage`, `third_party_liability`, and `not_a_claim`; k=5
adds `theft` and `total_loss`. k=8 covers all six, including `glass_only`.
With one single-label output per worked example, literal coverage of six
classes requires at least six examples. Therefore no k=3 or k=5 prompt can
meet the rubric's all-six-classes condition without changing k or changing
the task's single-label definition. The existing k=8 set covers all six.
This is a documented constraint, not a claim of full compliance. Existing
prompt files and evaluation versions were left unchanged.

Prompt D is optional in the case-study rubric and is **not implemented**.
The v1.3 candidate is an offline proposal only; it has not been run, scored,
or included in the existing evaluation bundle. It requests a short,
evidence-grounded decision summary and explicitly avoids hidden
chain-of-thought. That format is not equivalent to the rubric's requested
step-by-step reasoning strategy.

## Output parsing

Prompts A and B request only a JSON object. Prompt C emits a
`<decision_summary>` block followed by a `<final_json>` block. An experiment
runner must require exactly one `<final_json>` block, discard the
decision-summary block, and parse only the final block's contents. It must
reject malformed JSON, require exactly the nine output keys with their
expected types, and reject unexpected additional keys. The summary is not a
JSON field and must not be added to the output schema.

## Inherited labelling assumptions

These are provisional conventions from [`../data/labeling_guide.md`](../data/labeling_guide.md),
not additional assignment-defined facts:

- Medium/low urgency uses the guide's operational distinction because the
  assignment's high-urgency rule does not fully define those lower levels.
- A bare estimate is treated as INR for this Indian motor-insurance dataset;
  no foreign-currency conversion is inferred.
- A witnessed or factually reported theft attempt is `theft`; a merely
  speculated attempt with glass-only damage is `glass_only`. The customer's
  requested claim type does not override the facts.
- A single approximate amount is extracted as stated; ranges and vague
  amounts are `null`.
- Injury to the policyholder alone does not establish
  `third_party_liability`; classify the primary reported claim evidence and
  extract injury separately. Any reported injury still makes urgency `high`.
- When the latest sender explicitly identifies which incident is the
  current/main claim, that incident is used; chronology alone is not a
  precedence rule. If no primary incident is clear, human adjudication is
  required. The required JSON has no abstention value, so unresolved cases
  need an approved external handling policy before inclusion in scored
  evaluation. This handling policy remains unresolved.
- Conflicting extraction values are null; conflicting claim-type evidence
  requires review rather than silently resolving the conflict.

The synthetic dataset remains first-pass labelled; independent human review
is pending.
