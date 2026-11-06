"""Caller-pinned native TSM snapshot access; no guessed paths or governance defaults."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from importlib.metadata import distribution, PackageNotFoundError

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .policy import PINNED_SNAPSHOT
from .temporal import CONSERVATIVE_POLICY, prepare_conservative_bars

POLICY_SHA = '7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4'


class NativeSnapshotRejected(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f'{code}: {message}')


def _reject(code, message):
    raise NativeSnapshotRejected(code, message)


@dataclass(frozen=True)
class DateWindow:
    start: str
    end: str                         # inclusive New York calendar dates

    def __post_init__(self):
        if date.fromisoformat(self.start).isoformat()!=self.start or date.fromisoformat(self.end).isoformat()!=self.end:
            raise ValueError('canonical ISO dates required')
        if date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError('reversed date window')

    def overlaps(self, other):
        return self.start <= other.end and other.start <= self.end

    def contains(self, other):
        return self.start <= other.start and other.end <= self.end


@dataclass(frozen=True)
class ManifestLayout:
    """Explicit JSON pointers into the EXISTING manifest. No presumed native layout."""
    snapshot_id: str
    producer_sha: str
    symbol: str
    parquet_path: str
    parquet_sha256: str
    columns: str                     # logical timestamp/open/high/low/close/volume -> native name
    arrow_schema: str                # native column name -> str(Arrow DataType), all columns


def _pointer(doc, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        _reject('MANIFEST_LAYOUT', 'explicit JSON pointers required')
    try:
        for token in pointer[1:].split('/'):
            token = token.replace('~1','/').replace('~0','~')
            doc = doc[int(token)] if isinstance(doc, list) else doc[token]
        return doc
    except (KeyError, IndexError, TypeError, ValueError):
        _reject('MANIFEST_LAYOUT', f'pointer does not resolve: {pointer}')


def _pin(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        _reject('SHA_REQUIRED', 'explicit lowercase SHA256 required')
    return value


def _inside(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        _reject('UNSAFE_PATH', 'manifest path must be a confined relative path')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        _reject('UNSAFE_PATH', 'path escapes snapshot or is not a regular file')
    return path


def _stamp(path):
    s = path.stat()
    return dict(device=s.st_dev, inode=s.st_ino, size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)


@dataclass(frozen=True)
class FileIntegrityReceipt:
    parquet_sha256: str
    manifest_sha256: str
    stat: dict
    verification_ref: str

    @property
    def sha256(self):
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_file_bytes(path: str | Path, *, expected_sha256: str, manifest_sha256: str,
                      verification_ref: str, allow_full_file_hash: bool = False) -> FileIntegrityReceipt:
    """Optional receipt producer; NEVER invoked implicitly by bounded load.

    Reads the entire local file's opaque bytes, not Parquet rows. Caller must
    separately authorize that operation; it may include bytes belonging to holdout.
    Existing trusted receipts may instead be supplied by the caller. No source writes.
    """
    if allow_full_file_hash is not True:
        _reject('FULL_HASH_NOT_AUTHORIZED', 'reuse trusted integrity receipt or explicitly authorize opaque-byte hash')
    _pin(expected_sha256); _pin(manifest_sha256)
    if not verification_ref:
        _reject('INTEGRITY_RECEIPT', 'verification reference required')
    path = Path(path)
    before = _stamp(path)
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256 or before != _stamp(path):
        _reject('SNAPSHOT_SHA_MISMATCH', 'file checksum mismatch or file changed during verification')
    return FileIntegrityReceipt(expected_sha256, manifest_sha256, before, verification_ref)


def core_code_identity() -> dict:
    """Installed wheel VCS pin, or actual checkout HEAD plus dirty state."""
    root=Path(__file__).resolve().parents[2]
    if (root/'.git').exists():
        result=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True)
        if result.returncode==0 and re.fullmatch('[0-9a-f]{40}',result.stdout.strip()):
            status=subprocess.run(['git','-C',str(root),'status','--porcelain'],capture_output=True,text=True)
            return dict(core_git_sha=result.stdout.strip(),core_tree_dirty=bool(status.stdout) if status.returncode==0 else None)
    try:
        direct=json.loads(distribution('market-research-core').read_text('direct_url.json') or '{}')
        pin=direct.get('vcs_info',{}).get('commit_id','')
        if re.fullmatch('[0-9a-f]{40}',pin):
            return dict(core_git_sha=pin,core_tree_dirty=None)
    except (ValueError,TypeError,FileNotFoundError,PackageNotFoundError):
        pass
    return dict(core_git_sha='UNKNOWN',core_tree_dirty=None)


@dataclass(frozen=True)
class NativeTSMResult:
    native: pd.DataFrame             # original names, values, timestamp dtype/timezone
    source_1m: pd.DataFrame          # aliases for existing conservative preparation API
    conservative_15m: pd.DataFrame
    provenance: dict


def load_native_tsm(snapshot_path: str | Path, *, manifest_relative_path: str,
                    expected_manifest_sha256: str, layout: ManifestLayout,
                    integrity_receipt: FileIntegrityReceipt, expected_receipt_sha256: str,
                    symbol: str, dates: DateWindow, research_window: DateWindow,
                    holdout: tuple[DateWindow, ...], max_rows: int = 100_000) -> NativeTSMResult:
    """Read at most 31 requested dates, with mandatory allowed/holdout boundaries.

    Rows returned and materialized are predicate-bounded. Candidate row groups
    must have timestamp statistics wholly within the authorized research window
    and disjoint from holdout BEFORE scanning. Unsafe/unprovable groups fail closed.
    File authenticity is a trusted caller-pinned receipt, guarded by unchanged stat;
    this API performs NO full-file hashing, no recursive discovery and no data writes.
    """
    if symbol != 'TSM' or not DateWindow('2016-10-19','2026-10-16').contains(dates):
        _reject('UNSUPPORTED_SCOPE', 'only pinned TSM history is in scope')
    if not holdout or not research_window.contains(dates) or any(dates.overlaps(h) for h in holdout):
        _reject('HOLDOUT_OR_WINDOW', 'explicit nonempty holdout and permitted research dates required')
    if (date.fromisoformat(dates.end)-date.fromisoformat(dates.start)).days >= 31 or not 1 <= max_rows <= 100_000:
        _reject('UNBOUNDED_REQUEST', 'at most 31 dates and 100000 rows')
    if CONSERVATIVE_POLICY.sha256 != POLICY_SHA:
        _reject('POLICY_SHA_MISMATCH', 'approved policy pin changed')
    root = Path(snapshot_path).resolve(strict=True)
    if root.name != PINNED_SNAPSHOT.run_name:
        _reject('SNAPSHOT_ID_MISMATCH', 'root is not pinned snapshot')
    manifest_path = _inside(root, manifest_relative_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = sha256(manifest_bytes).hexdigest()
    if manifest_sha != _pin(expected_manifest_sha256):
        _reject('SNAPSHOT_SHA_MISMATCH', 'manifest SHA mismatch')
    doc = json.loads(manifest_bytes)
    values = {k: _pointer(doc, p) for k,p in asdict(layout).items()}
    if (values['snapshot_id'], values['producer_sha'], values['symbol']) != (PINNED_SNAPSHOT.run_name, PINNED_SNAPSHOT.producer_git_sha, 'TSM'):
        _reject('SNAPSHOT_ID_MISMATCH', 'manifest identity/producer/symbol mismatch')
    path = _inside(root, values['parquet_path'])
    file_sha = _pin(values['parquet_sha256'])
    receipt = integrity_receipt
    if not isinstance(receipt, FileIntegrityReceipt) or receipt.sha256 != _pin(expected_receipt_sha256) or not receipt.verification_ref:
        _reject('INTEGRITY_RECEIPT', 'trusted pinned file receipt required')
    stamp = _stamp(path)
    if receipt.manifest_sha256 != manifest_sha or receipt.parquet_sha256 != file_sha or receipt.stat != stamp:
        _reject('SNAPSHOT_SHA_MISMATCH', 'receipt does not bind unchanged file and pinned manifest')
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    actual_schema = {field.name:str(field.type) for field in schema}
    columns = values['columns']
    roles = {'timestamp','open','high','low','close','volume'}
    if (not isinstance(columns,dict) or set(columns) != roles or len(set(columns.values())) != 6
            or any(v not in actual_schema for v in columns.values()) or values['arrow_schema'] != actual_schema):
        _reject('SCHEMA_MISMATCH', 'explicit manifest columns and complete Arrow schema must match file')
    ts_name = columns['timestamp']; ts_type = schema.field(ts_name).type
    if not pa.types.is_timestamp(ts_type) or not ts_type.tz:
        _reject('SCHEMA_MISMATCH', 'timestamp must carry original Arrow timezone')
    lo = pd.Timestamp(dates.start,tz='America/New_York')
    hi = pd.Timestamp(date.fromisoformat(dates.end)+timedelta(days=1),tz='America/New_York')
    # Inspect footer statistics before decoding any data, including timestamp pages.
    ts_index = parquet.schema.names.index(ts_name)
    for i in range(parquet.num_row_groups):
        stats = parquet.metadata.row_group(i).column(ts_index).statistics
        if stats is None or not stats.has_min_max:
            _reject('ROW_GROUP_BOUNDARY_UNKNOWN', 'timestamp min/max required for holdout isolation')
        first,last = pd.Timestamp(stats.min),pd.Timestamp(stats.max)
        if first.tzinfo is None or last.tzinfo is None:
            _reject('ROW_GROUP_BOUNDARY_UNKNOWN', 'timezone-aware statistics required')
        if last >= lo and first < hi:
            group_window = DateWindow(first.tz_convert('America/New_York').date().isoformat(),last.tz_convert('America/New_York').date().isoformat())
            if not research_window.contains(group_window) or any(group_window.overlaps(h) for h in holdout):
                _reject('HOLDOUT_ROW_GROUP_OVERLAP', 'candidate row group crosses authorization or holdout; use isolated existing partitions')
    predicate = ((ds.field(ts_name) >= pa.scalar(lo.to_pydatetime(),type=ts_type)) &
                 (ds.field(ts_name) < pa.scalar(hi.to_pydatetime(),type=ts_type)))
    scanner = ds.dataset(path,format='parquet').scanner(columns=list(columns.values()),filter=predicate,batch_size=4096)
    batches=[]; count=0
    for batch in scanner.to_batches():
        count += batch.num_rows
        if count > max_rows:
            _reject('ROW_LIMIT', 'bounded row count exceeded')
        batches.append(batch)
    if not count:
        _reject('EMPTY_WINDOW', 'no native rows in requested dates')
    native = pa.Table.from_batches(batches).to_pandas()
    source = native.rename(columns={v:('source_timestamp' if k=='timestamp' else k) for k,v in columns.items()}).copy()
    source['symbol']='TSM';source['contract']='TSM'
    converted = prepare_conservative_bars(source,bar_minutes=15)
    if stamp != _stamp(path) or manifest_bytes != manifest_path.read_bytes():
        _reject('SOURCE_CHANGED', 'source or manifest changed during bounded read')
    provenance = dict(snapshot_id=PINNED_SNAPSHOT.run_name,producer_sha=PINNED_SNAPSHOT.producer_git_sha,
                      manifest_relative_path=manifest_relative_path,manifest_sha256=manifest_sha,
                      parquet_relative_path=values['parquet_path'],parquet_sha256=file_sha,
                      integrity_receipt_sha256=receipt.sha256,integrity_verification_ref=receipt.verification_ref,
                      integrity_method='REUSED_TRUSTED_SHA256_RECEIPT_WITH_STAT_GUARD',
                      arrow_schema=actual_schema,source_timestamp_column=ts_name,source_timezone=ts_type.tz,
                      requested_dates=asdict(dates),research_window=asdict(research_window),holdout=[asdict(h) for h in holdout],
                      native_rows=count,conservative_buckets=len(converted),policy_id=CONSERVATIVE_POLICY.policy_id,
                      policy_version=CONSERVATIVE_POLICY.version,policy_sha256=POLICY_SHA,scope='RESEARCH_ONLY',
                      source_timestamp_label='UNKNOWN',formal_execution_eligible=False,**core_code_identity())
    return NativeTSMResult(native,source,converted,provenance)
