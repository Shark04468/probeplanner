import React, { useCallback, useEffect, useRef, useState } from 'react';

const HANDLE_WIDTH = 5;

export default function ResizableSidebar({
  children,
  defaultWidth = 220,
  minWidth = 140,
  maxWidth = 480,
  side = 'left', // 'left' | 'right'
  style = {},
  className = '',
}) {
  const [width, setWidth] = useState(defaultWidth);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const onMouseDown = useCallback((e) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startWidth.current = width;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  const onKeyDown = useCallback((event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') {
      setWidth(minWidth);
      return;
    }
    if (event.key === 'End') {
      setWidth(maxWidth);
      return;
    }
    const visualDelta = event.key === 'ArrowRight' ? 16 : -16;
    const widthDelta = side === 'left' ? visualDelta : -visualDelta;
    setWidth((current) => Math.max(minWidth, Math.min(maxWidth, current + widthDelta)));
  }, [maxWidth, minWidth, side]);

  useEffect(() => {
    const onMove = (e) => {
      if (!dragging.current) return;
      const delta = side === 'left'
        ? e.clientX - startX.current
        : startX.current - e.clientX;
      const next = Math.max(minWidth, Math.min(maxWidth, startWidth.current + delta));
      setWidth(next);
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [maxWidth, minWidth, side]);

  const handleStyle = {
    position: 'absolute',
    top: 0,
    bottom: 0,
    width: HANDLE_WIDTH + 2,
    cursor: 'col-resize',
    zIndex: 30,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'background 0.15s',
    ...(side === 'left'
      ? { right: -Math.floor(HANDLE_WIDTH / 2) - 1, borderRight: '1px solid transparent' }
      : { left: -Math.floor(HANDLE_WIDTH / 2) - 1, borderLeft: '1px solid transparent' }),
  };

  return (
    <div
      className={className}
      style={{
        position: 'relative',
        width,
        flexShrink: 0,
        overflow: 'hidden',
        minHeight: 0,
        ...style,
      }}
    >
      {children}
      <div
        style={handleStyle}
        onMouseDown={onMouseDown}
        onKeyDown={onKeyDown}
        role="separator"
        aria-orientation="vertical"
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        tabIndex={0}
      >
        <DragHandle />
      </div>
    </div>
  );
}

function DragHandle() {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 4,
        height: 40,
        borderRadius: 2,
        background: hovered ? '#00d4ff88' : '#162d4888',
        transition: 'background 0.15s, height 0.15s',
      }}
    />
  );
}
