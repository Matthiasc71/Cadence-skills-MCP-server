"""Minimal streaming reader for Spectre PSF *ASCII* results (-format psfascii).

Handles swept analyses (tran, ac, dc sweeps, noise...) with scalar or complex traces,
GROUP traces, and non-swept results (dcOp, info) with scalar values; struct values are
returned as lists of numbers. Binary PSF / psfxl / sst2 / fsdb are not decoded: rerun with
``-format psfascii`` (spectre_run does that by default).
"""

from __future__ import annotations

import cmath
import fnmatch
import math
import re
from pathlib import Path
from typing import Any, Iterator

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')
_SECTIONS = {"HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END"}


def is_psf_ascii(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(6) == b"HEADER"
    except OSError:
        return False


def _tokens(path: Path) -> Iterator[str]:
    with path.open("r", errors="replace") as fh:
        for line in fh:
            yield from _TOKEN.findall(line)


def _unq(tok: str) -> str:
    return tok[1:-1].replace('\\"', '"') if tok.startswith('"') else tok


def _num(tok: str) -> float:
    try:
        return float(tok)
    except ValueError:
        low = tok.lower()
        if low in ("nan", "inf", "-inf", "+inf"):
            return float(low)
        raise


class _Stream:
    def __init__(self, it: Iterator[str]):
        self.it = it
        self.buf: list[str] = []

    def peek(self) -> str | None:
        if not self.buf:
            try:
                self.buf.append(next(self.it))
            except StopIteration:
                return None
        return self.buf[0]

    def next(self) -> str | None:
        tok = self.peek()
        if self.buf:
            self.buf.pop(0)
        return tok

    def skip_parens(self) -> None:
        """Consume a balanced (...) group; the opening '(' is the next token."""
        depth = 0
        while True:
            tok = self.next()
            if tok is None:
                return
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
                if depth <= 0:
                    return

    def skip_prop(self) -> None:
        if self.peek() == "PROP":
            self.next()
            self.skip_parens()

    def value(self, is_complex: bool = False) -> Any:
        tok = self.next()
        if tok is None:
            raise ValueError("unexpected end of PSF file")
        if tok == "(":
            nums: list[Any] = []
            depth = 1
            while depth:
                t = self.next()
                if t is None:
                    break
                if t == "(":
                    depth += 1
                elif t == ")":
                    depth -= 1
                else:
                    try:
                        nums.append(_num(t))
                    except ValueError:
                        nums.append(_unq(t))
            if is_complex and len(nums) == 2 and all(isinstance(x, float) for x in nums):
                return complex(nums[0], nums[1])
            return nums
        if tok.startswith('"'):
            return _unq(tok)
        return _num(tok)


def read_psf_ascii(path: str | Path, signals: list[str] | None = None, max_points: int = 200,
                   max_scalars: int = 300) -> dict[str, Any]:
    path = Path(path)
    if not is_psf_ascii(path):
        raise ValueError(f"{path} is not a PSF ASCII file (binary PSF/psfxl/sst2/fsdb): rerun with -format psfascii")
    st = _Stream(_tokens(path))
    header: dict[str, Any] = {}
    sweep: str | None = None
    traces: list[str] = []
    groups: dict[str, list[str]] = {}
    complex_types: set[str] = set()
    trace_type: dict[str, str] = {}
    section = None
    pending_group: tuple[str, int] | None = None

    # ---------------------------------------------------------- header part
    while True:
        tok = st.peek()
        if tok is None:
            break
        if tok in _SECTIONS:
            section = st.next()
            if section in ("VALUE", "END"):
                break
            continue
        if section == "HEADER":
            key = _unq(st.next() or "")
            header[key] = st.value()
        elif section == "TYPE":
            name = _unq(st.next() or "")
            words = []
            while st.peek() not in (None, "PROP") and not (st.peek() or "").startswith('"') and st.peek() not in _SECTIONS:
                w = st.next() or ""
                if w == "(":
                    st.buf.insert(0, "(")
                    st.skip_parens()
                    continue
                words.append(w)
            if "COMPLEX" in words:
                complex_types.add(name)
            st.skip_prop()
        elif section == "SWEEP":
            sweep = _unq(st.next() or "")
            if (st.peek() or "").startswith('"'):
                st.next()
            st.skip_prop()
        elif section == "TRACE":
            name = _unq(st.next() or "")
            if st.peek() == "GROUP":
                st.next()
                pending_group = (name, int(st.next() or 0))
                groups[name] = []
                continue
            if (st.peek() or "").startswith('"'):
                trace_type[name] = _unq(st.next() or "")
            st.skip_prop()
            if pending_group and len(groups[pending_group[0]]) < pending_group[1]:
                groups[pending_group[0]].append(name)
                if len(groups[pending_group[0]]) == pending_group[1]:
                    pending_group = None
            traces.append(name)
        else:
            st.next()

    def wanted(name: str) -> bool:
        if signals is None:
            return False
        return any(name == s or fnmatch.fnmatchcase(name, s) for s in signals)

    result: dict[str, Any] = {
        "file": str(path),
        "analysis_type": header.get("analysis type"),
        "analysis_name": header.get("analysis name"),
        "simulator_version": header.get("version"),
        "sweep": sweep,
        "signals": traces,
        "signal_count": len(traces),
    }

    # ----------------------------------------------------------- value part
    if section != "VALUE":
        result["points"] = 0
        return result

    if sweep:
        xs: list[float] = []
        data: dict[str, list[Any]] = {t: [] for t in traces if wanted(t)}
        while True:
            tok = st.next()
            if tok is None or tok == "END":
                break
            name = _unq(tok)
            if name == sweep:
                xs.append(_sweep_val(st))
                continue
            if name in groups:
                for member in groups[name]:
                    v = st.value(trace_type.get(member) in complex_types)
                    if member in data:
                        data[member].append(v)
            else:
                v = st.value(trace_type.get(name) in complex_types)
                if name in data:
                    data[name].append(v)
            st.skip_prop()
        result["points"] = len(xs)
        if xs:
            result["x_range"] = [xs[0], xs[-1]]
        result["data"] = {name: _summarise(xs, ys, max_points) for name, ys in data.items()}
        missing = [s for s in signals or [] if not any(n == s or fnmatch.fnmatchcase(n, s) for n in traces)]
        if missing:
            result["not_found"] = missing
    else:
        values: dict[str, Any] = {}
        count = 0
        while True:
            tok = st.next()
            if tok is None or tok == "END":
                break
            name = _unq(tok)
            typ = _unq(st.next() or "") if (st.peek() or "").startswith('"') else ""
            v = st.value(typ in complex_types)
            st.skip_prop()
            count += 1
            if (signals is None and len(values) < max_scalars) or wanted(name):
                values[name] = _jsonable(v)
        result["points"] = 0
        result["value_count"] = count
        result["values"] = values
        if signals is None and count > len(values):
            result["values_truncated"] = True
    return result


def _sweep_val(st: _Stream) -> float:
    v = st.value()
    return v.real if isinstance(v, complex) else float(v)


def _jsonable(v: Any) -> Any:
    if isinstance(v, complex):
        return {"re": v.real, "im": v.imag, "mag": abs(v), "phase_deg": math.degrees(cmath.phase(v))}
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _summarise(xs: list[float], ys: list[Any], max_points: int) -> dict[str, Any]:
    n = min(len(xs), len(ys))
    if n == 0:
        return {"points": 0}
    is_cplx = isinstance(ys[0], complex)
    step = max(1, math.ceil(n / max(1, max_points)))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    out: dict[str, Any] = {"points": n, "returned": len(idx)}
    if is_cplx:
        mags = [abs(y) for y in ys[:n]]
        out["columns"] = ["x", "mag", "mag_db", "phase_deg"]
        out["rows"] = [[xs[i], mags[i], _db(mags[i]), math.degrees(cmath.phase(ys[i]))] for i in idx]
        out["mag_first_db"] = _db(mags[0])
        out["mag_max_db"] = _db(max(mags))
        ref = mags[0]
        out["x_at_minus_3db"] = _first_cross(xs, mags, ref / math.sqrt(2)) if ref > 0 else None
        out["x_at_unity"] = _first_cross(xs, mags, 1.0) if mags[0] > 1.0 else None
        if out["x_at_unity"] is not None:
            k = min(range(n), key=lambda i: abs(xs[i] - out["x_at_unity"]))
            out["phase_at_unity_deg"] = math.degrees(cmath.phase(ys[k]))
    else:
        vals = [float(y) if not isinstance(y, list) else float("nan") for y in ys[:n]]
        finite = [v for v in vals if math.isfinite(v)]
        out["columns"] = ["x", "y"]
        out["rows"] = [[xs[i], vals[i]] for i in idx]
        if finite:
            out.update({"min": min(finite), "max": max(finite), "first": vals[0], "last": vals[-1],
                        "mean": sum(finite) / len(finite)})
    return out


def _db(m: float) -> float | None:
    return 20 * math.log10(m) if m > 0 else None


def _first_cross(xs: list[float], ys: list[float], level: float) -> float | None:
    for i in range(1, min(len(xs), len(ys))):
        a, b = ys[i - 1], ys[i]
        if (a - level) * (b - level) <= 0 and a != b:
            return xs[i - 1] + (level - a) * (xs[i] - xs[i - 1]) / (b - a)
    return None


def list_results(raw_dir: str | Path) -> list[dict[str, Any]]:
    raw = Path(raw_dir)
    out = []
    for p in sorted(raw.rglob("*")):
        if p.is_file():
            out.append({"file": str(p), "bytes": p.stat().st_size, "psf_ascii": is_psf_ascii(p)})
    return out[:500]
