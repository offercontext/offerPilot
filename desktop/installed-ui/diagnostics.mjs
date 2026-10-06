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
