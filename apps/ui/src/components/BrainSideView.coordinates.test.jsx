import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BrainSideView from './BrainSideView';

const props = { selectedRegions: new Set(), lang: 'en', hoveredProbe: null,
  onHoverProbe: vi.fn(), showUncovered: false, showAllRegions: false, sagML: 0, onSagMLChange: vi.fn() };

describe('side-view physical trajectory coordinates', () => {
  it.each([false, true])('renders the same posterior-going segment with endpoints=%s', endpoints => {
    const probe = { ap_mm: 3, ml_mm: 3, entryDvCorr_mm: 3, depthUm: 1000,
      ap_mm_corrected: -2, ml_mm_corrected: 0, entryDvCorr_mm_corrected: .5, depthUm_corrected: 2000,
      theta_deg: 30, phi_deg: 0 };
    if (endpoints) Object.assign(probe, { entry_xyz: [7400, 940, 5700], tip_xyz: [8400, 940+1000*Math.sqrt(3), 5700] });
    const { container } = render(<BrainSideView {...props} results={{ probes: [probe] }} />);
    const line = [...container.querySelectorAll('line')].find(item => item.getAttribute('stroke-linecap') === 'round');
    expect(line).toBeDefined();
    expect(Number(line.getAttribute('x1'))).toBeCloseTo(272, 8);
    expect(Number(line.getAttribute('x2'))).toBeCloseTo(298, 8);
    expect(Number(line.getAttribute('y1'))).toBeCloseTo(44, 8);
    expect(Number(line.getAttribute('y2'))).toBeCloseTo(44+24*Math.sqrt(3), 8);
  });
});
