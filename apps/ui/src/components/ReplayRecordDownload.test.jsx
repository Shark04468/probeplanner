import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { flaskFetch } from '../hooks/useBackend';
import ReplayRecordDownload from './ReplayRecordDownload';

vi.mock('../hooks/useBackend', () => ({ flaskFetch: vi.fn() }));
const result = { requestId: 'saved1', requestHash: 'a'.repeat(64), responseHash: 'b'.repeat(64) };
const record = { ...result, request: { targetKeys: ['CA1'] }, response: { ...result, selected: [] } };
const tr = key => key;

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); flaskFetch.mockReset(); });

it('downloads the saved record without changing the payload', async () => {
  const raw = JSON.stringify({ ...record, response: { ...record.response, stability: 0 } }, null, 2)
    .replace('"stability": 0', '"stability": -0.0') + '\n';
  flaskFetch.mockResolvedValue(raw);
  const objectURL = vi.fn(() => 'blob:test');
  class TestURL extends URL {
    static createObjectURL = objectURL;
    static revokeObjectURL() {}
  }
  vi.stubGlobal('URL', TestURL);
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    expect(this.download).toBe('probeplanner_replay_saved1.json');
  });
  render(<ReplayRecordDownload results={result} tr={tr} />);
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(flaskFetch).toHaveBeenCalledWith('/api/replay/saved1/download', 'GET', null, null, { responseType: 'text' });
  expect(objectURL.mock.calls[0][0].size).toBe(raw.length);
});

it('rejects a mismatch between the displayed result and the saved response', async () => {
  flaskFetch.mockResolvedValue(JSON.stringify({ ...record, response: { ...result, responseHash: 'c'.repeat(64) } }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click');
  render(<ReplayRecordDownload results={result} tr={tr} />);
  fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByRole('alert')).toHaveTextContent('replayRecordMismatch');
  expect(click).not.toHaveBeenCalled();
});

it('does not offer audit export for unverified imported geometry', () => {
  render(<ReplayRecordDownload results={{ ...result, validationStatus: 'imported_unverified' }} tr={tr} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
