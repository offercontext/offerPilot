import { open } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const invalid = () => new Error('OfferPilot: use an unmodified file from oc mcp config <connection_id> --format mcp.');

function exactKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}

// Accept only our existing CLI's connection export, not arbitrary MCP commands,
// environment overrides or an approval-capable CLI. The file is operator-owned
// configuration; this validation is not an OS sandbox or executable attestation.
export function validateConnection(value) {
  if (!exactKeys(value, ['mcpServers']) || !exactKeys(value.mcpServers, ['offerpilot'])) throw invalid();
  const server = value.mcpServers.offerpilot;
  if (!exactKeys(server, ['command', 'args'])
      || typeof server.command !== 'string' || !isAbsolute(server.command)
      || !Array.isArray(server.args) || server.args.length !== 6
      || server.args.some(arg => typeof arg !== 'string' || arg.includes('\0'))
      || server.command.includes('\0')) throw invalid();
  const [flag, module, dataFlag, dataDir, connectionFlag, connectionId] = server.args;
  if (flag !== '-m' || module !== 'offerpilot.external_agents.server'
      || dataFlag !== '--data-dir' || !isAbsolute(dataDir)
      || connectionFlag !== '--connection-id' || !UUID.test(connectionId)) throw invalid();
  return {
    serverName: 'offerpilot', transport: 'stdio',
    command: server.command, args: [...server.args],
    toolCallTimeoutMs: 15000, failOnStartupError: true,
  };
}

export async function loadConnection(configFile) {
  if (typeof configFile !== 'string' || !isAbsolute(configFile)) {
    throw new Error('OfferPilot: set configFile or OFFERPILOT_MCP_CONFIG to the absolute path of your oc mcp config export.');
  }
  const file = await open(configFile, 'r');
  try {
    // Bound the actual read as well as the reported size (file can change).
    const buffer = Buffer.alloc(65537);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > 65536) throw invalid();
    let value;
    try { value = JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')); }
    catch { throw invalid(); }
    return validateConnection(value);
  } finally {
    await file.close();
  }
}
