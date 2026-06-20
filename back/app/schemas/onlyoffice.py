"""ONLYOFFICE 통합 스키마 — WP-006 Phase 3. editor config 출력 + 저장 콜백 입/출력.

editor config 는 ONLYOFFICE Docs config 규격(중첩·서명 token 포함)이라 자유형 dict 로 둔다
(엄격 모델링 과함 — service 가 규격대로 구성). 콜백 입력은 ONLYOFFICE 규격 필드만 추출(extra 무시).
"""

from pydantic import BaseModel, ConfigDict


class CallbackIn(BaseModel):
    """ONLYOFFICE 저장 콜백 body — 규격 필드 일부만 사용(나머지 무시).

    `status`(1 editing·2 MustSave·3 error·4 closed·6 ForceSave·7 force error) + 편집본 `url` +
    `key`(세션 식별). body 의 `token`(서명)은 라우터가 헤더 토큰으로 검증하므로 여기선 선택.
    """

    status: int
    url: str | None = None
    key: str | None = None
    token: str | None = None

    model_config = ConfigDict(extra="ignore")


class CallbackAck(BaseModel):
    """ONLYOFFICE 규격 ack — `{"error": 0}`(성공). 비0 은 DocServer 가 재시도."""

    error: int = 0
