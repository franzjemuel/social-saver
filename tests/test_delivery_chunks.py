def test_album_chunk_math():
    files = list(range(23))
    chunks = [files[i:i+10] for i in range(0, len(files), 10)]
    assert [len(c) for c in chunks] == [10, 10, 3]
