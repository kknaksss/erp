"""연차 대장 2026 시드 — `seeds/leave_ledger_2026.json` → employee HR 필드 + leave_grant/leave_request.

historical import (T-016):
- lot `remaining` = admin 이 xlsx 에서 FEFO 차감까지 검증한 **최종값 그대로 insert**(시드에서 FEFO 재계산 X).
- request 는 전부 `승인됨`(구글폼 historical → channel=slack 매핑)이지만 **allocation 미생성** — 잔여는
  lot remaining 으로 이미 정확. (그래서 migrated 승인분은 취소·복원 시 lot 미연결 — 리포트 명시.)
- 멱등: 이미 leave_grant 가 있는 직원은 grant/request 재삽입 skip(중복 0 보장). hire_date·department
  세팅은 매 실행 동일값 덮어쓰기(idempotent).

T-015 employee 6컬럼 + WP-002 leave_* 스키마 사용(모델/migration 변경 없음). **워커는 실 DB commit 금지**
— `seed_ledger()` 는 flush 까지만, CLI 는 `--commit` 명시 때만 persist(기본 dry-run·rollback). admin 실행 전용.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.enums import (
    AmPm,
    GrantSource,
    GrantStatus,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest
from app.repositories import leave_grant as grant_repo

# 발생/이월/HR부여 일자가 JSON 에 없어 historical 시드는 회계 시작일로 고정(잔여는 remaining 으로 정확).
SEED_GRANTED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# mediness sync 누락 직원의 placeholder email (email 교정 필요 — 리포트 명시).
PLACEHOLDER_EMAILS: dict[str, str] = {"박소은": "soeun.park.seed@medisolveai.com"}

DEFAULT_JSON = Path(__file__).resolve().parent.parent / "seeds" / "leave_ledger_2026.json"


@dataclass
class SeedStats:
    """시드 결과 집계 — 행수 검산·email 교정 대상 추적."""

    matched: int = 0          # 이름 매칭된 기존 직원
    created: int = 0          # DB 부재로 placeholder 생성
    skipped: int = 0         # 이미 시드됨(grant 존재) → 재삽입 skip
    grants: int = 0
    requests: int = 0
    placeholder_emails: list[str] = field(default_factory=list)  # 교정 필요 (name, email)


async def _find_by_email(session: AsyncSession, email: str) -> Employee | None:
    """email 매칭(대장 email → employee.email = mediness users.id 연결키).

    **이름 매칭 금지**: employee.id == mediness users.id == 로그인 토큰 sub 라서, 연차를
    로그인 identity 에 정확히 붙이려면 안정 키인 email 로 찾아 그 행의 id 를 써야 한다.
    """
    return (await session.execute(
        select(Employee).where(Employee.email == email))).scalars().first()


async def _already_seeded(session: AsyncSession, employee_id: UUID) -> bool:
    """해당 직원 leave_grant 존재 여부 — 멱등 판정(있으면 grant/request 재삽입 skip)."""
    n = (await session.execute(
        select(func.count()).select_from(LeaveGrant).where(
            LeaveGrant.employee_id == employee_id))).scalar_one()
    return n > 0


async def _resolve_employee(session: AsyncSession, entry: dict, stats: SeedStats) -> Employee:
    """대장 entry → employee 행. 이름 매칭, 부재 시 placeholder 생성(email 교정 필요).

    매칭/생성 후 hire_date·department 세팅(email/role 등 기존 미러 값은 보존 — 덮어쓰지 않음).
    """
    name = entry["name"]
    email = entry["email"]
    emp = await _find_by_email(session, email)
    if emp is None:
        emp = Employee(id=uuid4(), email=email, name=name, role=None, active=True)
        session.add(emp)
        stats.created += 1
        stats.placeholder_emails.append(f"{name} <{email}>")
    else:
        stats.matched += 1

    emp.hire_date = date.fromisoformat(entry["hire_date"]) if entry.get("hire_date") else None
    if entry.get("department"):
        emp.department = entry["department"]
    await session.flush()
    return emp


async def _insert_grant(session: AsyncSession, employee_id: UUID, g: dict) -> None:
    await grant_repo.create_lot(
        session,
        employee_id=employee_id,
        category=LeaveCategory(g["category"]),
        amount=Decimal(str(g["amount"])),
        remaining=Decimal(str(g["remaining"])),  # FEFO 차감 후 최종값 그대로(재계산 X)
        source=GrantSource(g["source"]),
        expiry_date=date.fromisoformat(g["expiry_date"]) if g.get("expiry_date") else None,
        reason=g.get("reason"),
        granted_by=None,
        granted_at=SEED_GRANTED_AT,
        status=GrantStatus.ACTIVE,
    )


async def _insert_request(session: AsyncSession, employee_id: UUID, r: dict) -> None:
    # 직접 insert — request_repo.create 는 intake 전용(status=신청됨 강제). 시드는 승인됨·allocation 미생성.
    req = LeaveRequest(
        employee_id=employee_id,
        category=LeaveCategory(r["category"]),
        unit=LeaveUnit(r["unit"]),
        amount=Decimal(str(r["amount"])),
        am_pm=AmPm(r["am_pm"]) if r.get("am_pm") else None,
        use_date=date.fromisoformat(r["use_date"]),
        note=r.get("note"),
        status=RequestStatus(r["status"]),
        channel=RequestChannel(r["channel"]),
        approved_by=None,   # migrated — 승인자 미상
        approved_at=None,
    )
    session.add(req)


async def seed_ledger(
    session: AsyncSession, employees: list[dict], stats: SeedStats | None = None
) -> SeedStats:
    """대장 직원 배열 → employee/grant/request 적재(flush 까지, **commit 안 함**). 멱등.

    각 직원: resolve(매칭/placeholder) + hire_date/department 세팅 → 이미 시드됐으면 grant/request skip,
    아니면 grants/requests insert. allocation 은 만들지 않는다(historical, 잔여=remaining 정확).
    """
    stats = stats or SeedStats()
    for entry in employees:
        emp = await _resolve_employee(session, entry, stats)
        if await _already_seeded(session, emp.id):
            stats.skipped += 1
            continue
        for g in entry.get("grants", []):
            await _insert_grant(session, emp.id, g)
            stats.grants += 1
        for r in entry.get("requests", []):
            await _insert_request(session, emp.id, r)
            stats.requests += 1
        await session.flush()
    return stats


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _run(path: Path, commit: bool) -> SeedStats:
    from app.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            stats = await seed_ledger(session, _load(path))
            if commit:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
    await engine.dispose()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="연차 대장 2026 시드(admin 전용)")
    parser.add_argument("--file", type=Path, default=DEFAULT_JSON, help="대장 JSON 경로")
    parser.add_argument("--commit", action="store_true",
                        help="실 DB 영구 적재(미지정 = dry-run·rollback)")
    args = parser.parse_args()

    stats = asyncio.run(_run(args.file, args.commit))
    mode = "COMMIT" if args.commit else "DRY-RUN(rollback)"
    print(f"[{mode}] file={args.file}")
    print(f"  employees: matched={stats.matched} created={stats.created} skipped={stats.skipped}")
    print(f"  grants={stats.grants} requests={stats.requests}")
    if stats.placeholder_emails:
        print("  ⚠ email 교정 필요(placeholder):")
        for line in stats.placeholder_emails:
            print(f"    - {line}")
    if not args.commit:
        print("  (dry-run — 적재하려면 --commit. 워커는 실행 금지, admin 만)")


if __name__ == "__main__":
    main()
