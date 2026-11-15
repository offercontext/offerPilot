// Reviewed local CJS closure of the fixed product. The unsigned release policy
// leaves electron-updater's lazy dependency unloaded; no updater actions are run.
export const AUDITED_DESKTOP_PRODUCT = '4844b591b18c13acb1633b63e883f2ba6d861956';
export const AUDITED_DESKTOP_MODULE_SHA256 = Object.freeze({
  'main.cjs': 'a7a8566ac0fd4a7e9c31d12891e4712906a331249d288f2508b0a8d6fa35a6f6',
  'lifecycle.cjs': 'd2eb0e4bacc449b561f73b10f999d0458e9a3a64e9fdae90961daa8f9c2c4b9f',
  'capabilities.cjs': '0c6b3d8c8b8e10d6deeef91e528d1c7078dc3a256d1b2c45c94b26a375e50c9b',
  'haru.cjs': '7708bc8cc336b386fa80bcdccbb05c5da957a09f7f16f731d344c95833efc58b',
  'haru-protocol.cjs': 'a598bd63f9a8c58048f66fc76a9aba7f80f497ec83b88fc8dfeed3a121ef62ee',
  'preload.cjs': '46f98888bf5beca3da6bd7e415a2d92a661500d76c7208efbf91ab3f24069e31',
  'updater.cjs': '7e736da45e6b063cfc2d752e27e38fffdd53d214321a6851f7babbf977a42c0b',
  'update-safety.cjs': 'e979e1c8627e8f0fd13fc5654d946128eaa75000fd4306dacc0fd43ea6c9ebba',
  'update-backup.cjs': '40d55ca72e07c001d739bf58f050c83490f0575122c4b05a74ee5bca51cb20de',
  'update-install.cjs': 'd4f42a8bfe00c2e8b4a3cb5e8204d556f2d0b1e158ac2a922bf9ab14c038c537',
  'update-signature.cjs': '8131866ae4aefcefd49ab3cd46fc2a931f91ab528a3218e742ba916161d4d47f',
  'update-integrity.cjs': 'ae261c3edac0f574612ff411c0660cce4f9c8f2bc22718392774015e23640625',
});
export const DESKTOP_SOURCE_FILES = Object.freeze(Object.keys(AUDITED_DESKTOP_MODULE_SHA256));

// Current checkout's reviewed packaging closure. Keep this separate from the
// immutable historical installer hashes above: localization adds modules but
// must not relabel new source as the previously audited product.
export const CURRENT_DESKTOP_SOURCE_FILES = Object.freeze([
  ...DESKTOP_SOURCE_FILES,
  'locale.cjs',
  'locale-catalog.cjs',
]);
