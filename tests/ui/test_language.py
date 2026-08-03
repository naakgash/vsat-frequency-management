"""English-only interface guard rail.

Specification section 1 and acceptance criterion 26.1: the complete application is
English, and there must be no mixed Turkish-English interface text. Technical
abbreviations (RF, IF, LO, BUC, BDC, LNB, FWD, RTN, RHCP, LHCP, SR, roll-off) stay in
their standard technical form and are unaffected by these checks.
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import REPO_ROOT, tracked_files

ALLOWED_PREFIXES = ("tests/ui/test_language.py",)

# Characters that exist in the Turkish alphabet but not in English. Deliberately excludes
# ç, ö and ü: those appear in loanwords and proper nouns that are legitimate in English
# text, so including them would produce false positives without catching anything the
# dotless i and the cedilla-s do not already catch.
TURKISH_CHARACTERS = re.compile(r"[ğĞıİşŞ]")

# Short, unambiguous Turkish words matched on word boundaries. Kept small on purpose:
# a large list invites collisions with English and with technical identifiers.
TURKISH_WORDS = re.compile(
    r"\b(ve|veya|ile|için|kullanıcı|kullanici|frekans|ayarlar|kaydet|"
    r"iptal|sil|guncelle|güncelle|ara|listele|yeni|duzenle|düzenle|hata|uyari|uyarı)\b",
    re.IGNORECASE,
)

# The user-facing surface. Python is included because form labels, help text, validation
# messages and status labels are authored there.
SCANNED_SUFFIXES = (".html", ".py", ".js", ".txt", ".md")


def _scannable_files():
    for path in tracked_files(*SCANNED_SUFFIXES):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(ALLOWED_PREFIXES):
            continue
        yield path, relative


def test_no_turkish_specific_characters_in_the_product():
    offenders = []

    for path, relative in _scannable_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if TURKISH_CHARACTERS.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "Turkish characters found. The complete application must be English "
        "(specification section 1).\n" + "\n".join(offenders)
    )


def test_no_turkish_words_in_the_product():
    offenders = []

    for path, relative in _scannable_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = TURKISH_WORDS.search(line)
            if match:
                offenders.append(f"{relative}:{number}: matched {match.group(0)!r}: {line.strip()}")

    assert not offenders, (
        "Turkish words found. The complete application must be English "
        "(specification section 1).\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "sample",
    ["Kullanıcı ayarları", "Yeni frekans ekle", "Kaydet ve çık"],
)
def test_the_detectors_catch_real_turkish(sample):
    """Guard the guard rail: a detector that never fires is not a control."""
    assert TURKISH_CHARACTERS.search(sample) or TURKISH_WORDS.search(sample)


@pytest.mark.parametrize(
    "sample",
    [
        "Occupied bandwidth and roll-off",
        "FWD Satnet Path RHCP hub uplink",
        "BUC and BDC/LNB equipment profiles",
        "Symbol rate, LO frequency, L-band IF",
    ],
)
def test_the_detectors_leave_technical_english_alone(sample):
    """Technical abbreviations must survive the check unchanged (section 1)."""
    assert not TURKISH_CHARACTERS.search(sample)
    assert not TURKISH_WORDS.search(sample)
