"""CHANGES.rst читают люди (окно «Что нового» и /changes в админке) и CI выкатки по тегу: формат —
README «Версия и история изменений»."""

import re
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = ("Добавлено", "Изменено", "Исправлено", "Удалено")
HEADING = re.compile(r"(\d+)\.(\d+)\.(\d+) — (\d{2}\.\d{2}\.\d{4})")
Entry = tuple[tuple[int, int, int], date, list[tuple[str, list[str]]]]


def _underline(line: str, char: str, title: str) -> bool:
    return len(line) >= max(len(title), 3) and line == char * len(line)


def _entries(text: str) -> list[Entry]:
    lines = text.splitlines()
    assert lines[:2] == ["История изменений", "================="]
    start = next((i for i, line in enumerate(lines) if HEADING.fullmatch(line)), None)
    assert start is not None, "no version heading"
    entries: list[Entry] = []
    i = start
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if m := HEADING.fullmatch(line):
            assert _underline(nxt, "-", line), f"line {i + 2}: version underline"
            version = (int(m[1]), int(m[2]), int(m[3]))
            entries.append((version, datetime.strptime(m[4], "%d.%m.%Y").date(), []))
            i += 2
            continue
        sections = entries[-1][2]
        if nxt.startswith("~"):
            assert line in SECTIONS, f"line {i + 1}: section {line!r}"
            assert _underline(nxt, "~", line), f"line {i + 2}: section underline"
            sections.append((line, []))
            i += 2
            continue
        if line.startswith("- "):
            assert sections, f"line {i + 1}: item outside a section"
            assert line[2:].strip(), f"line {i + 1}: empty item"
            sections[-1][1].append(line[2:])
        elif line.startswith("  ") and line.strip():
            assert sections and sections[-1][1], f"line {i + 1}: continuation without an item"
        else:
            assert not line.strip(), f"line {i + 1}: unexpected text {line!r}"
        i += 1
    return entries


def test_changes_format() -> None:
    entries = _entries((ROOT / "CHANGES.rst").read_text(encoding="utf-8"))
    assert entries
    for version, _, sections in entries:
        titles = [title for title, _ in sections]
        assert titles, f"{version}: no sections"
        assert titles == sorted(titles, key=SECTIONS.index), f"{version}: section order"
        assert len(set(titles)) == len(titles), f"{version}: repeated section"
        assert all(items for _, items in sections), f"{version}: empty section"
    versions = [v for v, _, _ in entries]
    assert versions == sorted(versions, reverse=True)
    assert len(set(versions)) == len(versions)
    dates = [d for _, d, _ in entries]
    assert dates == sorted(dates, reverse=True)


GOOD = "История изменений\n=================\n\n0.2.0 — 02.10.2026\n------------------\n\n"


def test_parser_accepts_wrapped_item() -> None:
    [(_, _, sections)] = _entries(GOOD + "Добавлено\n~~~~~~~~~\n\n- Пункт и\n  перенос.\n")
    assert sections == [("Добавлено", ["Пункт и"])]


@pytest.mark.parametrize(
    "text",
    [
        GOOD + "Новое\n~~~~~\n\n- Пункт.\n",
        GOOD + "Добавлено\n~~~~~~~~~\n\nПросто текст.\n",
        GOOD + "- Пункт без раздела.\n",
        GOOD.replace("02.10.2026", "2026-10-02") + "Добавлено\n~~~~~~~~~\n\n- Пункт.\n",
        GOOD.replace("------------------", "-----") + "Добавлено\n~~~~~~~~~\n\n- Пункт.\n",
    ],
    ids=["unknown-section", "stray-text", "item-outside-section", "iso-date", "short-underline"],
)
def test_parser_rejects_broken_entries(text: str) -> None:
    with pytest.raises(AssertionError):
        _entries(text)
