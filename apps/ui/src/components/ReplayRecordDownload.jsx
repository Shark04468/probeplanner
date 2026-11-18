import { useState } from 'react';
import { flaskFetch } from '../hooks/useBackend';

const isSha256 = (s) => /^[0-9a-f]{64}$/.test(s || '');

export default function ReplayRecordDownload({ results, tr }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { requestId, requestHash, responseHash } = results;

  if (results.validationStatus === 'imported_unverified' ||
      !/^[A-Za-z0-9_-]{1,160}$/.test(requestId || '') ||
      !isSha256(requestHash) || !isSha256(responseHash)) return null;

  const download = async () => {
    setBusy(true);
    setError('');
    try {
      const raw = await flaskFetch(`/api/replay/${encodeURIComponent(requestId)}/download`, 'GET',
        null, null, { responseType: 'text' });
      const record = JSON.parse(raw);
      if (record?.requestId !== requestId || record.requestHash !== requestHash ||
          record.responseHash !== responseHash || record.response?.requestHash !== requestHash ||
          record.response?.responseHash !== responseHash || !record.request) {
        throw new Error(tr('replayRecordMismatch'));
      }
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `probeplanner_replay_${requestId}.json`;
      document.body.append(link);
      try {
        link.click();
      } finally {
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button type="button" onClick={download} disabled={busy}
        style={{ marginTop: 6, minHeight: 30 }}>
        {tr('downloadReplayRecord')}
      </button>
      {error && <div role="alert">{error}</div>}
    </div>
  );
}
