// Exercise the same market search and installBundle service used by the DSH UI.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

export const name = 'offerpilot-market-install-probe';
export const inject = ['pluginMarket', 'pluginManager', 'tools'];

export function apply(ctx, config) {
  setTimeout(async () => {
    const report = { passed: false, real_model_calls: 0 };
    try {
      for (const text of ['OfferPilot', '求职']) {
        const page = await ctx.pluginMarket.search(config.index, {
          text, offset: 0, limit: 10, sort: { kind: 'relevance' }, refresh: true,
        });
        const plugin = page.entries.find(item => item.name === 'dsh-offerpilot');
        assert.ok(plugin, `search failed: ${text}`);
        const version = plugin.versions.find(item => item.version === '0.1.0');
        assert.equal(version.source.commit, config.commit);
        assert.equal(version.source.path, 'plugins/dsh-offerpilot');
        assert.equal(version.compatibility.compatible, true);
        report.spec = version.install.spec;
      }
      assert.ok(report.spec.endsWith(`#${config.commit}&path:/plugins/dsh-offerpilot`));
      // Do not reconstruct a command string or pass the spec through a .cmd shim.
      report.install = await ctx.pluginManager.installBundle(report.spec, { enabled: true });
      assert.equal(report.install.packageResult?.exitCode, 0);
      assert.equal(report.install.bundle, 'dsh-offerpilot');
      assert.equal(report.install.version, '0.1.0');
      assert.equal(report.install.error, undefined, JSON.stringify(report.install.error));
      assert.ok(['applied', 'restart-required'].includes(report.install.application));
      let names = [];
      for (let i = 0; i < 600; i++) {
        names = ctx.tools.schemas().map(tool => tool.function?.name ?? tool.name)
          .filter(name => name.startsWith('mcp__offerpilot__'));
        if (names.length) break;
        await delay(100);
      }
      assert.ok(names.includes('mcp__offerpilot__list_applications'));
      assert.ok(!names.some(name => /approve|confirm/.test(name)));
      for (const name of ['list_applications', 'list_events']) {
        const result = await ctx.tools.execute({ callId: randomUUID(),
          name: `mcp__offerpilot__${name}`, arguments: {}, signal: AbortSignal.timeout(30000) });
        assert.ok(!result.isError, JSON.stringify(result));
        assert.equal(result.value.structuredContent.ok, true);
        assert.ok(Array.isArray(result.value.structuredContent.data.items));
      }
      report.tool_count = names.length;
      report.read_calls = ['list_applications', 'list_events'];
      report.passed = true;
    } catch (error) {
      report.error = String(error.stack ?? error);
    } finally {
      await ctx.root.fiber.dispose();
      report.disposed = true;
      await writeFile(config.report, JSON.stringify(report, null, 2), { flag: 'wx' });
      process.exitCode = report.passed ? 0 : 1;
    }
  }, 0);
}
