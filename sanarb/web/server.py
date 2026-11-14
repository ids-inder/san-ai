"""A dependency-free live dashboard for San Arb.

Built on the standard library's http.server so it needs nothing extra. It is a
READ-ONLY monitoring surface: it scans and shows signals, live prices vs fair
value, positions, cash, and recent activity. It never places orders - execution
stays with `sanarb run`. So opening the dashboard is always safe, even pointed
at live data.

Routes:
    GET /              the HTML page (auto-refreshes via fetch)
    GET /api/state     JSON: meta + signals + positions + audit
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from ..config import Settings
from ..engine.scanner import Scanner
from ..providers.base import MarketDataProvider
from ..store import Store
from ..strategies.registry import default_strategies


class Dashboard:
    """Holds the runtime and builds the state the page renders."""

    def __init__(self, settings: Settings, provider: MarketDataProvider,
                 provider_name: str, store: Store) -> None:
        self._s = settings
        self._provider = provider
        self._pname = provider_name
        self._store = store
        self._scanner = Scanner(default_strategies(), settings)
        self._cache: Dict[str, Any] = {}
        self._cache_at = 0.0

    def _broker(self):
        if self._s.live_enabled:
            from ..broker.fyers import FyersBroker
            return FyersBroker(self._s), "FYERS(LIVE)"
        from ..broker.paper import PaperBroker
        return PaperBroker(self._s, self._store), "paper"

    def state(self, ttl: float = 8.0) -> Dict[str, Any]:
        # Cache briefly so rapid refreshes don't hammer FYERS.
        if self._cache and (time.time() - self._cache_at) < ttl:
            return self._cache

        signals = self._scanner.scan(self._provider)
        syms = [s.instrument.symbol for s in signals]
        quotes = self._provider.quotes(syms) if syms else {}

        rows = []
        for s in signals:
            q = quotes.get(s.instrument.symbol)
            ltp = q.ltp if q else None
            premium = None
            if ltp and s.ref_price:
                premium = (ltp - s.ref_price) / s.ref_price
            rows.append({
                "action": s.action.value,
                "name": s.instrument.name,
                "symbol": s.instrument.symbol,
                "strategy": s.strategy,
                "ltp": ltp,
                "fair_value": s.ref_price,
                "premium": premium,
                "edge": s.edge_pct,
                "rationale": s.rationale,
                "limit": s.limit_price,
            })

        broker, blabel = self._broker()
        try:
            cash = broker.funds()
            positions = [{"symbol": p.symbol, "name": p.name, "qty": p.qty,
                          "avg": p.avg_price, "cost": p.cost} for p in broker.positions()]
        except Exception as exc:  # live broker may fail if token stale
            cash, positions = None, []
            self._store.audit("dashboard_broker_error", error=str(exc))

        state = {
            "meta": {
                "mode": self._s.mode,
                "live_enabled": self._s.live_enabled,
                "capital": self._s.capital,
                "data_source": self._pname,
                "broker": blabel,
                "per_trade_cap": self._s.per_trade_cap,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "signals": rows,
            "cash": cash,
            "deployed": sum(p["cost"] for p in positions),
            "positions": positions,
            "audit": list(reversed(self._store.read_audit(limit=20))),
        }
        self._cache, self._cache_at = state, time.time()
        return state


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default stderr logging
        pass

    def do_GET(self):
        dash: Dashboard = self.server.dashboard  # type: ignore[attr-defined]
        if self.path.startswith("/api/state"):
            body = json.dumps(dash.state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def serve(dashboard: Dashboard, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.dashboard = dashboard  # type: ignore[attr-defined]
    return httpd


# --------------------------------------------------------------------------- #
# The page. Single file, no external assets, dark/light aware, phone friendly.
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>San Arb</title>
<style>
:root{
  --bg:#0f1115; --panel:#171a21; --line:#242934; --fg:#e6e8ec; --muted:#8b93a3;
  --buy:#1f9d55; --buybg:#10351f; --avoid:#d64545; --avoidbg:#3a1414;
  --sell:#d99a1c; --sellbg:#352611; --hold:#5a6472; --holdbg:#1d2029;
  --accent:#5b8def;
}
@media (prefers-color-scheme: light){
  :root{--bg:#f4f5f7;--panel:#fff;--line:#e3e6ea;--fg:#1a1d23;--muted:#667085;
  --buybg:#e4f6ec;--avoidbg:#fbe7e7;--sellbg:#fbf1dd;--holdbg:#eef0f3;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  padding:0 16px 48px}
.wrap{max-width:1000px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;
  padding:18px 0 10px;border-bottom:1px solid var(--line);margin-bottom:16px}
h1{font-size:20px;margin:0;letter-spacing:.3px}
h1 small{color:var(--muted);font-weight:400;font-size:13px}
.badge{font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;
  text-transform:uppercase;letter-spacing:.5px}
.badge.paper{background:var(--holdbg);color:var(--muted);border:1px solid var(--line)}
.badge.live{background:var(--avoidbg);color:var(--avoid)}
.meta{color:var(--muted);font-size:12px;margin-left:auto;text-align:right}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:10px;margin:0 0 18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.card .v{font-size:20px;font-weight:700;margin-top:2px}
h2{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;
  margin:22px 0 8px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
tr:last-child td{border-bottom:none}
td.num{text-align:right}
.sig{font-weight:700;font-size:11px;padding:3px 8px;border-radius:6px;letter-spacing:.4px}
.sig.BUY{background:var(--buybg);color:var(--buy)}
.sig.AVOID{background:var(--avoidbg);color:var(--avoid)}
.sig.SELL{background:var(--sellbg);color:var(--sell)}
.sig.HOLD{background:var(--holdbg);color:var(--muted)}
.pos{color:var(--buy)} .neg{color:var(--avoid)}
.why{color:var(--muted);font-size:12px;max-width:380px}
.empty{padding:16px;color:var(--muted)}
.foot{color:var(--muted);font-size:11px;margin-top:20px;text-align:center}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--buy);
  margin-right:6px;vertical-align:middle;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.35}}
@media(max-width:620px){.why{display:none}.meta{width:100%;text-align:left;margin:4px 0 0}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>San&nbsp;Arb <small>arbitrage monitor</small></h1>
    <span id="mode" class="badge paper">paper</span>
    <div class="meta" id="meta">loading…</div>
  </header>

  <div class="cards" id="cards"></div>

  <h2><span class="dot"></span>Signals</h2>
  <div class="panel"><table>
    <thead><tr>
      <th>Instrument</th><th class="num">LTP</th><th class="num">Fair&nbsp;value</th>
      <th class="num">Prem/Disc</th><th>Signal</th><th class="num">Edge</th><th>Why</th>
    </tr></thead>
    <tbody id="sig"><tr><td colspan="7" class="empty">loading…</td></tr></tbody>
  </table></div>

  <h2>Positions</h2>
  <div class="panel"><table>
    <thead><tr><th>Instrument</th><th class="num">Qty</th><th class="num">Avg</th><th class="num">Cost</th></tr></thead>
    <tbody id="pos"><tr><td colspan="4" class="empty">—</td></tr></tbody>
  </table></div>

  <h2>Recent activity</h2>
  <div class="panel"><table><tbody id="aud"><tr><td class="empty">—</td></tr></tbody></table></div>

  <div class="foot">Read-only monitor · does not place orders · refreshes every 15s.
    Data and NAV are approximate; not investment advice.</div>
</div>

<script>
const inr = n => n==null ? "—" : "₹"+Number(n).toLocaleString("en-IN",{maximumFractionDigits:2});
const pct = n => n==null ? "—" : (n*100).toFixed(2)+"%";
const cls = n => n==null ? "" : (n>=0 ? "pos":"neg");

async function tick(){
  let s;
  try { s = await (await fetch("/api/state")).json(); }
  catch(e){ document.getElementById("meta").textContent="connection lost…"; return; }

  const m = s.meta;
  const mode = document.getElementById("mode");
  mode.textContent = m.live_enabled ? "LIVE" : "paper";
  mode.className = "badge " + (m.live_enabled ? "live":"paper");
  document.getElementById("meta").innerHTML =
    `data: <b>${m.data_source}</b> · broker: ${m.broker} · ${m.updated}`;

  document.getElementById("cards").innerHTML = [
    ["Capital", inr(m.capital)],
    ["Cash free", inr(s.cash)],
    ["Deployed", inr(s.deployed)],
    ["Per-trade cap", inr(m.per_trade_cap)],
    ["Actionable", s.signals.filter(r=>r.action=="BUY"||r.action=="SELL").length + " / " + s.signals.length],
  ].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  document.getElementById("sig").innerHTML = s.signals.length ? s.signals.map(r=>`
    <tr>
      <td>${r.name}<br><span class="why">${r.symbol}</span></td>
      <td class="num">${inr(r.ltp)}</td>
      <td class="num">${inr(r.fair_value)}</td>
      <td class="num ${cls(r.premium==null?null:-r.premium)}">${pct(r.premium)}</td>
      <td><span class="sig ${r.action}">${r.action}</span></td>
      <td class="num ${cls(r.edge)}">${r.edge?pct(r.edge):"—"}</td>
      <td class="why">${r.rationale||""}</td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty">no signals</td></tr>`;

  document.getElementById("pos").innerHTML = s.positions.length ? s.positions.map(p=>`
    <tr><td>${p.name||p.symbol}</td><td class="num">${p.qty}</td>
    <td class="num">${inr(p.avg)}</td><td class="num">${inr(p.cost)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">no open positions</td></tr>`;

  document.getElementById("aud").innerHTML = s.audit.length ? s.audit.map(a=>{
    const extra = Object.entries(a).filter(([k])=>!["ts","event"].includes(k))
      .map(([k,v])=>`${k}=${v}`).join(" ");
    return `<tr><td><b>${a.event}</b> <span class="why">${extra}</span></td></tr>`;
  }).join("") : `<tr><td class="empty">no activity yet</td></tr>`;
}
tick(); setInterval(tick, 15000);
</script>
</body>
</html>"""
