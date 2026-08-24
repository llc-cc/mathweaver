from storage.database import session_scope
from storage.models import History, User
from storage.object_storage import ObjectStorageError
from storage.reconciliation import StorageReconciler


class FakeStorage:
    def __init__(self):
        self.remote = {
            (1, "job-ready", "a" * 32),
            (1, "job-orphan", "b" * 32),
        }

    def list_committed_versions(self):
        return set(self.remote)

    def verify_version(self, user_id, job_id, version_id, checksum):
        del user_id, job_id, version_id, checksum
        raise ObjectStorageError("OSS version verification failed")


def test_reconciliation_reports_missing_and_orphan_versions_without_mutation():
    with session_scope() as session:
        user = User.create_account("student", "S-RECON", None, "Recon", "hash")
        session.add(user)
        session.flush()
        session.add(
            History(
                id="job-ready", user_id=user.id, filename="ready.md",
                storage_status="ready", storage_version="a" * 32,
                storage_checksum="1" * 64,
            )
        )

    report = StorageReconciler(FakeStorage()).scan()

    assert report.referenced_versions == 1
    assert report.missing_or_corrupt_versions == ((1, "job-ready", "a" * 32),)
    assert report.orphan_versions == ((1, "job-orphan", "b" * 32),)
