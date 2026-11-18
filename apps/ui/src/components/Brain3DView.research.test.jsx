import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Brain3DView from './Brain3DView';

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('identifies research MRI and disables unavailable surfaces', () => {
  render(<Brain3DView lang="en" backendStatus="online" activeAtlasMode="mri_ccf_hybrid"
    mriLayerStatus={{ researchOnly: true }}
    mriSurfaceMeshes={{ ready: false, missingLayers: ['brain_surface', 'outer_tissue', 'skull'], layers: {} }} />);
  expect(screen.getByText('MRI research preview')).toBeInTheDocument();
  expect(screen.queryByText('MRI profile')).not.toBeInTheDocument();
  for (const name of ['surface', 'outer', 'skull']) {
    expect(screen.getByTestId(`brain3d-mri-layer-${name}`)).toBeDisabled();
  }
  expect(screen.getByTestId('brain3d-mri-overlay')).not.toBeDisabled();
});
