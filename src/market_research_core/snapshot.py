import json
from pathlib import Path

import pandas as pd

from .errors import SnapshotMismatch
from .models import SnapshotIdentity
from .policy import INSTRUMENTS, PINNED_SNAPSHOT


def _manifest_value(manifest: dict, *keys: str):
    for key in keys:
        value = manifest.get(key)
        if value is not None:
            return value
    return None


def verify_snapshot(run_dir: str | Path) -> SnapshotIdentity:
    root = Path(run_dir).resolve()
    if root.name != PINNED_SNAPSHOT.run_name:
        raise SnapshotMismatch(f"Unpinned run directory: {root.name}")

    manifest_path = root / "final_gate_manifest.json"
    summary_path = root / "final_gate_summary.csv"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise SnapshotMismatch("FINAL GATE manifest or summary is missing")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sha = str(_manifest_value(manifest, "git_sha", "producer_git_sha", "code_git_sha") or "")
    if sha and not PINNED_SNAPSHOT.producer_git_sha.startswith(sha) and not sha.startswith(PINNED_SNAPSHOT.producer_git_sha):
        raise SnapshotMismatch(f"Producer Git SHA mismatch: {sha}")

    overall = str(_manifest_value(manifest, "overall_verdict", "overall", "verdict") or PINNED_SNAPSHOT.overall_verdict)
    if overall != PINNED_SNAPSHOT.overall_verdict:
        raise SnapshotMismatch(f"Overall verdict mismatch: {overall}")

    summary = pd.read_csv(summary_path)
    required = {"instrument", "verdict"}
    if not required.issubset(summary.columns):
        raise SnapshotMismatch("Summary lacks instrument/verdict columns")
    actual = dict(zip(summary["instrument"].astype(str).str.upper(), summary["verdict"].astype(str)))
    for symbol, policy in INSTRUMENTS.items():
        if actual.get(symbol) != policy.verdict:
            raise SnapshotMismatch(f"{symbol} verdict mismatch: {actual.get(symbol)}")

    return SnapshotIdentity(root.name, PINNED_SNAPSHOT.producer_git_sha, overall)

