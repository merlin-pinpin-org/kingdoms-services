"""Remote host management over SSH — private key auth only.

Hosts are stored in a per-user TOML config (~/.config/kingdoms/cli.toml).
The CLI never stores secrets: authentication relies on the user's own
ssh keys/agent (kingdoms-services#192).
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "kingdoms")
CONFIG_PATH = os.path.join(CONFIG_DIR, "cli.toml")
FIELDS = ("host", "user", "port", "key")
DESTRUCTIVE = ("rm ", "rm -", "mkfs", "dd ", ":(){", "shutdown", "reboot", "drop database")


@dataclass(frozen=True)
class Host:
    """A configured remote host."""

    name: str
    host: str
    user: str
    port: int = 22
    key: str | None = None


def _ensure_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_hosts() -> dict[str, Host]:
    """Read hosts from the TOML config; missing file means no hosts."""
    if not os.path.isfile(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "rb") as fh:
        data = tomllib.load(fh)
    hosts: dict[str, Host] = {}
    for name, table in data.get("hosts", {}).items():
        hosts[name] = Host(name=name, **{k: v for k, v in table.items() if k in FIELDS or k == "name"})
    return hosts


def _write_hosts(hosts: dict[str, Host]) -> None:
    _ensure_dir()
    lines = ["# kingdoms CLI config. No secrets here — ssh keys live in ~/.ssh.\n"]
    for host in hosts.values():
        lines.append(f'[hosts.{host.name}]\nhost = "{host.host}"\nuser = "{host.user}"\n')
        lines.append(f"port = {host.port}\n")
        if host.key:
            lines.append(f'key = "{host.key}"\n')
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _validate(name: str, host: str, user: str, port: str, key: str | None) -> Host | None:
    if not name or not host or not user:
        print("FAIL: --name, --host and --user are required.")
        return None
    try:
        port_n = int(port)
    except ValueError:
        print(f"FAIL: port {port!r} is not a number.")
        return None
    if key and not os.path.isfile(os.path.expanduser(key)):
        print(f"FAIL: key file not found: {key}")
        print(" -> Use an absolute path to your PRIVATE key (e.g. ~/.ssh/id_ed25519).")
        return None
    return Host(name=name, host=host, user=user, port=port_n, key=os.path.expanduser(key) if key else None)


def cmd_add(name: str, host: str, user: str, port: str, key: str | None) -> int:
    """Add (or overwrite, after confirmation) a host in the user config."""
    new = _validate(name, host, user, port, key)
    if new is None:
        return 1
    hosts = load_hosts()
    if name in hosts and input(f"Host '{name}' exists — overwrite? (yes/no): ").strip().lower() != "yes":
        print("Aborted.")
        return 0
    hosts[name] = new
    _write_hosts(hosts)
    print(f"Saved host '{name}' in {CONFIG_PATH}")
    print("Test it: kingdoms remote status " + name)
    return 0


def cmd_list() -> int:
    """List configured hosts."""
    hosts = load_hosts()
    if not hosts:
        print("No hosts yet. Add one: kingdoms remote add --name myhost --host 1.2.3.4 --user root")
        print(f"(stored in {CONFIG_PATH} — no passwords, only ssh keys.)")
        return 0
    for h in hosts.values():
        print(f"{h.name}: {h.user}@{h.host}:{h.port}" + (f" key={h.key}" if h.key else ""))
    return 0


def cmd_rm(name: str) -> int:
    """Remove a host from the user config."""
    hosts = load_hosts()
    if name not in hosts:
        print(f"FAIL: no host named '{name}'. Known: {', '.join(sorted(hosts)) or 'none'}")
        return 1
    del hosts[name]
    _write_hosts(hosts)
    print(f"Removed host '{name}'.")
    return 0


def _ssh_argv(h: Host, remote_cmd: list[str]) -> list[str]:
    """Build the ssh argv: key auth only, passwords refused."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "-p", str(h.port)]
    if h.key:
        argv += ["-i", h.key]
    argv.append(f"{h.user}@{h.host}")
    return argv + remote_cmd


def _warn_destructive(remote_cmd: list[str]) -> bool:
    """Ask for confirmation when the remote command looks destructive."""
    joined = " ".join(remote_cmd)
    if any(d in joined for d in DESTRUCTIVE):
        answer = input("WARNING: this command looks destructive. Type 'yes' to run it: ").strip().lower()
        return answer == "yes"
    return True


def _connect(h: Host, remote_cmd: list[str], *, interactive: bool = False, confirm_destructive: bool = False) -> int:
    if interactive:
        print(f"Opening an ssh session on {h.name} ({h.user}@{h.host}). Type 'exit' to come back.\n")
        result = subprocess.run(_ssh_argv(h, []), check=False)
        return result.returncode
    if confirm_destructive and not _warn_destructive(remote_cmd):
        print("Aborted. Nothing was executed.")
        return 0
    print(f"$ {' '.join(_ssh_argv(h, remote_cmd))}\n")
    result = subprocess.run(_ssh_argv(h, remote_cmd), check=False)
    rc = result.returncode
    if rc != 0:
        print(f"\nssh exited with code {rc}. Checklist:")
        print(" - Is the machine on and reachable? (kingdoms remote status " + h.name + ")")
        print(" - Is your ssh key loaded? Try: ssh-add ~/.ssh/<your-key>")
        print(" - Passwords are refused on purpose — use key authentication.")
    return rc


def main(argv: list[str]) -> int:
    """Dispatch `kingdoms remote <action>`; return exit code."""
    if len(argv) < 2:
        print("Usage: kingdoms remote <action> [args]")
        print("  add --name N --host H --user U [--port 22] [--key ~/.ssh/id_ed25519]")
        print("  list | rm <name> | ssh <name>")
        print("  status <name> | logs <name> | run <name> '<command>' | deploy <name>")
        return 2 if argv else 0
    action, rest = argv[0], argv[1:]
    if action == "list" and not rest:
        return cmd_list()
    if action == "add" and rest:
        opts = _parse_add(rest)
        return cmd_add(
            name=str(opts["name"]),
            host=str(opts["host"]),
            user=str(opts["user"]),
            port=str(opts["port"]),
            key=opts["key"],
        ) if opts else 1
    if action == "rm" and len(rest) == 1:
        return cmd_rm(rest[0])
    return _run_host_action(action, rest)


def _run_host_action(action: str, rest: list[str]) -> int:
    """Dispatch actions that need a configured host; return exit code."""
    hosts = load_hosts()
    if len(rest) < 1 or rest[0] not in hosts:
        known = ", ".join(sorted(hosts)) or "none yet (kingdoms remote add ...)"
        print(f"FAIL: unknown host '{rest[0] if rest else ''}'. Known: {known}")
        return 1
    h = hosts[rest[0]]
    if action == "ssh":
        return _connect(h, [], interactive=True)
    if action == "status":
        return _connect(h, ["uptime"])
    if action == "logs":
        return _connect(h, ["docker compose logs --tail 100 bot 2>/dev/null || docker logs --tail 100 kingdoms-bot"])
    if action == "run" and len(rest) >= 2:
        return _connect(h, rest[1:], confirm_destructive=True)
    if action == "deploy":
        print("Deploy: opening an ssh session — the deploy procedure on the remote host")
        print("is owned by the infra repo (kingdoms-infra). Follow its README there.\n")
        return _connect(h, [], interactive=True)
    print(f"FAIL: bad remote action '{action}'. See: kingdoms remote --help")
    return 2


def _parse_add(rest: list[str]) -> dict[str, str | None] | None:
    """Parse `--name N --host H --user U [--port P] [--key K]`."""
    opts: dict[str, str | None] = {"name": None, "host": None, "user": None, "port": "22", "key": None}
    i = 0
    while i < len(rest):
        flag = rest[i].lstrip("-")
        if flag in opts and i + 1 < len(rest):
            opts[flag] = rest[i + 1]
            i += 2
        else:
            print(f"FAIL: unexpected argument '{rest[i]}'.")
            return None
    if not all(opts[f] for f in ("name", "host", "user")):
        print("FAIL: --name, --host and --user are required.")
        return None
    return opts
