"""Tests for the kingdoms developer CLI (doctor, local, remote, runner)."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from kingdoms.cli import doctor, remote


def test_doctor_report_all_ok(capsys: pytest.CaptureFixture[str]) -> None:
    checks = [
        doctor.Check("python", doctor.OK, "3.12"),
        doctor.Check("uv", doctor.OK, "found"),
    ]
    assert doctor.print_report(checks) == 0
    out = capsys.readouterr().out
    assert "[OK ]" in out and "All good" in out


def test_doctor_report_failure_has_fix(capsys: pytest.CaptureFixture[str]) -> None:
    checks = [doctor.Check("uv", doctor.FAIL, "not found", "install uv")]
    assert doctor.print_report(checks) == 1
    out = capsys.readouterr().out
    assert "-> install uv" in out


def test_doctor_check_env(tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path)
    check = doctor._check_env(root)
    assert check.status == doctor.FAIL
    (tmp_path / ".env").write_text("DISCORD_TOKEN=x\n")
    check = doctor._check_env(root)
    assert check.status == doctor.OK


def test_remote_add_list_rm(tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("fake key\n")
    with mock.patch.object(remote, "CONFIG_PATH", str(tmp_path / "cli.toml")), \
         mock.patch.object(remote, "CONFIG_DIR", str(tmp_path)):
        rc = remote.cmd_add(name="myhost", host="1.2.3.4", user="root", port="22", key=str(key))
        assert rc == 0
        hosts = remote.load_hosts()
        assert "myhost" in hosts and hosts["myhost"].host == "1.2.3.4"
        assert remote.cmd_list() == 0
        assert "myhost" in capsys.readouterr().out
        assert remote.cmd_rm("myhost") == 0
        assert remote.load_hosts() == {}


def test_remote_add_bad_key(tmp_path: pytest.TempPathFactory) -> None:
    with mock.patch.object(remote, "CONFIG_PATH", str(tmp_path / "cli.toml")), \
         mock.patch.object(remote, "CONFIG_DIR", str(tmp_path)):
        rc = remote.cmd_add(name="h", host="1.2.3.4", user="root", port="22", key="/no/such/key")
        assert rc == 1
        assert remote.load_hosts() == {}


def test_remote_destructive_guard(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("builtins.input", return_value="no"), \
         mock.patch.object(remote, "subprocess") as sub:
        host = remote.Host(name="h", host="1.2.3.4", user="root")
        rc = remote._connect(host, ["rm", "-rf", "/"], confirm_destructive=True)
        assert rc == 0
        sub.run.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out


def test_ssh_argv_forces_key_auth() -> None:
    host = remote.Host(name="h", host="1.2.3.4", user="root", port=2222, key="/k")
    argv = remote._ssh_argv(host, ["uptime"])
    assert argv[0] == "ssh"
    assert "PasswordAuthentication=no" in argv
    assert "BatchMode=yes" in argv
    assert argv[argv.index("-i") + 1] == "/k"
    assert argv[-1] == "uptime"


def test_parse_add_flags() -> None:
    opts = remote._parse_add(["--name", "n", "--host", "h", "--user", "u", "--port", "2222"])
    assert opts == {"name": "n", "host": "h", "user": "u", "port": "2222", "key": None}


def test_main_dispatch_help(capsys: pytest.CaptureFixture[str]) -> None:
    from kingdoms.cli.__main__ import main

    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_local_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    from kingdoms.cli import local

    assert local.main(["nope"]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_runner_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    from kingdoms.cli import runner

    assert runner.main(["nope"]) == 2
    assert "setup" in capsys.readouterr().out.lower()


def test_doctor_python_check() -> None:
    check = doctor._check_python()
    assert check.status in (doctor.OK, doctor.FAIL)


def test_remote_config_path_no_secrets(tmp_path: pytest.TempPathFactory) -> None:
    with mock.patch.object(remote, "CONFIG_PATH", str(tmp_path / "cli.toml")), \
         mock.patch.object(remote, "CONFIG_DIR", str(tmp_path)):
        remote.cmd_add(name="a", host="1.1.1.1", user="u", port="22", key=None)
        content = (tmp_path / "cli.toml").read_text()
        assert "password" not in content.lower()
        assert "1.1.1.1" in content


def test_doctor_port_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSocket:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def settimeout(self, t: float) -> None:
            return None

        def connect_ex(self, addr: tuple[str, int]) -> int:
            return 0

    monkeypatch.setattr(doctor.socket, "socket", FakeSocket)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/docker" if n == "docker" else None)
    check = doctor._check_port()
    assert check.status == doctor.OK


def test_env_var_not_required() -> None:
    assert "HOME" in os.environ
