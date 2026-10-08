// Reviewed local CJS closure of the fixed product. The unsigned release policy
// leaves electron-updater's lazy dependency unloaded; no updater actions are run.
export const AUDITED_DESKTOP_PRODUCT = 'c040a5d2f1949ff8a4ae806e7c3b593c6481e6d0';
export const AUDITED_DESKTOP_MODULE_SHA256 = Object.freeze({
  'main.cjs': '62b039e5943460b8d8d78a1c4b4ecbff25287e13c56259d8e105c658fc57af26',
  'lifecycle.cjs': 'd2eb0e4bacc449b561f73b10f999d0458e9a3a64e9fdae90961daa8f9c2c4b9f',
  'capabilities.cjs': '0c6b3d8c8b8e10d6deeef91e528d1c7078dc3a256d1b2c45c94b26a375e50c9b',
  'haru.cjs': 'e32f14caa39ec9d6f4364953ae464556b3cd3482b6286aa0e74974c917f9c046',
  'haru-protocol.cjs': '25b863ce1f58e5653ca45e8385d819678f4d05f8b896d21f09c0946fb5f74f1d',
  'preload.cjs': 'aa7f8b7804e4c9f50cf571380393525a3420228c4f8e42615fc9e6ea15ed91b7',
  'updater.cjs': '26cebbb91a9aac1d4ce17c26ffc7aaac0bd44b267096af49a997cc867c27f38c',
  'update-safety.cjs': 'e979e1c8627e8f0fd13fc5654d946128eaa75000fd4306dacc0fd43ea6c9ebba',
  'update-backup.cjs': '40d55ca72e07c001d739bf58f050c83490f0575122c4b05a74ee5bca51cb20de',
  'update-install.cjs': 'd4f42a8bfe00c2e8b4a3cb5e8204d556f2d0b1e158ac2a922bf9ab14c038c537',
  'update-signature.cjs': '8131866ae4aefcefd49ab3cd46fc2a931f91ab528a3218e742ba916161d4d47f',
  'update-integrity.cjs': 'ae261c3edac0f574612ff411c0660cce4f9c8f2bc22718392774015e23640625',
});
export const DESKTOP_SOURCE_FILES = Object.freeze(Object.keys(AUDITED_DESKTOP_MODULE_SHA256));
