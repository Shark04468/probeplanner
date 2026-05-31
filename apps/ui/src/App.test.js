import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';

test('renders probe planner shell and toggles language', async () => {
  render(<App />);

  expect(screen.getByText(/多探针脑区轨迹规划系统/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'EN' }));
  expect(screen.getByText(/Multi-Probe BCI Trajectory Planner/)).toBeInTheDocument();
});
