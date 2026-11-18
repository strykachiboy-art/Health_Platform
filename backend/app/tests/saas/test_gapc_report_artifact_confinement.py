"""GAP-C report-storage cross-boundary cleanup negatives.

All tests are additive. No changes to existing baseline assertions.
"""
from __future__ import annotations
import os

import pytest

from app.core.exceptions import ValidationError
from app.modules.reports.services import reports_service as service


def _make(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / service.DEFAULT_STORAGE_DIR
    directory.mkdir()
    return directory


def test_report_cleanup_rejects_outside_root(tmp_path, monkeypatch):
    directory = _make(tmp_path, monkeypatch)
    foreign = tmp_path / 'foreign-tenant-report.csv'
    foreign.write_bytes(b'PHI')
    with pytest.raises(ValidationError):
        service._delete_report_file(str(foreign))
    assert foreign.read_bytes() == b'PHI'
    assert directory.is_dir()


def test_report_cleanup_rejects_sibling_prefix_confusion(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch)
    sibling = tmp_path / (service.DEFAULT_STORAGE_DIR + '_foreign')
    sibling.mkdir()
    foreign = sibling / 'clinic_2_patients_foreign.csv'
    foreign.write_bytes(b'PHI')
    with pytest.raises(ValidationError):
        service._delete_report_file(str(foreign))
    assert foreign.read_bytes() == b'PHI'


def test_report_cleanup_rejects_root_directory(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    with pytest.raises(ValidationError):
        service._delete_report_file(str(root))
    assert root.is_dir()


def test_report_cleanup_rejects_symlink_escape(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    target = tmp_path / 'other_hospital_report.csv'
    target.write_bytes(b'OTHER_HOSPITAL')
    link = root / 'clinic_1_patients_link.csv'
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip('Host lacks symlink privileges')
    with pytest.raises(ValidationError):
        service._delete_report_file(str(link), clinic_id=1)
    assert target.read_bytes() == b'OTHER_HOSPITAL'


def test_report_cleanup_rejects_wrong_clinic(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    target = root / 'clinic_22_patients_report.csv'
    target.write_bytes(b'CLINIC_22')
    with pytest.raises(ValidationError):
        service._delete_report_file(str(target), clinic_id=21)
    assert target.read_bytes() == b'CLINIC_22'


def test_report_cleanup_allows_own_clinic(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    target = root / 'clinic_21_patients_report.csv'
    target.write_bytes(b'CLINIC_21')
    service._delete_report_file(str(target), clinic_id=21)
    assert not target.exists()


def test_report_cleanup_allows_missing_own_path(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    service._delete_report_file(str(root / 'clinic_21_patients_missing.csv'), clinic_id=21)


def test_report_cleanup_rejects_invalid_clinic_id(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    target = root / 'clinic_1_patients_report.csv'
    target.write_bytes(b'PHI')
    with pytest.raises(ValidationError):
        service._delete_report_file(str(target), clinic_id=True)
    assert target.read_bytes() == b'PHI'


@pytest.mark.parametrize('bad_path', ['', '   ', 123, True, []])
def test_report_cleanup_rejects_bad_path_types(tmp_path, monkeypatch, bad_path):
    _make(tmp_path, monkeypatch)
    with pytest.raises(ValidationError):
        service._delete_report_file(bad_path)


def test_report_cleanup_keeps_legacy_in_root_delete(tmp_path, monkeypatch):
    root = _make(tmp_path, monkeypatch)
    target = root / 'old_report.csv'
    target.write_bytes(b'legacy')
    service._delete_report_file(str(target))
    assert not target.exists()
