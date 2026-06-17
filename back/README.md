# ERP back

사내 HR/재무 ERP 백엔드. FastAPI + PostgreSQL + SQLAlchemy + Alembic.
v1 도메인 = 연차관리. 구조/계약 SSOT = mediness `products/erp/`.

## 레이어

`Router → Schema → Service → Repository → Model`

```
app/
├── main.py        # FastAPI entry (CORS, 에러 핸들러, 라우터 include)
├── config.py      # pydantic-settings
├── core/          # db(세션) · errors · deps
├── models/        # SQLAlchemy (base.py)
├── schemas/       # Pydantic
├── repositories/  # DB 쿼리 캡슐화
├── services/      # 도메인 로직
└── routers/       # 엔드포인트
alembic/           # 마이그레이션 (단일 head)
tests/             # pytest
```

## 개발

```bash
uv sync --extra dev          # 의존성 설치
cp .env.example .env         # 환경변수
uv run uvicorn app.main:app --reload --port 28082   # 서버 (포트 28082 = mediness +2)
uv run pytest                # 테스트
uv run alembic revision --autogenerate -m "..."   # 마이그레이션 생성
uv run alembic upgrade head  # 적용
```

> 포트 **28082** (mediness back 28080 +2). DB = home-server Postgres 인스턴스(:25433) 의 별도 `erp` database. 상세 = SSOT `products/erp/40-architecture/system.md`.
