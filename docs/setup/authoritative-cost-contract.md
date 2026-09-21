# Authoritative Cost Contract V1

This contract supersedes the legacy client-PDF P80-as-total rule in Story 4.3
and the root P50-as-total mapping. The user explicitly approved using the new
contract everywhere. No Risk simulation or Cost Agent mathematics change.

| Field | Meaning | Contingency / simulation |
|---|---|---|
| Cost subtotals materials/labor/equipment | Deterministic direct costs at low/base rates | Neither |
| Cost locationAdjustedSubtotal | Location-adjusted direct costs | Neither |
| Cost total.low | Location-adjusted direct costs + overhead/profit/permits/tax + Cost allowance | Includes Cost contingency; not a simulated percentile |
| Risk monteCarlo p50/p80/p90 | Existing simulated percentiles; Risk uses Cost total as its baseline | Baseline already includes Cost allowance; simulation unchanged |
| baseEstimate | Location-adjusted deterministic costs + overhead/profit/permits/tax | Excludes Cost contingency and Risk allowance |
| contingency | User-selected allowance, otherwise explicit Risk dollarAmount | Applied once; not added on top of Cost contingency |
| finalEstimate / totalCost | baseEstimate + contingency | Same authoritative total in root, Final, UI and both PDF modes |
| root p50/p80/p90 | Risk percentiles when available | Separate from finalEstimate |

Final's costBreakdown.totalBeforeContingency and totalWithContingency remain
compatibility fields for baseEstimate and finalEstimate. locationAdjustment
explains the difference between unadjusted direct subtotals and the base.
Absent Risk retains the existing Cost-range fallback; this is legacy estimated
range data, not evidence of a simulation. No historical documents are migrated.
The UI prefers explicit finalEstimate or Final executiveSummary before an
ambiguous historical root totalCost. Binary PDF rendering requires native libs.

## Offline kitchen arithmetic

Direct = 13,158 + 7,500 + 450 = 21,108.
Location-adjusted direct = 22,163.40 (difference 1,055.40).
Base = 22,163.40 + 2,216.34 overhead + 2,437.97 profit + 1,125 permits = 27,942.71.
Cost allowance = 1,340.89; Cost total = 29,283.60.
The fixture separately supplies Risk P50 = 29,283.60, P80 = 32,797.63,
P90 = 35,067.77; those are stored fixture values, not recomputed simulations.
Old Final = 21,108 + 2,216.34 + 2,437.97 + 1,125 + 3,514.03 = 30,401.34.
It counted one allowance, but omitted the location adjustment.
New Final = 27,942.71 + 3,514.03 = 31,456.74: exactly one selected allowance.

Shared offline fixture: functions/tests/fixtures/authoritative_cost_contract.json.
Python tests verify actual Final/root and rendered client/contractor HTML;
TypeScript tests verify the selector used by EstimatePage against the same data.

## PDF compatibility and presentation

PDF totals prefer nested Final `finalEstimate`, then root `finalEstimate`,
then Final compatibility totals (`costBreakdown.totalWithContingency`,
`executiveSummary.totalCost`, `totalCost`), then legacy root `totalCost`.
No numeric equality with P80 is used to classify historical data.
Invalid explicit values render unavailable rather than selecting a legacy total.

Both report modes show base estimate plus the explicit selected contingency,
with cents. Contractor base components are shown only when all components
are available and reconcile to that base. Partial division details are not
presented as a complete project cost allocation. No category percentages
are used to manufacture monetary amounts. Displayed allowance percentage
is selected contingency divided by baseEstimate, not by finalEstimate or P50.
Risk numeric strings are accepted only when finite; unavailable values show
N/A without a currency symbol. Missing risk data does not create demo risks
or synthetic percentile amounts.
