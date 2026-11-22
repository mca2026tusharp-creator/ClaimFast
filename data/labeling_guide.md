# ClaimFast synthetic-data labelling guide

This guide applies to the synthetic messages in `dev.jsonl` and `test.jsonl`.
Each JSONL record has `original_message` and `label`. Do not add information
that is not supported by the message. The test split is held out and must not
be used to design prompts or revise labels for better model performance.

## Claim types

- `own_damage`: damage to the insured's own vehicle, where the message does
  not explicitly say it was written off and no third-party liability is the
  main reported claim. Example: “I scraped my car against a pillar.”
- `third_party_liability`: the sender says their vehicle or actions damaged
  someone else's property or injured someone other than the
  policyholder/claimant. For `third_party_liability`, an injured person means
  someone other than the policyholder/claimant. Injury to the policyholder
  alone does not establish `third_party_liability`. Classify the claim type
  using the primary reported claim evidence and extract injury separately.
  Any reported injury still makes urgency `high`. Example: “I hit a parked
  scooter and damaged its side.”
- `theft`: a vehicle or its parts are reported stolen, or an attempted theft
  is explicitly reported. Example: “My car was stolen overnight.”
- `total_loss`: the message explicitly says the vehicle was written off,
  declared a total loss, or is beyond economical repair. Do not infer this
  from damage severity or an amount alone.
- `glass_only`: reported vehicle damage is limited to glass and no theft
  attempt is reported. Example: “A stone cracked only my windscreen.”
- `not_a_claim`: a general enquiry, administrative message, or other message
  that does not report an incident for claim handling.

## Urgency

- `high`: any injury is reported, or the vehicle is explicitly described as
  both undrivable and unsafe.
- `medium`: an active claim needs insurer or claimant action and neither high
  condition is stated.
- `low`: no active claim action is requested (including `not_a_claim`) or the
  message is explicitly only informational and not time-sensitive.

An explicit statement that a vehicle is not roadworthy (for example,
“gaadi chalne layak nahi hai”) counts as both undrivable and unsafe. Do not
infer this from towing alone.

Do not infer injury, whether a vehicle can be driven, or whether it is unsafe.
The request defines high urgency but does not fully define all medium/low
boundaries; this dataset uses the operational rules above.

## Extraction and normalization

- `policy_number`: copy the stated identifier as text; otherwise use `null`.
- `incident_date`: normalize an explicitly stated full date to ISO
  `YYYY-MM-DD`. If the message gives a day and month without a year, use that
  day and month in 2026. Numeric dates are interpreted as DD/MM, so `14/09`
  means 14 September. Relative dates such as “yesterday”, “kal raat”, or “last
  week”, and messages with no date, give `null`.
- `location`: return the place name only, copied from the message. Do not
  include a street or landmark description or leading words such as “near”,
  “at”, or “in”; otherwise use `null`.
- `vehicle_reg_no`: uppercase and remove spaces and hyphens (for example
  `xx-01 ab 1234` becomes `XX01AB1234`); otherwise `null`.
- `estimated_amount_inr`: use an explicitly stated estimate as integer rupees;
  otherwise `null`. A bare repair estimate is treated as INR because this is
  an Indian motor-insurance dataset; a value with another currency must not be
  converted without an explicit exchange-rate rule. Normalize exact shorthand
  such as `18k` or `1.2 lakh` to integer rupees. A single approximate figure
  (`around 45k`, `approx 45000`) is extracted as that number; ranges or vague
  amounts are `null`.
- `injury_reported` and `police_report_filed`: use `true` or `false` only when
  explicitly stated; otherwise use `null`.
- Medical treatment or injury evidence stated in the message (hospital,
  clinic, ambulance, stitches, cast, bleeding, fracture) counts as
  `injury_reported = true`; a person merely being "involved" does not.

All nullable fields use JSON `null` when the source does not establish a
value. Never fill gaps from context, including policy number, date, location,
registration, amount, injury, or police-report status.

The dataset validator requires a non-null `incident_date` to be a valid ISO
date matching a date it recognizes in the message. It recognizes ISO dates,
day/month text dates (including supported month abbreviations and ordinal
suffixes), and numeric DD/MM dates; dates without a year are matched using
2026. A non-null date unsupported by the message is rejected, while `null`
remains allowed. For a non-null `location`, the validator only checks that the
value occurs case-insensitively in the message; it does not enforce the
place-name-only rule above or reject street/landmark descriptions.

## Ambiguity and message handling

- **Theft versus glass:** classify as `theft` when theft or attempted theft is
  stated as fact, including a witnessed attempt or an attempt reported as fact
  by another person, even if glass is damaged. If an attempt is only speculated
  (for example, “might have” or “lagta hai”) and the reported damage is limited
  to glass, classify as `glass_only`. The label must follow the reported facts;
  the customer's requested claim type does not determine it.
- **Total loss and overlapping damage:** use `total_loss` only for an explicit
  write-off/total-loss statement. Otherwise label the primary supported
  liability or own-damage claim.
- **Multiple incidents:** When the latest sender explicitly identifies which
  incident is the current/main claim, classify that incident. Do not select
  an incident merely because it is chronologically newer. If multiple
  distinct incidents are mentioned and no primary incident is clear, do not
  invent a precedence rule. Such cases require human adjudication. The
  required prediction schema has no abstention value; unresolved cases need
  an approved external handling policy before inclusion in scored evaluation.
- **Forwarded chains:** use the newest relevant message from the sender.
  Treat quoted/forwarded text as context, not as confirmation, unless the
  sender explicitly adopts it for the current claim. If the newest message
  does not establish a fact, label that field `null`.
- **Claim versus enquiry:** a message about an incident that seeks or implies
  insurance handling is a claim; a question about coverage or a purely
  administrative request without a reported incident is `not_a_claim`.
- **Conflicts:** do not choose between contradictory values by guessing.
  Use `null` for conflicting extraction fields and flag the record for review.
  For conflicting claim-type evidence, human adjudication is pending.

## Review status

These are synthetic first-pass labels. Independent human review is pending.
Provisional interpretations above are documented rather than treated as
assignment-specified rules.
