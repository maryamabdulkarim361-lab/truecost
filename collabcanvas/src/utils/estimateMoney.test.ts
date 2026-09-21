import { describe, it, expect } from 'vitest';
import contract from '../../../functions/tests/fixtures/authoritative_cost_contract.json';
import { selectEstimateMoney } from './estimateMoney';

describe('authoritative cost contract', () => {
  it('preserves final total separately from all risk percentiles', () => {
    const result = selectEstimateMoney(contract);
    expect(result.finalEstimate).toBe(contract.totalCost);
    expect(result.finalEstimate).toBeCloseTo(result.baseEstimate! + result.contingency!, 2);
    expect(result.p50).toBe(contract.p50);
    expect(result.p80).toBe(contract.p80);
    expect(result.p90).toBe(contract.p90);
    expect(result.finalEstimate).not.toBe(result.p50);
  });
  it('uses historical Final output before ambiguous legacy root totals', () => {
    const result = selectEstimateMoney({totalCost: contract.p50, finalOutput: {
      executiveSummary: {totalCost: contract.finalEstimate, baseCost: contract.baseEstimate,
        contingency: contract.contingency, confidenceRange: contract}}});
    expect(result.finalEstimate).toBe(contract.finalEstimate);
    expect(result.p80).toBe(contract.p80);
  });
  it('preserves explicit zero and never invents percentiles', () => {
    expect(selectEstimateMoney({finalEstimate:0,totalCost:50}).finalEstimate).toBe(0);
    expect(selectEstimateMoney({totalCost:50}).p50).toBeNull();
  });
});
