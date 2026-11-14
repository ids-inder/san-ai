"""San Arb - a minimum-capital arbitrage scanner and auto-trade engine for FYERS.

San Arb watches public market data for the handful of arbitrage-style edges that
work with a small account (under Rs 10,000), decides whether an edge is real
*after costs*, and - once armed and funded - places the trade through FYERS.

By design it refuses to act unless the edge survives every gate:

    scan  ->  fair-value adjust  ->  cost model  ->  risk gate  ->  execute

Live order placement is off by default. The engine runs in PAPER mode until it
is explicitly armed (see sanarb.config.Settings.arm_live) and given FYERS
credentials. See the README for the full safety model.
"""

__version__ = "0.1.0"
