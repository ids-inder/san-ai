"""Jinja templates, held in a dict so the package stays a single importable unit."""

BASE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} - Attendance</title>
<style>
 :root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#6b7280;--line:#e5e7eb;
       --accent:#1d4ed8;--ok:#047857;--warn:#b45309;--bad:#b91c1c;--radius:10px}
 @media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaed;
       --mute:#9aa3af;--line:#272b35;--accent:#6b9bff;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
      font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
 header{background:var(--card);border-bottom:1px solid var(--line);padding:0 16px}
 nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;max-width:1200px;margin:0 auto}
 nav a{padding:14px 12px;text-decoration:none;color:var(--mute);font-weight:500}
 nav a.on{color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent)}
 nav .brand{font-weight:700;color:var(--ink);padding:14px 12px 14px 0}
 nav .right{margin-left:auto}
 main{max-width:1200px;margin:0 auto;padding:16px}
 h1{font-size:20px;margin:4px 0 14px}
 h2{font-size:16px;margin:22px 0 10px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
       padding:14px;margin-bottom:14px;overflow-x:auto}
 .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
 .tile{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px}
 .tile .n{font-size:22px;font-weight:650}
 .tile .l{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
 table{border-collapse:collapse;width:100%;font-size:14px}
 th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
 th{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
 tbody tr:hover{background:color-mix(in srgb,var(--accent) 6%,transparent)}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .b{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
 .b.present{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
 .b.half_day,.b.short{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
 .b.absent,.b.incomplete,.b.error,.b.auth_rejected,.b.account_blocked{
       background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
 .b.week_off,.b.holiday,.b.untested{background:color-mix(in srgb,var(--mute) 18%,transparent);color:var(--mute)}
 .b.ok,.b.ok_no_data{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
 form.inline{display:inline}
 label{display:block;margin:10px 0 4px;font-size:13px;color:var(--mute)}
 input,select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;
      background:var(--bg);color:var(--ink);font:inherit}
 .btn{display:inline-block;padding:8px 14px;border:1px solid var(--line);border-radius:8px;
      background:var(--card);color:var(--ink);font:inherit;font-weight:600;cursor:pointer;
      text-decoration:none}
 .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
 .btn.danger{color:var(--bad)}
 .btn.sm{padding:5px 10px;font-size:13px}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
 .row>div{flex:1 1 160px}
 .msg{padding:10px 12px;border-radius:8px;margin-bottom:12px;border:1px solid}
 .msg.ok{border-color:var(--ok);color:var(--ok)}
 .msg.err{border-color:var(--bad);color:var(--bad)}
 .mute{color:var(--mute)} .small{font-size:13px}
 .detail{white-space:normal;max-width:52ch;line-height:1.35;margin-top:3px}
 td.actions{white-space:nowrap;text-align:right}
 td.status{min-width:30ch}
 td.actions form,td.actions a{margin-left:4px}
 pre{white-space:pre-wrap;word-break:break-all;background:var(--bg);padding:10px;
     border-radius:8px;font-size:12px;max-height:220px;overflow:auto}
 .grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
 @media(max-width:620px){th,td{padding:7px 6px}}
</style></head><body>
<header><nav>
 <span class="brand">Attendance</span>
 <a href="/{{ qs }}" class="{{ 'on' if page=='dash' }}">Dashboard</a>
 <a href="/roster{{ qs }}" class="{{ 'on' if page=='roster' }}">Roster</a>
 <a href="/exceptions{{ qs }}" class="{{ 'on' if page=='exc' }}">Exceptions</a>
 <a href="/accounts" class="{{ 'on' if page=='acct' }}">Connections</a>
 <a href="/settings" class="{{ 'on' if page=='set' }}">Settings</a>
 <span class="right"></span>
 <form method="post" action="/logout" class="inline">
   <input type="hidden" name="_csrf" value="{{ csrf }}">
   <button class="btn sm">Sign out</button></form>
</nav></header>
<main>
{% if flash %}<div class="msg {{ flash_kind }}">{{ flash }}</div>{% endif %}
{% block body %}{% endblock %}
</main></body></html>
"""

RANGE_FORM = """
<form method="get" class="card"><div class="row">
  <div><label>From</label><input type="date" name="from" value="{{ d1 }}"></div>
  <div><label>To</label><input type="date" name="to" value="{{ d2 }}"></div>
  <div><label>Clinic</label><select name="clinic">
    <option value="">All clinics</option>
    {% for c in clinics %}<option value="{{ c.id }}" {{ 'selected' if c.id==clinic_id }}>{{ c.name }}</option>{% endfor %}
  </select></div>
  {% if show_search %}<div><label>Search</label><input name="q" value="{{ q }}" placeholder="name or code"></div>{% endif %}
  <div style="flex:0 0 auto"><button class="btn primary">Apply</button></div>
  <div style="flex:0 0 auto"><a class="btn" href="/export.csv{{ qs }}">Export CSV</a></div>
</div></form>
"""

LOGIN = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - Attendance</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f1115;color:#e8eaed;
 font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
form{background:#171a21;border:1px solid #272b35;border-radius:12px;padding:22px;width:min(92vw,340px)}
h1{font-size:18px;margin:0 0 14px}
input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #272b35;border-radius:8px;
 background:#0f1115;color:#e8eaed;font:inherit;margin-bottom:12px}
button{width:100%;padding:10px;border:0;border-radius:8px;background:#6b9bff;color:#06101f;
 font:inherit;font-weight:700;cursor:pointer}
.err{color:#f87171;font-size:13px;margin-bottom:10px}
</style></head><body><form method="post" action="/login">
<h1>Attendance dashboard</h1>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<input type="password" name="password" placeholder="Dashboard password" autofocus>
<button>Sign in</button></form></body></html>
"""

DASH = """{% extends "base" %}{% block body %}
<h1>Overview <span class="mute small">{{ d1 }} to {{ d2 }}</span></h1>
""" + RANGE_FORM + """
<div class="tiles">
  <div class="tile"><div class="l">Staff seen</div><div class="n">{{ t.headcount }}</div></div>
  <div class="tile"><div class="l">Present</div><div class="n">{{ t.present_days }}</div></div>
  <div class="tile"><div class="l">Half days</div><div class="n">{{ t.half_days }}</div></div>
  <div class="tile"><div class="l">Absent</div><div class="n">{{ t.absent_days }}</div></div>
  <div class="tile"><div class="l">Incomplete</div><div class="n">{{ t.incomplete_days }}</div></div>
  <div class="tile"><div class="l">Late days</div><div class="n">{{ t.late_days }}</div></div>
  <div class="tile"><div class="l">Hours worked</div><div class="n">{{ (t.worked_minutes // 60) }}</div></div>
  <div class="tile"><div class="l">Overtime</div><div class="n">{{ (t.ot_minutes // 60) }}h</div></div>
</div>

<h2>By clinic</h2>
<div class="card">
{% if rows %}
<table><thead><tr><th>Clinic</th><th class="num">Staff</th><th class="num">Present</th>
<th class="num">Half</th><th class="num">Absent</th><th class="num">Incomplete</th>
<th class="num">Late days</th><th class="num">Hours</th><th class="num">OT</th></tr></thead><tbody>
{% for r in rows %}<tr>
 <td><a href="/roster?from={{ d1 }}&to={{ d2 }}&clinic={{ r.clinic_id or '' }}">{{ r.clinic }}</a></td>
 <td class="num">{{ r.headcount }}</td><td class="num">{{ r.present_days }}</td>
 <td class="num">{{ r.half_days }}</td><td class="num">{{ r.absent_days }}</td>
 <td class="num">{{ r.incomplete_days }}</td><td class="num">{{ r.late_days }}</td>
 <td class="num">{{ r.worked_minutes // 60 }}</td><td class="num">{{ r.ot_minutes // 60 }}h</td>
</tr>{% endfor %}</tbody></table>
{% else %}<p class="mute">No attendance computed for this range yet. Add a connection, then sync.</p>{% endif %}
</div>

<h2>Recent syncs</h2>
<div class="card">
{% if syncs %}
<table><thead><tr><th>When</th><th>Connection</th><th>Range</th><th class="num">Fetched</th>
<th class="num">New</th><th class="num">Days</th><th>Status</th></tr></thead><tbody>
{% for s in syncs %}<tr><td>{{ s.started_at|ts }}</td><td>{{ s.account }}</td>
<td class="small mute">{{ s.from_date }} - {{ s.to_date }}</td>
<td class="num">{{ s.fetched }}</td><td class="num">{{ s.inserted }}</td>
<td class="num">{{ s.days_computed }}</td>
<td><span class="b {{ s.status }}">{{ s.status }}</span>
{% if s.error %}<div class="small mute">{{ s.error[:160] }}</div>{% endif %}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="mute">Nothing synced yet.</p>{% endif %}
</div>
{% endblock %}
"""

ROSTER = """{% extends "base" %}{% block body %}
<h1>Roster <span class="mute small">{{ d1 }} to {{ d2 }}</span></h1>
""" + RANGE_FORM + """
<div class="card">
{% if rows %}
<table><thead><tr><th>Code</th><th>Name</th><th>Clinic</th><th>Shift</th>
<th class="num">Present</th><th class="num">Half</th><th class="num">Absent</th>
<th class="num">Incomp.</th><th class="num">Late</th><th class="num">Hours</th>
<th class="num">OT</th></tr></thead><tbody>
{% for r in rows %}<tr>
 <td><a href="/employee/{{ r.employee_id }}?from={{ d1 }}&to={{ d2 }}">{{ r.empcode }}</a></td>
 <td>{{ r.name or '-' }}</td><td>{{ r.clinic }}</td><td>{{ r.shift }}</td>
 <td class="num">{{ r.present_days }}</td><td class="num">{{ r.half_days }}</td>
 <td class="num">{{ r.absent_days }}</td><td class="num">{{ r.incomplete_days }}</td>
 <td class="num">{{ r.late_days }}</td><td class="num">{{ r.worked_minutes // 60 }}</td>
 <td class="num">{{ r.ot_minutes // 60 }}h</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="mute">No staff found for this range.</p>{% endif %}
</div>{% endblock %}
"""

EMPLOYEE = """{% extends "base" %}{% block body %}
<h1>{{ emp.name or emp.empcode }} <span class="mute small">{{ emp.empcode }} - {{ emp.clinic }} - {{ emp.shift }}</span></h1>
<div class="card">
 <form method="post" action="/employee/{{ emp.id }}/assign"><div class="row">
  <input type="hidden" name="_csrf" value="{{ csrf }}">
  <div><label>Clinic</label><select name="clinic_id">
    <option value="">(unassigned)</option>
    {% for c in clinics %}<option value="{{ c.id }}" {{ 'selected' if c.id==emp.clinic_id }}>{{ c.name }}</option>{% endfor %}
  </select></div>
  <div><label>Shift</label><select name="shift_id">
    <option value="">General (default)</option>
    {% for s in shifts %}<option value="{{ s.id }}" {{ 'selected' if s.id==emp.shift_id }}>{{ s.name }}</option>{% endfor %}
  </select></div>
  <div style="flex:0 0 auto"><button class="btn primary">Save &amp; recompute</button></div>
 </div></form>
</div>
<div class="card">
<table><thead><tr><th>Date</th><th>Status</th><th>First in</th><th>Last out</th>
<th class="num">Worked</th><th class="num">Late</th><th class="num">Early out</th>
<th class="num">OT</th><th class="num">Punches</th><th>Flags</th></tr></thead><tbody>
{% for d in days %}<tr>
 <td>{{ d.work_date }}</td><td><span class="b {{ d.status }}">{{ d.status }}</span></td>
 <td>{{ d.first_in[11:16] if d.first_in else '-' }}</td>
 <td>{{ d.last_out[11:16] if d.last_out else '-' }}</td>
 <td class="num">{{ d.worked_minutes|hhmm }}</td>
 <td class="num">{{ d.late_minutes or '' }}</td>
 <td class="num">{{ d.early_out_minutes or '' }}</td>
 <td class="num">{{ d.ot_minutes or '' }}</td>
 <td class="num">{{ d.punch_count }}</td>
 <td class="small mute">{{ d.anomalies }}</td></tr>{% endfor %}
</tbody></table>
{% if not days %}<p class="mute">No days computed in this range.</p>{% endif %}
</div>{% endblock %}
"""

EXCEPTIONS = """{% extends "base" %}{% block body %}
<h1>Exceptions <span class="mute small">{{ d1 }} to {{ d2 }}</span></h1>
<p class="mute small">Missing out-punches, shortfalls and absences - the days that need a human.</p>
""" + RANGE_FORM + """
<div class="card">
{% if rows %}
<table><thead><tr><th>Date</th><th>Code</th><th>Name</th><th>Clinic</th><th>Status</th>
<th>First in</th><th>Last out</th><th class="num">Worked</th><th>Flags</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{ r.work_date }}</td>
 <td><a href="/employee/{{ r.employee_id }}?from={{ d1 }}&to={{ d2 }}">{{ r.empcode }}</a></td>
 <td>{{ r.name or '-' }}</td><td>{{ r.clinic }}</td>
 <td><span class="b {{ r.status }}">{{ r.status }}</span></td>
 <td>{{ r.first_in[11:16] if r.first_in else '-' }}</td>
 <td>{{ r.last_out[11:16] if r.last_out else '-' }}</td>
 <td class="num">{{ r.worked_minutes|hhmm }}</td>
 <td class="small mute">{{ r.anomalies }}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="mute">Nothing to review in this range.</p>{% endif %}
</div>{% endblock %}
"""

ACCOUNTS = """{% extends "base" %}{% block body %}
<h1>Connections</h1>
<p class="mute small">One connection per attendance-software login. Credentials are
encrypted before they are stored and are never shown again.</p>
<div class="card">
{% if rows %}
<table><thead><tr><th>Label</th><th>Type</th><th>Corporate ID</th><th>User</th>
<th>Clinic</th><th class="num">Staff</th><th>Last check</th><th>Status</th><th></th></tr></thead><tbody>
{% for a in rows %}<tr>
 <td><strong>{{ a.label }}</strong>{% if not a.enabled %} <span class="b untested">disabled</span>{% endif %}</td>
 <td>{{ a.kind }}</td><td>{{ a.corp_id or '-' }}</td><td>{{ a.user_id or '-' }}</td>
 <td>{{ a.clinic_name }}</td><td class="num">{{ a.employee_count }}</td>
 <td class="small mute">{{ a.last_checked_at|ts }}</td>
 <td class="status"><span class="b {{ a.last_status }}">{{ a.last_status }}</span>
     {% if a.last_detail %}<div class="small mute detail">{{ a.last_detail|truncate(190, True, "...") }}</div>{% endif %}</td>
 <td class="actions">
   <form method="post" action="/accounts/{{ a.id }}/test" class="inline">
     <input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn sm">Test</button></form>
   <form method="post" action="/accounts/{{ a.id }}/sync" class="inline">
     <input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn sm">Sync</button></form>
   <a class="btn sm" href="/accounts/{{ a.id }}/edit">Edit</a>
   <form method="post" action="/accounts/{{ a.id }}/delete" class="inline"
         onsubmit="return confirm('Delete connection {{ a.label }}? Punches already imported are kept.')">
     <input type="hidden" name="_csrf" value="{{ csrf }}"><button class="btn sm danger">Delete</button></form>
 </td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="mute">No connections yet.</p>{% endif %}
</div>
{% if probe %}
<div class="card"><h2 style="margin-top:0">Last test: {{ probe.label }}</h2>
 <p><span class="b {{ probe.status }}">{{ probe.status }}</span> {{ probe.detail }}</p>
 {% if probe.sample %}<p class="mute small">Raw response (first 600 chars) - useful for
 confirming the field names once a connection works:</p><pre>{{ probe.sample }}</pre>{% endif %}
</div>
{% endif %}
<p><a class="btn primary" href="/accounts/new">Add connection</a></p>
{% endblock %}
"""

ACCOUNT_FORM = """{% extends "base" %}{% block body %}
<h1>{{ 'Edit' if a else 'Add' }} connection</h1>
<div class="card" style="max-width:620px">
<form method="post" action="{{ action }}">
 <input type="hidden" name="_csrf" value="{{ csrf }}">
 <label>Label <span class="mute">- how this connection is listed</span></label>
 <input name="label" value="{{ a.label if a else '' }}" required placeholder="e.g. Karol Bagh clinic">

 <label>Type</label>
 <select name="kind">
   <option value="etimeoffice" {{ 'selected' if a and a.kind=='etimeoffice' }}>eTimeOffice API</option>
   <option value="csv" {{ 'selected' if a and a.kind=='csv' }}>CSV file / portal export</option>
 </select>

 <label>Clinic <span class="mute">- staff from this login default to it</span></label>
 <select name="clinic_id">
   <option value="">(assign later)</option>
   {% for c in clinics %}<option value="{{ c.id }}" {{ 'selected' if a and c.id==a.clinic_id }}>{{ c.name }}</option>{% endfor %}
 </select>

 <label>Corporate ID</label>
 <input name="corp_id" value="{{ a.corp_id if a else '' }}" placeholder="as used on the portal login">
 <label>User ID</label>
 <input name="user_id" value="{{ a.user_id if a else '' }}">
 <label>Password {% if a and a.has_secret %}<span class="mute">- stored; leave blank to keep it</span>{% endif %}</label>
 <input type="password" name="password" autocomplete="new-password"
        placeholder="{{ 'unchanged' if a and a.has_secret else '' }}">

 <label>API base URL <span class="mute">- leave blank for the default</span></label>
 <input name="base_url" value="{{ a.base_url if a else '' }}" placeholder="{{ default_base }}">
 <label>Admin flag <span class="mute">- the 4th part of their credential string</span></label>
 <input name="admin_flag" value="{{ a.admin_flag if a else 'true' }}">

 <label><input type="checkbox" name="enabled" value="1" style="width:auto"
        {{ 'checked' if (not a) or a.enabled }}> Include in scheduled syncs</label>

 <p style="margin-top:16px">
   <button class="btn primary">{{ 'Save changes' if a else 'Add connection' }}</button>
   <a class="btn" href="/accounts">Cancel</a></p>
</form>
</div>
<p class="mute small" style="max-width:620px">For a CSV connection, put the file path
on the server in the <em>API base URL</em> field. Passwords are encrypted with the
service key before being written to disk.</p>
{% endblock %}
"""

SETTINGS = """{% extends "base" %}{% block body %}
<h1>Settings</h1>
<div class="grid2">
 <div class="card"><h2 style="margin-top:0">Clinics</h2>
  <table><thead><tr><th>Code</th><th>Name</th><th>Weekly off</th></tr></thead><tbody>
  {% for c in clinics %}<tr><td>{{ c.code }}</td><td>{{ c.name }}</td>
   <td>{{ dows[c.week_off_dow] if c.week_off_dow >= 0 else 'none' }}</td></tr>{% endfor %}
  </tbody></table>
  {% if not clinics %}<p class="mute">None yet.</p>{% endif %}
  <form method="post" action="/settings/clinic"><div class="row">
   <input type="hidden" name="_csrf" value="{{ csrf }}">
   <div><label>Code</label><input name="code" required placeholder="KB"></div>
   <div><label>Name</label><input name="name" required placeholder="Karol Bagh"></div>
   <div><label>Weekly off</label><select name="week_off_dow">
     {% for i in range(7) %}<option value="{{ i }}" {{ 'selected' if i==6 }}>{{ dows[i] }}</option>{% endfor %}
     <option value="-1">None</option></select></div>
   <div style="flex:0 0 auto"><button class="btn primary">Add</button></div>
  </div></form>
 </div>

 <div class="card"><h2 style="margin-top:0">Shifts</h2>
  <table><thead><tr><th>Code</th><th>Name</th><th>Window</th><th class="num">Grace</th>
   <th class="num">Half / Present / OT after</th></tr></thead><tbody>
  {% for s in shifts %}<tr><td>{{ s.code }}</td><td>{{ s.name }}</td>
   <td>{{ s.start_min|clock }} - {{ s.end_min|clock }}</td>
   <td class="num">{{ s.grace_in_min }}m</td>
   <td class="num">{{ s.half_day_minutes // 60 }}h / {{ s.present_minutes // 60 }}h / {{ s.full_day_minutes // 60 }}h</td></tr>{% endfor %}
  </tbody></table>
  {% if not shifts %}<p class="mute">None yet - a 09:00-18:00 general shift is assumed.</p>{% endif %}
  <form method="post" action="/settings/shift">
   <input type="hidden" name="_csrf" value="{{ csrf }}">
   <div class="row">
    <div><label>Code</label><input name="code" required placeholder="GEN"></div>
    <div><label>Name</label><input name="name" required placeholder="General"></div>
   </div>
   <div class="row">
    <div><label>Start</label><input type="time" name="start" value="09:00" required></div>
    <div><label>End</label><input type="time" name="end" value="18:00" required></div>
    <div><label>Crosses midnight</label><select name="overnight">
      <option value="">No</option><option value="1">Yes</option></select></div>
   </div>
   <div class="row">
    <div><label>Grace in (min)</label><input type="number" name="grace_in" value="15"></div>
    <div><label>Grace out (min)</label><input type="number" name="grace_out" value="15"></div>
    <div><label>Half day (hours)</label><input type="number" step="0.5" name="half" value="4"></div>
    <div><label>Present from (hours)</label><input type="number" step="0.5" name="present" value="7"></div>
    <div><label>Full day / OT after (hours)</label><input type="number" step="0.5" name="full" value="8"></div>
   </div>
   <p><button class="btn primary">Add shift</button></p>
  </form>
 </div>
</div>
<div class="card"><h2 style="margin-top:0">Import a portal export</h2>
 <p class="mute small">Backfill from a CSV already on the server. Employees appear
 automatically; assign them to a clinic afterwards from the roster.</p>
 <form method="post" action="/settings/import"><div class="row">
  <input type="hidden" name="_csrf" value="{{ csrf }}">
  <div style="flex:2 1 320px"><label>CSV path on this server</label>
    <input name="path" required placeholder="/var/lib/attendance/imports/nov.csv"></div>
  <div><label>Clinic</label><select name="clinic_id"><option value="">(assign later)</option>
    {% for c in clinics %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></div>
  <div style="flex:0 0 auto"><button class="btn primary">Import</button></div>
 </div></form>
</div>
{% endblock %}
"""

TEMPLATES = {
    "base": BASE, "login": LOGIN, "dash": DASH, "roster": ROSTER,
    "employee": EMPLOYEE, "exceptions": EXCEPTIONS, "accounts": ACCOUNTS,
    "account_form": ACCOUNT_FORM, "settings": SETTINGS,
}
