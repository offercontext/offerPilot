import z from '@deepseek-ai/schemastery';
import * as mcpClient from '@deepseek-ai/dsh-mcp-client';
import { loadConnection } from './connection.js';

export const name = 'offerpilot';
export const inject = ['tools'];
export const Config = z.object({
  configFile: z.string().default('').description('Absolute path to the OfferPilot MCP JSON export; empty uses OFFERPILOT_MCP_CONFIG.'),
});

export async function apply(ctx, config) {
  const connection = await loadConnection(config.configFile || process.env.OFFERPILOT_MCP_CONFIG);
  // Official bridge owns discovery, naming, subprocess cleanup and reconnect.
  // No grants, database writes, approval tools or model prompts are added here.
  await ctx.plugin(mcpClient, connection);
}
