"""Complete document generations using the existing F08 retry and DB handoff."""

from collections.abc import Callable, Sequence
from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from benefitfinder.application.collection import RawSnapshotStore, RecordVersionStore
from benefitfinder.application.official_documents import (
    SOURCE_NAME, OfficialDocumentFetcher, OfficialDocumentTarget,
)
from benefitfinder.ingestion.collect import CollectionResult
from benefitfinder.ingestion.official_documents import collect_official_documents
from benefitfinder.jobs.youthcenter_refresh import run_youthcenter_refresh


def refresh_official_documents(data_dir: Path, targets: tuple[OfficialDocumentTarget,...], *,
    fetcher: OfficialDocumentFetcher, snapshots: RawSnapshotStore,
    versions_factory: Callable[[Path],RecordVersionStore], clock: Callable[[],datetime],
    pdf_pages: Callable[[bytes],Sequence[str]], ingest_complete: Callable[[],bool] | None,
) -> bool:
    root = data_dir/'processed/f08-official-documents'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    root.chmod(0o700)
    identities = {'documents':[{'external_id':target.external_id,'policy_id':target.policy_id,
                               'categories':list(target.categories)}
                              for target in sorted(targets,key=lambda item:item.external_id)]}
    scope = json.dumps(identities,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

    def build(staging: Path, previous: Path | None) -> CollectionResult:
        scope_path = root/'scope.json'
        try:
            with scope_path.open('rb') as handle:
                if handle.read(256*1024+1) != scope:
                    raise ValueError('official_document_scope_changed')
        except FileNotFoundError:
            if previous is not None:
                raise ValueError('official_document_scope_missing') from None
            with os.fdopen(os.open(scope_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as handle:
                handle.write(scope)
                handle.flush()
                os.fsync(handle.fileno())
        record_path = staging/SOURCE_NAME/'records.json'
        record_path.parent.mkdir(mode=0o700)
        if previous is not None:
            shutil.copyfile(previous/SOURCE_NAME/'records.json',record_path)

        def save_manifest(value: dict[str,object]) -> None:
            path = staging/'observations.json'
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            path.chmod(0o600)

        result = collect_official_documents(targets,fetcher,snapshots,versions_factory(staging),
            clock,pdf_pages,save_manifest)
        if result.complete:
            if not record_path.exists():
                record_path.write_text('{}\n')
            for path in staging.rglob('*'):
                path.chmod(0o700 if path.is_dir() else 0o600)
        return result

    return run_youthcenter_refresh(root,build,clock,ingest_complete=ingest_complete)
