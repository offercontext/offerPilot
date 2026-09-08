import { describe, expect, it } from 'vitest';
import appShell from '@/layout/AppShell.tsx?raw';
import settings from '@/components/SettingsView.tsx?raw';

describe('AppShell Pilot mascot contract', () => {
  it('replaces only the idle desktop rail and restores it when hidden', () => {
    expect(appShell).toContain('readPilotMascotVisible');
    expect(appShell).toContain('<PilotMascot');
    expect(appShell).toContain('!pilotMascotVisible');
    expect(appShell).toContain('setPilotMascotPreference(false)');
    expect(appShell).not.toContain('if (!visible) setPilotDrawerOpen(false)');
  });

  it('offers a Settings recovery toggle', () => {
    expect(settings).toContain('显示 Haru');
    expect(settings).toContain('pilotMascotVisible');
    expect(settings).toContain('onPilotMascotVisibleChange');
  });
});
