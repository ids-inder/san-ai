"""Dashboard templates. Kept in-module so there is nothing to package or
fetch - the server may have no outbound internet access."""

BASE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}San AI{% endblock %}</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--fg:#e4e7ee;--dim:#8a93a6;
--green:#4ec98a;--yellow:#e3b341;--red:#f06a6a;--blue:#6aa9f0;--mag:#c88ae0;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid var(--line);padding:14px 22px;display:flex;
gap:18px;align-items:center;flex-wrap:wrap;background:var(--panel)}
header h1{font-size:15px;margin:0;letter-spacing:.14em;text-transform:uppercase}
header nav{display:flex;gap:14px;margin-left:auto;flex-wrap:wrap}
main{padding:22px;max-width:1180px;margin:0 auto}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:16px 18px;margin-bottom:18px}
.panel h2{margin:0 0 12px;font-size:12px;letter-spacing:.12em;
text-transform:uppercase;color:var(--dim)}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
letter-spacing:.08em}
tr:last-child td{border-bottom:none}
code,pre{font-family:inherit}
pre{background:#0b0d11;border:1px solid var(--line);border-radius:6px;
padding:11px 13px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;
margin:8px 0;max-height:420px;overflow-y:auto}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;
border:1px solid currentColor}
.mastered,.done,.ok,.allow{color:var(--green)}
.learning,.blocked,.queued,.awaiting_approval,.pending{color:var(--yellow)}
.failed,.halted,.deny,.rejected{color:var(--red)}
.demoted{color:var(--mag)}.new,.running{color:var(--blue)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
.stat{background:#0b0d11;border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.stat b{display:block;font-size:21px;font-weight:600;margin-bottom:2px}
.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.dim{color:var(--dim)}
.bar{height:5px;background:#0b0d11;border-radius:3px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--green)}
button{background:var(--panel);color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:7px 14px;cursor:pointer;font:inherit}
button:hover{border-color:var(--dim)}
button.danger{color:var(--red)}button.go{color:var(--green)}
.banner{border:1px solid var(--red);color:var(--red);border-radius:8px;
padding:12px 16px;margin-bottom:18px;display:flex;align-items:center;gap:14px}
.banner form{margin-left:auto}
.wrap{max-width:460px;overflow-wrap:anywhere}
</style></head><body>
<header>
  <h1>San AI</h1>
  <span class="dim">{{ cfg.provider }} &middot; {{ cfg.model }}</span>
  <span class="tag {{ 'failed' if cfg.autonomy.value == 'full' else 'learning' }}">
    autonomy: {{ cfg.autonomy.value }}</span>
  {% if not cfg.integrity_guard %}<span class="tag failed">guard off</span>{% endif %}
  <nav>
    <a href="/{{ q }}">overview</a>
    <a href="/audit{{ q }}">audit</a>
  </nav>
</header>
<main>
{% if stopped %}
<div class="banner">
  <b>KILL SWITCH ENGAGED</b>
  <span class="dim">no command will run</span>
  <form method="post" action="/resume{{ q }}"><button class="go">resume</button></form>
</div>
{% endif %}
{% block body %}{% endblock %}
</main></body></html>"""

INDEX = """{% extends "base" %}{% block body %}
<div class="panel">
  <h2>state</h2>
  <div class="grid">
    <div class="stat"><b>{{ stats.mastered }}</b><span>mastered</span></div>
    <div class="stat"><b>{{ stats.learning }}</b><span>learning</span></div>
    <div class="stat"><b>{{ stats.failed }}</b><span>failed</span></div>
    <div class="stat"><b>{{ stats.tasks }}</b><span>tasks</span></div>
    <div class="stat"><b>{{ stats.commands }}</b><span>commands run</span></div>
    <div class="stat"><b>{{ stats.refused }}</b><span>refused</span></div>
  </div>
  {% if not stopped %}
  <form method="post" action="/stop{{ q }}" style="margin-top:14px">
    <button class="danger">engage kill switch</button>
  </form>
  {% endif %}
</div>

<div class="panel">
  <h2>modules</h2>
  {% if modules %}
  <table><tr><th>module</th><th>status</th><th>mastery</th><th>rounds</th><th>goal</th></tr>
  {% for m in modules %}
  <tr>
    <td><a href="/module/{{ m.name|urlencode }}{{ q }}">{{ m.name }}</a></td>
    <td><span class="tag {{ m.status }}">{{ m.status }}</span></td>
    <td>{{ '%.0f' % (m.mastery * 100) }}%<div class="bar"><i style="width:{{ m.mastery * 100 }}%"></i></div></td>
    <td>{{ m.rounds }}</td><td class="dim wrap">{{ m.goal }}</td>
  </tr>
  {% endfor %}</table>
  {% else %}<p class="dim">Nothing learned yet.
  Run <code>sanai learn "&lt;concept&gt;"</code> to teach it something.</p>{% endif %}
</div>

<div class="panel">
  <h2>tasks</h2>
  {% if tasks %}
  <table><tr><th>id</th><th>status</th><th>request</th><th>result</th></tr>
  {% for t in tasks %}
  <tr><td><a href="/task/{{ t.id }}{{ q }}">{{ t.id }}</a></td>
    <td><span class="tag {{ t.status }}">{{ t.status }}</span></td>
    <td class="wrap">{{ t.request }}</td>
    <td class="dim wrap">{{ t.result }}</td></tr>
  {% endfor %}</table>
  {% else %}<p class="dim">No tasks yet.</p>{% endif %}
</div>
{% endblock %}"""

MODULE = """{% extends "base" %}{% block title %}{{ module.name }} - San AI{% endblock %}
{% block body %}
<div class="panel">
  <h2>{{ module.name }} <span class="tag {{ module.status }}">{{ module.status }}</span></h2>
  <p class="dim">{{ module.goal }}</p>
  <div class="grid">
    <div class="stat"><b>{{ '%.0f' % (module.mastery * 100) }}%</b><span>mastery</span></div>
    <div class="stat"><b>{{ '%.0f' % (cfg.mastery_threshold * 100) }}%</b><span>threshold</span></div>
    <div class="stat"><b>{{ module.rounds }}</b><span>study rounds</span></div>
  </div>
</div>

<div class="panel"><h2>concept card</h2><pre>{{ card }}</pre></div>

{% if exam %}
<div class="panel">
  <h2>last exam &middot; round {{ exam.round }} &middot; {{ '%.0f' % (exam.score * 100) }}%</h2>
  <table><tr><th>w</th><th>score</th><th>question</th><th>expected</th>
    <th>answered</th><th>verdict</th></tr>
  {% for d in detail %}
  <tr><td>{{ d.weight }}</td>
    <td class="{{ 'ok' if d.score >= 1 else ('blocked' if d.score > 0 else 'failed') }}">
      {{ '%.1f' % d.score }}</td>
    <td class="wrap">{{ d.question }}</td>
    <td class="dim wrap">{{ d.reference_answer }}</td>
    <td class="wrap">{{ d.answer }}{% if d.unsure %} <span class="tag blocked">unsure</span>{% endif %}</td>
    <td class="dim wrap">{{ d.verdict }}</td></tr>
  {% endfor %}</table>
  {% if gaps %}<h2 style="margin-top:16px">gaps</h2>
  <ul>{% for g in gaps %}<li class="blocked">{{ g }}</li>{% endfor %}</ul>{% endif %}
</div>
{% endif %}

{% if lessons %}
<div class="panel"><h2>lessons from running it</h2>
  <table><tr><th>outcome</th><th>lesson</th></tr>
  {% for l in lessons %}
  <tr><td><span class="tag {{ 'done' if l.outcome == 'success' else 'failed' }}">{{ l.outcome }}</span></td>
    <td class="wrap">{{ l.lesson }}</td></tr>
  {% endfor %}</table></div>
{% endif %}

{% if sources %}
<div class="panel"><h2>sources studied</h2>
  <table><tr><th>kind</th><th>origin</th><th>chars</th></tr>
  {% for s in sources %}
  <tr><td>{{ s.kind }}</td><td class="wrap">{{ s.origin }}</td><td>{{ s.size }}</td></tr>
  {% endfor %}</table></div>
{% endif %}
{% endblock %}"""

TASK = """{% extends "base" %}{% block title %}task {{ task.id }} - San AI{% endblock %}
{% block body %}
<div class="panel">
  <h2>task {{ task.id }} <span class="tag {{ task.status }}">{{ task.status }}</span></h2>
  <p class="wrap">{{ task.request }}</p>
  <p class="dim wrap">{{ task.result }}</p>
  {% if plan.understanding %}<p><b>understanding:</b> {{ plan.understanding }}</p>{% endif %}
</div>

{% if pending %}
<div class="panel">
  <h2>awaiting approval</h2>
  <table><tr><th>#</th><th>risk</th><th>command</th><th>rollback</th></tr>
  {% for s in pending %}
  <tr><td>{{ s.n }}</td>
    <td class="{{ 'failed' if s.risk == 'destructive' else ('blocked' if s.risk == 'mutating' else 'ok') }}">{{ s.risk }}</td>
    <td class="wrap">{{ s.command }}</td><td class="dim wrap">{{ s.rollback }}</td></tr>
  {% endfor %}</table>
  <p class="dim">Approve from the server:
    <code>sanai approve {{ task.id }}</code></p>
</div>
{% endif %}

{% if plan.steps %}
<div class="panel"><h2>plan</h2>
  <table><tr><th>#</th><th>risk</th><th>command</th><th>expected</th><th>rollback</th></tr>
  {% for s in plan.steps %}
  <tr><td>{{ s.n }}</td>
    <td class="{{ 'failed' if s.risk == 'destructive' else ('blocked' if s.risk == 'mutating' else 'ok') }}">{{ s.risk }}</td>
    <td class="wrap">{{ s.command }}</td><td class="dim wrap">{{ s.expect }}</td>
    <td class="dim wrap">{{ s.rollback }}</td></tr>
  {% endfor %}</table></div>
{% endif %}

<div class="panel"><h2>commands</h2>
  {% for r in runs %}
    {% if r.decision != 'allow' %}
      <p><span class="tag {{ r.decision }}">{{ r.decision }}</span>
        <code>{{ r.command }}</code><br><span class="dim">{{ r.reason }}</span></p>
    {% else %}
      <p><span class="tag {{ 'ok' if r.exit_code == 0 else 'failed' }}">exit {{ r.exit_code }}</span>
        <code>{{ r.command }}</code>
        <span class="dim">{{ '%.1f' % r.duration }}s</span></p>
      {% if r.stdout %}<pre>{{ r.stdout }}</pre>{% endif %}
      {% if r.stderr %}<pre class="failed">{{ r.stderr }}</pre>{% endif %}
    {% endif %}
  {% else %}<p class="dim">No commands recorded.</p>{% endfor %}
</div>
{% endblock %}"""

AUDIT = """{% extends "base" %}{% block title %}audit - San AI{% endblock %}
{% block body %}
<div class="panel"><h2>audit log (newest first)</h2>
  <table><tr><th>when</th><th>event</th><th>payload</th></tr>
  {% for a in entries %}
  <tr><td class="dim">{{ a.when }}</td><td>{{ a.event }}</td>
    <td class="dim wrap">{{ a.payload }}</td></tr>
  {% endfor %}</table></div>
{% endblock %}"""

TEMPLATES = {
    "base": BASE,
    "index": INDEX,
    "module": MODULE,
    "task": TASK,
    "audit": AUDIT,
}
