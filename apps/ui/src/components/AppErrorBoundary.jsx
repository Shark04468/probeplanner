import React from 'react';

const CARD = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: '#060e1c',
  color: '#d8eefa',
  padding: 24,
  boxSizing: 'border-box',
};

const PANEL = {
  width: 'min(760px, 96vw)',
  border: '1px solid #23476b',
  background: '#08121f',
  borderRadius: 12,
  padding: 20,
  boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
};

const BTN = {
  border: '1px solid #1f4c72',
  background: '#0a1a2c',
  color: '#7dd3fc',
  borderRadius: 8,
  padding: '8px 12px',
  cursor: 'pointer',
  marginRight: 10,
};

function clearProbePlannerStorage() {
  if (typeof window === 'undefined') return;
  try {
    const keys = Object.keys(window.localStorage || {});
    keys.forEach((k) => {
      if (k.toLowerCase().includes('probeplanner') || k.toLowerCase().includes('probeplanner')) {
        window.localStorage.removeItem(k);
      }
    });
  } catch {
    // ignore
  }
}

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      message: String(error?.message || error || 'Unknown runtime error'),
    };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[ProbePlanner][ErrorBoundary]', error, info);
  }

  handleRetry = () => {
    this.setState({ hasError: false, message: '' });
    if (typeof window !== 'undefined') {
      window.location.reload();
    }
  };

  handleResetStorage = () => {
    clearProbePlannerStorage();
    this.handleRetry();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div style={CARD}>
        <div style={PANEL}>
          <h2 style={{ marginTop: 0, color: '#67e8f9' }}>页面加载失败（已拦截白屏）</h2>
          <p style={{ marginBottom: 8 }}>
            检测到前端运行时异常，已阻止白屏。你可以直接重试，或清理本地缓存后重载。
          </p>
          <p style={{ color: '#94a3b8', marginTop: 0, wordBreak: 'break-word' }}>
            错误信息：{this.state.message}
          </p>
          <div style={{ marginTop: 12 }}>
            <button type="button" style={BTN} onClick={this.handleRetry}>重试加载</button>
            <button type="button" style={BTN} onClick={this.handleResetStorage}>清理缓存并重载</button>
          </div>
        </div>
      </div>
    );
  }
}

