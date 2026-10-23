# never call real docker prune here — all docker calls go through the stub executable.
import os
import shutil
import subprocess
import textwrap

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "build_timing.sh")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_stub(tmp_path, *, build_sleep: int = 0) -> str:
    """
    Write a fake `docker` binary into tmp_path/bin and return the bin dir path.

    Environment variables read by the stub:
      DOCKER_LOG        – path to an append-only log file (one call per line)
      DOCKER_PRUNE_EXIT – exit code for `docker builder prune` (default 0)
      DOCKER_BUILD_EXIT – exit code for `docker compose build` (default 0)
      DOCKER_BUILD_SLEEP – seconds to sleep before `compose build` returns (default 0)
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(r"""
        #!/usr/bin/env bash
        # Stub docker: logs args, honours per-command exit codes.
        LOG="${DOCKER_LOG:-/dev/null}"
        echo "$*" >> "$LOG"

        PRUNE_EXIT="${DOCKER_PRUNE_EXIT:-0}"
        BUILD_EXIT="${DOCKER_BUILD_EXIT:-0}"
        BUILD_SLEEP="${DOCKER_BUILD_SLEEP:-0}"

        case "$*" in
            "builder prune -af")
                exit "$PRUNE_EXIT"
                ;;
            compose\ build*)
                if [ "$BUILD_SLEEP" -gt 0 ]; then
                    sleep "$BUILD_SLEEP"
                fi
                exit "$BUILD_EXIT"
                ;;
        esac
        exit 0
    """).lstrip())
    stub.chmod(0o755)
    return str(bin_dir)


def run_script(args, *, env_extra=None, bin_dir=None):
    """Run build_timing.sh with given args; returns CompletedProcess."""
    env = dict(os.environ)
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", SCRIPT] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


# ---------------------------------------------------------------------------
# Syntax / strict-mode checks (no docker needed)
# ---------------------------------------------------------------------------

def test_bash_syntax_ok():
    result = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_strict_mode():
    content = open(SCRIPT).read()
    assert "#!/usr/bin/env bash" in content
    assert "set -euo pipefail" in content


# ---------------------------------------------------------------------------
# Usage / argument validation
# ---------------------------------------------------------------------------

def test_no_flag_exits_3():
    result = run_script([])
    assert result.returncode == 3
    assert "Usage" in result.stderr


def test_unknown_flag_exits_3():
    result = run_script(["--force"])
    assert result.returncode == 3
    assert "Usage" in result.stderr


def test_both_flags_exit_3():
    result = run_script(["--yes", "--dry-run"])
    assert result.returncode == 3
    assert "Usage" in result.stderr


def test_bad_limit_exits_3(tmp_path):
    bin_dir = make_stub(tmp_path)
    # Confirm stub resolves (not real docker)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={"BUILD_LIMIT_SECONDS": "abc"},
    )
    assert result.returncode == 3
    assert "Usage" in result.stderr


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------

def test_dry_run_calls_no_docker(tmp_path):
    bin_dir = make_stub(tmp_path)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--dry-run"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_LOG": str(log_file)},
    )
    assert result.returncode == 0
    # Stub log must stay empty — no docker was called
    assert not log_file.exists() or log_file.read_text().strip() == ""
    # Stdout must mention both commands and the limit
    assert "docker builder prune -af" in result.stdout
    assert "docker compose build --no-cache" in result.stdout
    assert "180" in result.stdout


# ---------------------------------------------------------------------------
# --yes happy-path
# ---------------------------------------------------------------------------

def test_yes_runs_prune_then_build_in_order(tmp_path):
    bin_dir = make_stub(tmp_path)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_LOG": str(log_file)},
    )
    assert result.returncode == 0
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert "builder prune -af" in lines[0]
    assert "compose build --no-cache" in lines[1]


def test_pass_when_fast(tmp_path):
    bin_dir = make_stub(tmp_path)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={
            "DOCKER_LOG": str(log_file),
            "BUILD_LIMIT_SECONDS": "180",
        },
    )
    assert result.returncode == 0
    stdout_lines = result.stdout.strip().splitlines()
    assert len(stdout_lines) == 3
    assert stdout_lines[0].startswith("build_seconds=")
    assert stdout_lines[1].startswith("limit_seconds=")
    assert stdout_lines[2] == "PASS"


def test_fail_when_over_limit(tmp_path):
    bin_dir = make_stub(tmp_path, build_sleep=2)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={
            "DOCKER_LOG": str(log_file),
            "BUILD_LIMIT_SECONDS": "1",
            "DOCKER_BUILD_SLEEP": "2",
        },
    )
    assert result.returncode == 1
    stdout_lines = result.stdout.strip().splitlines()
    assert stdout_lines[-1] == "FAIL"
    # build_seconds must be recorded even on FAIL
    assert any(l.startswith("build_seconds=") for l in stdout_lines)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_build_failure_exits_1(tmp_path):
    bin_dir = make_stub(tmp_path)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={
            "DOCKER_LOG": str(log_file),
            "DOCKER_BUILD_EXIT": "1",
        },
    )
    assert result.returncode == 1
    assert "build_failed" in result.stdout
    assert "FAIL" in result.stdout


def test_prune_failure_exits_1_and_skips_build(tmp_path):
    bin_dir = make_stub(tmp_path)
    assert shutil.which("docker", path=bin_dir) == str(tmp_path / "bin" / "docker")
    log_file = tmp_path / "docker.log"
    result = run_script(
        ["--yes"],
        bin_dir=bin_dir,
        env_extra={
            "DOCKER_LOG": str(log_file),
            "DOCKER_PRUNE_EXIT": "1",
        },
    )
    assert result.returncode == 1
    assert "build_failed" in result.stdout
    assert "FAIL" in result.stdout
    # compose build must NOT appear in the stub log
    log_text = log_file.read_text() if log_file.exists() else ""
    assert "compose build" not in log_text
