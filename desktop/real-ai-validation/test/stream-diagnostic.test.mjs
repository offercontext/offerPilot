import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { installStreamObservation, readStreamDiagnostic, removeStreamObservation } from '../ui-scenarios.mjs';
import { sanitizeUiDiagnostic, createUiDiagnostic } from '../ui-diagnostics.mjs';

// Exercise the exact serialized observer against synthetic DOM mutations.
// No UI/Pilot state is written and these tests do not certify Windows E2E.
function fixture() {
  const observers = [];
  const node = () => ({ isConnected: true, text: '', stop: false,
    querySelectorAll() { return this.text ? [{ textContent: this.text }] : []; },
    querySelector() { return this.stop ? {} : null; } });
  const original = node(); let current = original;
  const document = { documentElement: {}, querySelector: () => current,
    querySelectorAll: () => current ? [current] : [] };
  const window = {};
  class MutationObserver {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; }
  }
  const context = vm.createContext({ window, document, MutationObserver });
  const page = { async evaluate(fn) { return vm.runInContext(`(${fn.toString()})()`, context); } };
  return { page, original, window, observers,
    mutate(text, stop = false) { current.text = text; current.stop = stop;
      if (current === original) observers[0].callback(); observers[1].callback(); },
    replace() { original.isConnected = false; current = node(); observers[1].callback(); return current; },
  };
}

test('diagnostic records actual growth separately from growth rendered with Stop', async () => {
  const f = fixture(); await installStreamObservation(f.page);
  f.mutate('synthetic first', true); f.mutate('synthetic first second', true);
  const d = await readStreamDiagnostic(f.page);
  assert.deepEqual(d, { installed: true, originalTargetConnected: true, originalTargetCurrent: true,
    currentPilotUnique: true, targetReplacementObserved: false, stopPresentNow: true, readFailed: false,
    originalGrowthCount: 2, originalGrowthWithStopCount: 2, currentGrowthCount: 2, currentGrowthWithStopCount: 2 });
  assert.equal(f.window.__offerpilotBoundedUiObserver.updates, 2);
  assert.doesNotMatch(JSON.stringify(d), /synthetic|length|text/);
  await removeStreamObservation(f.page);
  assert.ok(f.observers.every(value => value.disconnected));
});

test('replacement root is diagnosed but never feeds the existing stream pass counter', async () => {
  const f = fixture(); await installStreamObservation(f.page); f.replace();
  f.mutate('first', true); f.mutate('first second', true);
  const d = await readStreamDiagnostic(f.page);
  assert.equal(d.originalTargetConnected, false); assert.equal(d.originalTargetCurrent, false);
  assert.equal(d.targetReplacementObserved, true); assert.equal(d.currentPilotUnique, true);
  assert.equal(d.originalGrowthCount, 0); assert.equal(d.currentGrowthCount, 2);
  assert.equal(d.currentGrowthWithStopCount, 2); assert.equal(f.window.__offerpilotBoundedUiObserver.updates, 0);
});

test('Stop appearing after aggregated text never backfills streaming evidence', async () => {
  const f = fixture(); await installStreamObservation(f.page);
  f.mutate('whole reply', false); f.mutate('whole reply', true);
  const d = await readStreamDiagnostic(f.page);
  assert.equal(d.originalGrowthCount, 1); assert.equal(d.currentGrowthCount, 1);
  assert.equal(d.originalGrowthWithStopCount, 0); assert.equal(d.currentGrowthWithStopCount, 0);
  assert.equal(d.stopPresentNow, true); assert.equal(f.window.__offerpilotBoundedUiObserver.updates, 0);
});

test('diagnostic counters saturate at a fixed bound while the original assertion stays unchanged', async () => {
  const f = fixture(); await installStreamObservation(f.page);
  for (let i = 1; i <= 300; i++) f.mutate('x'.repeat(i), true);
  const d = await readStreamDiagnostic(f.page);
  for (const key of ['originalGrowthCount', 'originalGrowthWithStopCount', 'currentGrowthCount', 'currentGrowthWithStopCount']) assert.equal(d[key], 255);
  assert.equal(f.window.__offerpilotBoundedUiObserver.updates, 300);
});

test('fixed diagnostic projection drops raw values, arbitrary keys and unbounded counts', async () => {
  const d = sanitizeUiDiagnostic({ stage: 'PILOT_STREAM_READBACK', stream: {
    originalTargetCurrent: 'true', installed: true, currentGrowthCount: 256,
    originalGrowthCount: 3, privatePrompt: 'private-input', textLength: 99, anotherCounter: 5,
  } });
  assert.equal(d.stream.installed, true); assert.equal(d.stream.originalTargetCurrent, false);
  assert.equal(d.stream.currentGrowthCount, 0); assert.equal(d.stream.originalGrowthCount, 3);
  assert.doesNotMatch(JSON.stringify(d), /private|textLength|anotherCounter/);
  const diagnostic = createUiDiagnostic(); diagnostic.stream(d.stream);
  assert.deepEqual((await diagnostic.snapshot()).stream, d.stream);
  diagnostic.mark('CASE_START'); assert.equal((await diagnostic.snapshot()).stream, undefined);
});

test('failed or stalled diagnostic read is bounded and never leaks raw errors', async t => {
  const failed = await readStreamDiagnostic({ async evaluate() { throw new Error('private-url-token'); } });
  assert.equal(failed.readFailed, true); assert.doesNotMatch(JSON.stringify(failed), /private/);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = readStreamDiagnostic({ evaluate: () => new Promise(() => {}) });
  t.mock.timers.tick(5001);
  assert.equal((await pending).readFailed, true);
});
