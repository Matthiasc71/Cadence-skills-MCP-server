"""Per-profile process environments (one profile = one Cadence installation/version).

A profile's ``setup`` script is sourced once in the matching shell and the resulting
environment is captured and cached, so every tool of that profile runs with the PATH,
CDSHOME, LM_LICENSE_FILE... that the site setup script defines.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from .config import Profile, Settings

_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def expand(value: str, env: dict[str, str]) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), ""), value)


def _capture_posix(script: Path) -> dict[str, str]:
    quoted = shlex.quote(str(script))
    if script.suffix in (".csh", ".tcsh") or script.name.endswith("cshrc"):
        shell = shutil.which("tcsh") or shutil.which("csh")
        if not shell:
            raise RuntimeError(f"{script} is a csh script but neither tcsh nor csh is installed")
        cmd = [shell, "-f", "-c", f"source {quoted} >& /dev/null; env -0"]
    else:
        shell = shutil.which("bash") or "/bin/sh"
        cmd = [shell, "-c", f". {quoted} >/dev/null 2>&1; env -0"]
    out = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(f"sourcing {script} failed: {out.stderr.decode(errors='replace')[-2000:]}")
    env: dict[str, str] = {}
    for item in out.stdout.split(b"\0"):
        if b"=" in item:
            k, _, v = item.partition(b"=")
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def _capture_windows(script: Path) -> dict[str, str]:
    out = subprocess.run(
        ["cmd", "/d", "/c", f'call "{script}" >nul 2>&1 && set'],
        capture_output=True, text=True, timeout=120, check=False,
    )
    env: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    return env


class ProfileManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def names(self) -> list[str]:
        return sorted(self.settings.profiles)

    def resolve_name(self, name: str | None) -> str | None:
        name = name or self.settings.default_profile
        if name and name not in self.settings.profiles:
            raise ValueError(f"unknown profile {name!r}; defined profiles: {self.names() or 'none'}")
        return name

    def env(self, name: str | None) -> dict[str, str]:
        name = self.resolve_name(name)
        if not name:
            return dict(os.environ)
        with self._lock:
            if name not in self._cache:
                self._cache[name] = self._build(self.settings.profiles[name])
            return dict(self._cache[name])

    def _build(self, prof: Profile) -> dict[str, str]:
        env = dict(os.environ)
        if prof.setup:
            script = Path(os.path.expandvars(prof.setup)).expanduser()
            if not script.is_file():
                raise RuntimeError(f"profile {prof.name!r}: setup script {script} not found")
            captured = _capture_windows(script) if os.name == "nt" else _capture_posix(script)
            if captured:
                env = captured
        for key, value in prof.env.items():
            env[key] = expand(value, env)
        return env

    def which(self, tool: str, profile: str | None) -> str | None:
        override = self.settings.binaries.get(tool)
        if override:
            return override if Path(override).exists() else shutil.which(override, path=self.env(profile).get("PATH"))
        return shutil.which(tool, path=self.env(profile).get("PATH"))

    def describe(self) -> list[dict]:
        out = []
        for name in self.names():
            p = self.settings.profiles[name]
            out.append({
                "name": name,
                "setup": p.setup,
                "env_keys": sorted(p.env),
                "description": p.description,
                "default": name == self.settings.default_profile,
                "loaded": name in self._cache,
            })
        return out
