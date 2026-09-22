"""
What each stage of the interview is trying to find out.

An agenda is the elicitation content of one stage: prose describing what to establish, plus
a short list of things that stage may not finish without. The five spine agendas are the
``STEP A`` to ``STEP E`` blocks of the Skill Specification's five skill prompts, which that
document asks to be used unparaphrased because "the phrasing carries behaviour".

WHAT WAS LEFT OUT, AND WHY
--------------------------
The skill prompts interleave two jobs: eliciting inputs, and computing the model from them.
Only the first belongs here. This chatbot produces an activity description; the cost build
happens downstream, against a calculation workbook, from that description. So the formulas
travel as :data:`SPINE_NOTES` — recorded, carried into the description, and handed to the
estimator — rather than as instructions the chatbot is told to execute.

That separation is not tidiness. The module this replaces pasted its workflow in whole and
documented what came of it: told the analyst was in Cairo, the assistant could not proceed
without asking which country Cairo is in. A prompt that says "Workload FTE = SUM(volume x
handling time...)" to a model whose only tool is conversation produces arithmetic in prose,
which the Skill Specification names as the failure mode the whole calculation workbook
exists to prevent: "a language model performing a forty-line cost roll-up in prose will
produce plausible, wrong numbers, and the error will be invisible to the user."

PROSE RATHER THAN A CHECKLIST
-----------------------------
Each agenda could have been a list of typed question objects, and the interview would then
walk it. That is what the fixed eight-step version did, and walking a list is exactly the
behaviour being removed — every analyst got every question whether or not the answer moved
anything. Prose lets the model skip what this particular service has already settled.

:data:`Agenda.essentials` is the counterweight. Those are the inputs where skipping is not a
judgement call, because the Cost Driver Library records what silence about them costs. A
stage cannot report itself finished while one is neither answered nor explicitly defaulted.
"""

from __future__ import annotations

from typing import NamedTuple


class Agenda(NamedTuple):
    """One stage of the interview."""

    stage: str
    label: str
    #: Shown to the analyst as the panel's stage heading.
    headline: str
    #: What to establish. Goes into the prompt verbatim.
    brief: str
    #: Slugs that must be captured or explicitly defaulted before the stage can finish.
    essentials: tuple[str, ...]
    #: Exchanges, not questions — a turn may carry up to three questions — and the last one
    #: is spent finishing rather than asking, so ``budget=4`` is three chances to ask.
    #:
    #: These were larger. A labour stage on six ran to five exchanges on a straightforward
    #: NOC brief and was still asking, because the agenda always has one more thing on it
    #: that could matter. The agenda lists what could matter for this KIND of service; the
    #: budget is what stops it being read as a list of what must be asked about THIS one.
    budget: int


# ---------------------------------------------------------------------------
# Stage 0 — classification
# ---------------------------------------------------------------------------

CLASSIFY = Agenda(
    stage="classify",
    label="Framing",
    headline="What is being bought",
    brief="""
Establish what the service actually is, so it can be placed in the category tree and costed
on the right spine. Most of this you infer from the brief rather than ask.

Also establish, where the brief does not already say and the answer would change the shape
of the model:
- The commercial model: time and materials, fixed price, unit rate, managed service, or
  staff augmentation.
- The purpose of the exercise: should-cost, budget, bid evaluation, make versus buy, rate
  card, or renewal. This sets how much precision is worth chasing.
- The scope boundary where it is genuinely unclear who supplies what: fuel, spares, tools,
  permits, vehicles, premises.

Ask about classification ONLY when the brief leaves it genuinely ambiguous between
subcategories that carry different spines, or when it names something absent from the tree.
A service you can place confidently is placed silently: state the classification in one
line, invite correction, and move on. Nobody came here to be asked what industry they work
in.
""".strip(),
    essentials=("service_description",),
    budget=2,
)


# ---------------------------------------------------------------------------
# Stage 1 — service and geography
# ---------------------------------------------------------------------------

SCOPE = Agenda(
    stage="scope",
    label="Scope and geography",
    headline="Where, when and how much",
    brief="""
Establish the physical shape of the work. From the Copilot Instructions, Step 1:

- Delivery locations, and the volume split by governorate cluster: Cairo, Delta/Alex,
  Canal, Upper Egypt, Red Sea/Sinai, Western Desert, New Cities. Density beats volume - 500
  sites in one governorate cost far less per site than 500 across eight - so the split
  matters more than the total and must not be skipped for a service delivered at scale
  across the country.
- Any part delivered remotely, and from where.
- Start date and the period the model should cover.
- Site or venue type; travel distance and time per visit.
- Permits, escorts, restricted or night-only access.
- The service window: 8x5, 12x6, 24x7 or on-call. Capture this even when the analyst has
  only described workload - on a manned service it sets the headcount floor regardless of
  how little work there is to do.
- The SLA, and the volume of the primary unit with its growth assumption.
- Seasonality, including the Ramadan window.

Skip anything the brief has already fixed, and anything that cannot bear on this service -
a software licence renewal has no travel distance and no governorate split.

Stay in your lane. The spine's own drivers belong to the stage that follows this one, and it
is better equipped to ask about them: staffing patterns and FTE, fee basis and unit rates,
tier tables, licence terms and territory, gross-versus-net and where rebates land. Asking
one of those here spends a question the next stage was going to spend anyway, and leaves it
with nothing to do.
""".strip(),
    essentials=("delivery_location", "period_covered", "primary_volume"),
    budget=3,
)


# ---------------------------------------------------------------------------
# Stage 2 — the five spines
# ---------------------------------------------------------------------------

LABOUR = Agenda(
    stage="labour",
    label="Staffing and employment",
    headline="Who does the work",
    brief="""
This service is delivered by people whose time is effectively being bought, and cost scales
with headcount. Establish:

WORKLOAD
Job titles of the front-line providers, or role descriptions if the titles are unknown;
skills and certifications required; annual activity volume by type; average handling time;
percent not completed first time; activities per team per day; minimum team size set by
health and safety or by law; skill mix; demand peaks; attrition.

COVERAGE
Whether any position is continuously manned, and on what pattern - 24x7, 12x6, 8x5 or
on-call. This is the input most often left out and it is the one that decides the answer: a
24x7 manned post needs roughly 4.6 to 5.2 FTE whatever its workload, because the roster has
to cover leave, sick and training. Establish it explicitly even when the analyst has
described only the work to be done.
For a contact centre, ask instead for contact volume by interval, average handling time,
the service level target, shrinkage and the occupancy target.

TIME AVAILABILITY
The model year, so the right calendar applies. Any non-standard leave, sick or training
allowance. How Ramadan is covered on a continuously manned service - overtime or relief
headcount. Whether travel time sits inside handling time or has to be deducted separately,
which on dispersed Egyptian sites is the difference between a credible model and a wrong
one.

SUPERVISION AND INDIRECT ROLES
Supervisory layers and ratio. Then ask explicitly about the indirect roles, because analysts
do not volunteer them and suppliers do not itemise them: planners and dispatch, QA, HSE,
trainers, spares controllers, admin and HR support, a service delivery or contract manager,
SLA reporting.

EMPLOYMENT COST
Whether there is an internal HR band or a benchmark to price the roles against, and at what
percentile. Which optional benefits are in scope - medical cover, bonus, allowances,
transport. Unsociable-hours and night premiums where the pattern requires them. Recruitment
and onboarding against the attrition rate, and any certification that has to be renewed.
""".strip(),
    essentials=("roles", "service_window", "supervision_structure"),
    budget=4,
)

DELIVERABLE = Agenda(
    stage="deliverable",
    label="Deliverables and effort",
    headline="What is being delivered",
    brief="""
The vendor is paid per deliverable, per project or per man-day of a named grade, and
staffing is their problem rather than yours. Do not build this from headcount. Establish:

DELIVERABLES
What is actually being bought: each deliverable type, the quantity per year, and what "done"
means for each. Whether the fee model is per project, per deliverable, a retainer, or a
scope of work priced as vendor FTE. For a retainer, what is in scope and what falls to
out-of-scope hourly rates - that is where retainers leak.

EFFORT AND GRADE MIX
Days by grade for each deliverable: partner or director, senior, junior, support. The review
and revision cycle - how many rounds are included and what a further round costs. Overrun
there is the commonest cause of budget breach on this spine.

DELIVERY MODEL
Onsite versus remote days; a local firm versus an international one; whether senior time is
guaranteed or substitutable; ramp or onboarding time at the start; Arabic and dialect
adaptation effort; urgency or out-of-hours premiums.

PASS-THROUGHS AND EXPENSES
What sits outside the fee: travel and per diem, disbursements, court and filing fees,
notarisation and translation, third-party specialists, production costs. Whether an expenses
cap applies, and what handling fee is charged on pass-throughs.

COMMERCIAL TERMS
Success or contingency fee elements; volume or annual commitments and any rebate; rate card
escalation over the term; termination and notice cost; conflict check and onboarding;
performance-linked fee elements.
""".strip(),
    essentials=("deliverables", "fee_model", "grade_mix"),
    budget=4,
)

TRANSACTION = Agenda(
    stage="transaction",
    label="Volume and unit rates",
    headline="What the unit is, and how many",
    brief="""
Cost here is a unit rate times a volume, with tier breaks - not a headcount build.
Establish:

VOLUME
The primary unit and its annual volume, then the volume profile by month or season, because
a flat annual figure hides the peaks that drive the price. The mix of transaction types
where they differ in effort, and the volume of each. The growth assumption per year over the
model period.

UNIT RATE STRUCTURE
The rate per unit and whether tier breaks apply - if so, the full tier table, not just the
rate at current volume. Any minimum commitment or minimum monthly charge, and what happens
if volume falls below it: most tier structures reward volume growth and punish volume
decline, and that asymmetry has to be modelled in both directions. Whether the rate is fixed
for the term or indexed.

EFFECTIVE COST DRIVERS
What makes the nominal unit rate differ from the effective cost per successful outcome. Ask
the ones that apply:
- Recruitment: offer decline rate, replacement guarantee scope, time to fill
- Payroll: off-cycle runs, correction rate, number of legal entities and pay groups
- Training: no-show and drop-out rate, minimum viable class size, runs per year
- Research: incidence rate and screening cost, fieldwork difficulty, waves
- BPO: rework rate on accuracy failures, automation share over the term
- Logistics: failed delivery rate, drops per route, cash-on-delivery handling

ONE-OFF AND SCOPE COSTS
Implementation and setup, parallel run, data migration, any system or platform licence
separate from the service fee, integration to operator systems, exit and data handback. What
is excluded from the unit rate and billed separately.

PRODUCTIVITY OVER THE TERM
On a multi-year deal, whether the unit rate steps down as the vendor automates or learns. If
it does not, say so plainly - a flat unit rate over a long term hands the productivity gain
to the vendor.
""".strip(),
    essentials=("primary_unit", "annual_volume", "unit_rate_basis"),
    budget=4,
)

RIGHTS = Agenda(
    stage="rights",
    label="Rights and licence terms",
    headline="What is being licensed",
    brief="""
What is being bought is a licence or a right, not the labour to make something. Never build
this from headcount. Keep the fee for services and the usage or licence fee separate
throughout - a quote showing only one of them is incomplete and you must say so. Establish:

WHAT IS BEING LICENSED
The talent tier or property: local, pan-Arab, or international. What exactly is granted.
Whether the operator or the rights holder owns the resulting material.

THE FIVE PRICE MULTIPLIERS
Capture each explicitly. They drive the price far more than the headline fee does:
1. Territory - Egypt only, MENA, or worldwide. Ask where the material will actually run;
   buying wider than you will use is common and expensive.
2. Term in months, and the renewal or extension rate agreed NOW. Negotiating an extension
   after a campaign has succeeded gives away all leverage.
3. Exclusivity - none, category exclusivity, or full. Usually the single largest multiplier
   on a talent fee.
4. Media channels granted - TV, digital, social, OOH, print, in-store, internal. Licensed
   separately; adding one later is a new negotiation.
5. Archive and post-term use - whether material may remain online after expiry. A compliance
   exposure as well as a cost.

FEE FOR SERVICES
Separately from the licence fee: shoot days and appearance days; overtime and overrun terms;
travel, hospitality and rider costs; approval rounds and who bears reshoot liability; agent
or agency commission, typically 10 to 20%, and whether quoted figures already include it.

ASSOCIATED PRODUCTION
Whether production is in scope at all - it is not part of the rights fee. For a sponsorship,
the activation budget: as a rule of thumb activation costs at least as much again as the
rights, and a rights fee quoted alone understates the commitment.

RISK
As explicit items rather than assumptions: the replacement scenario if a morality clause
triggers, and what reshoot, remake and rebuy of media would cost; renewal risk at end of
term; termination and force majeure exposure; Ramadan and peak-window commitments made far
ahead of use.

Withholding tax on talent, especially non-resident talent, is confirmed with Tax rather than
assumed. Record that it is outstanding; do not put a rate on it.
""".strip(),
    essentials=("licensed_property", "territory", "term_months", "exclusivity"),
    budget=4,
)

PASSTHROUGH = Agenda(
    stage="passthrough",
    label="Pass-through and fee",
    headline="Third-party spend and the fee on it",
    brief="""
Most of the money here is third-party spend, and the negotiation is about the fee basis and
the transparency of the underlying spend - not the vendor's headcount. Establish:

THE TWO LAYERS, NEVER MERGED
The annual third-party spend by type, and its growth assumption. Then, separately, the
vendor's fee. Whether quoted rates are GROSS or NET, and where any discount, rebate or
commission from the third party lands - with the vendor or with the operator. That single
question is usually worth more than the fee negotiation itself.

FEE BASIS
Which applies: a percentage of billings or spend, a fixed fee or retainer, an FTE-based
scope of work priced as a named team, or a per-transaction fee. Whether the fee is capped,
and whether it steps down above a spend threshold - a percentage fee rewards the vendor for
spending more of your money.

THE UNDERLYING SPEND DRIVERS
Ask the ones specific to the category:
- Media: TV cost per point and channel mix; digital CPM and the viewability standard; OOH
  site rates by location and duration; the RAMADAN PREMIUM as its own line with its own
  rate, never one twelfth of an annual budget; make-goods and under-delivery remedies; ad
  serving and verification fees; annual volume rebates.
- Travel: booking volume, online adoption rate, hotel programme rates, after-hours support,
  any savings guarantee.
- Fuel and freight: volume, the price index and its step behaviour, delivery distance, loss
  and pilferage allowance, demurrage and storage.
- Facilities and utilities: consumption volume, tariff band, contracted escalation.

TRANSPARENCY AND CONTROL
What audit rights exist over the underlying spend, what reporting is provided and how often,
and whether the operator can see the third-party invoices. Where there is no visibility, say
so - an unauditable pass-through is an unverifiable cost. Any volume commitments made to
third parties on the operator's behalf, and who carries the exposure if volumes fall.
""".strip(),
    essentials=("third_party_spend", "fee_basis", "gross_or_net"),
    budget=4,
)


# ---------------------------------------------------------------------------
# Stage 3 — commercial frame
# ---------------------------------------------------------------------------

COMMERCIALS = Agenda(
    stage="commercials",
    label="Commercial frame",
    headline="Currency, term and what gets added on top",
    brief="""
The last stage, and a short one. Establish the frame the estimate is built inside, then
prompt once for the cost categories nobody volunteers.

THE FRAME
- Modelling basis: annual cost, total contract value, or a rate card.
- Currency, and if anything is priced in foreign currency, the FX rate and its date. Tag
  what is EGP-linked, FX-linked and fuel-linked; they escalate differently.
- Tax treatment: net of recoverable VAT, or with VAT, with WHT and customs as memo lines.
- Contract term, and for a multi-year model, what escalates on what basis - salaries on wage
  inflation rather than CPI, imported and licensed items on the FX path, fuel on its own
  price path because it steps with subsidy reform, rent on its contract clause.

WHAT GETS ADDED ON TOP
Offer the defaults and ask only for confirmation or a different number, one message, not
four: SG&A overhead 20%, risk contingency 3 to 5%, operating margin 5%. On a deal with
pass-through spend, also ask whether SG&A and margin apply to it - a 3 to 8% handling fee
instead is usually the single largest lever available.

THE CATEGORIES NOBODY VOLUNTEERS
Prompt once, as a single list, for whichever of these bear on this service, and record what
comes back: mobilisation and transition from the incumbent; demobilisation, exit and data
handback; parallel run and knowledge transfer; performance bonds and advance payment
guarantees; working capital from receivable days; SLA penalty exposure; recurring
certification and compliance training; tool and test-equipment calibration.

Anything the analyst does not want to decide gets the stated default and moves on. This
stage exists to be finished quickly, not to be thorough.
""".strip(),
    essentials=("currency",),
    budget=2,
)


#: The formulas and decision rules that belong to the estimator, not to this conversation.
#: Carried into the activity description so they travel with the facts instead of being
#: rediscovered downstream, and so the analyst can see which rule is about to be applied to
#: the numbers they just gave.
SPINE_NOTES = {
    "labour": (
        "Workload FTE = SUM(annual volume x handling time x (1 + rework) x (1 + travel "
        "factor)) / net productive hours per FTE. Coverage FTE for a continuously manned "
        "post = (weekly coverage hours x 52) / net productive hours per FTE; 24x7 is about "
        "4.6-5.2 FTE per post, 12x6 about 2.0-2.3, 8x5 about 1.0-1.15. Take the HIGHER of "
        "the two and state which bound the result. Net productive hours = available days x "
        "8 x productivity factor, default 90%. Available days = 365 - weekly rest - public "
        "holidays - annual leave - sick - training - other absence, from Working Days for "
        "the model year. Apply the coverage multiplier to supervisors too. For a contact "
        "centre use Erlang C for agents, then divide by (1 - shrinkage); shrinkage default "
        "30%, occupancy target 80-85%."
    ),
    "deliverable": (
        "Effort = SUM(days by grade x day rate by grade). Blended day rate = total fee / "
        "total days; that is the benchmarkable number, not the individual grade rates. Flag "
        "any deliverable where partner or director time exceeds 15% of total days."
    ),
    "transaction": (
        "Cost = unit rate x volume, stepped through the tier table rather than at a single "
        "rate. Effective cost per outcome = total cost / successful outcomes, never / units "
        "attempted; show both. Amortise one-off implementation across the term separately."
    ),
    "rights": (
        "Licence fee and fee for services stay separate lines throughout; total deal value "
        "is their sum plus agent commission. Cost per usage-year = total deal value / term "
        "in years. Activation is budgeted separately from the rights fee and is expected to "
        "be at least equal to it."
    ),
    "passthrough": (
        "Third-party spend and vendor fee stay separate lines; show gross and net spend "
        "both. Effective fee percentage = vendor fee / third-party spend. Fuel and energy "
        "escalate on their own step path, not on CPI."
    ),
}


#: Benchmark bands from the Cost Driver Library, Part 7, and the Skill Specification's
#: per-skill checks. Not applied here - this conversation collects inputs and does not
#: validate a model - but carried into the description so the estimator validates against
#: the same bands the analyst was interviewed under.
BENCHMARKS = {
    "labour": (
        "24x7 manned post 4.6-5.2 FTE; continuous guard post 4.5-5 guards; indirect "
        "headcount under 25% on simple field service and 8-20% on a complex 24x7 service; "
        "field service wrench time 45-60%; contact centre shrinkage 28-35% and occupancy "
        "80-85%; seats 1 per 2.5-3 agents on a 24x7 centre; agency admin fee on manpower "
        "8-15%; attrition 20-35% a year for Egyptian technical and care roles."
    ),
    "deliverable": (
        "Labour share of fee 70-90%; travel and expenses 5-15% of fee; partner or director "
        "time under 15% of total days; two revision rounds typical; handling fee on "
        "pass-throughs 3-8%."
    ),
    "transaction": (
        "Recruitment fee 15-25% of first-year salary; training no-show allowance 5-15%; "
        "always show effective alongside nominal unit cost; model minimum-commitment "
        "asymmetry in both directions; a multi-year unit rate should step down."
    ),
    "rights": (
        "Fee for services and usage fee always two separate lines; agent or agency "
        "commission 10-20%; sponsorship activation budget at least equal to the rights fee; "
        "renewal or extension rate agreed at signature; category exclusivity is the largest "
        "single multiplier; withholding tax on talent confirmed with Tax, never assumed."
    ),
    "passthrough": (
        "Handling fee on pure pass-through 3-8%; gross versus net stated explicitly; "
        "Ramadan media on its own line at its own rate; a fee cap above a spend threshold; "
        "audit rights over the underlying spend, and a lower confidence score without them."
    ),
}


SPINE_AGENDAS = {
    "labour": LABOUR,
    "deliverable": DELIVERABLE,
    "transaction": TRANSACTION,
    "rights": RIGHTS,
    "passthrough": PASSTHROUGH,
}

AGENDAS = {
    a.stage: a
    for a in (CLASSIFY, SCOPE, LABOUR, DELIVERABLE, TRANSACTION, RIGHTS, PASSTHROUGH, COMMERCIALS)
}


def planned_stages(spine: str, secondary: tuple[str, ...] = ()) -> list[str]:
    """The stages this conversation will run, given its spine.

    The point of the graph: a rights deal never sees the staffing build, and a labour
    service never sees the five price multipliers. Used for the progress indicator, so the
    analyst is shown the length of the interview they are actually getting rather than
    "step 2 of 8" when six of the eight will not run.
    """
    middle = [s for s in (spine, *secondary) if s in SPINE_AGENDAS]
    return ["classify", "scope", *middle, "commercials", "write"]
