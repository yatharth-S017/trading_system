import React, { useState, useEffect, useRef } from 'react';
import './App.css';

function formatExpiry(iso) {
  if (!iso) return '---';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    // Format: "11 Nov 2026, 08:00 UTC"
    const day = d.getUTCDate().toString().padStart(2, '0');
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[d.getUTCMonth()];
    const year = d.getUTCFullYear();
    const hours = d.getUTCHours().toString().padStart(2, '0');
    const mins = d.getUTCMinutes().toString().padStart(2, '0');
    return `${day} ${month} ${year}, ${hours}:${mins} UTC`;
  } catch {
    return iso;
  }
}

function App() {
  const [state, setState] = useState({
    node_status: 'STARTING',
    is_connected: false,
    bybit_environment: 'TESTNET',
    execute_trades: false,
    is_dry_run: true,
    reference_price: null,
    reference_price_updated_at: null,
    total_quotes_received: 0,
    total_instruments_discovered: 0,
    total_expiries: 0,
    atm_selected: false,
    atm_strike: null,
    atm_expiry: null,
    atm_call_id: null,
    atm_put_id: null,
    straddle_status: 'NOT_STARTED',
    call_order_status: 'NOT_SUBMITTED',
    call_instrument_id: null,
    call_client_order_id: null,
    call_venue_order_id: null,
    call_submitted_qty: 0.0,
    call_filled_qty: 0.0,
    call_avg_fill_price: 0.0,
    call_limit_price: null,
    call_rejection_reason: null,
    put_order_status: 'NOT_SUBMITTED',
    put_instrument_id: null,
    put_client_order_id: null,
    put_venue_order_id: null,
    put_submitted_qty: 0.0,
    put_filled_qty: 0.0,
    put_avg_fill_price: 0.0,
    put_limit_price: null,
    put_rejection_reason: null,
    call_position_id: null,
    call_position_side: null,
    call_position_qty: 0.0,
    put_position_id: null,
    put_position_side: null,
    put_position_qty: 0.0,
    straddle_established: false,
    call_bid: null,
    call_ask: null,
    put_bid: null,
    put_ask: null,
  });

  const [wsConnected, setWsConnected] = useState(false);
  const [instruments, setInstruments] = useState([]);
  const [instrumentFilter, setInstrumentFilter] = useState('');
  const [executing, setExecuting] = useState(false);
  const [actionMessage, setActionMessage] = useState(null);

  const wsRef = useRef(null);

  const getBackendUrls = () => {
    const isDev = window.location.port === '3000';
    const httpBase = isDev ? 'http://localhost:8000' : '';
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost = isDev ? 'localhost:8000' : window.location.host;
    const wsUrl = `${wsProtocol}//${wsHost}/ws`;
    return { httpBase, wsUrl };
  };

  // ── Establish WebSocket connection ─────────────────────────────────────────
  useEffect(() => {
    let reconnectTimer = null;

    const connectWebSocket = () => {
      const { wsUrl } = getBackendUrls();
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'heartbeat') return;
          setState((prev) => ({ ...prev, ...data }));
        } catch (err) {
          console.error('Error parsing WS message:', err);
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        reconnectTimer = setTimeout(connectWebSocket, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connectWebSocket();

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // ── Poll REST fallback and fetch discovered instruments ───────────────────
  useEffect(() => {
    const fetchStatusAndInstruments = async () => {
      const { httpBase } = getBackendUrls();
      try {
        const res = await fetch(`${httpBase}/api/status`);
        if (res.ok) {
          const data = await res.json();
          setState((prev) => ({ ...prev, ...data }));
        }
      } catch (e) {
        // Backend connecting
      }

      try {
        const res = await fetch(`${httpBase}/api/instruments`);
        if (res.ok) {
          const data = await res.json();
          if (data.instruments && data.instruments.length > 0) {
            setInstruments(data.instruments);
          }
        }
      } catch (e) {
        // Backend connecting
      }
    };

    fetchStatusAndInstruments();
    const interval = setInterval(fetchStatusAndInstruments, 3000);
    return () => clearInterval(interval);
  }, []);

  // ── Handle SELL STRADDLE Button Click ─────────────────────────────────────
  const handleSellStraddle = async () => {
    if (!state.atm_selected) {
      alert('Cannot execute: ATM Straddle has not been identified yet.');
      return;
    }

    const confirmMsg = state.is_dry_run
      ? `Execute SIMULATED Short Straddle on ATM Strike $${state.atm_strike?.toLocaleString()}? (DRY-RUN mode)`
      : `CAUTION: Submit LIVE SELL orders on Bybit Testnet for Strike $${state.atm_strike?.toLocaleString()}?`;

    if (!window.confirm(confirmMsg)) return;

    setExecuting(true);
    setActionMessage(null);

    try {
      const { httpBase } = getBackendUrls();
      const res = await fetch(`${httpBase}/api/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (data.success) {
        setActionMessage({
          type: 'success',
          text: `Straddle order submitted (${data.is_dry_run ? 'SIMULATED DRY-RUN' : 'LIVE TESTNET'}). Strike: $${data.strike?.toLocaleString()}`,
        });
      } else {
        setActionMessage({
          type: 'error',
          text: `Execution failed: ${data.error}`,
        });
      }
    } catch (err) {
      setActionMessage({
        type: 'error',
        text: `Network error: ${err.message}`,
      });
    } finally {
      setExecuting(false);
    }
  };

  const filteredInstruments = instruments.filter((inst) => {
    if (!instrumentFilter) return true;
    const f = instrumentFilter.toLowerCase();
    return (
      inst.instrument_id?.toLowerCase().includes(f) ||
      inst.option_type?.toLowerCase().includes(f) ||
      String(inst.strike).includes(f)
    );
  });

  return (
    <div className="terminal-container">
      {/* ── Top Header ────────────────────────────────────────────────── */}
      <header className="terminal-header">
        <div className="header-left">
          <span className="brand-badge">NautilusTrader</span>
          <div>
            <h1 className="terminal-title">BTC ATM Short Straddle</h1>
            <p className="terminal-subtitle">Bybit Testnet Options Algorithmic System</p>
          </div>
        </div>

        <div className="header-right">
          <div className="status-chip">
            <span className={`status-dot ${wsConnected ? 'connected' : 'disconnected'}`}></span>
            <span>{wsConnected ? 'WS Live' : 'WS Offline'}</span>
          </div>

          <div className="status-chip">
            <span>Node: {state.node_status}</span>
          </div>

          <div className="status-chip">
            <span>{state.bybit_environment}</span>
          </div>

          <div className={`mode-badge ${state.is_dry_run ? 'dry-run' : 'live'}`}>
            {state.is_dry_run ? 'DRY-RUN MODE' : 'LIVE TESTNET'}
          </div>
        </div>
      </header>

      {/* ── Reference Price Bar ────────────────────────────────────────── */}
      <div className="price-strip">
        <div className="price-item">
          <span className="price-label">BTC Spot Reference:</span>
          <span className="price-value btc">
            {state.reference_price != null
              ? `$${state.reference_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : 'Streaming...'}
          </span>
          <span className="price-currency">USDT</span>
        </div>

        <div className="price-item">
          <span className="price-label">Quotes Streamed:</span>
          <span className="price-value-sm">
            {state.total_quotes_received.toLocaleString()}
          </span>
        </div>

        <div className="price-item">
          <span className="price-label">Discovered Contracts:</span>
          <span className="price-value-sm">
            {state.total_instruments_discovered || instruments.length} ({state.total_expiries || 10} expiries)
          </span>
        </div>

        <div className="price-item">
          <span className="price-label">Straddle State:</span>
          <span className={`status-badge ${state.straddle_status}`}>
            {state.straddle_status}
          </span>
        </div>
      </div>

      {/* ── Main 2x2 Grid ─────────────────────────────────────────────── */}
      <main className="main-grid">
        {/* TOP LEFT: ATM Strike Selection & Straddle Pair */}
        <section className="dashboard-card highlight">
          <div className="card-header">
            <h2 className="card-title">
              <span className="card-title-icon">⚡</span>
              ATM Strike Selection & Straddle Pair
            </h2>
            <span
              className={`status-badge ${state.atm_selected ? 'FILLED' : 'NOT_SUBMITTED'}`}
            >
              {state.atm_selected ? 'PAIR MATCHED' : 'AWAITING SPOT PRICE'}
            </span>
          </div>

          <div className="atm-hero">
            <div className="atm-strike-metric">
              <div className="atm-strike-label">Nearest ATM Strike</div>
              <div className="atm-strike-number">
                {state.atm_strike != null ? `$${state.atm_strike.toLocaleString()}` : '---'}
              </div>
              <div className="atm-expiry-row">
                <span>Target Expiration:</span>
                <strong>{formatExpiry(state.atm_expiry)}</strong>
              </div>
            </div>

            {/* ATM CALL Leg */}
            <div className="atm-leg-card call">
              <div className="leg-card-header">
                <span className="leg-tag call">ATM CALL LEG</span>
                <span className="leg-strike-tag">Strike: ${state.atm_strike?.toLocaleString() || '---'}</span>
              </div>
              <div className="leg-instrument">{state.atm_call_id || 'Contract pending...'}</div>
              <div className="leg-details">
                <div className="leg-detail-item">
                  <span className="detail-label">Expiry:</span>
                  <span className="detail-value">{formatExpiry(state.atm_expiry)}</span>
                </div>
                <div className="leg-detail-item">
                  <span className="detail-label">Best Bid:</span>
                  <span className="bid-val">{state.call_bid != null ? `$${state.call_bid.toLocaleString()}` : '---'}</span>
                </div>
                <div className="leg-detail-item">
                  <span className="detail-label">Best Ask:</span>
                  <span className="ask-val">{state.call_ask != null ? `$${state.call_ask.toLocaleString()}` : '---'}</span>
                </div>
              </div>
            </div>

            {/* ATM PUT Leg */}
            <div className="atm-leg-card put">
              <div className="leg-card-header">
                <span className="leg-tag put">ATM PUT LEG</span>
                <span className="leg-strike-tag">Strike: ${state.atm_strike?.toLocaleString() || '---'}</span>
              </div>
              <div className="leg-instrument">{state.atm_put_id || 'Contract pending...'}</div>
              <div className="leg-details">
                <div className="leg-detail-item">
                  <span className="detail-label">Expiry:</span>
                  <span className="detail-value">{formatExpiry(state.atm_expiry)}</span>
                </div>
                <div className="leg-detail-item">
                  <span className="detail-label">Best Bid:</span>
                  <span className="bid-val">{state.put_bid != null ? `$${state.put_bid.toLocaleString()}` : '---'}</span>
                </div>
                <div className="leg-detail-item">
                  <span className="detail-label">Best Ask:</span>
                  <span className="ask-val">{state.put_ask != null ? `$${state.put_ask.toLocaleString()}` : '---'}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Action Button: SELL STRADDLE */}
          <div className="action-container">
            <button
              className={`btn-sell-straddle ${state.is_dry_run ? 'dry' : ''}`}
              onClick={handleSellStraddle}
              disabled={
                !state.atm_selected ||
                executing ||
                state.straddle_status === 'SUBMITTED' ||
                state.straddle_status === 'STRADDLE_ESTABLISHED'
              }
            >
              {executing
                ? 'Submitting Orders...'
                : state.straddle_status === 'STRADDLE_ESTABLISHED'
                ? '✓ Short Straddle Established'
                : state.straddle_status === 'SUBMITTED'
                ? 'Orders In Flight...'
                : state.is_dry_run
                ? 'SELL STRADDLE (Dry-Run Simulate)'
                : 'SELL STRADDLE (Live Testnet)'}
            </button>

            {actionMessage && (
              <div
                className={`action-feedback ${actionMessage.type}`}
              >
                {actionMessage.text}
              </div>
            )}
          </div>
        </section>

        {/* TOP RIGHT: Discovered BTC Option Instruments (moved here in place of Order Lifecycle) */}
        <section className="dashboard-card">
          <div className="card-header">
            <h2 className="card-title">
              <span className="card-title-icon">🔍</span>
              Discovered BTC Option Instruments
            </h2>
            <div className="instruments-summary">
              <span className="summary-pill">Total: <strong>{state.total_instruments_discovered || instruments.length}</strong></span>
              <span className="summary-pill">Expiries: <strong>{state.total_expiries || 10}</strong></span>
            </div>
          </div>

          <div className="search-bar-wrapper">
            <input
              type="text"
              placeholder="Search by strike, type (call/put), or expiry..."
              value={instrumentFilter}
              onChange={(e) => setInstrumentFilter(e.target.value)}
              className="search-input"
            />
            {instrumentFilter && (
              <button className="clear-btn" onClick={() => setInstrumentFilter('')}>✕</button>
            )}
          </div>

          <div className="instruments-table-wrapper">
            <table className="instruments-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Strike</th>
                  <th>Expiry Date</th>
                  <th>Symbol</th>
                </tr>
              </thead>
              <tbody>
                {filteredInstruments.slice(0, 60).map((inst) => (
                  <tr key={inst.instrument_id}>
                    <td>
                      <span className={`leg-tag ${inst.option_type?.toLowerCase()}`}>
                        {inst.option_type}
                      </span>
                    </td>
                    <td className="strike-cell">
                      ${inst.strike?.toLocaleString()}
                    </td>
                    <td className="expiry-cell">
                      {formatExpiry(inst.expiry)}
                    </td>
                    <td className="symbol-cell">{inst.instrument_id}</td>
                  </tr>
                ))}
                {filteredInstruments.length === 0 && (
                  <tr>
                    <td colSpan="4" className="empty-table-cell">
                      {instruments.length === 0
                        ? 'Loading option instruments from Bybit cache...'
                        : 'No option contracts match the filter.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* BOTTOM LEFT: Order Lifecycle & Executions */}
        <section className="dashboard-card">
          <div className="card-header">
            <h2 className="card-title">
              <span className="card-title-icon">📋</span>
              Order Lifecycle & Executions
            </h2>
            <span className="status-badge SUBMITTED">LEVEL 1 EXECUTION</span>
          </div>

          <div className="order-tracking-grid">
            {/* CALL Leg Order Box */}
            <div className="order-box">
              <div className="order-box-header">
                <span className="leg-tag call">CALL SELL ORDER</span>
                <span className={`status-badge ${state.call_order_status}`}>
                  {state.call_order_status}
                </span>
              </div>
              <div className="order-row">
                <span className="order-label">Client Order ID:</span>
                <span className="order-val">{state.call_client_order_id || '<none>'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Venue Order ID:</span>
                <span className="order-val">{state.call_venue_order_id || '<none>'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Submitted Qty:</span>
                <span className="order-val">{state.call_submitted_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Filled Qty:</span>
                <span className="order-val fill-qty">{state.call_filled_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Avg Fill Price:</span>
                <span className="order-val">
                  {state.call_avg_fill_price > 0 ? `$${state.call_avg_fill_price.toLocaleString()}` : '---'}
                </span>
              </div>
              {state.call_rejection_reason && (
                <div className="order-row error">
                  <span className="order-label">Error:</span>
                  <span className="order-val">{state.call_rejection_reason}</span>
                </div>
              )}
            </div>

            {/* PUT Leg Order Box */}
            <div className="order-box">
              <div className="order-box-header">
                <span className="leg-tag put">PUT SELL ORDER</span>
                <span className={`status-badge ${state.put_order_status}`}>
                  {state.put_order_status}
                </span>
              </div>
              <div className="order-row">
                <span className="order-label">Client Order ID:</span>
                <span className="order-val">{state.put_client_order_id || '<none>'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Venue Order ID:</span>
                <span className="order-val">{state.put_venue_order_id || '<none>'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Submitted Qty:</span>
                <span className="order-val">{state.put_submitted_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Filled Qty:</span>
                <span className="order-val fill-qty">{state.put_filled_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Avg Fill Price:</span>
                <span className="order-val">
                  {state.put_avg_fill_price > 0 ? `$${state.put_avg_fill_price.toLocaleString()}` : '---'}
                </span>
              </div>
              {state.put_rejection_reason && (
                <div className="order-row error">
                  <span className="order-label">Error:</span>
                  <span className="order-val">{state.put_rejection_reason}</span>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* BOTTOM RIGHT: Resulting Positions & Straddle Verification */}
        <section className="dashboard-card">
          <div className="card-header">
            <h2 className="card-title">
              <span className="card-title-icon">🛡️</span>
              Resulting Positions & Straddle Verification
            </h2>
            <span
              className={`status-badge ${state.straddle_established ? 'FILLED' : 'NOT_SUBMITTED'}`}
            >
              {state.straddle_established ? 'ESTABLISHED' : 'PENDING'}
            </span>
          </div>

          <div
            className={`position-banner ${
              state.straddle_established
                ? 'established'
                : state.straddle_status === 'ONE_LEG_FAILED'
                ? 'failed'
                : 'waiting'
            }`}
          >
            {state.straddle_established
              ? '✓ Short Straddle Verified: Both Legs SHORT with Matching Strike & Expiry'
              : state.straddle_status === 'ONE_LEG_FAILED'
              ? '⚠️ ALERT: One-Leg Failure Detected! Risk Exposure Active.'
              : 'Awaiting Order Execution to Establish Short Positions'}
          </div>

          <div className="position-cards">
            {/* CALL Position */}
            <div className="position-card">
              <div className="position-card-header">
                <span className="leg-tag call">CALL POSITION</span>
                <span className="position-side-short">{state.call_position_side || '---'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Position Qty:</span>
                <span className="order-val">{state.call_position_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Position ID:</span>
                <span className="order-val">{state.call_position_id || '<none>'}</span>
              </div>
            </div>

            {/* PUT Position */}
            <div className="position-card">
              <div className="position-card-header">
                <span className="leg-tag put">PUT POSITION</span>
                <span className="position-side-short">{state.put_position_side || '---'}</span>
              </div>
              <div className="order-row">
                <span className="order-label">Position Qty:</span>
                <span className="order-val">{state.put_position_qty} BTC</span>
              </div>
              <div className="order-row">
                <span className="order-label">Position ID:</span>
                <span className="order-val">{state.put_position_id || '<none>'}</span>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
