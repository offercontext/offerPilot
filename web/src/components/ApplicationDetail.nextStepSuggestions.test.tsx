import { describe, expect, it } from 'vitest';
import applicationDetailSource from './ApplicationDetail.tsx?raw';

describe('ApplicationDetail next-step suggestion contract', () => {
  it('renders the shared read-only component from the supplied fact snapshot', () => {
    expect(applicationDetailSource).toContain('NextStepSuggestions');
    expect(applicationDetailSource).toContain('nextStepSuggestions');
    expect(applicationDetailSource).not.toContain('writeMaterialKitHandoff');
  });

  it('accepts application-scoped session and navigation callbacks', () => {
    expect(applicationDetailSource).toContain('nextStepSessionState');
    expect(applicationDetailSource).toContain('onSetDisposition');
    expect(applicationDetailSource).toContain('onNextStepNavigate');
    expect(applicationDetailSource).toContain('onNextStepReadonlyNavigate');
    expect(applicationDetailSource).toContain('isReadonlyNavigationAvailable');
  });

  it('does not add a material-kit action when its fact is unknown', () => {
    expect(applicationDetailSource).toContain('nextStepSuggestions &&');
    expect(applicationDetailSource).not.toContain('materialKit: { status: \'known\'' );
  });
});
