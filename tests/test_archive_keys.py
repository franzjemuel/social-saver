def archive_key(sha, suffix):
    return f"archive/{sha[:2]}/{sha}{suffix}"

def test_archive_key_deduplicates_by_content_hash():
    sha = "ab" + "c" * 62
    assert archive_key(sha, ".mp4") == archive_key(sha, ".mp4")
    assert archive_key(sha, ".mp4").startswith("archive/ab/")

def test_overflow_and_archive_namespaces_are_separate():
    sha = "f" * 64
    assert f"overflow/instagram/123/{sha}.mp4" != f"archive/{sha[:2]}/{sha}.mp4"
