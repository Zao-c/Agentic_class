from pathlib import Path

from scripts.audit_public_release import audit


def test_public_release_audit_detects_secret_without_exposing_value(tmp_path: Path):
    key_file = tmp_path / "unsafe.txt"
    key_file.write_text("token=sk-abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")  # public-audit: allow=api_key

    findings = audit(tmp_path, [key_file])

    assert [(item.severity, item.rule, item.path, item.line) for item in findings] == [
        ("error", "api_key", "unsafe.txt", 1)
    ]
    assert "abcdefghijklmnopqrstuvwxyz" not in repr(findings)


def test_public_release_audit_rejects_local_corpus_and_large_files(tmp_path: Path):
    local_file = tmp_path / "data" / "active" / "manual.pdf"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"0" * (10 * 1024 * 1024 + 1))

    findings = audit(tmp_path, [local_file])

    assert {item.rule for item in findings} == {
        "local_only_file_tracked",
        "large_file_over_10mb",
    }


def test_public_release_audit_reports_paths_and_personal_data_as_warnings(tmp_path: Path):
    doc = tmp_path / "README.md"
    doc.write_text(
        "local=C:\\Users\\student\\project\nowner=student@example.edu\nphone=13800138000\n",  # public-audit: allow=local_absolute_path,email_address,china_mobile_number
        encoding="utf-8",
    )

    findings = audit(tmp_path, [doc])

    assert {item.rule for item in findings} == {
        "local_absolute_path",
        "email_address",
        "china_mobile_number",
    }
    assert all(item.severity == "warning" for item in findings)
