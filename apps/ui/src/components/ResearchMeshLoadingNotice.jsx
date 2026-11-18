import { useEffect, useId, useRef, useState } from 'react';
import './ResearchMeshLoadingNotice.css';

export default function ResearchMeshLoadingNotice({ policy, tr }) {
  const dialog = useRef(null);
  const titleId = useId();
  const bodyId = useId();
  const [dismissed, setDismissed] = useState(false);
  const show = !dismissed && policy?.mode === 'serial' && policy.reason === 'low_memory';

  useEffect(() => {
    if (show && !dialog.current.open) dialog.current.showModal();
  }, [show]);

  if (!show) return null;
  return (
    <dialog ref={dialog} className="pp-memory-notice" role="alertdialog"
      aria-labelledby={titleId} aria-describedby={bodyId}
      onClose={() => setDismissed(true)}>
      <h2 id={titleId}>{tr('researchLowMemoryTitle')}</h2>
      <p id={bodyId}>{tr('researchLowMemoryMessage')}</p>
      <footer>
        <button type="button" autoFocus onClick={() => dialog.current.close()}>
          {tr('researchLowMemoryContinue')}
        </button>
      </footer>
    </dialog>
  );
}
