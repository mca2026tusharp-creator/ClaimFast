# ClaimFast one-page labeling guide

Use this guide to label one synthetic motor-insurance message at a time.
Record only facts supported by the message; use JSON `null` for unknown or
conflicting extraction values. The test split is frozen and must not be used
to write or tune prompts. This concise guide summarizes the detailed
[project labeling guide](../data/labeling_guide.md); provisional project
conventions are not additional facts from the case-study rubric.

## Claim type

Choose exactly one: `own_damage` (insured vehicle damage),  
`third_party_liability` (damage to another person's property or injury to
someone other than the claimant), `theft` (reported theft or attempted theft),
`total_loss` (explicit write-off / total-loss statement), `glass_only` (glass
damage without a reported theft attempt), or `not_a_claim` (enquiry or
administrative message with no incident for claim handling). Injury to the
claimant alone does not make the claim third-party liability. Do not infer a
write-off from severity or cost.

## Urgency

- `high`: any reported injury, or the vehicle is explicitly both undrivable
  and unsafe.
- `medium`: an active claim needs insurer or claimant action and neither high
  condition applies.
- `low`: no active claim action is needed, including a non-claim.

Do not infer injury or vehicle safety from towing alone.

## Nine output fields

`claim_type` and `urgency` use the rules above. `policy_number` is copied as
text. `incident_date` is ISO `YYYY-MM-DD`; a stated day/month without a year
uses 2026, and numeric dates are DD/MM. Relative or absent dates are `null`.
`location` is the place name only. `vehicle_reg_no` is uppercase with spaces
and hyphens removed. `estimated_amount_inr` is an explicit integer-rupee
estimate; exact shorthand may be normalized. A bare amount uses the
provisional INR convention; do not convert another currency.
`injury_reported` and `police_report_filed` are `true` or `false` only when
explicitly stated; otherwise use `null`. Medical evidence of injury counts
as reported injury.

## Ambiguity and review

Treat a stated theft attempt as `theft`, even with glass damage. A merely
speculated attempt with glass-only damage is `glass_only`. In forwarded
chains, use the newest relevant sender text unless it is only quoted context.
For multiple incidents, use one only when the sender clearly identifies the
current/main incident; chronology alone is insufficient. Conflicting
extraction values become `null`; conflicting claim-type evidence and
unresolved multi-incident cases require human adjudication. The required
schema has no abstention value, so do not silently guess a label.
