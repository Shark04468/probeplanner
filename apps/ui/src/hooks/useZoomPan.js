/**
 * useZoomPan — Hook for SVG zoom/pan interaction.
 * Provides scroll-to-zoom, drag-to-pan, and double-click-to-reset.
 */

import { useState, useCallback, useRef, useEffect } from "react";

export function useZoomPan(w, h) {
  const [vb, setVb]   = useState([0, 0, w, h]);
  const svgRef         = useRef(null);
  const drag           = useRef(null);
  const vbRef          = useRef(vb);

  useEffect(() => { vbRef.current = vb; }, [vb]);

  const reset = useCallback(() => setVb([0, 0, w, h]), [w, h]);

  const onWheel = useCallback((e) => {
    e.preventDefault();
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    setVb(([x, y, cw, ch]) => {
      const mx = x + (e.clientX - rect.left) * cw / rect.width;
      const my = y + (e.clientY - rect.top)  * ch / rect.height;
      const f  = e.deltaY > 0 ? 1.2 : 1 / 1.2;
      const nw = Math.min(w * 6, Math.max(w * 0.07, cw * f));
      const nh = nw * h / w;
      return [mx - (mx - x) * nw / cw, my - (my - y) * nh / ch, nw, nh];
    });
  }, [w, h]);

  const onMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const [x, y, cw, ch] = vbRef.current;
    drag.current = {
      sx: x + (e.clientX - rect.left) * cw / rect.width,
      sy: y + (e.clientY - rect.top)  * ch / rect.height,
      vb: [x, y, cw, ch],
    };
    e.preventDefault();
  }, []);

  const onMouseMove = useCallback((e) => {
    if (!drag.current) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const [ox, oy, ow, oh] = drag.current.vb;
    const cx = ox + (e.clientX - rect.left) * ow / rect.width;
    const cy = oy + (e.clientY - rect.top)  * oh / rect.height;
    setVb([ox + (drag.current.sx - cx), oy + (drag.current.sy - cy), ow, oh]);
  }, []);

  const onMouseUp = useCallback(() => { drag.current = null; }, []);

  const onDoubleClick = useCallback(() => reset(), [reset]);

  useEffect(() => {
    const up = () => { drag.current = null; };
    window.addEventListener('mouseup', up);
    return () => window.removeEventListener('mouseup', up);
  }, []);

  const viewBox = vb.map(n => n.toFixed(1)).join(' ');

  return {
    svgRef,
    viewBox,
    onWheel,
    onMouseDown,
    onMouseMove,
    onMouseUp,
    onDoubleClick,
    reset,
    style: { cursor: 'grab', userSelect: 'none' },
  };
}
