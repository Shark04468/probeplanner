import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';

const lifecycle = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));
vi.mock('./components/Brain3DView', async () => {
  const React = await import('react');
  return { default: () => {
    React.useEffect(() => { lifecycle.mounts++; return () => { lifecycle.unmounts++; }; }, []);
    return React.createElement('div', { 'data-testid': 'live-viewer' });
  } };
});
vi.mock('./components/PinpointIO', () => ({
  default: ({ onImport }) => <button onClick={() => onImport([
    { probeType: 'NP2.0-1sh', shanks: 1, depthUm: 4000, ap: 0, ml: 1 },
  ])}>Import recorded single-shank fixture</button>,
}));
afterEach(cleanup);

it('retains the initialized viewer while inspecting results and history', async () => {
  render(<App />);
  await userEvent.click(screen.getByRole('button', { name: 'EN', exact: true }));
  await userEvent.click(screen.getByTestId('probeplanner-toggle-3d'));
  const viewer = screen.getByTestId('live-viewer');
  expect(lifecycle.mounts).toBe(1);
  await userEvent.click(screen.getByRole('button', { name: /^R\s*Results$/ }));
  expect(viewer).not.toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: /^H\s*History$/ }));
  expect(lifecycle.unmounts).toBe(0);
  await userEvent.click(screen.getByRole('button', { name: /^V\s*Regions$/ }));
  expect(screen.getByTestId('live-viewer')).toBe(viewer);
  expect(viewer).toBeVisible();
  expect(lifecycle.mounts).toBe(1);
});

it('does not advertise unmeasured algorithm latency', async () => {
  render(<App />);
  await userEvent.click(screen.getByRole('button', { name: 'EN', exact: true }));
  expect(screen.queryByText(/~0\.1-0\.8s/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Expected.*0\.1-0\.8s/)).not.toBeInTheDocument();
});

it('shows recorded result hardware in the view header without changing run configuration', async () => {
  render(<App />);
  await userEvent.click(screen.getByRole('button', { name: 'EN', exact: true }));
  await userEvent.click(screen.getByRole('button', { name: /^I\s*I\/O$/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Import recorded single-shank fixture', exact: true }));
  await userEvent.click(screen.getByRole('button', { name: /^V\s*Regions$/ }));
  expect(screen.getByTestId('probeplanner-toggle-3d')).toHaveTextContent('NP2.0-1sh x1');
  expect(screen.getByTestId('probeplanner-toggle-3d')).not.toHaveTextContent('NP2.0-4sh');
  expect(screen.getByRole('option', { name: /NP 2.0.*4-shank/ }).selected).toBe(true);
});
