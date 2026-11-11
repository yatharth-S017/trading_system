# BTC ATM Short Straddle — NautilusTrader + Bybit Testnet

## 1. Project Purpose
This project is an automated cryptocurrency derivatives trading system designed to trade a **BTC At-The-Money (ATM) Short Straddle** using **NautilusTrader** and the **Bybit Testnet** exchange.

The project is built incrementally milestone by milestone to ensure correctness, rigorous architectural understanding, and maintainable design.

---

## 2. Project Status & Roadmap

- [x] **Milestone 1:** Project Setup & Basic NautilusTrader + Bybit Testnet Connectivity
- [x] **Milestone 2:** BTC Options Market Data & Instrument Discovery
- [x] **Milestone 3:** ATM Strike Selection & Call/Put Pairing
- [x] **Milestone 4:** Short Straddle Order Execution & Position Tracking (Current)
- [ ] **Milestone 5:** Risk Management (Stop-Loss, Take-Profit, Automated Exits)

---

## 3. System Architecture & UI Design

```text
                    ┌──────────────────┐
                    │   React Frontend │
                    │                  │
                    │ Market Data      │
                    │ Instruments      │
                    │ ATM Selection    │
                    │ Order Status     │
                    │ Positions        │
                    │                  │
                    │ [SELL STRADDLE]  │
                    └────────┬─────────┘
                             │
                         HTTP / WS
                             │
                             ▼
                    ┌──────────────────┐
                    │ FastAPI Bridge   │
                    │ (REST + WS)      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Existing Python  │
                    │ Backend          │
                    │                  │
                    │ ShortStraddle    │
                    │ Strategy         │
                    │ Execution Logic  │
                    └────────┬─────────┘
                             │
                             ▼
                       NautilusTrader
                             │
                             ▼
                       Bybit Adapter
                             │
                             ▼
                        Bybit Testnet
```

### Overview:
Milestone 4 takes the At-The-Money (ATM) Call and Put options identified in Milestone 3, submits `SELL` orders for both legs (short straddle = SELL CALL + SELL PUT), and tracks the entire order lifecycle, execution fills, and resulting open `SHORT` positions.

> **Strict Scope Boundary:** Milestone 4 implements Level 1 core execution and tracking only. Level 2 risk management (stop-loss, take-profit, automated exits) is intentionally deferred to Milestone 5.

```text
BTC Reference Price (e.g. 75,029.45)
               │
               ▼
   Target Expiry Selection (Nearest unexpired: 2026-11-10 08:00 UTC)
               │
               ▼
   ATM Strike Calculation (e.g. 75,000.00; tie-breaker: lower)
               │
               ▼
   ATM Call & Put Pairing & Validation
               │
               ▼
   ShortStraddleExecution Coordinator
   ├── Safety Check: Enforce Bybit Testnet (Fail fast if Mainnet)
   ├── Idempotency Guard: Prevent duplicate order submissions
   ├── Lot Size Resolution: Derive min valid contract qty (e.g. 0.01 BTC)
   └── Mode Decision:
       ├── If EXECUTE_TRADES=false -> Record simulated DRY-RUN orders
       └── If EXECUTE_TRADES=true  -> Construct & submit live SELL orders via OrderFactory
               │
               ▼
   Order Lifecycle State Machine (per leg: CALL & PUT)
   ├── OrderSubmitted  -> Track ClientOrderId
   ├── OrderAccepted   -> Track VenueOrderId
   ├── OrderFilled     -> Track cumulative fills & volume-weighted avg price
   └── OrderRejected / OrderDenied / OrderCanceled -> Record failure reasons
               │
               ▼
   Resulting Position Tracking & Verification
   ├── on_position_opened -> Confirm side == PositionSide.SHORT
   ├── on_position_changed -> Track quantity updates
   └── Straddle Status Evaluation:
       ├── STRADDLE_ESTABLISHED (Both legs FILLED, SHORT positions confirmed)
       ├── ONE_LEG_FAILED (One leg filled, other leg failed/rejected)
       └── EXECUTION_FAILED (Both legs rejected)
```

### Safety & Invariant Guarantees:
1. **Testnet-Only Enforcement:** Both `AppConfig`, `TradingNode`, and `ShortStraddleStrategy` fail fast if `BybitEnvironment.MAINNET` is detected.
2. **Controlled Execution Flag (`EXECUTE_TRADES`):** Defaults to `false`. By default, the system runs in safe **DRY-RUN** mode, discovering the pair, simulating order submission, and logging exact order parameters without touching real or testnet capital.
3. **Idempotency Guard:** `_execution_initiated` boolean guard prevents duplicate submissions across subsequent quote ticks.
4. **Independent Leg Tracking:** Call and Put legs are tracked independently in `LegExecutionState` objects with dedicated timestamps, IDs, fill volumes, and average prices.
5. **One-Leg Failure Handling:** If one leg fills while the other is rejected or canceled, the system transitions to `ONE_LEG_FAILED` and alerts prominently; it never reports false success.

---

## 4. Representative Live Execution Output (Bybit Testnet)

```text
2026-11-09 13:47:15 [INFO] btc_straddle.main: Starting BTC ATM Short Straddle - Milestone 4
2026-11-09 13:47:15 [INFO] btc_straddle.main: Short Straddle Order Execution & Position Tracking
2026-11-09 13:47:15 [INFO] btc_straddle.main: Step 1: Loading configuration from environment / .env...
2026-11-09 13:47:15 [INFO] btc_straddle.main: Loaded configuration: TraderID=BTC-STRADDLE-001, Environment=Testnet, Credentials Configured=False, Execute Trades=False, Order Type=LIMIT
2026-11-09 13:47:15 [INFO] btc_straddle.main: *** RUNNING IN DRY-RUN MODE *** (EXECUTE_TRADES=False). ATM Straddle will be selected and orders will be simulated without sending live orders to Bybit.
2026-11-09 13:47:15 [INFO] btc_straddle.main: Step 2: Initializing NautilusTrader TradingNode with Bybit Testnet adapter...
2026-11-09 13:47:15 [INFO] BTC-STRADDLE-001.TradingNode: Initialized in 232ms
2026-11-09 13:47:17 [INFO] BTC-STRADDLE-001.BybitInstrumentProvider: Loaded 704 instruments
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy: Discovered 704 BTC option contracts across 10 expiration dates.
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy: Subscribing to BTC reference price feed: BTCUSDT-LINEAR.BYBIT...
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy: ATM STRADDLE PAIR SELECTED:
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   BTC Reference Price: 75,029.45
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   Target Expiry:       2026-11-10 08:00 UTC
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   Selected ATM Strike: 75,000.00
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   ATM Call Contract:   BTC-10NOV26-75000-C-USDT-OPTION.BYBIT
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   ATM Put Contract:    BTC-10NOV26-75000-P-USDT-OPTION.BYBIT
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy: DRY-RUN / SIMULATED STRADDLE SUBMISSION:
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   SELL CALL: BTC-10NOV26-75000-C-USDT-OPTION.BYBIT | Qty: 0.01 | Type: LIMIT @ 100.0
2026-11-09 13:47:18 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy:   SELL PUT:  BTC-10NOV26-75000-P-USDT-OPTION.BYBIT | Qty: 0.01 | Type: LIMIT @ 100.0
2026-11-09 13:47:19 [INFO] BTC-STRADDLE-001.ShortStraddleStrategy: [ATM PUT QUOTE #1] BTC-10NOV26-75000-P-USDT-OPTION.BYBIT | Bid: 0 | Ask: 2500
...
==================================================
SHORT STRADDLE EXECUTION REPORT (DRY-RUN / SIMULATED)
==================================================
Overall Status:        DRY_RUN
BTC Reference Price:   75,029.45
Target Expiry:         2026-11-10 08:00 UTC
ATM Strike:            75,000.00
--------------------------------------------------
CALL LEG:              BTC-10NOV26-75000-C-USDT-OPTION.BYBIT
  Order Side / Type:   SELL LIMIT
  Order Status:        SUBMITTED
  Client Order ID:     DRYRUN-CALL-001
  Venue Order ID:      <none>
  Submitted Qty:       0.01
  Filled Qty:          0
  Avg Fill Price:      0.00
  Position ID:         <none>
  Position Side / Qty: <none> (0)
--------------------------------------------------
PUT LEG:               BTC-10NOV26-75000-P-USDT-OPTION.BYBIT
  Order Side / Type:   SELL LIMIT
  Order Status:        SUBMITTED
  Client Order ID:     DRYRUN-PUT-001
  Venue Order ID:      <none>
  Submitted Qty:       0.01
  Filled Qty:          0
  Avg Fill Price:      0.00
  Position ID:         <none>
  Position Side / Qty: <none> (0)
--------------------------------------------------
Straddle Established:  NO
==================================================
```

---

## 5. Setup & Running Instructions

### Step 1: Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies
```powershell
pip install -e ".[dev]"
```

### Step 3: Configure Environment
Copy `.env.example` to `.env`:
```powershell
cp .env.example .env
```
To run in live execution mode on Bybit Testnet, provide valid Testnet API keys:
```ini
BYBIT_API_KEY=your_testnet_api_key_here
BYBIT_API_SECRET=your_testnet_api_secret_here
EXECUTE_TRADES=false
```

### Step 4: Run Application & React Frontend

**Option A — Running with Embedded React Frontend (Recommended):**
The compiled React frontend is automatically served at `http://localhost:8000`:
```powershell
python -u -m src.btc_straddle.main --run-seconds 0
```
Open your browser at **http://localhost:8000** to view the live trading terminal with real-time WebSocket state streaming.

**Option B — Running Frontend in Vite Dev Mode (Hot Reload):**
1. Start backend:
   ```powershell
   python -u -m src.btc_straddle.main --run-seconds 0
   ```
2. In a second terminal, start Vite dev server:
   ```powershell
   cd frontend
   npm run dev
   ```
   Open **http://localhost:3000** (proxied to FastAPI on port 8000).

**Live Testnet Execution Mode (Requires Testnet API Keys in `.env`):**
```powershell
python -u -m src.btc_straddle.main --execute-trades --order-type LIMIT --order-qty 0.01 --run-seconds 0
```

### Step 5: Run Unit Tests
```powershell
pytest tests/ -v
```
All **43 unit tests** run locally without external network dependencies in < 4 seconds, verifying:
- FastAPI REST endpoints (`/api/status`, `/api/atm`, `/api/orders`, `/api/positions`)
- API key masking & configuration validation
- Instrument discovery & OptionMetadata extraction
- Expiry grouping & ATM strike distance algorithms
- Call/Put pairing integrity checks
- Mainnet fail-fast boundary enforcement
- Dry-run vs live order generation
- Idempotency guards (duplicate execution prevention)
- Order lifecycle transitions (`Submitted` $\to$ `Accepted` $\to$ `Filled`)
- Order rejection & pre-flight denial handling
- Volume-weighted average fill price calculations
- One-leg execution failure detection
- Short position establishment verification (`PositionSide.SHORT`)
