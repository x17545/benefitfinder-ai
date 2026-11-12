# 정책·기준금리 통합 갱신

수집 DB에 [0006 원천 변경·재검수](../infrastructure/database/POLICY_SOURCE_RECHECKS.md)를 적용하면 수집 성공본 저장과 연결된 게시 정책·F13의 승인 해제 및 재검수 기록이 함께 반영된다. 새 정책 자동 승인·F12 자동 소비는 후속이며 재검수 목록은 별도 읽기 명령으로 확인한다.

`refresh-data`는 온통청년 목록 수집과 ECOS 기준금리 갱신을 별도 작업으로 동시에 시작하고 결과를 한 번 보고한다. 한 소스가 지연되거나 실패해도 다른 소스를 시도한다. 각각의 기존 저장기가 성공한 결과만 반영하므로, 실패한 소스의 마지막 성공본과 시각은 보존한다. 성공한 다른 소스의 갱신을 되돌리지 않는다.

## 실행

작업 프로세스에 `YOUTHCENTER_API_KEY`, `ECOS_API_KEY`, `BASE_RATE_WRITE_DATABASE_URL`을 설정한다. 알림을 요청하면 `DISCORD_WEBHOOK_URL`도 필요하다. 실제 값은 Git 제외 파일 `data/local/f08.env` 또는 실행 환경에만 두고 파일 권한을 600으로 제한한다. DB에는 [ECOS 0004 마이그레이션과 쓰기 역할](../infrastructure/database/ECOS_RATES.md)이 준비되어 있어야 한다. `.env`는 자동으로 읽지 않는다.

수집 성공본도 DB에 인계하려면 [0005·전용 적재 역할](../infrastructure/database/COLLECTED_POLICIES.md)을 준비하고 `COLLECTION_WRITE_DATABASE_URL`을 설정한다. 미설정은 파일 보관 모드이며 DB 인계 성공을 뜻하지 않는다. 설정한 DB가 실패하면 온통청년 단계는 실패하되 금리는 독립적으로 시도한다. DB 실패 후에는 완전한 파일 성공본을 보존하고 다음 수집 전에 그 성공본의 인계를 재시도한다. `ingest-youthcenter --data-dir data`로 API를 다시 호출하지 않고 복구할 수도 있다.

```sh
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  refresh-data --data-dir data --notify-discord
```

`--notify-discord`를 생략하면 외부 메시지를 보내지 않는다. 요청하면 두 소스가 끝난 뒤 실행 시각과 소스별 성공·실패만 한 번 전송한다. API 키·웹훅·DB 주소·원문·프로필·정책 ID·예외 내용은 전송하지 않는다. 알림 실패 때문에 이미 끝난 수집을 다시 실행하지 않는다.

종료 코드 0은 두 소스·실행 기록·요청한 알림이 성공했다는 뜻이다. 1은 소스·실행 기록·알림 실패 또는 다른 통합 실행이 진행 중임을 뜻한다. 잘못된 CLI 인자는 2다. 정책 수집의 일시 실패 재시도는 기존 실행기가 담당하며 전체 통합 작업을 자동으로 다시 돌리지 않는다.

## 실행 기록

`data/processed/f08/status.json`에는 실행 ID·시각·소스별 고정 상태와 종료 코드만 저장한다. 원문과 비밀값은 들어가지 않는다. 파일은 임시 파일을 만든 뒤 원자적으로 교체한다.

| 항목 | 의미 |
| --- | --- |
| `last_success` | 두 소스가 모두 성공한 마지막 실행의 ID·완료 시각. 알림 성공과 별개이며 아직 없으면 null |
| `last_attempt.data_status` | `running`, `success`, `failed`. 강제 종료된 실행은 running으로 남아 완료로 오인하지 않음 |
| `last_attempt.stages` | 온통청년·ECOS의 시작·완료 시각과 성공 여부·종료 코드 |
| `last_attempt.notification_status` | `not_requested`, `pending`, `sent`, `failed` |
| `last_attempt.record_status` | 최종 기록이 있으면 `success` 또는 `failed`. 기록 자체가 불가능하면 기존 파일과 종료 코드·로그를 함께 확인 |

정책 건수·신규/변경/누락 건수와 선택한 원본 생성본은 기존 `data/processed/f08-youthcenter/status.json`에서 확인한다. 금리의 마지막 성공 관측일·확인 시각은 PostgreSQL `public.ecos_base_rate`에 있다. 통합 상태의 시각을 정책의 사람 검수일이나 금리의 관측일로 사용하지 않는다.

`data/processed/f08/.refresh-lock`의 OS 잠금은 통합 실행의 중복을 막는다. 프로세스가 종료되면 잠금이 해제되고 파일은 남는다. 실행 중 잠금 파일을 삭제하지 않는다. 첫 실행 기록을 만들지 못하거나 기존 기록이 손상되어 있으면 소스 작업을 시작하지 않는다. 이후 기록 쓰기 오류는 다른 소스의 시도를 막지 않으며 종료 코드 1로 보고한다.

## 03:00 KST 스케줄 설정

두 생성기는 설정 파일만 만든다. 작업 디렉터리는 브랜치 전환에 영향을 받지 않는 검토된 커밋의 실행 사본을 사용하고 그 사본에서 `uv sync --locked --no-dev`를 먼저 실행한다. 비밀값과 운영 데이터는 실행 코드 사본 밖에 둔다. 생성된 파일의 경로·명령·스케줄을 검토한 뒤 해당 운영 환경에서 등록한다.

macOS는 [기존 launchd 준비 절차](README.md#macos에서-매일-0300-kst-실행)를 따른다. 생성기의 기본 작업은 `refresh-data`이며, 기존 단독 수집이 필요한 환경은 `--job refresh-youthcenter`를 명시한다. launchd 달력은 실제 호스트 시간대가 KST여야 한다.

Linux·WSL에서 systemd 사용자 관리자가 실행 중이면 다음 설정을 생성할 수 있다. 타이머의 시간대를 `Asia/Seoul`로 지정하므로 호스트가 UTC여도 03:00 KST에 예약한다. `Persistent=false`로 설치 직후 과거 실행분을 따라 실행하지 않으며, 시스템이 꺼져 있거나 사용자 관리자가 멈춘 동안의 예정 실행은 건너뛴다.

```sh
uv run --locked python scripts/f08_systemd.py \
  --project-dir /absolute/runtime --uv /absolute/bin/uv \
  --env-file /absolute/private/f08.env --data-dir /absolute/data \
  --output-dir data/local/f08-systemd
systemd-analyze verify data/local/f08-systemd/dev.benefitfinder.f08.service \
  data/local/f08-systemd/dev.benefitfinder.f08.timer
```

아래는 운영자가 검토 후 실행하는 등록 예다. 생성기와 검사는 등록·실행을 수행하지 않는다.

```sh
mkdir -p "$HOME/.config/systemd/user"
cp data/local/f08-systemd/dev.benefitfinder.f08.service "$HOME/.config/systemd/user/"
cp data/local/f08-systemd/dev.benefitfinder.f08.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now dev.benefitfinder.f08.timer
systemctl --user list-timers dev.benefitfinder.f08.timer
```

서비스의 수집을 수동으로 시작하려면 `systemctl --user start dev.benefitfinder.f08.service`, 예약을 중지하려면 `systemctl --user disable --now dev.benefitfinder.f08.timer`를 사용한다. 실행 기록은 데이터 디렉터리의 `logs/f08.stdout.log`, `logs/f08.stderr.log`와 `journalctl --user -u dev.benefitfinder.f08.service`에서 확인한다. 로그아웃 후에도 실행하려면 운영 환경의 사용자 관리자 유지 설정이 필요하다. 실제 적용은 호스트 운영 정책에 따라 확인한다.

Linux 서비스는 전체 실행이 30분을 넘으면 systemd가 종료한다. 이때 통합 기록은 `running`으로 남을 수 있고, 완료한 소스의 성공본은 보존되지만 최종 Discord 보고는 보장하지 않는다. 해당 실패는 systemd 실행 상태·저널과 소스별 마지막 성공 시각을 함께 확인한다. 아직 등록하지 않은 설정 파일의 검사 성공을 정시 실행 성공으로 기록하지 않는다.

## 남은 F08 범위

이 명령의 성공은 온통청년 목록과 기준금리의 갱신 및 설정한 경우 수집 성공본의 DB 인계다. 보조금24, 변경 원문 F12 분석·검수 대기 생성, 원문 변경 시 기존 승인 해제와 게시 전환까지 완료했다는 뜻이 아니다. 검수 자료가 없는 현재는 실제 정책을 자동 게시하지 않는다. 운영 API 키로 수집 성공·실제 Discord 도착·설치 호스트에서의 정시 실행은 별도 검증해야 한다.
