import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Socket } from 'node:net';
import OpenAI from 'openai';
import { isOpenAIEnabled } from './openaiAvailability';
import { generateProductMetadata, selectBestMatch } from './priceComparison';
import { selectBestGlobalMatch, validateGlobalMatch } from './globalMaterials';
import { GlobalMaterial } from './types/globalMaterials';

const mocks = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('openai', () => ({
  default: vi.fn(function () {
    return { chat: { completions: { create: mocks.create } } };
  }),
}));
vi.mock('firebase-admin', () => ({ app: vi.fn(), initializeApp: vi.fn() }));
vi.mock('firebase-admin/firestore', () => ({ getFirestore: vi.fn() }));
vi.mock('firebase-functions/v2/https', () => ({
  onRequest: vi.fn((_config, handler) => handler),
  onCall: vi.fn((_config, handler) => handler),
  HttpsError: class extends Error {},
}));

// Retain only a reference to the original environment. Never read/save a real
// credential, even for restoration; all access to these two names is intercepted.
const originalEnv = process.env;
let fakeEnv: NodeJS.ProcessEnv = {};
beforeAll(() => {
  process.env = new Proxy(originalEnv, {
    get(target, property) {
      if (property === 'OPENAI_API_KEY' || property === 'DISABLE_OPENAI') {
        return fakeEnv[property];
      }
      return Reflect.get(target, property);
    },
  });
  const blocked = () => { throw new Error('Network forbidden in offline OpenAI guard tests'); };
  vi.spyOn(globalThis, 'fetch').mockImplementation(blocked);
  vi.spyOn(Socket.prototype, 'connect').mockImplementation(blocked);
});
afterAll(() => {
  process.env = originalEnv;
  vi.restoreAllMocks();
});
beforeEach(() => {
  fakeEnv = {};
  vi.mocked(OpenAI).mockClear();
  mocks.create.mockReset();
  mocks.create.mockResolvedValue({ choices: [{ message: { content: JSON.stringify({
    index: 0, confidence: 0.9, reasoning: 'offline match', aliases: ['cabinet'], description: 'Cabinet',
  }) } }] });
});

const candidate: GlobalMaterial = {
  id: 'cabinet', name: 'Cabinet', normalizedName: 'cabinet', description: 'Cabinet',
  aliases: ['cabinet'], zipCode: '80202', retailers: {}, createdAt: 0,
  updatedAt: 0, matchCount: 0, source: 'seed',
};

async function exerciseFallbacks() {
  expect(await selectBestMatch('Cabinet', [{}], 'homeDepot')).toEqual({
    index: 0, confidence: 0.5, reasoning: 'OpenAI not configured - defaulting to first result',
  });
  expect(await generateProductMetadata('Cabinet', '  BASE CABINET  ')).toEqual({
    aliases: ['base cabinet'], description: 'Cabinet',
  });
  expect(await selectBestGlobalMatch('Cabinet', [candidate, { ...candidate, id: 'other' }])).toEqual({
    candidate, confidence: 0.5, reasoning: 'OpenAI not configured',
  });
  expect(await validateGlobalMatch('Cabinet', candidate)).toEqual({
    confidence: 0.95, reasoning: 'Word overlap fallback (100% match)',
  });
  // Single-candidate routing also reaches the guarded validation fallback.
  expect((await selectBestGlobalMatch('Cabinet', [candidate])).confidence).toBe(0.95);
  expect(OpenAI).not.toHaveBeenCalled();
  expect(mocks.create).not.toHaveBeenCalled();
  expect(globalThis.fetch).not.toHaveBeenCalled();
  expect(Socket.prototype.connect).not.toHaveBeenCalled();
}

describe('comparison OpenAI kill switch', () => {
  it.each(['true', 'TRUE', '1', 'yes', 'YES', 'on', 'ON', ' True '])(
    'disables all four paths for flag %s without even reading the key', async flag => {
      fakeEnv = { DISABLE_OPENAI: flag };
      Object.defineProperty(fakeEnv, 'OPENAI_API_KEY', {
        get() { throw new Error('Disabled paths must not read a credential'); },
      });
      await exerciseFallbacks();
    },
  );

  it.each(['sk-proj-offline-fabricated-test-key', 'offline-sentinel', undefined])(
    'does not construct a client with disabled key variant %#', async key => {
      fakeEnv = { DISABLE_OPENAI: 'true', OPENAI_API_KEY: key };
      await exerciseFallbacks();
    },
  );

  it.each([undefined, 'false', 'FALSE', '0', 'no', 'off'])(
    'preserves existing client use for non-disabling flag %s', async flag => {
      fakeEnv = { DISABLE_OPENAI: flag, OPENAI_API_KEY: 'offline-fake-key' };
      await selectBestMatch('Cabinet', [{}], 'homeDepot');
      await generateProductMetadata('Cabinet', 'cabinet');
      await selectBestGlobalMatch('Cabinet', [candidate, { ...candidate, id: 'other' }]);
      await validateGlobalMatch('Cabinet', candidate);
      expect(OpenAI).toHaveBeenCalledTimes(4);
      expect(mocks.create).toHaveBeenCalledTimes(4);
      expect(globalThis.fetch).not.toHaveBeenCalled();
      expect(Socket.prototype.connect).not.toHaveBeenCalled();
    },
  );

  it('preserves missing-key fallbacks without the disable flag', async () => {
    await exerciseFallbacks();
  });

  it('evaluates the flag at call time rather than caching it', () => {
    const env = { OPENAI_API_KEY: 'offline-fake-key', DISABLE_OPENAI: 'false' };
    expect(isOpenAIEnabled(env)).toBe(true);
    env.DISABLE_OPENAI = 'true';
    expect(isOpenAIEnabled(env)).toBe(false);
  });

  it('keeps empty-result paths independent of OpenAI', async () => {
    expect((await selectBestMatch('Cabinet', [], 'homeDepot')).index).toBe(-1);
    expect((await selectBestGlobalMatch('Cabinet', [])).candidate).toBeNull();
    expect(OpenAI).not.toHaveBeenCalled();
  });
});
