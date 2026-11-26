"""PCB backend: Allegro X / Allegro PCB Editor (SKILL axl*) and OrCAD (X) Capture (Tcl).

Allegro batch:  allegro -s script.scr -nograph board.brd   (script lines: "skill load(...)")
Capture batch:  capture.exe script.tcl                       (the script must exit Capture)
Both are best-effort: they depend on the license/product selection of the site.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .jobs import JobManager, new_run_dir, tail
from .profiles import ProfileManager
from .safety import check_skill, check_tcl, confine, require
from .versions import ToolVersion, parse_spb
from .virtuoso import SkillResult, helper_path, parse_result_file, skill_str

ALLEGRO_BINARIES = ("allegro", "allegro_pcb", "pcb_editor", "allegro.exe")
CAPTURE_BINARIES = ("capture", "capture.exe")


def _fwd(p: Path) -> str:
    return str(p).replace("\\", "/")


BOARD_SUMMARY_SKILL = r"""
let((d out)
  d = axlDBGetDesign()
  CdsMcpJson(CdsMcpObj(
    "design" car(errset(axlCurrentDesign()))
    "version" car(errset(axlVersion('fullVersion)))
    "components" car(errset(length(d->components)))
    "nets" car(errset(length(d->nets)))
    "symbols" car(errset(length(d->symbols)))
    "etchLayers" car(errset(axlGetParam("paramLayerGroup:etch")->groupMembers))
    "units" car(errset(car(axlDBGetDesignUnits())))
  ))
)
"""


class PcbBackend:
    def __init__(self, settings: Settings, jobs: JobManager, profiles: ProfileManager):
        self.settings = settings
        self.jobs = jobs
        self.profiles = profiles

    def _find(self, names: tuple[str, ...], key: str, profile: str | None) -> str | None:
        if key in self.settings.binaries:
            return self.profiles.which(key, profile)
        for n in names:
            exe = self.profiles.which(n, profile)
            if exe:
                return exe
        return None

    def info(self, profile: str | None = None) -> dict[str, Any]:
        allegro = self._find(ALLEGRO_BINARIES, "allegro", profile)
        capture = self._find(CAPTURE_BINARIES, "capture", profile)
        env = self.profiles.env(profile)
        hint = " ".join(filter(None, [allegro, capture, env.get("CDSROOT"), env.get("CDS_SITE")]))
        v: ToolVersion = parse_spb(hint)
        return {"allegro": allegro, "capture": capture, "cdsroot": env.get("CDSROOT"),
                "version": v.as_dict(), "platform": os.name}

    def allegro_skill(self, board: str, code: str, timeout_s: float | None = None, wait: bool = True,
                      profile: str | None = None, extra_args: list[str] | None = None,
                      trusted: bool = False) -> dict[str, Any]:
        require(self.settings, "run", "allegro batch SKILL")
        if not trusted:
            check_skill(code, self.settings, live=False)
        exe = self._find(ALLEGRO_BINARIES, "allegro", profile)
        if not exe:
            raise FileNotFoundError("Allegro PCB Editor ('allegro') not found for this profile")
        brd = confine(board, self.settings)
        run = new_run_dir(self.settings.workdir, "allegro")
        user = run / "user_code.il"
        user.write_text(code, encoding="utf-8")
        result = run / "result.txt"
        scr = run / "run.scr"
        scr.write_text(
            f"skill load({skill_str(_fwd(helper_path()))})\n"
            f"skill CdsMcpWriteResult({skill_str(_fwd(result))} CdsMcpEvalFile({skill_str(_fwd(user))}))\n"
            "exit\n", encoding="utf-8")
        cmd = [exe, "-s", str(scr), "-nograph", *(extra_args or []), str(brd)]
        job = self.jobs.start("allegro", cmd, run, run, self.profiles.env(profile), log_name="stdout.log",
                              board=str(brd))
        timeout = self.settings.timeout_default_s if timeout_s is None else timeout_s
        done = self.jobs.wait(job, timeout) if wait else False
        out = job.to_dict(tail_lines=0 if done else 15)
        if done:
            parsed: SkillResult | None = parse_result_file(result.read_text(errors="replace")) if result.exists() else None
            if parsed is None:
                out["status"] = "error"
                out["message"] = ("Allegro exited without a result file: check the license/product selection "
                                  "and the .jrl/.log files in the run directory")
                out["log_tail"] = tail(Path(job.log), 40)
                journals = sorted(run.glob("*.jrl")) + sorted(run.glob("*.log"))
                out["journals"] = [str(j) for j in journals][:10]
            else:
                parsed.mode = "allegro-batch"
                out.update(parsed.as_dict(self.settings.max_output_chars))
        return out

    def board_summary(self, board: str, profile: str | None = None, timeout_s: float = 600) -> dict[str, Any]:
        import json
        out = self.allegro_skill(board, BOARD_SUMMARY_SKILL, timeout_s=timeout_s, profile=profile, trusted=True)
        if out.get("status") == "ok":
            try:
                out["board"] = json.loads(out.pop("value"), strict=False)
            except ValueError:
                pass
        return out

    def capture_tcl(self, script: str, timeout_s: float | None = None, wait: bool = True,
                    profile: str | None = None) -> dict[str, Any]:
        require(self.settings, "run", "OrCAD Capture Tcl batch")
        check_tcl(script, self.settings)
        exe = self._find(CAPTURE_BINARIES, "capture", profile)
        if not exe:
            raise FileNotFoundError("OrCAD Capture ('capture' / capture.exe) not found for this profile")
        run = new_run_dir(self.settings.workdir, "capture")
        user = run / "user.tcl"
        user.write_text(script, encoding="utf-8")
        result = run / "result.txt"
        wrapper = run / "wrapper.tcl"
        wrapper.write_text(
            "set ::cdsmcp_out [open {%s} w]\n"
            "if {[catch {source {%s}} ::cdsmcp_res]} {\n"
            "  puts $::cdsmcp_out \"CDSMCP_STATUS error\"\n"
            "} else {\n"
            "  puts $::cdsmcp_out \"CDSMCP_STATUS ok\"\n"
            "}\n"
            "puts $::cdsmcp_out $::cdsmcp_res\n"
            "close $::cdsmcp_out\n"
            "exit\n" % (_fwd(result), _fwd(user)), encoding="utf-8")
        job = self.jobs.start("capture", [exe, str(wrapper)], run, run, self.profiles.env(profile),
                              log_name="stdout.log")
        timeout = self.settings.timeout_default_s if timeout_s is None else timeout_s
        done = self.jobs.wait(job, timeout) if wait else False
        out = job.to_dict(tail_lines=0 if done else 10)
        if done:
            if result.exists():
                text = result.read_text(errors="replace")
                m = re.match(r"CDSMCP_STATUS (\w+)\n?(.*)", text, re.S)
                out["status"] = m.group(1) if m else "unknown"
                out["value"] = (m.group(2) if m else text)[: self.settings.max_output_chars]
            else:
                out["status"] = "error"
                out["message"] = "Capture exited without a result file (script did not run or Capture did not start)"
        return out
