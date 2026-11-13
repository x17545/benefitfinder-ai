# 개발용 llama.cpp와 운영 외부 API

개발 중에는 이 PC의 GPU에서 llama.cpp를 실행한다. 운영에서는 외부 API를 사용하며 공급자·모델은 아직 정하지 않았다. 둘 다 표준 `/v1/chat/completions` 인터페이스로 연결하고 자격·금액·순위는 기존 코드로 계산한다. API의 기본값은 모델 비활성화이므로 키 없이 실행하는 개발 예제와 F13 기본 문구가 유지된다.

## 로컬 서버 실행

이미 설치된 CUDA 지원 `llama-server`와 GGUF 모델을 사용한다. [llama.cpp 서버 안내](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)의 빌드·도구 호출 지원을 확인한다. 저장소가 실행 파일·모델을 다운로드하거나 저장소 안에 커밋하지 않는다.

```sh
uv run --locked python scripts/llama_server.py \
  --server /path/to/llama-server --model /path/to/model.gguf \
  --cuda-lib-dir /path/to/cuda-libraries
```

CUDA 라이브러리가 시스템에 준비돼 있으면 `--cuda-lib-dir`는 생략한다. 이 옵션은 실행할 프로세스의 라이브러리 경로만 바꾼다. CUDA 장치가 인식되지 않으면 실행을 중단한다. 기본값은 loopback `127.0.0.1:18080`, 모델 별칭 `gemma4-e4b`, 문맥 8192, 동시 처리 슬롯 1, GPU 레이어 99, Jinja 도구 호출, reasoning 비활성화다. `--alias`, `--port`, `--context-size`로 바꿀 수 있다.

공식 문서를 여러 개 읽는 F12 분석은 도구 응답·대화 이력을 합쳐 기본 문맥을 넘을 수 있다. 실제 API와 첨부 공고를 함께 분석할 때 8,222토큰 요청이 8,192 한도에 거절된 것을 확인했다. 이 개발 환경에서는 같은 모델·키에 `--context-size 16384`를 지정했다. 다른 GPU에서는 메모리 여유와 실제 분석 완료를 확인해 값을 정하며, 문맥 오류를 근거 누락이나 분석 완료로 처리하지 않는다.

처음 실행할 때 Git 제외 `data/local/llama-cpp/api-key`를 0600 권한으로 생성한다. 기존 키를 덮어쓰지 않으며 `--key-file`로 경로를 바꿀 수 있다. 서버가 실행 중인 터미널에서 Ctrl+C로 종료한다. 다른 터미널에서 API·F12 명령을 실행할 때 다음 환경 변수를 전달한다.

```sh
export LLM_PROVIDER=llama_cpp
export LLM_BASE_URL=http://127.0.0.1:18080/v1
export LLM_MODEL=gemma4-e4b
export LLM_API_KEY_FILE=data/local/llama-cpp/api-key
pnpm dev
```

`.env` 파일은 자동으로 읽지 않는다. 키는 백엔드에서만 읽으며 `VITE_*`로 전달하지 않는다. `LLM_API_KEY`를 직접 전달할 수도 있지만 `LLM_API_KEY_FILE`과 동시에 설정하면 실패한다. local 모드는 loopback 주소만 허용한다.

F12도 같은 설정을 사용한다. `rule-review extract`, `extract-all`, `extract-facts`의 `--model`은 선택이며 생략하면 `LLM_MODEL`을 사용한다. `extract-chunked-facts`의 구조화 응답에도 같은 제공자를 사용한다. 추출 파일은 계속 검수 대기이며 자동 게시하지 않는다. F13은 검색을 포함한 기존 5초 예산과 인용·숫자 검증을 유지하고 실패 시 기본 문구로 돌아간다. 프로필 전체·특수 조건·부모 정보는 모델에 보내지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs explain-example --category employment
```

이 PC의 RTX 3060과 설치된 Gemma 4 E4B Q8 모델에서 도구 호출 왕복·JSON schema 응답, 가상 정책의 F13 인용 설명과 F12 제한·예외 후보 추출을 확인했다. 최초 F12 실행에서 모델이 방향 코드 대신 한국어 설명을 제출해 거부됐으므로 도구 스키마에 enum의 뜻을 명시했다. `동시에 받을 수 없습니다`라는 명시적 금지 문구를 놓치던 검증도 보완하고 회귀 테스트를 추가했다. 이는 가상 사례의 연동 확인이며 실제 정책의 정확도·누락률 평가를 대신하지 않는다. 잘못된 코드·인용은 계속 거부하며 추출 결과는 승인 대기다.

## 운영 외부 API 설정

`LLM_PROVIDER=external_api`와 공급자가 제공하는 HTTPS `LLM_BASE_URL`, `LLM_MODEL`, 백엔드 `LLM_API_KEY` 또는 키 파일을 명시한다. 외부 제공자는 도구 호출과 JSON schema 구조화 출력을 지원해야 한다. 이 코드는 외부 API 연결 형식과 가상 HTTP 응답만 검증하며 특정 유료 제공자의 운영 품질·비용을 검증한 것은 아니다. 로컬 서버 주소를 운영에 그대로 넣어 개발 PC를 원격으로 사용하는 방식으로 연결하지 않는다.

기존 명시적 Ollama 경로는 호환을 위해 유지한다. 새 제공자를 설정하지 않았을 때 `F13_OLLAMA_MODEL`이나 CLI의 `--model`을 명시하면 이전 경로를 사용한다. API에서 새 제공자와 `F13_OLLAMA_MODEL`을 동시에 설정하면 모호한 설정으로 실패한다.
