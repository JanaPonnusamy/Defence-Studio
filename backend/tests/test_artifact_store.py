import hashlib

import pytest

from defence_studio.storage.artifacts import ArtifactStore, StoragePathError, safe_filename


JOB = "11111111-1111-1111-1111-111111111111"


def test_write_and_hash_roundtrip(tmp_path):
    store = ArtifactStore(tmp_path)
    data = b"hello defence studio"
    stored = store.write_bytes(JOB, f"jobs/{JOB}/research/a.txt", data, "text/plain")
    assert stored.bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert stored.path.read_bytes() == data
    # Physical location is under <root>/jobs/<job_id>/...
    assert stored.path == (tmp_path / "jobs" / JOB / "research" / "a.txt").resolve()
    assert store.sha256_of(JOB, stored.uri) == stored.sha256


@pytest.mark.parametrize("uri", [
    f"jobs/{JOB}/../../escape.txt",
    f"jobs/{JOB}/../{JOB}x/escape.txt",
    "jobs/other-job/file.txt",
    f"/jobs/{JOB}/abs.txt",
    f"jobs\\{JOB}\\back.txt",
    f"jobs/{JOB}/a/../../b.txt",
    "jobs/only-two.txt",
])
def test_path_traversal_and_foreign_job_rejected(tmp_path, uri):
    store = ArtifactStore(tmp_path)
    with pytest.raises(StoragePathError):
        store.write_bytes(JOB, uri, b"x", "text/plain")


def test_unsafe_job_id_rejected(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(StoragePathError):
        store.write_bytes("../evil", "jobs/../evil/x.txt", b"x", "text/plain")


def test_write_is_atomic_leaves_no_partial(tmp_path):
    store = ArtifactStore(tmp_path)
    store.write_bytes(JOB, f"jobs/{JOB}/a.bin", b"1234", "application/octet-stream")
    leftovers = list((tmp_path / "jobs" / JOB).glob("*.part"))
    assert leftovers == []


def test_safe_filename_caps_and_sanitizes():
    long_slug = "a" * 300
    name = safe_filename(long_slug, "http://x/y", ".html")
    assert len(name) <= 85 and name.endswith(".html")
    # Entirely unsafe input falls back to a deterministic hash stem.
    fallback = safe_filename("///", "http://x/y", ".pdf")
    assert fallback.endswith(".pdf") and len(fallback) > 4
    assert "/" not in fallback and "\\" not in fallback
