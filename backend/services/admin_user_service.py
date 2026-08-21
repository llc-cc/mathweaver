"""管理员批量导入学生的 CSV 校验与业务编排。"""

from __future__ import annotations

import csv
import io
import re
import secrets
from dataclasses import dataclass
from typing import BinaryIO

from werkzeug.security import generate_password_hash

from services.auth_service import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    AuthenticatedUser,
    AuthorizationError,
)
from storage.auth_repository import (
    AuthRepository,
    StudentBatchImportError,
    StudentImportRecord,
)


MAX_CSV_BYTES = 5 * 1024 * 1024
ACCEPTED_HEADERS = {
    "student_no",
    "display_name",
    "email",
    "class_code",
    "initial_password",
}
REQUIRED_HEADERS = {"student_no", "display_name"}
EMAIL_LOCAL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
EMAIL_PATTERN = re.compile(
    rf"^{EMAIL_LOCAL_ATOM}(?:\.{EMAIL_LOCAL_ATOM})*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class _PhysicalLineTracker:
    """记录 CSV reader 实际消耗的物理行，用于定位多行记录起点。"""

    def __init__(self, text: str) -> None:
        self._iterator = iter(io.StringIO(text, newline=""))
        self._consumed: list[str] = []

    def __iter__(self) -> "_PhysicalLineTracker":
        return self

    def __next__(self) -> str:
        physical_line = next(self._iterator)
        self._consumed.append(physical_line)
        return physical_line

    @property
    def line_count(self) -> int:
        return len(self._consumed)

    def record_start_after(self, consumed_before: int) -> int:
        # csv 会静默跨过空物理行；定位本次消费片段中的首个非空行才是记录起点。
        for line_number, physical_line in enumerate(
            self._consumed[consumed_before:], start=consumed_before + 1
        ):
            if physical_line.strip():
                return line_number
        return consumed_before + 1


@dataclass(frozen=True)
class ImportErrorDetail:
    line: int
    field: str
    message: str


@dataclass(frozen=True)
class NormalizedStudentRow:
    line: int
    student_no: str
    display_name: str
    email: str | None
    class_code: str | None
    initial_password: str | None


@dataclass(frozen=True)
class GeneratedCredential:
    student_no: str
    initial_password: str


@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[NormalizedStudentRow, ...]
    errors: tuple[ImportErrorDetail, ...]


@dataclass(frozen=True)
class ImportResult:
    created: int
    generated_credentials: tuple[GeneratedCredential, ...]
    errors: tuple[ImportErrorDetail, ...]


class AdminUserService:
    """完整校验 CSV 后，将已哈希账号批次交给仓储原子写入。"""

    def __init__(self, repository: AuthRepository) -> None:
        self._repository = repository

    def validate_csv(self, stream: BinaryIO) -> ImportPreview:
        raw = stream.read(MAX_CSV_BYTES + 1)
        if not isinstance(raw, bytes):
            return self._failed("file must be binary")
        if len(raw) > MAX_CSV_BYTES:
            # 文件大小必须在解析前判定，避免超大输入消耗 CSV 解析资源。
            return self._failed("file exceeds 5 MiB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return self._failed("file must be valid UTF-8")

        tracked_lines = _PhysicalLineTracker(text)
        reader = csv.DictReader(tracked_lines, strict=True)
        try:
            raw_headers = reader.fieldnames
        except csv.Error:
            return self._failed("malformed CSV")
        if raw_headers is None:
            return self._failed("CSV header is required")

        headers = [header.strip() if header is not None else "" for header in raw_headers]
        errors: list[ImportErrorDetail] = []
        if len(headers) != len(set(headers)):
            errors.append(ImportErrorDetail(1, "file", "duplicate header"))
        for header in headers:
            if header not in ACCEPTED_HEADERS:
                errors.append(ImportErrorDetail(1, header or "file", "unsupported header"))
        for required in sorted(REQUIRED_HEADERS - set(headers)):
            errors.append(ImportErrorDetail(1, required, "required header missing"))
        if errors:
            return ImportPreview((), tuple(errors))
        reader.fieldnames = headers

        rows: list[NormalizedStudentRow] = []
        seen_student_numbers: set[str] = set()
        seen_emails: set[str] = set()
        while True:
            consumed_before = tracked_lines.line_count
            try:
                source = next(reader)
            except StopIteration:
                break
            except csv.Error:
                errors.append(
                    ImportErrorDetail(
                        tracked_lines.record_start_after(consumed_before),
                        "file",
                        "malformed CSV",
                    )
                )
                break
            line = tracked_lines.record_start_after(consumed_before)
            if None in source:
                errors.append(ImportErrorDetail(line, "file", "row has too many columns"))
            cells = {
                header: (source.get(header) or "").strip()
                for header in ACCEPTED_HEADERS
            }
            if not any(cells.values()):
                continue

            student_no = cells["student_no"]
            display_name = cells["display_name"]
            email = cells["email"].lower()
            class_code = cells["class_code"]
            initial_password = cells["initial_password"]

            self._validate_required_length(errors, line, "student_no", student_no, 64)
            self._validate_required_length(errors, line, "display_name", display_name, 255)
            self._validate_optional_length(errors, line, "email", email, 255)
            self._validate_optional_length(errors, line, "class_code", class_code, 64)
            if email and not EMAIL_PATTERN.fullmatch(email):
                errors.append(ImportErrorDetail(line, "email", "invalid email"))
            if initial_password and not (
                MIN_PASSWORD_LENGTH <= len(initial_password) <= MAX_PASSWORD_LENGTH
            ):
                errors.append(
                    ImportErrorDetail(
                        line,
                        "initial_password",
                        "password length must be 8 to 128 characters",
                    )
                )

            if student_no:
                if student_no in seen_student_numbers:
                    errors.append(
                        ImportErrorDetail(
                            line,
                            "student_no",
                            "duplicate student number in file",
                        )
                    )
                else:
                    seen_student_numbers.add(student_no)
            if email:
                if email in seen_emails:
                    errors.append(
                        ImportErrorDetail(line, "email", "duplicate email in file")
                    )
                else:
                    seen_emails.add(email)

            rows.append(
                NormalizedStudentRow(
                    line=line,
                    student_no=student_no,
                    display_name=display_name,
                    email=email or None,
                    class_code=class_code or None,
                    initial_password=initial_password or None,
                )
            )

        if not rows:
            errors.append(ImportErrorDetail(0, "file", "CSV contains no data rows"))
        return ImportPreview(tuple(rows), tuple(errors))

    def import_students(
        self, stream: BinaryIO, actor: AuthenticatedUser
    ) -> ImportResult:
        if actor.role != "admin":
            raise AuthorizationError
        preview = self.validate_csv(stream)
        if preview.errors:
            return ImportResult(0, (), preview.errors)

        records: list[StudentImportRecord] = []
        generated_credentials: list[GeneratedCredential] = []
        for row in preview.rows:
            # 明文只在当前迭代局部变量中存在，仓储边界只接收不可逆哈希。
            plaintext_password = row.initial_password
            if plaintext_password is None:
                plaintext_password = secrets.token_urlsafe(12)
                generated_credentials.append(
                    GeneratedCredential(row.student_no, plaintext_password)
                )
            records.append(
                StudentImportRecord(
                    line=row.line,
                    student_no=row.student_no,
                    display_name=row.display_name,
                    email=row.email,
                    class_code=row.class_code,
                    password_hash=generate_password_hash(plaintext_password),
                )
            )

        try:
            conflicts = self._repository.import_student_batch(records, actor.id)
        except StudentBatchImportError:
            return ImportResult(
                0,
                (),
                (ImportErrorDetail(0, "file", "database rejected import"),),
            )
        if conflicts:
            return ImportResult(
                0,
                (),
                tuple(
                    ImportErrorDetail(item.line, item.field, item.message)
                    for item in conflicts
                ),
            )
        return ImportResult(len(records), tuple(generated_credentials), ())

    @staticmethod
    def _failed(message: str) -> ImportPreview:
        return ImportPreview((), (ImportErrorDetail(0, "file", message),))

    @staticmethod
    def _validate_required_length(
        errors: list[ImportErrorDetail],
        line: int,
        field: str,
        value: str,
        maximum: int,
    ) -> None:
        if not value:
            errors.append(ImportErrorDetail(line, field, "value is required"))
        elif len(value) > maximum:
            errors.append(ImportErrorDetail(line, field, f"value exceeds {maximum} characters"))

    @staticmethod
    def _validate_optional_length(
        errors: list[ImportErrorDetail],
        line: int,
        field: str,
        value: str,
        maximum: int,
    ) -> None:
        if len(value) > maximum:
            errors.append(ImportErrorDetail(line, field, f"value exceeds {maximum} characters"))
