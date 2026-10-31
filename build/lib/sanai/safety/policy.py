"""Command policy.

Three autonomy levels (see config.Autonomy). Independently of them, the
*integrity guard* refuses the small set of commands that would destroy the
machine San AI runs on, or its own memory and audit trail - an agent that
wipes its own host has failed the task, not completed it. The guard is
configurable (SANAI_INTEGRITY_GUARD=false removes it); everything else runs
unattended under autonomy=full.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ..config import Autonomy, Settings

STOP_FILE_NAME = "STOP"


@dataclass(frozen=True)
class Decision:
    action: str   # "allow" | "deny" | "queued"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


# --------------------------------------------------------------------------
# Integrity guard: irrecoverable damage to this host or this agent.
# --------------------------------------------------------------------------
INTEGRITY_RULES: list[tuple[str, str]] = [
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\bwipefs\b", "filesystem signature wipe"),
    (r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|vd|xvd|hd)", "raw write to a block device"),
    (r"\bshred\b[^|;&]*/dev/", "shredding a block device"),
    (r">\s*/dev/(sd|nvme|vd|xvd|hd)\w*", "redirect over a block device"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bchmod\s+(-[a-zA-Z]+\s+)*0*00?0?\s+/(\s|$)", "stripping permissions from /"),
    (r"\bchmod\s+-[a-zA-Z]*R[a-zA-Z]*\s+0*000\s+/", "recursive permission wipe from /"),
    (r"\bchown\s+-[a-zA-Z]*R[a-zA-Z]*\s+\S+\s+/(\s|$)", "recursive chown of /"),
    (r"\b(systemctl|service)\s+(stop|disable|mask)\s+(ssh|sshd)\b",
     "disabling SSH - would lock you out of the server"),
    (r"\b(pkill|killall)\s+(-\w+\s+)*sshd\b", "killing sshd"),
    (r"\biptables\s+(-P|--policy)\s+INPUT\s+DROP\b",
     "default-DROP on INPUT - would lock you out of the server"),
    (r"\bufw\s+(--force\s+)?(default\s+deny\s+incoming|reset)\b",
     "firewall reset/deny-all - would lock you out of the server"),
    (r"\b(userdel|deluser)\s+(-\w+\s+)*root\b", "deleting root"),
    (r">\s*/etc/(passwd|shadow|sudoers)\b", "overwriting an auth database"),
    (r"\bcrontab\s+-r\b", "wiping all cron jobs"),
    (r"\bmv\s+/\s", "moving the filesystem root"),
]

_COMPILED_INTEGRITY = [(re.compile(p, re.I), why) for p, why in INTEGRITY_RULES]

# Commands safe to run unattended even in guarded mode.
ALLOWLIST: list[str] = [
    r"^\s*(ls|pwd|whoami|id|hostname|uname|uptime|date)\b",
    r"^\s*(cat|head|tail|wc|file|stat|readlink|realpath)\b",
    r"^\s*(grep|rg|egrep|fgrep|awk|sed\s+-n|cut|sort|uniq|tr|column)\b",
    r"^\s*(find|locate|which|type|command\s+-v)\b",
    r"^\s*(df|du|free|lsblk|blkid|mount)\s*(-\w+)*\s*$",
    r"^\s*(ps|top\s+-b|pgrep|lsof)\b",
    r"^\s*(ss|netstat|ip\s+(a|addr|r|route|link)|ping\s+-c)\b",
    r"^\s*systemctl\s+(status|is-active|is-enabled|list-units|show|cat)\b",
    r"^\s*journalctl\b(?![^|;&]*--vacuum)",
    r"^\s*(nginx\s+-t|apachectl\s+configtest|sshd\s+-t)\b",
    r"^\s*(docker|podman)\s+(ps|images|logs|inspect|version|info|stats)\b",
    r"^\s*git\s+(status|log|diff|show|branch|remote|rev-parse|ls-files)\b",
    r"^\s*(apt|apt-get|dpkg)\s+(-s|--simulate|list|show|-l)\b",
    r"^\s*(pip|pip3)\s+(list|show|freeze)\b",
    r"^\s*(curl|wget)\s+(-[a-zA-Z]+\s+)*(-I|--head)\b",
    r"^\s*(man|--help|\w+\s+--help|\w+\s+-h)\b",
    r"^\s*echo\b",
    r"^\s*test\s+-",
]

_COMPILED_ALLOW = [re.compile(p, re.I) for p in ALLOWLIST]


# Directories whose deletion breaks the machine. Matched exactly (and with a
# trailing slash or /* glob) - a path *inside* one of these is ordinary work,
# so `rm -rf /var/lib/app/cache` is fine while `rm -rf /var` is not.
CRITICAL_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
    "/usr/bin", "/usr/lib", "/usr/local", "/usr/sbin", "/etc/ssh", "/var/lib",
}

# Wrappers that may precede the real command.
_WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice",
             "ionice", "setsid", "stdbuf", "xargs"}

_RECURSIVE_FLAGS = {"-r", "-R", "--recursive"}


_DELETE_CMDS = {"rm", "shred", "srm"}


def _strip_wrappers(toks: list[str]) -> list[str]:
    """Skip `sudo`, `env FOO=bar`, `nice -n 5` etc. to reach the real command.

    Returns [] if a non-wrapper command is found first, so that `echo rm -rf /`
    is not mistaken for a delete.
    """
    i = 0
    prev_was_flag = False
    while i < len(toks):
        tok = toks[i]
        if os.path.basename(tok) in _DELETE_CMDS:
            return toks[i:]
        if tok in _WRAPPERS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            prev_was_flag = False
        elif tok.startswith("-"):
            # A wrapper's own flag, e.g. `sudo -u app`.
            prev_was_flag = "=" not in tok
        elif prev_was_flag:
            # The value belonging to that flag.
            prev_was_flag = False
        else:
            return []
        i += 1
    return []


def _tokenise(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _normalise_target(raw: str) -> str:
    """Reduce a delete target to the directory it would destroy."""
    t = raw.strip().strip("'\"")
    if t in ("/", "/*", "/."):
        return "/"
    if t.endswith("/*"):
        t = t[:-2]
    t = t.rstrip("/")
    return t or "/"


def dangerous_delete(segment: str) -> str | None:
    """Reason a delete command is refused, else None.

    Parses flags and targets rather than pattern-matching their order, so
    `rm -rf --no-preserve-root /` and `rm --recursive --force /` are caught
    as readily as the textbook `rm -rf /`.
    """
    toks = _strip_wrappers(_tokenise(segment))
    if not toks:
        return None

    flags: list[str] = []
    targets: list[str] = []
    for tok in toks[1:]:
        if tok == "--":
            continue
        if tok.startswith("--"):
            flags.append(tok)
        elif tok.startswith("-") and len(tok) > 1:
            flags.extend(f"-{ch}" for ch in tok[1:])
        else:
            targets.append(tok)

    recursive = any(f in _RECURSIVE_FLAGS for f in flags)
    for raw in targets:
        norm = _normalise_target(raw)
        if norm in CRITICAL_PATHS and (norm == "/" or recursive or raw.endswith("/*")):
            return f"delete targeting {norm} (a critical system directory)"
    return None


class PolicyEngine:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    # -- kill switch -------------------------------------------------------
    @property
    def stop_file(self) -> Path:
        return self.cfg.db_path.parent / STOP_FILE_NAME

    def stopped(self) -> bool:
        return self.stop_file.exists()

    def engage_stop(self, note: str = "") -> None:
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.stop_file.write_text(note or "stopped by operator\n", encoding="utf-8")

    def release_stop(self) -> None:
        self.stop_file.unlink(missing_ok=True)

    # -- the decision ------------------------------------------------------
    def integrity_violation(self, command: str) -> str | None:
        """Return the reason a command is refused by the guard, else None."""
        text = _normalise(command)
        for rx, why in _COMPILED_INTEGRITY:
            if rx.search(text):
                return why
        for seg in _segments(text):
            why = dangerous_delete(seg)
            if why:
                return why
        if self._touches_own_memory(text):
            return "would destroy San AI's own memory or audit trail"
        return None

    def _touches_own_memory(self, text: str) -> bool:
        protected = [str(self.cfg.db_path), str(self.cfg.db_path.parent)]
        destructive = re.search(r"\b(rm|shred|truncate|mv)\b|>\s*\S", text)
        if not destructive:
            return False
        return any(p in text and p != str(self.cfg.workdir) for p in protected)

    def allowlisted(self, command: str) -> bool:
        text = _normalise(command)
        # A compound command is only allowlisted if every segment is.
        segments = _segments(text)
        if not segments:
            return False
        return all(any(rx.match(seg) for rx in _COMPILED_ALLOW) for seg in segments)

    def evaluate(self, command: str) -> Decision:
        if not command.strip():
            return Decision("deny", "empty command")

        if self.stopped():
            return Decision("deny", "kill switch engaged (STOP file present)")

        if self.cfg.integrity_guard:
            why = self.integrity_violation(command)
            if why:
                return Decision("deny", f"integrity guard: {why}")

        if self.cfg.autonomy is Autonomy.FULL:
            return Decision("allow", "autonomy=full")

        if self.cfg.autonomy is Autonomy.GUARDED:
            if self.allowlisted(command):
                return Decision("allow", "allowlisted read-only command")
            return Decision("queued", "autonomy=guarded: needs approval")

        return Decision("queued", "autonomy=approval: needs approval")


def _segments(command: str) -> list[str]:
    """Split a compound command into the individual commands it runs."""
    parts = re.split(r"&&|\|\||;|\||\n", command)
    return [p.strip() for p in parts if p.strip()]


def _normalise(command: str) -> str:
    """Collapse whitespace and strip quoting that hides a pattern."""
    text = command.strip()
    text = text.replace("\\\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text
