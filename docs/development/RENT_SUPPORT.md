# 월세지원 실행 규칙과 실제 데이터 재생

정형 API 필드를 기본값으로 읽고 본문의 가구별 소득·재산, 원가구 미고려 예외,
주거급여 월차임분 차감을 실행 가능한 규칙 후보로 정규화한다.
`ingestion/rent_support.py`는 명시적으로 지원하는 2차 청년월세 원문 형태만 처리한다.
다른 정책·다른 문구에 이 숫자나 예외를 일반 적용하지 않는다.

## 준비·수정·검수

- `compile_policy`의 `normalized.executable_rent_support_candidate`에 실행 규칙,
  원본 필드, 모든 본문, 근거 위치·해시, `field_updates`, 미승인 `catalog_record`를 남긴다.
- 소득 숫자 필드의 0을 소득 무제한이나 소득 0원 조건으로 해석하지 않는다.
  본문의 청년가구와 원가구 조건을 별개 규칙으로 보완한다.
- API 연령값과 본문이 다르면 수정 전·후, 원문 근거, Decider의 실제 선택 구간,
  `trigger`와 검수 상태를 남긴다. 지원하는 문구는 코드로 재파싱한다.
  새 모델 호출이나 모델의 자기 신뢰 점수로 값을 승인하지 않는다.
- 수정 후보의 `conditions`에는 본문이 제시한 값이 반영되지만, 원본 API 값은 보존한다.
  모델 인용 위치가 원문과 다르거나 두 소득 본문이 상충하면 후보를 만들지 않는다.
- 후보 카탈로그는 `reviewed:false`, `unresolved_combination_rules:true`,
  `payments:null`이다. 검수·승인 없이 운영 계산 대상으로 게시하지 않는다.
  지원하지 않는 잔여 조건·제외조건은 전체 원문과 함께 검수한다.

## 판정과 금액

`domain_data.cash_facts.rent_support`는 요청 안에서만 쓰는 선택 입력이다.
기존 연봉 구간을 가구별 소득평가액이나 중위소득 비율로 환산하지 않는다.
`youth_income_ratio_bps`·`original_income_ratio_bps`는 공식 심사 기준의 가구별 비율이며
10000은 100%, 6000은 60%다. 임의의 개인 연봉 비율을 넣는 필드가 아니다.
금액은 원 단위 정수이며, 누락/null은 0이나 충족으로 바꾸지 않는다.
입력 스키마와 개인정보 원칙은 [계약 설명](../../contracts/README.md)을 따른다.

- 청년가구 소득과 재산은 각각 포함 경계(`<=`)로 판정한다.
- 원가구 소득·재산은 원문의 연령 이상, 혼인, 또는 기관이 인정한 원가구 미고려 사실이
  확인된 경우에만 생략한다. 혼인 예정은 혼인으로 처리하지 않는다.
  예외가 미상이면 부모 기준 미충족을 바로 탈락으로 단정하지 않는다.
  예외가 적용되어도 청년가구의 소득·재산은 계속 검사한다.
- 청약통장·부모로부터 독립·무주택·월세 계약과 기타 제외조건 확인도 유지한다.
  `other_exclusions_clear`는 검수한 원문의 남은 요건을 확인한 사실이다.
  이 값이 없으면 검증된 부분만으로 전체 자격을 충족으로 확정하지 않는다.
- 월 지급액은 `max(0, min(실제 월세, 월 지원 상한) - 주거급여 월차임분)`이다.
  주거급여 전체액이나 보증금 지원액을 넣지 않는다. 월차임분 미상 시 계산하지 않는다.
  수급액이 0이면 0원이며 음수가 되지 않는다.
- 요청 개월 수와 과거 지원 개월 수의 합은 원문 최대기간을 넘을 수 없다.
  첫 지급예정월과 마지막 지급예정월 모두 원문의 지급기간 안에 있어야 한다.
  첫 지급월이 미상 또는 과거라면 소급 지급을 추정하지 않는다.
- 입력한 월세·월차임분·가구 조건이 유지되는 시나리오의 월별 추정액이다.
  기간 중 변동은 재계산 대상이다. 지급월을 현금흐름으로 만들고 기존 기간별 합계·현재가치
  계산에 전달한다. 상한 금액을 고정 지급액처럼 합산하지 않는다.
- 소득 증가·취업·독립 시 기존 가구 소득 심사 비율을 무효화한다.
  개인 소득 변화로 가구 심사 비율을 추정해 계속 지원한다고 표시하지 않는다.

`CatalogRecord`의 `rent_support` 조건은 검수된 동적 상한 메타데이터만 허용하고
고정 상한 지급 일정을 거부한다. `PublishedRecommendations`에는
`rent_support_payments`를 조립하여 신청자별 현금흐름을 전달한다.
조립되지 않은 경우 `payments:null`인 정책은 계산되지 않는다.

## 검증 재현

공유 회귀는 `backend/tests/test_rent_support.py`의 가상 정책을 사용한다.
실제 수집 원본과 결과는 Git 제외 경로 `data/`에 둔다.

```sh
uv run --locked python -m unittest backend.tests.test_rent_support backend.tests.test_rent_support_profile
uv run --locked python scripts/verify_rent_support.py \
  --manifest data/local/structured-first-verification-v6/manifest.json \
  --policy-id youth:youthcenter:20250115005400110295 \
  --as-of 2024-12-01 \
  --model-comparison data/local/structured-first-verification/comparison.json \
  --output data/local/rent-support-verification/report.json
```

이 명령은 실제 정책과 **가상 신청자**를 결합한다. 미승인 후보가 게시될 수 없음을 검사하고,
로컬에서만 검수가 완료됐다고 가정한 카탈로그로 판정·추천 계산 경로를 재생한다.
실제 승인·DB 게시·유료 API 호출은 하지 않는다. 기존 실제 GPU 추출 결과를 줄 경우
원문 좌표를 재검증하여 수정 후보의 근거로만 사용한다.

소득 비율 경계의 ±1bp, 재산 경계의 ±1원, 원가구 연령 예외 직전·경계,
혼인·기관 인정·예외 미상, 청년가구 제한 유지, 차감 0·상한 직전·상한·초과,
월세가 상한보다 낮은 경우, 입력 누락, 잔여기간·지급기간 초과를 검사한다.
신청 종료 정책을 현재 모집 중으로 바꾸지 않는다.

실제 정책의 당시 수치·예외는 [동일 정책 공식 안내](https://youth.seoul.go.kr/infoData/youthPlcyInfo/view.do?key=2309160001&plcyBizId=20250115005400110295)와 대조했다.
주거급여 월차임분 차감의 공식 예시는 [인천 청년포털 FAQ](https://youth.incheon.go.kr/bbs/bbsMsgDetail.do?bcd=monthly_faq&msg_seq=9)로 교차 확인했다.
현재 다른 연도 정책에 동일 조건이 적용된다는 의미는 아니다.


2026-11-16 저장된 실제 정책 `20250115005400110295`의 재생은 29개 사례를 통과했다.
가구별 비율·재산 경계, 원가구 예외, 차감, 미상·기간 초과를 판정·금액으로 확인했다.
2024-12-01 기준 가상 신청자에서 월세 30만 원·주거급여 월차임분 10만 원이면
월 10만 원, 1년 120만 원·24개월 240만 원이었다. 현재 신청기간은 종료로 유지한다.
검증 결과는 `data/local/rent-support-verification/report.json`에 있고,
운영 DB 게시나 실제 신청자의 수급을 검증한 결과는 아니다.
