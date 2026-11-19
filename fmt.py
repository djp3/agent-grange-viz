# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""How numbers are written, the same everywhere."""
from __future__ import annotations

from typing import Optional


def age(seconds: Optional[float]) -> str:
    """3s · 45s · 2m05 · 1h12 · — for unknown."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def short_age(seconds: float) -> str:
    """Whole units only, for a narrow cell: 45s · 8m · 2h."""
    s = max(0, int(seconds))
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h"


def secs(seconds: Optional[float]) -> str:
    """Whole seconds, capped: the WireGuard handshake age."""
    if seconds is None:
        return "—"
    return ">999s" if seconds >= 999.5 else f"{round(seconds)}s"


def ms_int(v: Optional[float]) -> str:
    """Round trip in whole milliseconds, as the tailnet row shows it."""
    if v is None:
        return "—"
    if v >= 999.5:
        return ">999ms"
    return "<1ms" if v < 0.5 else f"{round(v)}ms"


def ms(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}ms" if v < 10 else f"{v:.0f}ms"


def size(n: float) -> str:
    """812B · 4.1k · 2.3M (bytes)."""
    if n < 1000:
        return f"{int(n)}B"
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def fit(width: int, *variants: str) -> str:
    """The first variant that fits, else the last one cut."""
    for v in variants:
        if len(v) <= width:
            return v
    return variants[-1][:width]


def counts(d: Optional[dict]) -> str:
    """push 12 · repair 40, or — when not known."""
    if d is None:
        return "—"
    return " · ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"
