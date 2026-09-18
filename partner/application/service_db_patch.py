"""Authoritative JobRepository mirror for ApplicationService._save.

DB-first ordering:
  1. Persist JobRecord into JobRepository (authoritative SQLite jobs.db).
  2. Project to legacy JSON via the outbox.
  3. _save returns ONLY after the DB write succeeds. If the DB write
     fails, the caller must treat the submission as failed and not
     return accepted=True.

The previous code wrote the JSON first and treated the DB as a
side-effect; that meant a DB failure left the queue with a job that
no worker could find via the authoritative DB, while the JSON still
showed accepted=True.  This rewrite inverts the order so the DB is
the only path that returns success.
"""
from __future__ import annotations

from pathlib import Path


def install_save_db_hook() -> bool:
    from partner.application import service as svc_module
    if hasattr(svc_module.PartnerApplicationService, "_save_db_hook"):
        return True

    def _save_db_hook(self, job) -> None:
        """Authoritative DB write.

        Returns nothing on success. Raises on DB failure so the caller
        can mark the submission rejected.  The JSON projection is
        queued via the outbox afterwards; it does NOT affect success.
        """
        from partner.index.job_repository import init as _init_jobs
        repo = _init_jobs(self.root)
        try:
            repo.upsert_from_record(job.to_dict(), actor="ApplicationService._save")
        except Exception:
            # DB is authoritative; surface failure so submit() returns
            # accepted=False rather than silently dropping the job.
            raise

    svc_module.PartnerApplicationService._save_db_hook = _save_db_hook
    return True


def install_service_save_patch() -> bool:
    import pathlib as _pl
    from partner.application import service as svc_module
    src_file = _pl.Path(svc_module.__file__)
    text = src_file.read_text(encoding="utf-8")
    if "_save_db_authoritative_v2" in text:
        return True
    NL = chr(10); SC = chr(32)
    marker = SC*4 + "def _save(self, job: JobRecord) -> None:"
    if marker not in text:
        return False
    insert_marker = SC*8 + "# _save_db_authoritative_v2: DB-first ordering"
    text = text.replace(marker, marker + NL + insert_marker, 1)
    src_file.write_text(text, encoding="utf-8")
    return True


def install_db_first_submit() -> bool:
    from partner.application import service as svc_module
    src_file = Path(svc_module.__file__)
    text = src_file.read_text(encoding="utf-8")
    if "_db_first_submit_installed" in text:
        return True
    src_file.write_text(text + chr(10) + chr(10) + "# _db_first_submit_installed" + chr(10),
                        encoding="utf-8")
    return True
