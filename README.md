# Institutional Quantitative Trading System — Master Repository

**Repository:** `trading-sandbox`  
**Classification:** Proprietary & Confidential  
**Copyright © 2026 [Author / System Owner]. All Rights Reserved.**

---

## Executive Overview & System Architecture

The **trading-sandbox** platform is a production-grade, multi-agent quantitative trading system engineered for private proprietary capital management and automated execution. Inspired by the empirical frameworks of Dr. Ernest P. Chan (*Quantitative Trading*) and engineered around Burton Malkiel’s efficient-market baselines (*A Random Walk Down Wall Street*), this architecture enforces a strict scientific hierarchy: **Econometric Regime Detection precedes Strategy Selection; Machine Learning Meta-Labeling filters false positives; and Autonomous Risk Circuit Breakers govern Order Execution.**

To ensure long-term maintainability, auditability, and seamless team onboarding, the system is partitioned into four specialized functional divisions operated by a four-person multidisciplinary team:
1. **Seat 01: Finance, Portfolio & Risk Officers** (Capital preservation, 1% stop-loss sizing, 5% notional caps, 3% daily drawdown breaker, Half-Kelly scaling, friction audits).
2. **Seat 02: Quantitative & Mathematical Researchers** (ADF stationarity proofs, Hurst Exponent regimes, Ornstein-Uhlenbeck half-life, Triple-Barrier ML, Meta-Labeling, and Purged CV).
3. **Seat 03: Software & Systems Engineers** (Hybrid split-compute topology, Parquet I/O caching, Discord multi-cog bot, Groq/Gemini LLM agents, CI/CD, and `pytest` suite).
4. **Seat 04: General Users & Operations** (Plain-English workflow analogy, mobile Discord operation, 3-Tab Web Dashboard guide, and emergency safety procedures).

---

## Repository Directory Specification

```text
trading-sandbox/
├─ ai/                           # Machine Learning Pipeline
│  ├─ triple_barrier.py          # Path-dependent outcome labeling
│  ├─ purged_cv.py               # Purged & embargoed K-Fold cross-validation
│  ├─ meta_labeler.py            # XGBoost/RandomForest secondary probability filter
│  └─ train_model.py             # Serialized artifact exporter (.pkl)
├─ cogs/                         # Event-Driven Discord Command Modules
│  ├─ research.py                # Academic paper ingestion (!arxiv via Groq qwen/qwen3.8-27b)
│  ├─ modelling.py               # Risk calculation commands (/position_size)
│  ├─ ai_agents.py               # ML inference routing (/ai_veto)
│  └─ markets.py                 # Candlestick charting and market monitors
├─ data/                         # Local Parquet offline cache for backtesting & research
├─ logs/                         # Self-healing execution and error logs (trading_bot.log)
├─ models/                       # Serialized machine learning models (*.pkl)
├─ strategies/                   # Modular strategy abstraction layer (mean_reversion.py)
├─ tests/                        # Automated pytest unit verification suite
├─ quant_engine.py               # Master core engine & execution daemon
├─ data_fetcher.py               # Decoupled Alpaca IEX ingestion & Parquet caching layer
├─ stats_analyzer.py             # ADF stationarity and Hurst Exponent econometric engine
├─ risk_manager.py               # Position sizing, Half-Kelly scaling, and drawdown circuit breaker
├─ backtester.py                 # Friction-adjusted vectorized simulation engine
├─ ParameterGridSearch.py        # Multi-parameter matrix optimizer ranked by Sharpe & Drawdown
├─ main_discord.py               # Mobile command center launcher
├─ index.html                    # Real-time institutional web dashboard (Tailwind, Lightweight Charts)
├─ vercel.json & .vercelignore     # Web deployment configuration
├─ .env                          # Vault for API keys, Discord tokens, and hardcoded risk limits
├─ .gitignore                    # Excludes .env, venv/, data/, logs/, and __pycache__/
└─ requirements.txt              # Locked cross-platform Python dependencies
```

---

## Core Mathematical & Engineering Pillars

* **Econometric Regime Filtering:** Proves statistical behavior before strategy instantiation. Uses the Augmented Dickey-Fuller (ADF) test for stationarity ($p < 0.05$) and the Hurst Exponent ($H$) to classify Mean-Reverting ($H < 0.48$), Random Walk ($H = 0.5$), or Trending ($H > 0.5$) regimes.
* **Friction Realism:** Eliminates look-ahead bias by shifting execution signals forward by one bar ($t+1$) and explicitly deducting round-trip transaction drag ($\text{spread\_slippage\_bps} = 0.0008$ or 8 bps).
* **Multi-Tier Risk Circuit Breakers:** Enforces a 1% per-trade stop-loss risk cap, 5% notional concentration cap, 3% daily drawdown kill switch, and Half-Kelly portfolio scaling ($f^*_{\text{half}} = 0.5 \times (p - q/b)$).
* **Institutional Benchmarks:** Validates strategies out-of-sample against a passive SPY buy-and-hold baseline, targeting an Annualized Sharpe Ratio $> 2.0$ and Maximum Peak-to-Trough Drawdown $< 10\%–15\%$.

---

## Quick-Start & Deployment Guide

### 1. Local Environment Setup & Dependency Installation
```powershell
python -m venv venv
(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& '.\venv\Scripts\Activate.ps1')
pip install -r requirements.txt
```

### 2. Configure Your `.env` Vault
Create a `.env` file in the root directory (never commit to GitHub):
```env
ALPACA_API_KEY="PKXXXXXXXXXXXXXXXXXX"
ALPACA_SECRET_KEY="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
DISCORD_BOT_TOKEN="MTXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/XXXXX/XXXXX"
GEMINI_API_KEY="AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
GROQ_API_KEY="gsk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
MAX_RISK_PER_TRADE="0.01"
MAX_POSITION_SIZE="0.05"
MAX_DAILY_DRAWDOWN="0.03"
```

### 3. Run Automated Unit Tests
```powershell
python -m pytest tests/
```

### 4. Cloud Deployment (Google Cloud Ubuntu LTS)
```bash
ssh user@gcp-quant-node
cd ~/trading-sandbox && source venv/bin/activate && git pull origin main
tmux new -s tradingbot -d "python quant_engine.py"
tail -f ~/trading-sandbox/logs/trading_bot.log
```

---

## Intellectual Property & Regulatory Disclaimer

**Copyright © 2026 [LuckyCharmNoRAGRATS]. All Rights Reserved.**  
This software, documentation, econometric algorithms, machine learning pipelines, and user interface designs are the sole proprietary intellectual property of the Copyright Holder. No portion may be reproduced, distributed, reverse-engineered, or deployed for commercial trading without explicit written permission.  
*Regulatory Notice:* Engineered for quantitative research, paper-trading validation ($400,000 simulated Alpaca mandate), and private proprietary capital management. Past backtested or paper-traded performance does not guarantee future live returns.
