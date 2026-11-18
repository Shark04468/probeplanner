import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { t } from '../constants/i18n';
import ResearchMeshLoadingNotice from './ResearchMeshLoadingNotice';

const tr = (key) => t(key, 'en');
const lowMemory = { mode: 'serial', reason: 'low_memory' };
const dialogPrototype = HTMLDialogElement.prototype;
const stubDialog = typeof dialogPrototype.showModal !== 'function';

beforeAll(() => {
  if (!stubDialog) return;
  dialogPrototype.showModal = function () { this.open = true; };
  dialogPrototype.close = function () {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
});
afterEach(cleanup);
afterAll(() => {
  if (!stubDialog) return;
  delete dialogPrototype.showModal;
  delete dialogPrototype.close;
});

it.each([
  null,
  { mode: 'parallel', reason: 'memory_sufficient' },
  { mode: 'serial', reason: 'memory_unavailable' },
])('does not claim low memory for %j', (policy) => {
  render(<ResearchMeshLoadingNotice policy={policy} tr={tr} />);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});

it('explains the real fallback and does not reopen after status refresh', () => {
  const { rerender } = render(<ResearchMeshLoadingNotice policy={lowMemory} tr={tr} />);
  const notice = screen.getByRole('alertdialog', { name: 'Available memory is low' });
  expect(notice).toHaveAttribute('open');
  expect(notice).toHaveAccessibleDescription(expect.stringContaining('from parallel to serial'));
  fireEvent.click(screen.getByRole('button', { name: 'Continue loading' }));
  rerender(<ResearchMeshLoadingNotice policy={{ ...lowMemory }} tr={tr} />);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});
