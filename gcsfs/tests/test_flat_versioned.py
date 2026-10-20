"""Integration tests for object versioning on Standard (Flat) buckets."""

import logging
import os
import posixpath

import pytest
from google.cloud import storage

from gcsfs import GCSFileSystem
from gcsfs.tests.settings import TEST_VERSIONED_BUCKET
from gcsfs.tests.utils import is_real_gcs

a = TEST_VERSIONED_BUCKET + "/tmp/test/a"
b = TEST_VERSIONED_BUCKET + "/tmp/test/b"

# Flag to track if the bucket was created by this test run.
_VERSIONED_BUCKET_CREATED_BY_TESTS = False


def is_versioning_enabled():
    """
    Helper function to check if the test bucket has versioning enabled.
    Returns a tuple of (bool, reason_string).
    """
    # Don't skip when using an emulator, as we create the versioned bucket ourselves.
    global _VERSIONED_BUCKET_CREATED_BY_TESTS
    if not is_real_gcs():
        return True, ""
    try:
        gcs = GCSFileSystem(project=os.getenv("GCSFS_TEST_PROJECT", "project"))
        if not gcs.exists(TEST_VERSIONED_BUCKET):
            logging.info(
                f"Creating versioned bucket for tests: {TEST_VERSIONED_BUCKET}"
            )
            gcs.mkdir(TEST_VERSIONED_BUCKET, enable_versioning=True)
            _VERSIONED_BUCKET_CREATED_BY_TESTS = True

        client = storage.Client(
            credentials=gcs.credentials.credentials, project=gcs.project
        )
        bucket = client.get_bucket(TEST_VERSIONED_BUCKET)
        if bucket.versioning_enabled:
            return True, ""
        return (
            False,
            f"Bucket '{TEST_VERSIONED_BUCKET}' does not have versioning enabled.",
        )
    except Exception as e:
        return (
            False,
            f"Could not verify versioning status for bucket '{TEST_VERSIONED_BUCKET}': {e}",
        )


pytestmark = pytest.mark.skipif(
    not is_versioning_enabled()[0], reason=is_versioning_enabled()[1]
)


def test_info_versioned(gcs_versioned):
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v1")
    v1 = gcs_versioned.info(a)["generation"]
    assert v1 is not None
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v2")
    v2 = gcs_versioned.info(a)["generation"]
    assert v2 is not None and v1 != v2
    assert gcs_versioned.info(f"{a}#{v1}")["generation"] == v1
    assert gcs_versioned.info(f"{a}?generation={v2}")["generation"] == v2


@pytest.mark.parametrize("version_aware", [False, True])
@pytest.mark.parametrize("cache_state", ["cold", "latest", "deleted"])
def test_info_generation_keyword(gcs_versioned, version_aware, cache_state):
    path = TEST_VERSIONED_BUCKET + "/explicit-generation"
    old = b"older-version-is-longer"
    gcs_versioned.pipe_file(path, old)
    generation = gcs_versioned.info(path)["generation"]
    gcs_versioned.pipe_file(path, b"new")
    if cache_state == "deleted":
        gcs_versioned.rm_file(path)
    if cache_state != "cold":
        gcs_versioned.ls(TEST_VERSIONED_BUCKET, refresh=True)
    if cache_state == "deleted":
        with pytest.raises(FileNotFoundError):
            gcs_versioned.info(path)
    else:
        assert gcs_versioned.info(path)["size"] == 3

    gcs_versioned.version_aware = version_aware
    details = gcs_versioned.info(path, generation=generation)
    assert details["generation"] == generation
    assert details["size"] == len(old)
    assert details["type"] == "file"
    if version_aware:
        assert gcs_versioned.info(f"{path}#{generation}") == details
        assert (
            gcs_versioned.info(f"{path}#{generation}", generation=generation) == details
        )


@pytest.mark.parametrize("version_aware", [False, True])
@pytest.mark.parametrize("cache_state", ["cold", "latest", "deleted"])
def test_info_missing_generation(gcs_versioned, version_aware, cache_state):
    path = TEST_VERSIONED_BUCKET + "/missing-generation"
    gcs_versioned.pipe_file(path, b"data")
    generation = gcs_versioned.info(path)["generation"]
    missing = str(int(generation) - 1)
    if cache_state == "deleted":
        gcs_versioned.rm_file(path)
    if cache_state != "cold":
        gcs_versioned.ls(TEST_VERSIONED_BUCKET, refresh=True)
    gcs_versioned.version_aware = version_aware
    with pytest.raises(FileNotFoundError):
        gcs_versioned.info(path, generation=missing)
    if version_aware:
        with pytest.raises(FileNotFoundError):
            gcs_versioned.info(f"{path}#{missing}")


def test_info_conflicting_generation(gcs_versioned):
    path = TEST_VERSIONED_BUCKET + "/conflicting-generation"
    gcs_versioned.pipe_file(path, b"data")
    generation = gcs_versioned.info(path)["generation"]
    with pytest.raises(ValueError, match="Cannot coalesce generations"):
        gcs_versioned.info(f"{path}#{generation}", generation="1")


def test_cat_versioned(gcs_versioned):
    with gcs_versioned.open(b, "wb") as wo:
        wo.write(b"v1")
    v1 = gcs_versioned.info(b)["generation"]
    assert v1 is not None
    with gcs_versioned.open(b, "wb") as wo:
        wo.write(b"v2")
    assert gcs_versioned.cat(f"{b}#{v1}") == b"v1"


def test_cp_versioned(gcs_versioned):
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v1")
    v1 = gcs_versioned.info(a)["generation"]
    assert v1 is not None
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v2")
    gcs_versioned.cp_file(f"{a}#{v1}", b)
    assert gcs_versioned.cat(b) == b"v1"


def test_ls_versioned(gcs_versioned):
    with gcs_versioned.open(b, "wb") as wo:
        wo.write(b"v1")
    v1 = gcs_versioned.info(b)["generation"]
    with gcs_versioned.open(b, "wb") as wo:
        wo.write(b"v2")
    v2 = gcs_versioned.info(b)["generation"]
    dpath = posixpath.dirname(b)
    versions = {f"{b}#{v1}", f"{b}#{v2}"}
    assert versions == set(gcs_versioned.ls(dpath, versions=True))
    assert versions == {
        entry["name"] for entry in gcs_versioned.ls(dpath, detail=True, versions=True)
    }
    assert gcs_versioned.ls(TEST_VERSIONED_BUCKET, versions=True) == [
        f"{TEST_VERSIONED_BUCKET}/tmp"
    ]


def test_find_versioned(gcs_versioned):
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v1")
    v1 = gcs_versioned.info(a)["generation"]
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"v2")
    v2 = gcs_versioned.info(a)["generation"]
    versions = {f"{a}#{v1}", f"{a}#{v2}"}
    assert versions == set(gcs_versioned.find(a, versions=True))
    assert versions == set(gcs_versioned.find(a, detail=True, versions=True))


def test_write_captures_generation(gcs_versioned):
    with gcs_versioned.open(a, "wb") as wo:
        wo.write(b"test content")
    assert wo.generation is not None
    assert wo.generation == gcs_versioned.info(a)["generation"]


def test_write_captures_generation_multipart(gcs_versioned):
    with gcs_versioned.open(b, "wb") as wo:
        wo.write(b"first chunk")
        wo.flush()
        wo.write(b"second chunk")
    assert wo.generation is not None
    assert wo.generation == gcs_versioned.info(b)["generation"]
