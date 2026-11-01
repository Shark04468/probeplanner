import React from 'react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HistoryPanel from './HistoryPanel';

afterEach(() => { cleanup(); localStorage.clear(); });

it('preserves the requested probe model and actual result in saved history', () => {
  const results = { probes: [{ ap_mm: -2.727, depthUm: 5325 }], requestId: 'real-request' };
  const probeParams = { depthUm: 6000, shanks: 1 };
  const correction = { apScale: 1.01 };
  const safetyIssues = [{ msgEn: 'Risk masks unavailable' }];
  render(<HistoryPanel lang="en" currentState={{ probePreset: 'NP2.0-1sh', probeParams, correction,
    safetyIssues, results, regions: ['CA1', 'CA3'] }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Save current plan' }));
  const [entry] = JSON.parse(localStorage.getItem('probePlannerHistory'));
  expect(entry.probePreset).toBe('NP2.0-1sh');
  expect(entry.results).toEqual(results);
  expect(entry.probeParams).toEqual(probeParams);
  expect(entry.correction).toEqual(correction);
  expect(entry.safetyIssues).toEqual(safetyIssues);
});

it('returns the saved result unchanged to the application restore handler', () => {
  const record = { id: 'old', savedAt: 1, label: 'Case', regions: ['CA1'], params: {},
    results: { probes: [{ ap_mm: -2.727, depthUm: 5325 }], requestId: 'saved-request' } };
  localStorage.setItem('probePlannerHistory', JSON.stringify([record]));
  const restore = vi.fn();
  render(<HistoryPanel lang="en" onRestore={restore} />);
  fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
  expect(restore).toHaveBeenCalledWith(record);
});
