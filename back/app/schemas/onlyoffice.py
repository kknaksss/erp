"""ONLYOFFICE 통합 스키마 — WP-006 Phase 3. editor config 출력 + 저장 콜백 입/출력.

editor config 는 ONLYOFFICE Docs config 규격(중첩·서명 token 포함)이라 자유형 dict 로 둔다
(엄격 모델링 과함 — service 가 규격대로 구성). 콜백 입력은 ONLYOFFICE 규격 필드만 추출(extra 무시).
"""

from pydantic import BaseModel, ConfigDict


class CallbackIn(BaseModel):
    """ONLYOFFICE 저장 콜백 body — 규격 필드 일부만 사용(나머지 무시).

    인증·저장 결정의 SSOT 는 body `token`(콜백 payload 전체 서명 JWT). 라우터가 token 을 검증하고
    그 claims 의 status/url 로 저장 처리하므로 top-level status/url/key 는 서명 안 된 echo(미신뢰).
    status(1 editing·2 MustSave·3 error·4 closed·6 ForceSave·7 force error)는 claims 부재 시만
    참고 — top-level 은 선택(토큰 없는 body 도 라우터에서 401 처리하도록 status optional).
    """

    status: int | None = None
    url: str | None = None
    key: str | None = None
    token: str | None = None

    model_config = ConfigDict(extra="ignore")


class CallbackAck(BaseModel):
    """ONLYOFFICE 규격 ack — `{"error": 0}`(성공). 비0 은 DocServer 가 재시도."""

    error: int = 0
