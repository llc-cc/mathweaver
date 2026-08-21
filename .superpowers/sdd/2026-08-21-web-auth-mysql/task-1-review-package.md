# Task 1 review package

## Review scope

The source snapshot has no Git metadata, so a conventional base-to-head diff cannot be generated. Every production/test file below is new except the two explicitly marked modified. Review their full current contents against the brief and report.

## Requirements and implementation report

- Brief: `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-brief.md`
- Report: `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-report.md`

## New files to inspect in full

- `D:\ywkeji\pdfPipeline-main\backend\storage\__init__.py`
- `D:\ywkeji\pdfPipeline-main\backend\storage\database.py`
- `D:\ywkeji\pdfPipeline-main\backend\storage\models.py`
- `D:\ywkeji\pdfPipeline-main\backend\migrations\alembic.ini`
- `D:\ywkeji\pdfPipeline-main\backend\migrations\env.py`
- `D:\ywkeji\pdfPipeline-main\backend\migrations\script.py.mako`
- `D:\ywkeji\pdfPipeline-main\backend\migrations\versions\20260821_01_web_auth_mysql.py`
- `D:\ywkeji\pdfPipeline-main\backend\tests\conftest.py`
- `D:\ywkeji\pdfPipeline-main\backend\tests\test_database_models.py`
- `D:\ywkeji\pdfPipeline-main\backend\pytest.ini`
- `D:\ywkeji\pdfPipeline-main\.env.example`

## Modified file to inspect

- `D:\ywkeji\pdfPipeline-main\backend\requirements.txt`

The intended modification is only addition of the exact SQLAlchemy, Alembic, PyMySQL, cryptography, and pytest compatible ranges from the brief. Treat unrelated changes as a scope violation.

## Verification evidence

- RED: test collection failed with `ModuleNotFoundError: No module named 'storage'` before production files existed.
- GREEN: `9 passed in 0.13s`.
- Alembic offline SQL: exit 0, nine tables, no `uniprism_alphatest_user` reference.
- Concern: no live MySQL was available.
- Concern: `backend/pytest.ini` disables pytest cacheprovider due a verified Windows shutdown hang; determine whether this is a narrowly justified environment workaround.

## Binding review constraints

- Production must require `MATHWEAVER_DATABASE_URL`; no silent SQLite fallback.
- Unit tests may explicitly use in-memory SQLite.
- Models, migrations, constraints, indexes and names must agree.
- No secrets or real RDS values.
- Concise Chinese comments must explain configuration, transaction and data-safety boundaries.
- No implementation from later tasks.
- Report separate verdicts for specification compliance and code quality.

