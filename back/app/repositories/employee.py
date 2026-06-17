"""employee repository — roster upsert (미러 갱신 / ERP 소유 보존 / hard delete 금지).

upsert 불변식(SPEC-002 §3):
- 미러 필드(`email`·`name`·`role`·`active`)만 mediness 값으로 갱신.
- ERP 소유 필드(`position`·`department`)는 어떤 동기에서도 보존(덮어쓰지 않음).
- hard delete 금지 — 비활성 유저도 행 유지(`active=false` 표시).

매칭키 = `id`(= mediness `users.id`). 호출부(service)가 commit 책임.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

# mediness 응답에서 미러에 쓰는 필드만 추출(발명 금지 — position 등은 미러 안 함)
_MIRROR_FIELDS = ("email", "name", "role", "active")


def _mirror_values(row: dict) -> dict:
    return {f: row.get(f) for f in _MIRROR_FIELDS}


async def upsert_mirror(session: AsyncSession, rows: list[dict]) -> tuple[int, int]:
    """mediness 유저 rows 를 employee 로 upsert. 반환 (updated, new).

    각 row 는 최소 `id` + 미러 필드를 포함(mediness `/auth/me`·`/admin/users` 응답).
    기존 행 → 미러 필드만 갱신(position/department 보존). 없으면 신규 insert.
    """
    if not rows:
        return (0, 0)

    ids = [UUID(str(r["id"])) for r in rows]
    existing = (await session.execute(select(Employee).where(Employee.id.in_(ids)))).scalars().all()
    by_id = {e.id: e for e in existing}

    updated = new = 0
    for row in rows:
        eid = UUID(str(row["id"]))
        vals = _mirror_values(row)
        emp = by_id.get(eid)
        if emp is None:
            # 신규 — 미러 필드 seed, ERP 소유(position/department)는 미지정(None)
            session.add(Employee(id=eid, **vals))
            new += 1
        else:
            # 기존 — 미러 필드만 갱신, position/department 는 손대지 않음(보존)
            for f, v in vals.items():
                setattr(emp, f, v)
            updated += 1

    await session.flush()
    return (updated, new)


async def get_by_id(session: AsyncSession, employee_id: UUID) -> Employee | None:
    return await session.get(Employee, employee_id)


async def list_all(session: AsyncSession) -> list[Employee]:
    """전 직원 명부 — 이름순. (디렉토리 목록 조회, SPEC-002 §3)"""
    result = await session.execute(select(Employee).order_by(Employee.name))
    return list(result.scalars().all())
