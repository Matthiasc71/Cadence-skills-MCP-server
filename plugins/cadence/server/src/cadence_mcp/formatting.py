"""Response formatting shared by all tools: markdown/json rendering, pagination, truncation."""

from __future__ import annotations

import json
from typing import Any, Literal, Sequence

ResponseFormat = Literal["markdown", "json"]

CHARACTER_LIMIT = 25_000  # default; Settings.max_output_chars overrides it
_CELL_MAX = 80
_TABLE_MAX_COLS = 8
_INLINE_LIST_MAX = 30


def paginate(items: Sequence[Any], offset: int, limit: int, total: int | None = None) -> dict[str, Any]:
    """Slice ``items`` and return the standard pagination envelope.

    When ``items`` is already the page (server-side slicing), pass ``total`` and offset=0 slicing is skipped.
    """
    if total is None:
        total = len(items)
        page = list(items[offset:offset + limit])
    else:
        page = list(items[:limit])
    end = offset + len(page)
    return {
        "total": total,
        "count": len(page),
        "offset": offset,
        "has_more": end < total,
        "next_offset": end if end < total else None,
        "items": page,
    }


def _scalar(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _cell(v: Any) -> str:
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = _scalar(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= _CELL_MAX else s[: _CELL_MAX - 1] + "…"


def _is_records(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _table(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    cols = cols[:_TABLE_MAX_COLS]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(_cell(r.get(c)) for c in cols) + " |")
    return out


def to_markdown(data: Any, title: str | None = None, level: int = 2) -> str:
    lines: list[str] = []
    if title:
        lines += [f"{'#' * max(1, level - 1)} {title}", ""]
    _emit(data, lines, level)
    return "\n".join(lines).rstrip() + "\n"


def _emit(data: Any, lines: list[str], level: int) -> None:
    h = "#" * min(level, 6)
    if isinstance(data, dict):
        nested: list[tuple[str, Any]] = []
        for k, v in data.items():
            if isinstance(v, str) and ("\n" in v or len(v) > 160):
                nested.append((k, v))
            elif isinstance(v, dict) and v:
                nested.append((k, v))
            elif _is_records(v):
                nested.append((k, v))
            elif isinstance(v, list):
                if not v:
                    lines.append(f"- **{k}**: —")
                elif all(not isinstance(x, (dict, list)) for x in v):
                    shown = ", ".join(_scalar(x) for x in v[:_INLINE_LIST_MAX])
                    more = f" … (+{len(v) - _INLINE_LIST_MAX})" if len(v) > _INLINE_LIST_MAX else ""
                    lines.append(f"- **{k}**: {shown}{more}")
                else:
                    nested.append((k, v))
            else:
                lines.append(f"- **{k}**: {_scalar(v)}")
        for k, v in nested:
            lines += ["", f"{h} {k}", ""]
            if isinstance(v, str):
                lines += ["```", v.rstrip(), "```"]
            elif _is_records(v):
                lines += _table(v)
            else:
                _emit(v, lines, level + 1)
    elif _is_records(data):
        lines += _table(data)
    elif isinstance(data, list):
        for x in data:
            lines.append(f"- {_cell(x)}")
    elif isinstance(data, str):
        lines.append(data)
    else:
        lines.append(_scalar(data))


def render(data: Any, response_format: ResponseFormat, title: str | None = None,
           limit: int = CHARACTER_LIMIT) -> str:
    text = (json.dumps(data, indent=2, ensure_ascii=False, default=str) if response_format == "json"
            else to_markdown(data, title))
    if len(text) <= limit:
        return text
    note = (f"\n\n[Response truncated at {limit} of {len(text)} characters. Narrow it with limit/offset, "
            "a pattern filter, signals=[...], max_points or tail_lines.]")
    return text[:limit] + note
