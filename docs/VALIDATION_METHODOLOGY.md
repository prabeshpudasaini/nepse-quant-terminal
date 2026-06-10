# Validation Methodology & Honest Caveats

This document exists so the headline backtest number (**C5 baseline: +88% historical
return, Sharpe ~2.2 vs NEPSE +27%**) is not read as more than it is.

## What the number is

- **In-sample / historical**, on 6+ years of NEPSE daily price data.
- Produced by the C5 strategy configuration (`configs/long_term.py`,
  `LONG_TERM_CONFIG`): ~40 trading-day holding period, max 5 positions, regime
  filter, stop-loss / trailing-stop exits, realistic transaction costs (SEBON
  levy, broker commission, DP charge).
- A figure from a **parameter sweep** — the best-looking configuration out of
  several that were tried.
- A demonstration that the **tooling** (backtest engine, walk-forward harness,
  cost model, regime logic) works end-to-end.

The repo currently ships **no artifact that reproduces +88% as a forward-tested
or out-of-sample return.** It is an in-sample backtest figure.

## What the number is NOT

- It is **not** corrected for the number of strategy variants that were tried.
  When many configurations are searched, the best one looks good **by chance**;
  the honest metric is the **deflated Sharpe ratio**, which adjusts for the
  number of trials. Always read the deflated Sharpe, not the raw one.
- It is **not** an out-of-sample or forward-performance claim. Past performance
  does not indicate future results.
- It is **not** evidence of a tradeable edge on its own. A high return can be
  mostly **market beta** (exposure to a rising market), not **alpha** (skill).

## How to check it yourself

The repo ships the tools to pressure-test the claim — use them and believe those
numbers over the headline. The suite validates the shipped C5 baseline by
default, so you are testing the same strategy the headline quotes:

```bash
python -m validation.run_all          # full battery, C5 baseline
python -m validation.run_all --fast   # quick mode (fewer simulations)
```

It prints a GO / NO-GO verdict and writes a JSON + PDF report under
`reports/validation/`. Look specifically at:

- **Deflated Sharpe ratio (DSR)** — Sharpe corrected for multiple testing. If DSR
  is near zero, the apparent edge is likely noise.
- **Random-baseline percentile** — where the strategy ranks against thousands of
  random-entry portfolios run on the same universe and cost model. Below the
  ~50th percentile means *worse than random*; the suite's GO gate asks for the
  95th percentile.
- **CSCV / PBO** — probability of backtest overfitting (Bailey et al.). Reported,
  but see the gate note below.
- **Benchmark-relative** — alpha vs. beta against NEPSE; is the return skill or
  just the market? Reported, but see the gate note below.
- **Walk-forward** — Sharpe across rolling out-of-sample test windows (see the
  caveat below on what this harness does and does not do).

### What gates the GO verdict (and what doesn't)

The GO / NO-GO verdict is decided only by a subset of the tests: base backtest,
transaction costs, statistical significance (PSR/DSR/t-test), walk-forward,
Monte Carlo, regime stress, sensitivity, random baseline, slippage, and max
drawdown.

The **CSCV/PBO overfitting test** and the **benchmark (alpha-vs-beta)
comparison** run and are written to the report, but they **do not gate the
verdict**. A run can print GO while still showing a high PBO or near-zero alpha.
Read those two outputs directly — do not rely on the headline verdict to catch
them.

## Caveats specific to the walk-forward harness

The walk-forward phase (`validation/walk_forward.py`) slides a train/test window
across the history and stitches the per-window out-of-sample equity curves
together. It is a **robustness check, not out-of-sample model selection**:

- It **replays a fixed configuration** on each test window. There is **no
  training step** — parameters are not re-fit or re-selected per window using
  only that window's training data.
- The train window only provides context (e.g. regime detection looks back
  before the test period). It does not search for the best config.

So a strong walk-forward result says the fixed C5 config held up across
subperiods; it does **not** demonstrate that a fresh model selected on past data
would have chosen well going forward.

## Known limitations of NEPSE backtesting

These limitations bias historical results upward and are not all corrected in
the base engine:

- **No slippage in the base engine.** `run_backtest` charges fees but fills at
  the open with zero slippage. Slippage is only estimated in a separate phase
  (`validation/slippage.py`); the headline figure does not include it.
- **Circuit-locked days fill at the band.** Entry prices are clamped to the ±10%
  circuit limit (`apply_circuit_breaker`), so a day where a name is limit-locked
  still "fills" at the band — a price you often could not actually transact at on
  a thin NEPSE stock.
- **Survivorship bias.** The universe is the set of symbols currently present in
  the bundled database (a static snapshot). Delisted or suspended names are not
  reconstructed point-in-time.
- **Frontier-market microstructure.** ±10% circuit breakers, T+2 settlement, and
  thin order books mean real execution costs on illiquid names can far exceed
  modelled slippage.
- **Point-in-time correctness** must be verified for any fundamental or
  corporate-action signal — unadjusted prices around bonus issues fabricate
  returns.

**Bottom line:** treat the bundled backtest as a worked example of the pipeline,
not as a reason to expect those returns. If you trade real capital, do your own
out-of-sample validation first.
