import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';

export type OfflineModelMetadata = {
  schemaVersion: 1;
  revision: string;
  cachedBytes: number;
  verifiedAt: string;
  ready: true;
};

export interface OfflineModelStorePorts {
  readMetadata(): Promise<OfflineModelMetadata | undefined>;
  writeMetadata(metadata: OfflineModelMetadata): Promise<void>;
  deleteMetadata(): Promise<void>;
  countCacheEntries(): Promise<number>;
  deleteCache(): Promise<boolean>;
  estimateStorage(): Promise<{ quota?: number; usage?: number } | undefined>;
}

const DATABASE_NAME = 'offerpilot-offline-models';
const STORE_NAME = 'metadata';
const MODEL_KEY = 'whisper-balanced';

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'));
      return;
    }
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB unavailable'));
  });
}

async function metadataOperation<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const database = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, mode);
      const request = run(transaction.objectStore(STORE_NAME));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('模型元数据操作失败'));
    });
  } finally {
    database.close();
  }
}

export function createBrowserModelStorePorts(): OfflineModelStorePorts {
  return {
    readMetadata: () => metadataOperation('readonly', (store) => store.get(MODEL_KEY)),
    writeMetadata: async (metadata) => { await metadataOperation('readwrite', (store) => store.put(metadata, MODEL_KEY)); },
    deleteMetadata: async () => { await metadataOperation('readwrite', (store) => store.delete(MODEL_KEY)); },
    countCacheEntries: async () => {
      if (typeof caches === 'undefined') return 0;
      const cache = await caches.open(OFFLINE_WHISPER_MANIFEST.cacheNamespace);
      return (await cache.keys()).length;
    },
    deleteCache: async () => typeof caches !== 'undefined' && caches.delete(OFFLINE_WHISPER_MANIFEST.cacheNamespace),
    estimateStorage: async () => {
      if (typeof navigator === 'undefined' || !navigator.storage?.estimate) return undefined;
      return navigator.storage.estimate();
    },
  };
}

export class OfflineModelStore {
  constructor(private readonly ports: OfflineModelStorePorts = createBrowserModelStorePorts()) {}

  async inspect(): Promise<{ ready: boolean; cachedBytes: number }> {
    try {
      const [metadata, entries] = await Promise.all([
        this.ports.readMetadata(),
        this.ports.countCacheEntries(),
      ]);
      const ready = Boolean(
        metadata?.ready
        && metadata.schemaVersion === OFFLINE_WHISPER_MANIFEST.schemaVersion
        && metadata.revision === OFFLINE_WHISPER_MANIFEST.revision
        && entries > 0,
      );
      return ready ? { ready: true, cachedBytes: metadata?.cachedBytes ?? 0 } : { ready: false, cachedBytes: 0 };
    } catch {
      return { ready: false, cachedBytes: 0 };
    }
  }

  async checkCapacity(requiredBytes = OFFLINE_WHISPER_MANIFEST.approximateBytes): Promise<'sufficient' | 'insufficient' | 'unknown'> {
    try {
      const estimate = await this.ports.estimateStorage();
      if (!estimate || !Number.isFinite(estimate.quota) || !Number.isFinite(estimate.usage)) return 'unknown';
      return (estimate.quota! - estimate.usage!) >= requiredBytes ? 'sufficient' : 'insufficient';
    } catch {
      return 'unknown';
    }
  }

  async markReady(cachedBytes: number): Promise<void> {
    await this.ports.writeMetadata({
      schemaVersion: 1,
      revision: OFFLINE_WHISPER_MANIFEST.revision,
      cachedBytes: Math.max(0, Math.floor(cachedBytes)),
      verifiedAt: new Date().toISOString(),
      ready: true,
    });
  }

  async remove(): Promise<void> {
    await Promise.all([this.ports.deleteCache(), this.ports.deleteMetadata()]);
  }
}
