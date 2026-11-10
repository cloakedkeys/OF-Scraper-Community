"""Stored hashes must not change with the xxhash version (#404 step 4):
medias.hash in existing databases and profile IDs were made with these."""

import xxhash

from ofscraper.utils.hash import _calc_hash

DATA = b"ofscraper media hash pin"


def test_xxh128_digest_is_unchanged():
    assert xxhash.xxh128(DATA).hexdigest() == "26437130c38a29f102af0efd3cf07be7"
    assert xxhash.xxh128().hexdigest() == "99aa06d3014798d86001c324468d497f"


def test_xxh32_digest_is_unchanged():
    assert xxhash.xxh32(DATA).hexdigest() == "e9384262"


def test_media_file_hash_is_unchanged(tmp_path):
    path = tmp_path / "media.mp4"
    path.write_bytes(DATA * 10_000)  # several read blocks
    assert _calc_hash(path) == "e8aa41d1ac9a7efaa1bbdce860769acc"
