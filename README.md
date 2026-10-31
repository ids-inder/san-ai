# San AI

A self-hosted agent that learns a concept, **proves it understands it**, and only
then executes it on your server.

The premise is the gate. Most "AI runs your server" tools let a model improvise
shell commands from whatever it half-remembers. San AI refuses to act on a
concept until a closed-book exam says it actually learned it.

```
sanai learn "nginx reverse proxy" --url https://nginx.org/en/docs/http/ngx_http_proxy_module.html
  ingest    gathering material on nginx reverse proxy
  ingest    7 source(s) stored
  distill   round 1: writing concept card
  exam      round 1: closed-book exam (10 questions)
  exam      round 1: scored 71% (need 85%) - 3 gap(s)
  ingest    pulled 4 extra source(s) targeting the gaps
  distill   round 2: writing concept card
  exam      round 2: scored 92% (need 85%) - PASS

sanai do "put app.example.com behind nginx on port 3000"
```

## How it works

```
       ┌─────────┐   sources, verbatim
       │ INGEST  │   urls · files · dirs · notes · local man/--help
       └────┬────┘
            ▼
       ┌─────────┐   a Concept Card in the agent's own words:
       │ DISTILL │   mental model · key facts · commands + risk
       └────┬────┘   invariants · failure modes · verification
            ▼
       ┌──────────────┐
       │ COMPREHEND   │  examiner  sees SOURCES only  → questions + answers
       │  (the gate)  │  student   sees the CARD only → answers closed-book
       └────┬─────────┘  grader    sees neither       → scores, names gaps
            │
      score < threshold ──► study the gaps, re-distill, re-examine
            │                          (up to max_learn_rounds, then fail)
      score ≥ threshold
            ▼
       ┌─────────┐   plan → preflight → execute → verify
       │   ACT   │   bounded diagnose-and-replan on failure
       └────┬────┘   refuses outright if no mastered module covers the task
            ▼
       ┌─────────┐   durable lessons written back to the module
       │ REFLECT │   notes that proved wrong → module demoted, must re-learn
       └─────────┘
```

**Why the isolation matters.** The student never sees the source material it's
being tested on, and the grader never sees the notes. So a card that dropped a
load-bearing fact — a default port, an ordering constraint, the difference
between `reload` and `restart` — fails the exam instead of failing in
production. Scoring is weighted by consequence: a question marked "outage if
wrong" counts triple, and an ungraded or malformed answer scores zero. The gate
fails closed.

## Install

```bash
git clone <this repo> san-ai && cd san-ai
sudo ./install.sh
$EDITOR /etc/sanai/sanai.env     # set ANTHROPIC_API_KEY
sanai doctor
```

## Use

```bash
# teach it
sanai learn "systemd units" --probe --note "we deploy with systemd templates"
sanai learn "our deploy process" --dir /srv/app/deploy --file /etc/nginx/nginx.conf

# see what it knows and how it scored
sanai modules
sanai show "systemd units"

# put it to work
sanai do "restart the api service and confirm it is healthy"
sanai do "add a swapfile of 2G" --dry-run        # plan only
sanai do "..." --budget 2.00                     # stop at $2 of model spend

# review
sanai tasks
sanai task 7            # every command, exit code and output
sanai audit

# emergency stop
sanai stop              # refuses every command until `sanai resume`
```

Each `learn`/`do` prints what it cost. `--budget` caps it.

## The dashboard

```bash
systemctl enable --now sanai-web
grep SANAI_WEB_TOKEN /etc/sanai/sanai.env
ssh -L 8899:127.0.0.1:8899 root@your-server
# open http://127.0.0.1:8899/?token=<token>
```

Binds loopback only. Shows modules and mastery, the full exam with expected vs
given answers, every plan and command with its output, the audit log, and a
kill switch. To publish it, put it behind TLS with `deploy/nginx.conf.example`.

## Autonomy

Set in `/etc/sanai/sanai.env`:

| `SANAI_AUTONOMY` | Behaviour |
|---|---|
| `full` | Executes immediately. Nobody in the loop. **Default.** |
| `guarded` | Read-only allowlisted commands run; everything else queues for `sanai approve` |
| `approval` | Every command queues |

Independently, `SANAI_INTEGRITY_GUARD=true` refuses the narrow set of commands
that would destroy this host or lock you out of it — formatting a disk, killing
`sshd`, default-DROP on INPUT, deleting `/`, wiping San AI's own audit trail. It
parses `rm` flags and targets rather than matching their order, so
`rm -rf --no-preserve-root /` is caught as readily as the textbook form, while
`rm -rf /var/lib/myapp/cache` runs untouched. All of it is editable in
`sanai/safety/policy.py` — `INTEGRITY_RULES`, `CRITICAL_PATHS`, `ALLOWLIST`.

**What is not limited under `full`:** privileges (it runs as whoever starts it),
filesystem reach outside the guard list, outbound network, or time of day. For
containment that doesn't reduce autonomy, uncomment the sandbox block in
`deploy/sanai-task@.service` — that bounds the blast radius at the kernel rather
than by regex.

## The brain

```bash
SANAI_PROVIDER=anthropic        # SANAI_MODEL=claude-opus-5-5
SANAI_PROVIDER=ollama           # fully offline; SANAI_OLLAMA_MODEL=qwen2.5:14b-instruct
```

The engine talks to a two-method interface (`text`, `json`) and knows nothing
about which model answers. To add your own, implement `providers/base.py:LLM`
and register it in `providers/__init__.py`. Nothing else changes.

Claude requests stream, use adaptive thinking, cache the system prompt, and opt
into server-side refusal fallback — an unattended task shouldn't die because one
call tripped a safety classifier.

## Tuning the gate

| Variable | Default | Effect |
|---|---|---|
| `SANAI_MASTERY_THRESHOLD` | `0.85` | Weighted exam score required to unlock execution |
| `SANAI_MAX_LEARN_ROUNDS` | `4` | Study attempts before a module is marked `failed` |
| `SANAI_QUIZ_SIZE` | `10` | Questions per exam |
| `SANAI_MAX_ACT_ROUNDS` | `12` | Plan/diagnose iterations per task |
| `SANAI_CMD_TIMEOUT` | `300` | Per-command seconds, then killed |

At 0.85 over 10 weighted questions a module needs roughly nine right from
memory alone. Raise it to 0.95 and the gate gets genuinely hard; drop it to 0.6
and it's decorative.

## Layout

```
sanai/
  config.py          settings and autonomy levels
  db.py              SQLite store + FTS5 BM25 retrieval (no embedding service)
  executor.py        the one chokepoint every command passes through
  providers/         swappable brain: base · anthropic · ollama
  safety/policy.py   integrity guard, allowlist, kill switch
  engine/
    ingest.py        collect source material
    distill.py       write the Concept Card
    comprehend.py    the gate: examiner / student / grader
    learn.py         the study loop
    act.py           plan · preflight · execute · verify · diagnose
    reflect.py       write experience back, demote bad knowledge
  web/app.py         dashboard
tests/               82 tests, no API calls required
```

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

The engine suite drives the whole loop against a stub brain with real shell
commands, and asserts the exam's information isolation actually holds.

## Honest limits

- **It is as good as what you feed it.** `--probe` reads local man pages, which
  are often thin. Give it real documentation with `--url` and `--file`.
- **Passing the exam is not competence, it's evidence of it.** The gate catches
  notes that lost information. It cannot catch a source that was wrong.
- **Reflection demotes on repeated failure, not instantly** — two recorded
  knowledge failures, so one bad night doesn't erase a good module.
- **No approval UI.** Queued commands are approved from a shell, on purpose.
