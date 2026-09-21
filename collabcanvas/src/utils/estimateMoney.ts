/** Final totals and probabilistic percentiles are deliberately separate.
 * totalCost is the legacy alias for finalEstimate on completed estimates.
 */
export function selectEstimateMoney(data: Record<string, any>) {
  const final = data.finalOutput ?? {};
  const summary = final.executiveSummary ?? {};
  const range = summary.confidenceRange ?? {};
  return {
    baseEstimate: data.baseEstimate ?? final.baseEstimate ?? summary.baseCost ?? null,
    contingency: data.contingency ?? final.contingency ?? summary.contingency ?? null,
    finalEstimate: data.finalEstimate ?? final.finalEstimate ?? summary.totalCost ?? data.totalCost ?? null,
    p50: data.p50 ?? range.p50 ?? null,
    p80: data.p80 ?? range.p80 ?? null,
    p90: data.p90 ?? range.p90 ?? null,
  };
}
