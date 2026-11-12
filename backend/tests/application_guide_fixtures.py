"""Fictional source-bound F07 instructions; never actual submission requirements."""

from copy import deepcopy
import hashlib
from typing import Any

METHOD = "[가상] 온라인 신청 후 가상 창구에 방문합니다."
OFFICE = "[가상] 운영 기관과 다른 접수 창구에서 신청을 받습니다."
DOCUMENTS = ("[가상] 가상 확인서를 제출합니다.", "[가상] 추가 서류는 가상 안내에서 확인합니다.")


def with_guide(data: dict[str, Any], original: str) -> tuple[dict[str, Any], str]:
    item = deepcopy(data)

    def citation(quote: str) -> dict[str, Any]:
        return {"quote": quote, "location": "[가상] 신청 안내", "source": item["benefit"]["source"].copy()}

    item["benefit"]["application_guide"] = {
        "method": citation(METHOD),
        "office": citation(OFFICE),
        "documents": [citation(quote) for quote in DOCUMENTS],
    }
    text = "\n".join((original, METHOD, OFFICE, *DOCUMENTS))
    item["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return item, text
