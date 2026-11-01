import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import ResultsPanel from './ResultsPanel';
import ProbeSpecPanel from './ProbeSpecPanel';
import RegionSelector from './RegionSelector';

function results(expertAcceptanceRate = null, audit = { claimStatus: 'unavailable' }, thresholdClass = 'entry_corridor_boundary') {
  return {
    algorithm: 'greedy', coverage: { covered: 1, total: 1 },
    primaryEndpointSummary: { expertAcceptanceRate, safetyPassProbability: 1, continuousMinSafetyDistanceMm: null },
    stratifiedCalibration: { global: { count: 1, ece: 0, brier: 0 },
      dimensions: [{ dimension: 'brain_region', sampleCount: 1, groupCount: 1, eceWeighted: null, brierWeighted: null, calibrationSlopeWeighted: null }] },
    probes: [{ regionsHit: ['CA1'], internalRiskAudit: audit,
      statisticalSafety: { pSafetyGtThreshold: 1, thresholdMm: 0, thresholdClass },
      minimumInternalRiskMarginMm: null }],
  };
}

describe('Evidence availability in actual result components', () => {
  test.each([null, undefined, '', ' ', false, true, NaN, Infinity])('does not convert absent/non-numeric expert evidence (%s) to zero', (value) => {
    render(<ResultsPanel lang="en" results={results(value)} />);
    expect(screen.getByText('Expert acceptance rate').parentElement).toHaveTextContent('Unavailable');
    expect(within(screen.getByTestId('primary-endpoint-summary')).getByText('Available-layer distance').parentElement).toHaveTextContent('Unavailable');
  });

  test('preserves measured zero expert acceptance', () => {
    render(<ResultsPanel lang="en" results={results(0)} />);
    expect(screen.getByText('Expert acceptance rate').parentElement).toHaveTextContent('0.0%');
  });

  test('does not present boundary-only probability as internal-risk evidence', () => {
    render(<ResultsPanel lang="en" results={results()} />);
    fireEvent.click(screen.getByRole('button', { name: /Probe.*1/i }));
    const labels = screen.getAllByText('Available-layer model probability');
    expect(labels.length).toBeGreaterThanOrEqual(2);
    for (const label of labels) expect(label.parentElement).toHaveTextContent('Unavailable');
    expect(screen.queryByText('Safety probability')).not.toBeInTheDocument();
    expect(screen.getByText('Internal model consistency')).toBeInTheDocument();
    expect(screen.getByText('Same-plan threshold outcomes; not independent validation.')).toBeInTheDocument();
  });

  test('retains a supported model probability without inventing missing confidence bounds', () => {
    const audit = { claimStatus: 'partial', availableClasses: ['vessel'], classes: { vessel: { available: true, distanceMm: 0.5 } } };
    render(<ResultsPanel lang="en" results={results(null, audit, 'vessel')} />);
    fireEvent.click(screen.getByRole('button', { name: /Probe.*1/i }));
    const detail = screen.getAllByText('Available-layer model probability').at(-1).parentElement;
    expect(detail).toHaveTextContent('100.0%');
    expect(detail).not.toHaveTextContent('[0.0, 0.0]');
  });

  test('null dimension diagnostics stay unavailable', () => {
    render(<ResultsPanel lang="en" results={results()} />);
    const row = screen.getByText('brain_region').parentElement;
    expect(row.textContent).not.toContain('0.000');
  });

  test('a legacy weighted zero without any estimable group slope is not an estimated slope', () => {
    const value = results();
    value.stratifiedCalibration.dimensions[0].calibrationSlopeWeighted = 0;
    value.stratifiedCalibration.groups = [{ dimension: 'brain_region', label: 'CA1', count: 1, calibrationSlope: null }];
    render(<ResultsPanel lang="en" results={value} />);
    expect(screen.getByText('brain_region').parentElement.lastElementChild).toHaveTextContent('--');
  });

  test('advanced content uses shrinkable tracks instead of clipping mobile measurements', () => {
    render(<ResultsPanel lang="en" results={results()} />);
    const summary = screen.getByTestId('primary-endpoint-summary');
    expect(summary).toHaveStyle({ minWidth: '0', gridTemplateColumns: 'minmax(0, 1fr)' });
    expect(summary.parentElement).toHaveStyle({ gridTemplateColumns: 'minmax(0, 1fr)' });
  });
});

describe('Configuration and virtual list geometry', () => {
  test.each([
    ['NP2.0-1sh', { shanks: 1 }, '0 um'],
    ['NP2.0-4sh', { shanks: 4 }, '250 um'],
    ['custom', { shanks: 3, shankPitchUm: 180 }, '180 um'],
  ])('shows resolved hardware pitch for %s', (probePreset, params, pitch) => {
    render(<ProbeSpecPanel lang="en" probePreset={probePreset}
      probeParams={{ depthUm: 10000, entrySpacingUm: 500, ...params }}
      setProbePreset={vi.fn()} setProbeParams={vi.fn()} />);
    expect(screen.getByText('Shank pitch').parentElement).toHaveTextContent(pitch);
    expect(screen.queryByText(/x 250um/)).not.toBeInTheDocument();
  });

  test('bounds virtual rows and headers while retaining full accessible names and selection', () => {
    const toggle = vi.fn();
    const name = 'A deliberately long anatomical region name for narrow sidebars';
    render(<RegionSelector lang="en" selectedRegions={new Set(['TEST'])} onToggleRegion={toggle}
      regionCatalog={{ TEST: { name, groupKey: 'CTX', groupName: 'Long anatomical grouping' } }} />);
    const row = screen.getByRole('button', { name: new RegExp(name) });
    expect(row).toHaveAttribute('title', expect.stringContaining(name));
    expect(row).toHaveStyle({ height: '58px', boxSizing: 'border-box' });
    expect(row.querySelector('[data-region-subtitle]')).toHaveStyle({ whiteSpace: 'nowrap', overflow: 'hidden' });
    for (const header of screen.getAllByTestId(/^region-section-/)) {
      expect(header).toHaveStyle({ height: '30px', boxSizing: 'border-box' });
    }
    fireEvent.click(row);
    expect(toggle).toHaveBeenCalledWith('TEST');
  });
});
