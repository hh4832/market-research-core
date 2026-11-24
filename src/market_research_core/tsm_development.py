"""Immutable development-only producer. Planning is footer-only; mixed decoding needs lead approval."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .policy import PINNED_SNAPSHOT
from .tsm_native import DateWindow, NativeSnapshotRejected, _stamp, core_code_identity, verify_file_bytes

WINDOW=DateWindow('2024-05-29','2024-06-28')
LONG_WINDOW=DateWindow('2016-10-19','2024-12-31')
WINDOWS={'20240529_20240628':WINDOW,'20161019_20241231':LONG_WINDOW}
HOLDOUT=DateWindow('2025-01-01','2026-10-16')
COLUMNS={'timestamp':'timestamp_utc','open':'open','high':'high','low':'low','close':'close','volume':'volume'}
LAYOUT={k:'/'+k for k in ('snapshot_id','producer_sha','symbol','parquet_path','parquet_sha256','columns','arrow_schema')}


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_sha(path):
    h=sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def refuse(code,message):
    raise NativeSnapshotRejected(code,message)


@dataclass(frozen=True)
class DevelopmentSnapshotPin:
    snapshot_id: str
    producer_sha: str
    lineage_sha256: str
    start: str='2024-05-29'
    end: str='2024-06-28'

    def __post_init__(self):
        if not re.fullmatch(r'TSM_DEV_1M_'+window_key(self.window)+r'_[0-9a-f]{20}',self.snapshot_id):
            refuse('DERIVED_ID','unsupported development snapshot ID')
        if not re.fullmatch('[0-9a-f]{40}',self.producer_sha) or not re.fullmatch('[0-9a-f]{64}',self.lineage_sha256):
            refuse('DERIVED_PIN','full producer and lineage pins required')
        if self.window not in WINDOWS.values():
            refuse('DERIVED_WINDOW','only approved preparation dates supported')


    @property
    def window(self):
        return DateWindow(self.start,self.end)


def window_key(window):
    for key,value in WINDOWS.items():
        if value==window:return key
    refuse('DERIVED_WINDOW','only allow-listed development windows supported')


def snapshot_id(window,plan_sha,producer):
    return 'TSM_DEV_1M_'+window_key(window)+'_'+digest({'plan':plan_sha,'producer':producer})[:20]


def window_identity(window):
    return {'key':window_key(window),'timezone':'America/New_York',
            'filter_start':window.start+'T00:00:00',
            'filter_end_exclusive':(date.fromisoformat(window.end)+timedelta(days=1)).isoformat()+'T00:00:00',
            'holdout_start':'2025-01-01'}


def validate_derived_manifest(root,doc,pin,dates,holdout):
    window=pin.window
    version='v1.1' if window==WINDOW else 'v1.2'
    if doc.get('contract')!='tsm-development-snapshot-'+version or doc.get('scope')!='DEVELOPMENT_ONLY_RESEARCH':
        refuse('DERIVED_MANIFEST','versioned development-only contract required')
    if doc.get('window')!=asdict(window) or not window.contains(dates) or not any(h.contains(HOLDOUT) for h in holdout):
        refuse('DERIVED_WINDOW','derived window/holdout mismatch')
    path=(root/doc.get('lineage_path','')).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or file_sha(path)!=pin.lineage_sha256:
        refuse('DERIVED_LINEAGE','lineage SHA/path mismatch')
    lineage=json.loads(path.read_text())
    if (lineage.get('parent_snapshot_id'),lineage.get('parent_producer_sha'))!=(PINNED_SNAPSHOT.run_name,PINNED_SNAPSHOT.producer_git_sha):
        refuse('DERIVED_LINEAGE','unrecognized parent identity')
    if lineage.get('producer_sha')!=pin.producer_sha or pin.snapshot_id!=snapshot_id(window,lineage.get('plan_sha256'),pin.producer_sha):
        refuse('DERIVED_LINEAGE','producer / content-derived snapshot identity mismatch')
    if any(not re.fullmatch('[0-9a-f]{64}',s.get('sha256','')) or s.get('sha256_independently_computed') is not True for s in lineage.get('sources',[])):
        refuse('DERIVED_LINEAGE','independently hashed sources required')
    if lineage.get('output_window')!=asdict(window) or lineage.get('holdout_in_output') is not False or not lineage.get('sources'):
        refuse('DERIVED_LINEAGE','source lineage / holdout exclusion missing')
    if window==LONG_WINDOW:
        plan=lineage.get('producer_plan',{})
        body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if (lineage.get('contract')!='tsm-development-lineage-v1.2'
                or plan.get('contract')!='tsm-producer-conversion-plan-v1.2'
                or digest(body)!=plan.get('plan_sha256') or plan.get('plan_sha256')!=lineage.get('plan_sha256')
                or plan.get('output_window')!=asdict(window)
                or any(obj.get('window_identity')!=window_identity(window) for obj in (doc,lineage,plan))
                or any(obj.get('output_holdout_rows')!=0 for obj in (doc,lineage,plan))):
            refuse('DERIVED_LINEAGE','v1.2 window, plan and zero-holdout proof must agree')
        sources=lineage['sources']
        if len(sources)!=len(plan.get('sources',[])):refuse('DERIVED_LINEAGE','source inventory differs')
        risk=False
        for actual,planned in zip(sources,plan['sources']):
            decoded=any(g['decode_may_include_holdout'] for g in planned['selected_row_groups'])
            risk |= planned['opaque_hash_includes_holdout_bytes'] or decoded
            if (actual['path']!=planned['path'] or actual['lineage_ref']!=planned['lineage_ref']
                    or actual['decoded_row_groups']!=[g['index'] for g in planned['selected_row_groups']]
                    or actual['holdout_decode_occurred']!=decoded
                    or actual.get('opaque_hash_includes_holdout_bytes')!=planned['opaque_hash_includes_holdout_bytes']
                    or (planned['expected_sha256'] is not None and actual['sha256']!=planned['expected_sha256'])):
                refuse('DERIVED_LINEAGE','source hash/decode lineage differs from plan')
        if plan.get('requires_research_lead_approval')!=risk:refuse('DERIVED_LINEAGE','risk declaration differs')
        _validate_approval(plan,lineage.get('conversion_approval'))
    if doc.get('lineage_sha256')!=pin.lineage_sha256:
        refuse('DERIVED_LINEAGE','manifest lineage pin differs')
    if doc.get('row_group_policy')!='ONE_NEW_YORK_DATE_PER_GROUP':
        refuse('DERIVED_MANIFEST','daily row groups required')


def plan_conversion(source_files: list[dict], *, parent_manifest_path: str|Path,
                    expected_parent_manifest_sha256: str, window: DateWindow=WINDOW) -> dict:
    """Read ONLY parent manifest and Parquet footers. No file-body hashing or OHLCV decoding."""
    window_key(window)
    manifest=Path(parent_manifest_path).resolve(strict=True)
    if file_sha(manifest)!=expected_parent_manifest_sha256:
        refuse('PARENT_MANIFEST_SHA','parent manifest checksum mismatch')
    if manifest.parent.name!=PINNED_SNAPSHOT.run_name:
        refuse('PARENT_SNAPSHOT','wrong parent directory')
    if not source_files:refuse('SOURCE_INVENTORY','explicit isolated source candidates or normalized fallback required')
    lo=pd.Timestamp(window.start,tz='America/New_York');hi=pd.Timestamp(date.fromisoformat(window.end)+timedelta(days=1),tz='America/New_York')
    parent_doc=json.loads(manifest.read_text())
    parent_results=[r for r in parent_doc.get('results',[]) if isinstance(r,dict) and r.get('symbol')=='TSM']
    parent_claim=parent_results[0].get('published_sha256') if len(parent_results)==1 else None
    entries=[]
    for source in source_files:
        if set(source)!={'path','expected_sha256','lineage_ref'} or not source['lineage_ref']:
            refuse('SOURCE_INVENTORY','path, known checksum (or explicit null), lineage_ref required')
        p=Path(source['path']).resolve(strict=True)
        if window==LONG_WINDOW and (len(source_files)!=1 or p!=manifest.parent/'tsm_normalized.parquet'
                or not isinstance(parent_claim,str) or not re.fullmatch('[0-9a-f]{64}',parent_claim)
                or source['expected_sha256']!=parent_claim):
            refuse('SOURCE_SHA','v1.2 requires the existing Final Gate normalized TSM file and parent-declared SHA')
        pf=pq.ParquetFile(p)
        if set(pf.schema_arrow.names)!=set(COLUMNS.values()):refuse('SOURCE_SCHEMA','native six-column schema required; no implicit normalization')
        ts=pf.schema_arrow.field('timestamp_utc').type
        if not pa.types.is_timestamp(ts) or not ts.tz:refuse('SOURCE_SCHEMA','timezone-aware native timestamp required')
        if source['expected_sha256'] is not None and not re.fullmatch('[0-9a-f]{64}',source['expected_sha256']):
            refuse('SOURCE_SHA','invalid known source checksum')
        groups=[];all_holdout=False
        for i in range(pf.num_row_groups):
            rg=pf.metadata.row_group(i);st=rg.column(pf.schema.names.index('timestamp_utc')).statistics
            if not st or not st.has_min_max:refuse('SOURCE_BOUNDARY','unknown source row-group boundary')
            first,last=pd.Timestamp(st.min),pd.Timestamp(st.max)
            if first.tzinfo is None or last.tzinfo is None:refuse('SOURCE_BOUNDARY','naive source statistics')
            w=DateWindow(first.tz_convert('America/New_York').date().isoformat(),last.tz_convert('America/New_York').date().isoformat())
            overlap=last>=pd.Timestamp('2025-01-01',tz='America/New_York');all_holdout|=overlap
            if last>=lo and first<hi:
                extents=[]
                for col in range(rg.num_columns):
                    c=rg.column(col);starts=[x for x in (c.dictionary_page_offset,c.data_page_offset) if x is not None and x>=0]
                    begin=min(starts);extents.append({'column':c.path_in_schema,'offset':begin,'compressed_bytes':c.total_compressed_size})
                groups.append(dict(index=i,rows=rg.num_rows,first_date=w.start,last_date=w.end,
                                   decode_may_include_holdout=overlap,compressed_column_extents=extents))
        entries.append(dict(path=str(p),expected_sha256=source['expected_sha256'],lineage_ref=source['lineage_ref'],
                            stat=_stamp(p),schema={f.name:str(f.type) for f in pf.schema_arrow},
                            selected_row_groups=groups,full_hash_byte_range=[0,p.stat().st_size],
                            opaque_hash_includes_holdout_bytes=all_holdout))
    if len({e['path'] for e in entries})!=len(entries):refuse('SOURCE_INVENTORY','duplicate sources')
    if not any(e['selected_row_groups'] for e in entries):refuse('EMPTY_SOURCE','no source group covers requested dates')
    needs=any(e['opaque_hash_includes_holdout_bytes'] or any(g['decode_may_include_holdout'] for g in e['selected_row_groups']) for e in entries)
    plan=dict(contract='tsm-producer-conversion-plan-v1',parent_manifest_path=str(manifest),parent_manifest_sha256=expected_parent_manifest_sha256,
              parent_snapshot_id=PINNED_SNAPSHOT.run_name,parent_producer_sha=PINNED_SNAPSHOT.producer_git_sha,
              parent_declared_parquet_sha256=parent_claim,parent_declared_hash_verified=False,
              output_window=asdict(window),holdout=asdict(HOLDOUT),sources=entries,requires_research_lead_approval=needs,
              behavior='HASH_SOURCE_OPAQUE_BYTES_THEN_DECODE_SELECTED_GROUPS_FILTER_WINDOW_WRITE_NEW_DAILY_GROUPS',
              output_holdout_rows=0,strategy_run=False,batch_consumption=0)
    if window==LONG_WINDOW:
        plan.update(contract='tsm-producer-conversion-plan-v1.2',window_identity=window_identity(window))
    return {**plan,'plan_sha256':digest(plan)}


def _validate_plan(plan,approval):
    body={k:v for k,v in plan.items() if k!='plan_sha256'}
    if digest(body)!=plan.get('plan_sha256'):refuse('PLAN_SHA','plan modified')
    if file_sha(plan['parent_manifest_path'])!=plan['parent_manifest_sha256']:refuse('PARENT_MANIFEST_SHA','parent changed')
    for source in plan['sources']:
        if _stamp(Path(source['path']))!=source['stat']:refuse('SOURCE_CHANGED','source changed since footer plan')
    # A self-rehashed JSON flag is not authority: rederive ALL physical risk facts from actual footers.
    fresh=plan_conversion([{k:s[k] for k in ('path','expected_sha256','lineage_ref')} for s in plan['sources']],
                          parent_manifest_path=plan['parent_manifest_path'],
                          expected_parent_manifest_sha256=plan['parent_manifest_sha256'],window=DateWindow(**plan['output_window']))
    if fresh['plan_sha256']!=plan['plan_sha256']:refuse('PLAN_SHA','physical footer plan differs; risk flags cannot be overridden')
    for source in plan['sources']:
        if _stamp(Path(source['path']))!=source['stat']:refuse('SOURCE_CHANGED','source changed since footer plan')
    _validate_approval(plan,approval)


def _validate_approval(plan,approval):
    if plan['requires_research_lead_approval']:
        if not approval or approval.get('status')!='APPROVED' or approval.get('approved_by')!='hh4832' or not str(approval.get('approval_ref','')).startswith('https://'):
            refuse('PRODUCER_APPROVAL_REQUIRED','mixed source requires research-lead-signed approval; Codex cannot sign')
        if (approval.get('plan_sha256')!=plan['plan_sha256'] or approval.get('allow_holdout_decode_for_conversion_only') is not True
                or approval.get('allow_full_source_opaque_hash') is not True or approval.get('allow_strategy_run') is not False):
            refuse('PRODUCER_APPROVAL_MISMATCH','approval must bind exact plan, hash/decode permissions and forbid strategies')


def produce_development_snapshot(plan: dict, output_parent: str|Path, *, producer_sha: str,
                                  approval: dict|None=None) -> dict:
    """No default mixed-source permission. Never modifies source or any authorization log."""
    _validate_plan(plan,approval)
    window=DateWindow(**plan['output_window'])
    identity=core_code_identity()
    if (not re.fullmatch('[0-9a-f]{40}',producer_sha) or identity['core_git_sha']!=producer_sha or identity['core_tree_dirty'] is True):
        refuse('PRODUCER_SHA','producer must match installed clean reviewed Core commit')
    parent=Path(output_parent).resolve()
    if any(parent==Path(s['path']).parent or Path(s['path']).parent in parent.parents for s in plan['sources']):
        refuse('OUTPUT_PATH','output must be outside all source directories')
    sid=snapshot_id(window,plan['plan_sha256'],producer_sha)
    out=parent/sid;out.mkdir(parents=True,exist_ok=False)
    tables=[];lineage=[]
    try:
        for s in plan['sources']:
            source_sha=file_sha(s['path'])
            if s['expected_sha256'] and source_sha!=s['expected_sha256']:refuse('SOURCE_SHA','source differs from declared checksum')
            if _stamp(Path(s['path']))!=s['stat']:refuse('SOURCE_CHANGED','source changed during checksum verification')
            pf=pq.ParquetFile(s['path'])
            groups=[g['index'] for g in s['selected_row_groups']]
            lineage.append(dict(path=s['path'],sha256=source_sha,sha256_independently_computed=True,
                                matched_declared_sha256=s['expected_sha256']==source_sha if s['expected_sha256'] else None,
                                lineage_ref=s['lineage_ref'],decoded_row_groups=groups,
                                opaque_hash_includes_holdout_bytes=s['opaque_hash_includes_holdout_bytes'],
                                holdout_decode_occurred=any(g['decode_may_include_holdout'] for g in s['selected_row_groups'])))
            if not groups:continue
            table=pf.read_row_groups(groups,columns=list(COLUMNS.values()))
            ts=table['timestamp_utc'].type
            lo=pa.scalar(pd.Timestamp(window.start,tz='America/New_York').to_pydatetime(),type=ts)
            hi=pa.scalar(pd.Timestamp(date.fromisoformat(window.end)+timedelta(days=1),tz='America/New_York').to_pydatetime(),type=ts)
            selected=table.filter(pc.and_(pc.greater_equal(table['timestamp_utc'],lo),pc.less(table['timestamp_utc'],hi)))
            if selected.num_rows:tables.append(selected)
            if _stamp(Path(s['path']))!=s['stat']:refuse('SOURCE_CHANGED','source changed while decoding')

        if not tables:refuse('EMPTY_SOURCE','no development rows')
        table=pa.concat_tables(tables)
        table=table.take(pc.sort_indices(table,sort_keys=[('timestamp_utc','ascending')]))
        times=table['timestamp_utc'].to_pandas()
        if times.isna().any() or times.duplicated().any():refuse('SOURCE_DUPLICATES','duplicate/null timestamps; no deduplication permitted')
        days=times.dt.tz_convert('America/New_York').dt.date
        if days.min()<date.fromisoformat(window.start) or days.max()>date.fromisoformat(window.end):refuse('HOLDOUT_OUTPUT','output exceeds development dates')
        data=out/'tsm_development_1m.parquet'
        with pq.ParquetWriter(data,table.schema) as writer:
            for day in sorted(set(days)):
                group=table.filter(pa.array((days==day).to_numpy()))
                writer.write_table(group,row_group_size=group.num_rows)
        # Independent round-trip equality proves unchanged Arrow values, timestamp type and OHLCV schema.
        restored=pq.read_table(data)
        if not restored.equals(table,check_metadata=True):refuse('ROUNDTRIP_VALUES','Parquet round-trip changed values/types')
        line=dict(contract='tsm-development-lineage-v1',parent_snapshot_id=PINNED_SNAPSHOT.run_name,
                  parent_producer_sha=PINNED_SNAPSHOT.producer_git_sha,parent_manifest_sha256=plan['parent_manifest_sha256'],
                  producer_sha=producer_sha,plan_sha256=plan['plan_sha256'],producer_plan=plan,sources=lineage,output_window=asdict(window),
                  parent_declared_parquet_sha256=plan['parent_declared_parquet_sha256'],
                  parent_declared_hash_verified=any(x['sha256']==plan['parent_declared_parquet_sha256'] and Path(x['path']).parent==Path(plan['parent_manifest_path']).parent for x in lineage),
                  holdout_in_output=False,conversion_holdout_decode_approved=bool(approval),
                  conversion_approval=approval,strategy_run=False,batch_consumption=0)
        if window==LONG_WINDOW:
            line.update(contract='tsm-development-lineage-v1.2',window_identity=window_identity(window),output_holdout_rows=0)
        lp=out/'lineage.json';lp.write_text(json.dumps(line,indent=2)+'\n');lsha=file_sha(lp)
        manifest=dict(contract='tsm-development-snapshot-v1.1',snapshot_id=sid,producer_sha=producer_sha,symbol='TSM',
                      parquet_path=data.name,parquet_sha256=file_sha(data),columns=COLUMNS,
                      arrow_schema={f.name:str(f.type) for f in restored.schema},scope='DEVELOPMENT_ONLY_RESEARCH',
                      window=asdict(window),row_group_policy='ONE_NEW_YORK_DATE_PER_GROUP',lineage_path=lp.name,lineage_sha256=lsha,
                      native_rows=table.num_rows,source_snapshot_id=PINNED_SNAPSHOT.run_name)
        if window==LONG_WINDOW:
            manifest.update(contract='tsm-development-snapshot-v1.2',window_identity=window_identity(window),output_holdout_rows=0)
        mp=out/'manifest.json';mp.write_text(json.dumps(manifest,indent=2)+'\n');msha=file_sha(mp)
        receipt=verify_file_bytes(data,expected_sha256=manifest['parquet_sha256'],manifest_sha256=msha,
                                  verification_ref='DERIVED_OUTPUT_INDEPENDENT_HASH:'+plan['plan_sha256'],allow_full_file_hash=True)
        pin=DevelopmentSnapshotPin(sid,producer_sha,lsha,window.start,window.end)
        bundle=dict(snapshot_root=str(out),manifest_relative_path=mp.name,manifest_sha256=msha,layout=LAYOUT,
                    integrity_receipt=asdict(receipt),integrity_receipt_sha256=receipt.sha256,snapshot_pin=asdict(pin))
        (out/'binding.json').write_text(json.dumps(bundle,indent=2)+'\n')
        # Reader's unchanged holdout, receipt, schema, temporal and row-group guards apply to the new artifact.
        report=preflight_development_snapshot(bundle)
        (out/'preflight.json').write_text(json.dumps(report,indent=2)+'\n')
        return bundle
    except Exception as error:
        (out/'FAILED.json').write_text(json.dumps({'status':'FAILED','error':str(error),'batch_consumption':0},indent=2)+'\n')
        raise


def preflight_development_snapshot(bundle: dict) -> dict:
    from .tsm_native import load_native_tsm,ManifestLayout,FileIntegrityReceipt
    pin=DevelopmentSnapshotPin(**bundle['snapshot_pin'])
    from .tsm_native import _inside
    from .temporal import validate_conservative_bars,CONSERVATIVE_POLICY
    root=Path(bundle['snapshot_root']).resolve(strict=True)
    mp=_inside(root,bundle['manifest_relative_path'])
    if file_sha(mp)!=bundle['manifest_sha256']:refuse('SNAPSHOT_SHA_MISMATCH','manifest changed')
    doc=json.loads(mp.read_text())
    validate_derived_manifest(root,doc,pin,pin.window,(HOLDOUT,))
    queries=[pin.window]
    if pin.window==LONG_WINDOW:
        # Footer-only discovery; the native loader checks ALL daily boundaries before each scan.
        pf=pq.ParquetFile(_inside(root,doc['parquet_path']))
        idx=pf.schema.names.index(doc['columns']['timestamp'])
        days=set()
        for i in range(pf.num_row_groups):
            st=pf.metadata.row_group(i).column(idx).statistics
            if not st or not st.has_min_max:refuse('DERIVED_ROW_GROUP','missing daily statistics')
            first,last=pd.Timestamp(st.min),pd.Timestamp(st.max)
            if first.tzinfo is None or last.tzinfo is None:refuse('DERIVED_ROW_GROUP','naive statistics')
            day=first.tz_convert('America/New_York').date().isoformat()
            if day!=last.tz_convert('America/New_York').date().isoformat() or not pin.window.contains(DateWindow(day,day)):
                refuse('DERIVED_ROW_GROUP','group outside fixed development window or spans days')
            days.add(day)
        queries=[]
        for day in sorted(days):
            if queries and (date.fromisoformat(day)-date.fromisoformat(queries[-1].start)).days<31:
                queries[-1]=DateWindow(queries[-1].start,day)
            else:queries.append(DateWindow(day,day))
        if not queries:refuse('EMPTY_SOURCE','no development groups')
    native_rows=conservative_buckets=eligible_buckets=0
    for query in queries:
        loaded=load_native_tsm(bundle['snapshot_root'],manifest_relative_path=bundle['manifest_relative_path'],
            expected_manifest_sha256=bundle['manifest_sha256'],layout=ManifestLayout(**bundle['layout']),
            integrity_receipt=FileIntegrityReceipt(**bundle['integrity_receipt']),expected_receipt_sha256=bundle['integrity_receipt_sha256'],
            symbol='TSM',dates=query,research_window=pin.window,holdout=(HOLDOUT,),snapshot_pin=pin)
        validate_conservative_bars(loaded.conservative_15m,CONSERVATIVE_POLICY)
        native_rows+=len(loaded.native)
        conservative_buckets+=len(loaded.conservative_15m)
        eligible_buckets+=int(loaded.conservative_15m.signal_eligible.sum())
    if pin.window==LONG_WINDOW and native_rows!=doc.get('native_rows'):
        refuse('DERIVED_MANIFEST','preflight row count differs from manifest')
    return dict(status='PASS',checks={k:'PASS' for k in ('Manifest','ParquetSHA256','IntegrityReceipt','ArrowSchema','RowGroupBoundaries',
                     'HoldoutIsolation','CoreNativeLoader','1mTo15m','PITTemporalProtection')},
                native_rows=native_rows,conservative_buckets=conservative_buckets,
                eligible_buckets=eligible_buckets,provenance=loaded.provenance,
                verified_windows=[asdict(q) for q in queries],output_holdout_rows=0,
                batch_consumption=0,strategy_run=False,research_authorization='PENDING_NEW_RESEARCH_LEAD_SIGNATURE',formal_gate='UNCHANGED')


def inventory_isolated_sources(roots: list[str], *, parent_manifest_path: str, parent_manifest_sha256: str,
                               max_files: int=1000) -> dict:
    """Metadata-only inventory in EXPLICIT caller-declared TSM upstream roots; no whole-Drive search."""
    if not roots:
        return {'status':'UPSTREAM_ROOTS_NOT_PROVIDED','inventory_complete':False,'safe_sources':[],'observations':[]}
    candidates=[]
    for root in roots:
        base=Path(root).resolve(strict=True)
        for p in sorted(base.rglob('*.parquet')):
            if len(candidates)>=max_files:
                return {'status':'INVENTORY_LIMIT','inventory_complete':False,'safe_sources':[],'observations':[]}
            candidates.append({'path':str(p),'expected_sha256':None,'lineage_ref':'CALLER_DECLARED_TSM_UPSTREAM_ROOT:'+str(base)})
    observations=[];safe=[]
    for source in candidates:
        try:
            p=plan_conversion([source],parent_manifest_path=parent_manifest_path,expected_parent_manifest_sha256=parent_manifest_sha256)
            observations.append({'path':source['path'],'plan_sha256':p['plan_sha256'],'requires_lead_approval':p['requires_research_lead_approval']})
            if not p['requires_research_lead_approval']:safe.append(source)
        except (ValueError,OSError,pa.ArrowException) as e:
            observations.append({'path':source['path'],'status':'NOT_USABLE','reason':str(e)})
    return {'status':'SAFE_CANDIDATES_FOUND' if safe else 'NO_SAFE_CANDIDATE_IN_PROVIDED_ROOTS',
            'inventory_complete':True,'safe_sources':safe,'observations':observations,
            'source_identity':'CALLER_DECLARED_TSM_ROOTS; NOT_INFERRED_FROM_OHLCV'}


def renew_development_receipt(bundle: dict) -> dict:
    """Portable proof renewal ONLY for an already pinned development-only file.

    Validate manifest/lineage and ALL footer dates before any file-body checksum.
    No original/source file is opened. Return a new binding; never rewrite snapshot.
    """
    import copy
    doc,data=_development_file(bundle)
    receipt=verify_file_bytes(data,expected_sha256=doc['parquet_sha256'],manifest_sha256=bundle['manifest_sha256'],
              verification_ref='DEVELOPMENT_RECEIPT_RENEWAL:'+bundle['integrity_receipt_sha256'],allow_full_file_hash=True)
    result=copy.deepcopy(bundle)
    result.update(integrity_receipt=asdict(receipt),integrity_receipt_sha256=receipt.sha256)
    return result


def _development_file(bundle):
    """Validate pinned metadata and ALL development-only footers, without hashing data.

    Shared by renewal and recovery so even an alleged development file containing
    Holdout is refused before copying or hashing its record bytes.
    """
    from .tsm_native import _inside
    root=Path(bundle['snapshot_root']).resolve(strict=True)
    pin=DevelopmentSnapshotPin(**bundle['snapshot_pin'])
    if root.name!=pin.snapshot_id:refuse('DERIVED_ID','wrong development root')
    mp=_inside(root,bundle['manifest_relative_path'])
    if file_sha(mp)!=bundle['manifest_sha256']:refuse('SNAPSHOT_SHA_MISMATCH','manifest changed')
    doc=json.loads(mp.read_text())
    validate_derived_manifest(root,doc,pin,pin.window,(HOLDOUT,))
    if doc.get('snapshot_id')!=pin.snapshot_id or doc.get('producer_sha')!=pin.producer_sha:
        refuse('DERIVED_PIN','manifest identity mismatch')
    data=_inside(root,doc['parquet_path']);pf=pq.ParquetFile(data)
    if {f.name:str(f.type) for f in pf.schema_arrow}!=doc['arrow_schema']:
        refuse('SCHEMA_MISMATCH','development schema changed')
    idx=pf.schema.names.index(doc['columns']['timestamp'])
    for i in range(pf.num_row_groups):
        st=pf.metadata.row_group(i).column(idx).statistics
        if not st or not st.has_min_max:refuse('DERIVED_ROW_GROUP','cannot establish development-only bytes')
        a,b=pd.Timestamp(st.min),pd.Timestamp(st.max)
        if a.tzinfo is None or b.tzinfo is None:refuse('DERIVED_ROW_GROUP','naive timestamp stats')
        w=DateWindow(a.tz_convert('America/New_York').date().isoformat(),b.tz_convert('America/New_York').date().isoformat())
        if w.start!=w.end or not pin.window.contains(w) or w.overlaps(HOLDOUT):
            refuse('DERIVED_ROW_GROUP','receipt renewal must never hash a holdout-containing file')
    return doc,data


def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--sources',required=True);p.add_argument('--parent-manifest',required=True);p.add_argument('--parent-manifest-sha',required=True);p.add_argument('--out',required=True);p.add_argument('--window',choices=tuple(WINDOWS),default='20240529_20240628')
    p=sub.add_parser('produce');p.add_argument('--plan',required=True);p.add_argument('--output-parent',required=True);p.add_argument('--producer-sha',required=True);p.add_argument('--approval')
    p=sub.add_parser('preflight');p.add_argument('--binding',required=True)
    a=ap.parse_args(argv)
    if a.command=='plan':
        result=plan_conversion(json.loads(Path(a.sources).read_text()),parent_manifest_path=a.parent_manifest,expected_parent_manifest_sha256=a.parent_manifest_sha,window=WINDOWS[a.window])
        with Path(a.out).open('x') as f:json.dump(result,f,indent=2)
    elif a.command=='produce':
        result=produce_development_snapshot(json.loads(Path(a.plan).read_text()),a.output_parent,producer_sha=a.producer_sha,
                                           approval=json.loads(Path(a.approval).read_text()) if a.approval else None)
    else:result=preflight_development_snapshot(json.loads(Path(a.binding).read_text()))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
