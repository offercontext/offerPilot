const CLASSES = new Set(['Error', 'AssertionError', 'TimeoutError', 'SyntaxError', 'TypeError']);
const CODES = new Set(['ERR_ASSERTION', 'ENOENT', 'EACCES', 'EPERM', 'ECONNREFUSED', 'ECONNRESET',
  'ETIMEDOUT', 'COMMAND_TIMEOUT', 'COMMAND_EXIT', 'COMMAND_OUTPUT_LIMIT', 'COMMAND_START']);
const TOOLS = new Set(['powershell', '7zip']);

// Exceptions can embed tokens, personal paths, and debug websocket URLs. Never return messages/stacks/causes.
export function safeFailure(error) {
  const result = { errorClass: CLASSES.has(error?.name) ? error.name : 'Error' };
  if (CODES.has(error?.code)) result.errorCode = error.code;
  if (TOOLS.has(error?.tool)) result.tool = error.tool;
  if (Number.isInteger(error?.exitCode)) result.exitCode = error.exitCode;
  return result;
}
export function commandFailure(code, tool, exitCode) {
  const error = new Error('validation subprocess failed');
  error.code = CODES.has(code) ? code : 'COMMAND_START';
  error.tool = TOOLS.has(tool) ? tool : undefined;
  if (Number.isInteger(exitCode)) error.exitCode = exitCode;
  return error;
}

const SECURITY_EXPECTATIONS = Object.freeze({ packaged: true, nodeIntegration: false,
  contextIsolation: true, sandbox: true, webSecurity: true, devToolsOpened: false });
const SECURITY_SWITCHES = Object.freeze(['no-sandbox', 'disable-web-security', 'disable-site-isolation-trials',
  'allow-running-insecure-content', 'ignore-certificate-errors']);
function diagnosticBoolean(value) {
  if (typeof value === 'boolean') return value;
  if (value === undefined) return 'undefined';
  if (value === null) return 'null';
  return 'invalid-type';
}
export function securityDiagnostics(security) {
  const observed = {};
  const failedExpectations = [];
  for (const [field, expected] of Object.entries(SECURITY_EXPECTATIONS)) {
    observed[field] = diagnosticBoolean(security?.[field]);
    if (security?.[field] !== expected) failedExpectations.push(field);
  }
  // Electron 44.5.1 omits devTools from getLastWebPreferences; keep that getter diagnostic-only.
  observed.devToolsPreference = diagnosticBoolean(security?.devTools);
  if (security?.devTools !== undefined && security.devTools !== false) failedExpectations.push('devToolsPreference');
  observed.devToolsProbe = {};
  for (const field of ['beforeOpen', 'beforeContents', 'openedEvent', 'afterOpen', 'afterContents']) {
    observed.devToolsProbe[field] = diagnosticBoolean(security?.devToolsProbe?.[field]);
    if (security?.devToolsProbe?.[field] !== false) failedExpectations.push(`devToolsProbe.${field}`);
  }
  const switches = security?.unsafeSwitches;
  observed.unsafeSwitches = {
    type: Array.isArray(switches) ? 'array' : diagnosticBoolean(switches),
    present: SECURITY_SWITCHES.filter((name) => Array.isArray(switches) && switches.includes(name)),
    unexpectedEntries: Array.isArray(switches) && switches.some((name) => !SECURITY_SWITCHES.includes(name)),
  };
  if (!Array.isArray(switches) || switches.length !== 0) failedExpectations.push('unsafeSwitches');
  return { observed, failedExpectations };
}
export async function recordSecurityBeforeValidation(launch, security, persist) {
  launch.security = securityDiagnostics(security);
  launch.securityValidation = 'pending';
  await persist();
}
