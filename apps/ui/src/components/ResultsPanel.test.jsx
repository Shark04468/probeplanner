import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import ResultsPanel from './ResultsPanel';

const auditedProbe = {
  ap: 0.4,
  ml: 1.1,
  dvEntry: 0.44,
  azimuth: 2,
  elevation: 85,
  depthUm: 4125,
  entry_xyz: [5000, 440, 6800],
  tip_xyz: [5200, 4500, 6900],
  regionsHit: ['CA1'],
  brainContainmentPassed: true,
  tipInTarget: true,
  distalOvershootMm: 0,
  internalRiskViolationCount: 0,
  minimumInternalRiskMarginMm: null,
  entrySurfaceCrossingStatus: 'allowed_entry_surface_semantics',
  internalRiskAudit: { claimStatus: 'unavailable' },
  objectiveScores: {},
};

describe('ResultsPanel strict planning contract', () => {
  test('shows selected depth and does not convert unavailable internal-risk evidence into zero margin', () => {
    render(
      <ResultsPanel
        lang="en"
        probeSpec={{ depth: 7.6 }}
        selectedRegions={['CA1']}
        results={{
          probes: [auditedProbe],
          selected: [auditedProbe],
          requestedTargets: ['CA1'],
          uncovered: [],
          coverage: { covered: 1, total: 1, byRegion: { CA1: { frac: 1 } } },
          warnings: [],
          algorithm: 'greedy',
          elapsedMs: 12,
          stabilitySummary: null,
        }}
      />,
    );

    expect(screen.getByText('Feasible plan')).toBeInTheDocument();
    expect(screen.getByText('Review required')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Probe.*1/i }));
    expect(screen.getByText('4.125 mm')).toBeInTheDocument();
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
    expect(screen.getByText('Minimum internal-risk margin').parentElement).toHaveTextContent('Unavailable');
  });

  test('renders uncovered targets as fail-closed when no probe is selected', () => {
    render(
      <ResultsPanel
        lang="en"
        selectedRegions={['DG']}
        results={{
          probes: [],
          selected: [],
          requestedTargets: ['DG'],
          uncovered: ['DG'],
          coverage: { covered: 0, total: 1, byRegion: { DG: { frac: 0 } } },
          warnings: [],
          algorithm: 'greedy',
          elapsedMs: 8,
        }}
      />,
    );

    expect(screen.getByText('Fail-closed: no valid trajectory')).toBeInTheDocument();
    expect(screen.getByText(/Uncovered targets: DG/)).toBeInTheDocument();
  });
});
