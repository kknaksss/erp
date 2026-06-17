"""ORM 모델 — Base.metadata 에 등록. 새 모델은 여기에 export 추가."""

from app.models.employee import Employee

__all__ = ["Employee"]
