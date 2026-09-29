"""Базовые образы Dockerfile закреплены по digest: плавающий тег давал бы другой рантайм и статику
при пересборке того же коммита."""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def test_base_images_pinned_by_digest() -> None:
    bases = re.findall(r"^FROM\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert len(bases) == 3
    assert [b for b in bases if not re.fullmatch(r"[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}", b)] == []
