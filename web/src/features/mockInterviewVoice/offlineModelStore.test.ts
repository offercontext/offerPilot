import { describe, expect, it, vi } from 'vitest';
import { OfflineModelStore, type OfflineModelStorePorts } from './offlineModelStore';
import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';

function ports(overrides: Partial<OfflineModelStorePorts> = {}): OfflineModelStorePorts {
  return {
    readMetadata: vi.fn(async () => undefined),
    writeMetadata: vi.fn(async () => undefined),
    deleteMetadata: vi.fn(async () => undefined),
    countCacheEntries: vi.fn(async () => 0),
    deleteCache: vi.fn(async () => true),
    estimateStorage: vi.fn(async () => ({ quota: 1024 ** 3, usage: 0 })),
    ...overrides,
  };
}

describe('OfflineModelStore', () => {
  it('only reports ready when matching metadata and cache entries both exist', async () => {
    const store = new OfflineModelStore(ports({
      readMetadata: vi.fn(async () => ({
        schemaVersion: 1 as const,
        revision: OFFLINE_WHISPER_MANIFEST.revision,
        cachedBytes: 123,
        verifiedAt: '2026-08-13T00:00:00.000Z',
        ready: true as const,
      })),
      countCacheEntries: vi.fn(async () => 2),
    }));
    await expect(store.inspect()).resolves.toEqual({ ready: true, cachedBytes: 123 });
  });

  it('fails closed when metadata is stale or cache is missing', async () => {
    const store = new OfflineModelStore(ports({
      readMetadata: vi.fn(async () => ({
        schemaVersion: 1 as const,
        revision: '0'.repeat(40),
        cachedBytes: 123,
        verifiedAt: '2026-08-13T00:00:00.000Z',
        ready: true as const,
      })),
    }));
    await expect(store.inspect()).resolves.toEqual({ ready: false, cachedBytes: 0 });
  });

  it('distinguishes insufficient, sufficient and unknown capacity', async () => {
    await expect(new OfflineModelStore(ports({
      estimateStorage: vi.fn(async () => ({ quota: 100, usage: 90 })),
    })).checkCapacity(20)).resolves.toBe('insufficient');
    await expect(new OfflineModelStore(ports()).checkCapacity(20)).resolves.toBe('sufficient');
    await expect(new OfflineModelStore(ports({ estimateStorage: vi.fn(async () => undefined) })).checkCapacity(20)).resolves.toBe('unknown');
  });

  it('clears only the model cache and metadata', async () => {
    const fake = ports();
    await new OfflineModelStore(fake).remove();
    expect(fake.deleteCache).toHaveBeenCalledOnce();
    expect(fake.deleteMetadata).toHaveBeenCalledOnce();
  });
});
