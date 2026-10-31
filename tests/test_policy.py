import pytest

from sanai.config import Autonomy, Settings
from sanai.safety.policy import PolicyEngine


def engine(tmp_path, **over):
    cfg = Settings(
        db_path=tmp_path / "sanai.db",
        workdir=tmp_path / "work",
        _env_file=None,
        **over,
    )
    return PolicyEngine(cfg)


DESTRUCTIVE = [
    "rm -rf /",
    "rm -rf /*",
    "rm -fr /",
    "sudo rm -rf --no-preserve-root /",
    "rm -rf /etc",
    "mkfs.ext4 /dev/sda1",
    "wipefs -a /dev/sda",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "shred -n 3 /dev/nvme0n1",
    "echo x > /dev/sda",
    ":(){ :|:& };:",
    "chmod -R 000 /",
    "chown -R nobody /",
    "systemctl stop sshd",
    "systemctl disable ssh",
    "pkill -9 sshd",
    "iptables -P INPUT DROP",
    "ufw --force reset",
    "userdel -r root",
    "echo '' > /etc/passwd",
    "crontab -r",
]


@pytest.mark.parametrize("cmd", DESTRUCTIVE)
def test_integrity_guard_blocks_host_destruction(tmp_path, cmd):
    """Full autonomy still refuses to destroy the host it runs on."""
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    d = pol.evaluate(cmd)
    assert d.action == "deny", f"{cmd!r} should be denied"
    assert "integrity guard" in d.reason


@pytest.mark.parametrize("cmd", DESTRUCTIVE[:6])
def test_guard_can_be_switched_off(tmp_path, cmd):
    """The guard is the operator's choice, not a hard-coded refusal."""
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=False)
    assert pol.evaluate(cmd).action == "allow"


LEGITIMATE = [
    "systemctl restart nginx",
    "apt-get install -y redis-server",
    "rm -rf /var/lib/sanai/work/tmp-build",
    "docker compose up -d",
    "git clone https://github.com/example/repo /srv/app",
    "nginx -t && systemctl reload nginx",
    "certbot --nginx -d example.com",
    "ufw allow 443/tcp",
    "psql -c 'DROP TABLE staging_tmp'",
    "rm -rf ./node_modules",
    "dd if=/dev/zero of=/swapfile bs=1M count=2048",
]


@pytest.mark.parametrize("cmd", LEGITIMATE)
def test_real_work_is_not_blocked(tmp_path, cmd):
    """The guard must not become a general-purpose veto."""
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    d = pol.evaluate(cmd)
    assert d.action == "allow", f"{cmd!r} should run, got {d.reason}"


def test_guard_protects_own_memory(tmp_path):
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    assert pol.evaluate(f"rm -rf {tmp_path / 'sanai.db'}").action == "deny"
    assert pol.evaluate(f"rm -rf {tmp_path}").action == "deny"


def test_kill_switch_overrides_everything(tmp_path):
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=False)
    assert pol.evaluate("ls").action == "allow"
    pol.engage_stop("operator halt")
    d = pol.evaluate("ls")
    assert d.action == "deny" and "kill switch" in d.reason
    pol.release_stop()
    assert pol.evaluate("ls").action == "allow"


def test_guarded_mode_allows_readonly_queues_rest(tmp_path):
    pol = engine(tmp_path, autonomy=Autonomy.GUARDED)
    assert pol.evaluate("systemctl status nginx").action == "allow"
    assert pol.evaluate("df -h").action == "allow"
    assert pol.evaluate("ss -tlnp").action == "allow"
    assert pol.evaluate("systemctl restart nginx").action == "queued"
    assert pol.evaluate("apt-get install redis").action == "queued"


def test_guarded_mode_rejects_smuggled_compound(tmp_path):
    """An allowlisted head must not drag a mutation along behind it."""
    pol = engine(tmp_path, autonomy=Autonomy.GUARDED)
    assert pol.evaluate("ls && systemctl restart nginx").action == "queued"
    assert pol.evaluate("echo hi; apt-get install redis").action == "queued"
    assert pol.evaluate("cat /etc/hosts | tee /etc/hosts.bak").action == "queued"


def test_approval_mode_queues_everything(tmp_path):
    pol = engine(tmp_path, autonomy=Autonomy.APPROVAL)
    assert pol.evaluate("ls").action == "queued"
    assert pol.evaluate("systemctl restart nginx").action == "queued"


def test_newline_continuation_cannot_hide_a_pattern(tmp_path):
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    assert pol.evaluate("rm -rf \\\n  /").action == "deny"


def test_empty_command_denied(tmp_path):
    assert engine(tmp_path).evaluate("   ").action == "deny"


FLAG_VARIANTS = [
    "rm --recursive --force /",
    'rm -rf "/"',
    "rm -rf '/'",
    "rm -rf /var/*",
    "rm -rf /usr/*",
    "sudo -u root rm -rf /",
    "env FOO=bar rm -rf /",
    "nohup rm -rf / &",
    "rm -r --no-preserve-root /",
    "/bin/rm -rf /",
    "rm -rf /home",
    "make build && rm -rf /etc",
]


@pytest.mark.parametrize("cmd", FLAG_VARIANTS)
def test_delete_analyser_catches_flag_order_variants(tmp_path, cmd):
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    assert pol.evaluate(cmd).action == "deny", f"{cmd!r} should be denied"


ORDINARY_DELETES = [
    "rm -rf /var/lib/myapp/cache",
    "rm -rf /tmp/build-123",
    "rm -rf ./dist",
    "rm -f /etc/nginx/sites-enabled/default",
    "rm -rf /home/deploy/releases/old",
    "rm -rf /srv/app/node_modules",
]


@pytest.mark.parametrize("cmd", ORDINARY_DELETES)
def test_deletes_inside_critical_dirs_are_ordinary_work(tmp_path, cmd):
    """Only the critical directory itself is protected, not its contents."""
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    d = pol.evaluate(cmd)
    assert d.action == "allow", f"{cmd!r} should run, got {d.reason}"


def test_quoted_or_echoed_text_is_not_a_delete(tmp_path):
    """A dangerous string inside an argument is not a dangerous command."""
    pol = engine(tmp_path, autonomy=Autonomy.FULL, integrity_guard=True)
    for cmd in [
        'echo "rm -rf /"',
        "echo rm -rf /",
        "git commit -m 'never rm -rf / again'",
        "grep -r 'rm -rf /' /var/log",
    ]:
        assert pol.evaluate(cmd).action == "allow", cmd
