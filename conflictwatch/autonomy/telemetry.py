"""telemetry — mission telemetry logging, deterministic replay & after-action harness.

Government developmental test & evaluation (and continuous autonomy tuning) needs the run
to be reconstructable exactly. This module is a structured recorder for the navigation,
comms, EMCON, and load/CASEVAC streams the rest of the autonomy suite produces. It stores
time-ordered, typed records; replays them deterministically (optionally filtered by
channel or time window); and rolls an after-action summary — counts, duration, per-channel
tallies, and any flagged events — for a T&E report.

Recording and analysis only; it observes the vehicle's own telemetry. No control, weapon,
or targeting function. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

CHANNELS = ("nav", "comms", "emcon", "load", "casevac", "mode", "event")


@dataclass
class Record:
    t: float
    channel: str
    kind: str
    data: dict = field(default_factory=dict)
    seq: int = 0

    def as_dict(self) -> dict:
        return {"t": self.t, "channel": self.channel, "kind": self.kind,
                "data": self.data, "seq": self.seq}


class Recorder:
    """Append-only, time-ordered telemetry recorder with replay + after-action."""

    def __init__(self):
        self._records: list[Record] = []
        self._seq = 0

    def log(self, t: float, channel: str, kind: str, **data) -> Record:
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel {channel!r}; expected one of {CHANNELS}")
        self._seq += 1
        r = Record(t=float(t), channel=channel, kind=str(kind), data=dict(data),
                   seq=self._seq)
        self._records.append(r)
        return r

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> list:
        return list(self._records)

    def replay(self, channel: Optional[str] = None,
               t0: Optional[float] = None, t1: Optional[float] = None) -> list:
        """Deterministic replay in (t, seq) order, optionally filtered.

        Stable ordering is by timestamp then insertion sequence, so out-of-order logging
        still replays reproducibly.
        """
        recs = sorted(self._records, key=lambda r: (r.t, r.seq))
        out = []
        for r in recs:
            if channel is not None and r.channel != channel:
                continue
            if t0 is not None and r.t < t0:
                continue
            if t1 is not None and r.t > t1:
                continue
            out.append(r)
        return out

    def after_action(self) -> dict:
        """Summarise the run for a T&E / after-action report."""
        recs = self._records
        if not recs:
            return {"records": 0, "duration_s": 0.0, "by_channel": {},
                    "events": [], "start_t": None, "end_t": None}
        by_channel = {}
        for r in recs:
            by_channel[r.channel] = by_channel.get(r.channel, 0) + 1
        ts = [r.t for r in recs]
        events = [r.as_dict() for r in recs if r.channel == "event"]
        return {
            "records": len(recs),
            "start_t": min(ts),
            "end_t": max(ts),
            "duration_s": round(max(ts) - min(ts), 3),
            "by_channel": dict(sorted(by_channel.items())),
            "events": events,
            "event_count": len(events),
        }

    def to_jsonl(self) -> str:
        """Serialise the log as JSON-lines for archival / transfer."""
        return "\n".join(json.dumps(r.as_dict(), sort_keys=True)
                         for r in sorted(self._records, key=lambda r: (r.t, r.seq)))

    @classmethod
    def from_jsonl(cls, text: str) -> "Recorder":
        """Rehydrate a recorder from JSON-lines (round-trips `to_jsonl`)."""
        rec = cls()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rec.log(d["t"], d["channel"], d["kind"], **d.get("data", {}))
        return rec
