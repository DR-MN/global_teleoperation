"""Synchronized demonstration recorder.

Records time-aligned frames of (joint states, EE pose, gripper, command,
timestamps, and camera frame references) into an episode. On stop, the episode
is written in a LeRobot-friendly tabular layout: Parquet for the low-dimensional
state/action stream (+ optional HDF5), with image frames referenced by path so
the same schema scales to behavior-cloning / VLA training.

Parquet/HDF5 are optional deps: if unavailable, the recorder transparently
falls back to newline-delimited JSON so recording never fails on a minimal box.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.types import JointCommand, RobotState, now

log = logging.getLogger(__name__)


@dataclass
class Frame:
    t: float
    state: Dict[str, Any]
    action: Optional[Dict[str, Any]] = None
    global_frame: Optional[str] = None   # path to global camera image
    wrist_frame: Optional[str] = None    # path to wrist camera image


@dataclass
class Episode:
    task: str
    started: float = field(default_factory=now)
    ended: Optional[float] = None
    success: Optional[bool] = None
    attempts: int = 1
    interventions: int = 0
    notes: str = ""
    frames: List[Frame] = field(default_factory=list)


class Recorder:
    def __init__(self, output_dir: str = "recordings") -> None:
        self.dir = Path(output_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._episode: Optional[Episode] = None

    @property
    def recording(self) -> bool:
        return self._episode is not None

    def start(self, task: str, attempts: int = 1) -> None:
        if self._episode is not None:
            log.warning("start() called while recording; finishing previous episode")
            self.stop(success=None)
        self._episode = Episode(task=task, attempts=attempts)
        log.info("Recording started: task=%r", task)

    def record(self, state: RobotState, action: Optional[JointCommand] = None,
               global_frame: Optional[str] = None,
               wrist_frame: Optional[str] = None) -> None:
        if self._episode is None:
            return
        self._episode.frames.append(Frame(
            t=now(),
            state=state.to_dict(),
            action=action.to_dict() if action else None,
            global_frame=global_frame,
            wrist_frame=wrist_frame,
        ))

    def note_intervention(self) -> None:
        if self._episode:
            self._episode.interventions += 1

    def stop(self, success: Optional[bool] = None, notes: str = "") -> Optional[Path]:
        if self._episode is None:
            return None
        ep = self._episode
        ep.ended = now()
        ep.success = success
        ep.notes = notes
        self._episode = None
        path = self._write(ep)
        log.info("Recording stopped: %d frames -> %s", len(ep.frames), path)
        return path

    # ---- serialization ----------------------------------------------------
    def _write(self, ep: Episode) -> Path:
        stamp = int(ep.started)
        base = self.dir / f"episode_{stamp}"
        meta = {
            "task": ep.task,
            "started": ep.started,
            "ended": ep.ended,
            "success": ep.success,
            "attempts": ep.attempts,
            "interventions": ep.interventions,
            "notes": ep.notes,
            "num_frames": len(ep.frames),
        }
        (base.with_suffix(".meta.json")).write_text(json.dumps(meta, indent=2))
        rows = [self._flatten(f) for f in ep.frames]
        if self._write_parquet(rows, base.with_suffix(".parquet")):
            return base.with_suffix(".parquet")
        # Fallback: JSONL, always available.
        with base.with_suffix(".jsonl").open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return base.with_suffix(".jsonl")

    @staticmethod
    def _flatten(f: Frame) -> Dict[str, Any]:
        row: Dict[str, Any] = {"t": f.t}
        for i, p in enumerate(f.state.get("positions", [])):
            row[f"q{i}"] = p
        for i, v in enumerate(f.state.get("velocities", [])):
            row[f"dq{i}"] = v
        pose = f.state.get("pose", {})
        for k in ("x", "y", "z", "qx", "qy", "qz", "qw"):
            row[f"ee_{k}"] = pose.get(k)
        row["gripper"] = f.state.get("gripper_position")
        if f.action:
            for i, p in enumerate(f.action.get("positions", [])):
                row[f"a_q{i}"] = p
            row["a_gripper"] = f.action.get("gripper")
        row["global_frame"] = f.global_frame
        row["wrist_frame"] = f.wrist_frame
        return row

    @staticmethod
    def _write_parquet(rows: List[Dict[str, Any]], path: Path) -> bool:
        try:
            import pandas as pd  # type: ignore
        except Exception:
            return False
        try:
            pd.DataFrame(rows).to_parquet(path, index=False)
            return True
        except Exception:  # e.g. no pyarrow/fastparquet engine installed
            log.info("parquet engine unavailable; falling back to JSONL")
            return False
