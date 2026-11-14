"""San Arb command-line interface (stdlib argparse - no extra deps).

Commands:
  sanarb config         show effective settings (secrets redacted)
  sanarb scan           one scan pass, print signals (no orders)
  sanarb run            scan + execute; paper unless armed live
  sanarb positions      show current positions + cash (paper or live)
  sanarb ledger         tail the audit log
  sanarb auth-url       print the FYERS login URL
  sanarb auth-token     exchange an auth_code for an access token

Safety: `run` executes on the PaperBroker unless BOTH SANARB_MODE=live and
SANARB_ARM_LIVE=1 are set (and FYERS creds exist). The live banner makes the
mode unmistakable before anything is placed.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .config import load_settings, Settings
from .models import Action, Signal
from .store import Store


# ----------------------------------------------------------------------------
# rendering helpers
# ----------------------------------------------------------------------------
_ICON = {Action.BUY: "BUY ", Action.SELL: "SELL", Action.AVOID: "AVOID", Action.HOLD: "hold"}


def _fmt_signal(s: Signal) -> str:
    edge = f"{s.edge_pct*100:+5.1f}%" if s.edge_pct else "   -  "
    tag = _ICON.get(s.action, "?")
    return (f"  [{tag}] {s.instrument.name:<34} edge {edge}  "
            f"ref {s.ref_price:.2f}\n        {s.rationale}")


def _provider(settings: Settings, force_mock: bool):
    """Pick a data provider. Mock unless live creds exist and not forced-mock.

    When live, the FYERS provider is paired with the AMFI NAV provider so ETF
    quotes come back with a NAV (FYERS itself has none).
    """
    if not force_mock and settings.fyers_client_id and settings.fyers_access_token:
        from .providers.fyers import FyersProvider
        from .providers.nav import AmfiNavProvider
        return FyersProvider(settings, nav_provider=AmfiNavProvider()), "FYERS"
    from .providers.mock import MockProvider
    return MockProvider(), "MOCK"


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def cmd_config(args, settings: Settings) -> int:
    print("San Arb effective settings:")
    for k, v in settings.redacted().items():
        print(f"  {k:<24} {v}")
    print(f"\n  live_enabled           {settings.live_enabled}  "
          f"(mode={settings.mode}, arm_live={settings.arm_live})")
    return 0


def cmd_scan(args, settings: Settings) -> int:
    from .engine.scanner import Scanner
    from .strategies.registry import default_strategies
    provider, pname = _provider(settings, args.mock)
    print(f"Scanning (data: {pname}, capital Rs {settings.capital:.0f})...\n")
    signals = Scanner(default_strategies(), settings).scan(provider)
    actionable = [s for s in signals if s.actionable]
    for s in signals:
        print(_fmt_signal(s))
    print(f"\n{len(actionable)} actionable / {len(signals)} total.")
    if not actionable:
        print("No real edge right now - nothing to do (this is the common, correct case).")
    return 0


def cmd_run(args, settings: Settings) -> int:
    from .engine.runner import Runner
    provider, pname = _provider(settings, args.mock)
    store = Store(settings.data_dir)

    if settings.live_enabled:
        print("=" * 60)
        print(" LIVE MODE ARMED - real orders will be placed via FYERS")
        print(" Requires an API app registered under SEBI retail-algo rules.")
        print("=" * 60)
    else:
        why = "paper mode" if settings.mode != "live" else "live gate CLOSED (arm_live off)"
        print(f"[PAPER] simulated execution - {why}")

    runner = Runner(settings, provider, store=store, execute=True)
    passes = args.passes
    print(f"Data: {pname}  Broker: {runner.broker_label}  "
          f"interval: {settings.scan_interval_sec}s\n")

    n = 0
    for result in runner.loop(max_passes=passes):
        n += 1
        print(f"--- pass {n} ---")
        placed = [o for o in result.outcomes if o.placed]
        vetoed = [o for o in result.outcomes if not o.placed]
        for o in placed:
            r = o.result
            print(f"  PLACED {r.status} {o.decision.order.side.value} "
                  f"{r.filled_qty} {o.signal.instrument.name} @ {r.avg_price:.2f} "
                  f"({'paper' if r.paper else 'LIVE'})")
        for o in vetoed:
            if o.signal.action in (Action.BUY, Action.SELL):
                print(f"  veto  {o.signal.instrument.name}: {o.decision.reason}")
        if not result.outcomes:
            print("  (no actionable signals)")
        if passes is None:
            print(f"  sleeping {settings.scan_interval_sec}s... (Ctrl-C to stop)")
    return 0


def cmd_positions(args, settings: Settings) -> int:
    if settings.live_enabled:
        from .broker.fyers import FyersBroker
        broker = FyersBroker(settings)
        label = "FYERS(LIVE)"
    else:
        from .broker.paper import PaperBroker
        broker = PaperBroker(settings, Store(settings.data_dir))
        label = "paper"
    print(f"Account ({label}):  cash Rs {broker.funds():.2f}")
    positions = broker.positions()
    if not positions:
        print("  no open positions")
        return 0
    total = 0.0
    for p in positions:
        print(f"  {p.name or p.symbol:<34} qty {p.qty:<5} avg {p.avg_price:.2f} "
              f"cost Rs {p.cost:.2f}")
        total += p.cost
    print(f"  {'deployed':<34}            Rs {total:.2f}")
    return 0


def cmd_ledger(args, settings: Settings) -> int:
    store = Store(settings.data_dir)
    rows = store.read_audit(limit=args.n)
    if not rows:
        print("no audit history yet")
        return 0
    for r in rows:
        ev = r.get("event", "?")
        extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("ts", "event"))
        print(f"  {ev:<16} {extra}")
    return 0


def cmd_dashboard(args, settings: Settings) -> int:
    from .web.server import Dashboard, serve
    provider, pname = _provider(settings, args.mock)
    store = Store(settings.data_dir)
    dash = Dashboard(settings, provider, pname, store)
    httpd = serve(dash, args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    mode = "LIVE" if settings.live_enabled else "paper"
    print(f"San Arb dashboard on {url}  (data: {pname}, mode: {mode})")
    print("Read-only monitor - does not place orders. Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()
    return 0


def cmd_auth_url(args, settings: Settings) -> int:
    from .fyers_auth import auth_url
    print(auth_url(settings))
    print("\nOpen this URL, log in, then copy the ?auth_code=... from the redirect.")
    return 0


def cmd_auth_token(args, settings: Settings) -> int:
    from .fyers_auth import exchange_code
    token = exchange_code(settings, args.auth_code)
    print("Access token obtained. Set it in your environment / .env as:")
    print(f"\n  FYERS_ACCESS_TOKEN={token}\n")
    return 0


# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sanarb", description="San Arb - minimum-capital arbitrage engine for FYERS")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("config", help="show effective settings")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("scan", help="one scan pass, print signals")
    sp.add_argument("--mock", action="store_true", help="force mock data even if creds exist")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("run", help="scan + execute (paper unless armed live)")
    sp.add_argument("--mock", action="store_true", help="force mock data")
    sp.add_argument("--passes", type=int, default=1, help="number of passes (default 1; omit value with --loop)")
    sp.add_argument("--loop", action="store_true", help="run forever at scan interval")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("positions", help="show cash + positions")
    sp.set_defaults(func=cmd_positions)

    sp = sub.add_parser("ledger", help="tail the audit log")
    sp.add_argument("-n", type=int, default=30, help="rows to show")
    sp.set_defaults(func=cmd_ledger)

    sp = sub.add_parser("dashboard", help="live web dashboard (read-only monitor)")
    sp.add_argument("--mock", action="store_true", help="force mock data")
    sp.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    sp.add_argument("--port", type=int, default=8777, help="port (default 8777)")
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("auth-url", help="print FYERS login URL")
    sp.set_defaults(func=cmd_auth_url)

    sp = sub.add_parser("auth-token", help="exchange auth_code for access token")
    sp.add_argument("auth_code", help="the ?auth_code=... from the redirect")
    sp.set_defaults(func=cmd_auth_token)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # --loop means run forever
    if getattr(args, "loop", False):
        args.passes = None
    settings = load_settings()
    try:
        return args.func(args, settings)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
