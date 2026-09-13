# AIOS MAIN 환경 문맥과 실제 모델 소비

> 문서 역할: Linux-hosted MAIN의 환경 관측·모델 입력·독립 검증 운영 가이드
> 문서 수명주기: 활성
> 내용 검토일: 2026-09-13 — v0.10 실제 모델 TaskSmoke의 종료 판정과 source별 근거 확인; 보존된 v0.8/v0.9 기록은 별도
> 구현 상태: `PARTIAL`; 보존된 v0.8 실제 소비는 수정 검증기 재생 PASS. 이 체크포인트의 개발 소스는 v0.10이며 보존된 v0.9 개발 이력·Linux 프로세스 fixture 검증과 실제 모델 판정을 구분한다. 실제 모델 TaskSmoke는 §6.3의 한정 범위에서 PASS이며 새 운영 이미지 검증은 별도다.
> 상위 정본: [제품 목적](../meta/aios_product_direction_ko.md), [에이전트 운용 계약](../autonomy/agent_operating_contract_ko.md)
> 전역 순서: [현재 작업흐름](../meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next)

## 1. 동작과 사용자 경험

v0.8에서 도입해 v0.10에 유지한 `ask` 문맥 경로는 MAIN 서비스가 관측한 자신의 실행 환경을 사용자 질문과 함께
실제 모델 입력으로 전달한다. `space`는 관측을 갱신하고 사용자에게 같은 상태를 보여 준다.
첫 `ask` 전에 관측이 없으면 MAIN이 최초 관측을 만든다. 이후에는 관측의 원래 시점을
유지한다. 매 질문을 새 관측으로 표시하거나 자동 관리 재결속을 수행하지 않는다.

```text
backend start
agent start
room discover
room bind
space
ask What can you observe about your execution environment?
task status <접수된 UUID>
task result <같은 UUID>
```

v0.10의 `ask`는 UUID 접수를 기록한 뒤 프롬프트로 돌아온다. `task status`로 상태를
확인하고 `task result`로 같은 질문의 결과를 읽는다. 진행 중인 질문의 명시적 중단은
`task cancel <같은 UUID>`이며, 취소 접수와 실제 실행 종료는 §6.1의 별도 증거로 확인한다.
이 예시의 UUID 자리에는 실제 접수 값을 넣으며 새로운 질문을 자동으로 다시 보내지 않는다.

이미 시작·결속된 서비스에는 필요한 조회나 질문만 실행한다. 기존 인스턴스의
`already-running`과 재시작 뒤의 명시적 `room reconcile`은
[MAIN 결속 가이드](aios_agent_binding_guide_ko.md)의 계약을 따른다.

`space`가 보여 주는 runtime directory는 MAIN 프로세스의 실제 현재 작업 디렉터리다.
사용자가 선택한 작업공간이나 해당 디렉터리 파일의 읽기 권한을 뜻하지 않는다.
관측 중에는 파일 본문, 사용자 비밀값, 임의 디렉터리 목록을 수집하지 않는다.

## 2. 관측 범위와 신선도

| 사실 | 관측 원본 | 경계 |
|---|---|---|
| 실행 디렉터리 | MAIN의 `os.getcwd()` | source 프로세스의 실행 위치; 사용자 선택 workspace는 별도 |
| 논리 CPU 수 | MAIN의 `os.cpu_count()` | Linux가 표시한 수; Cell 소유권·CPU quota·실제 처리량이 아님 |
| 총 메모리 | `/proc/meminfo`의 `MemTotal` 한 줄 | Linux-visible 전체 값; 여유 메모리·Cell 귀속과 별도 |
| 네트워크 도달 가능성 | 미관측 | `UNKNOWN`; CLI에서 사용자가 실행한 인터넷 검사 결과를 자동 합치지 않음 |
| 사용자 선택 작업공간 | 선택 계약 미구현 | `UNKNOWN`; 실행 디렉터리로 대체하지 않음 |

관측의 첫 OS 읽기 전에 boot-local monotonic 시각을 기록한다. 요청 문맥을 구성할 때
같은 boot·프로세스의 관측인지 확인하며, 관측 후 30초까지 `CURRENT`, 초과하면 `STALE`다.
미관측 항목은 시간이 지나도 `UNKNOWN`이다. 과거 값이 없는데 `STALE`이나 0을 만들지 않는다.

오래된 원본은 증거에 보존하지만 모델 입력에서는 해당 값을 `null`로 가리고 `STALE`을
표시한다. `CURRENT`도 기록된 확인 시점의 판정이며, 긴 모델 응답이 완료되는 순간까지
모든 값이 현재라는 보장은 아니다. `space`로 명시적으로 새 관측을 만들 수 있다.

관측 데이터가 오래된 경우에는 유효한 모델이 그 한계를 설명할 수 있다. 반면 MAIN의
실행 대상·관리 결속이 무효이면 기존 요청 gate가 추론을 차단한다. 모델을 호출할 수 없는
실패는 운용 계층의 거부 증거로 설명하며, 모델이 생성한 답변으로 표시하지 않는다.

## 3. 계약과 보존 증거

| 표면 | 개발 버전 | 소유 구현 |
|---|---|---|
| CLI | v0.10 / session schema 10 / source 35개; 보존된 v0.9 / session 9 / source 32개와 보존된 space-03 v0.8은 별도 | `hosted/linux/aios_console/` |
| MAIN IPC·run | v0.10 schema 6 / source 28개; 보존된 v0.8/v0.9는 schema 5 / source 25개 | `hosted/linux/aios_agent/` |
| 환경 관측·요청 packet | schema 1 | `hosted/linux/aios_agent/space.py` |
| 추론 receipt | schema 3 유지; Task UUID·revision·실행 상태는 별도 record | `hosted/linux/aios_agent/inference.py` |

packet은 원본·정규화 값·관측 시각·확인 시각, MAIN source 인스턴스·세대·모델,
별도 hosted authority·Cell·Node·결속 세대를 연결한다. Linux PID는 source metadata이고
canonical Node ID가 아니다. `space`는 조회와 관측 갱신이며 발견·결속 상태를 변경하지 않는다.

- `spaces/<uuid>.json`과 MAIN event의 `space_file`: 명시적 `space` 조회 결과.
- 질문 receipt의 `user_prompt`: 사용자 질문 원문.
- 질문 receipt의 `space_context`: 그 요청에서 사용한 packet과 관측 원본.
- `request_body`와 해시: 실제 전송한 ChatML·JSON 입력.
- `response_body`와 해시, `backend_execution`: 같은 backend에 보낸 요청과 받은 결과의 증거.
- 기존 `source_before/source_after`, authority·binding generation: 요청 전후의 MAIN 관계.
- v0.10의 `tasks/<uuid>/<revision>.json`과 `REQUEST/REQUEST_RESULT`: 접수·조회·취소·한 번의 완료 처리와 실행 대상의 연결. Task UUID는 canonical Node identity가 아니다.

원본 관측의 내용 ID는 연결과 일관성 검사 용도다. 해시나 ID만으로 실제 OS 관측을
인증했다고 주장하지 않는다. 독립 verifier는 producer 소스, source/authority 기록,
원본→정규화, 문맥→요청 bytes, backend 실행 증거와 응답을 각각 대조한다.

## 4. 모델 입력과 한계

문맥과 질문은 canonical JSON `{space_data, question}`으로 구성한다. 질문을 JSON으로
해석하면 원문을 복원할 수 있고, ChatML 구분자로 쓰일 수 있는 문자는 이스케이프한다.
관측은 해석할 데이터이며 행동 지시나 권한이 아니다. 모델 출력은 계속 텍스트로만 다룬다.

JSON envelope 전체는 UTF-8 4096 bytes 이내이며 합산 상한 초과는 `space-budget`으로
모델 호출 전에 거부한다. 문맥 요청은 응답 최대 192 tokens, warmup은 기존 8 tokens다.
`cache_prompt=False`, `stream=False`를 유지한다. 이 구현은 아직 이전 대화나 작업 기록을
자동 문맥으로 제공하지 않는다.

4096 bytes는 tokenizer의 token 수나 backend의 문맥 길이 보장이 아니다. 현재 고정 backend
설정은 1024-token context다. 문맥 요청의 성공에는 backend 응답의 `truncated=false`와
입력 prompt의 정확한 echo가 필요하다. 잘림·누락·입력 불일치를 성공으로 처리하지 않는다.
실패 뒤에는 현재 MAIN readiness·결속을 확인하고 해당 명시적 복구 절차를 따른다.

TCG의 보존된 실제 모델 실행은 입력 처리도 느렸다. 문맥 요청에는 최대 2400초,
해당 IPC는 2415초, 모델 콘솔 checkpoint는 2450초의 상한을 둔다. 이는 응답 성능 목표나
자동 재시도 주기가 아니다. warmup·기존 일반 추론의 420초 상한은 유지한다.
v0.10은 실행 중 조회·명시적 취소 기능을 추가했지만, 실제 모델 Task의 사용자 흐름과
범용 수정·연속 대화의 검증을 이 문맥 입력 계약만으로 완료하지 않는다.

2026-09-09에 [Qwen3-0.6B 공식 모델 카드](https://huggingface.co/Qwen/Qwen3-0.6B)의
thinking/non-thinking 형식을 확인했다. 현재 pin과 기존 non-thinking 형식을 유지하며,
모델 카드가 제공하는 최대 문맥 길이를 로컬 실행기의 실제 설정으로 대신 읽지 않는다.

## 5. 검증 경로

### v0.10 Task

현재 연결한 전용 진입은 `Start-AiosConsole.ps1 -TaskSmoke`와
`verify_agent.py <run-directory> --tasks`다. `task_smoke_guest.py`가 한 CLI의 입력·출력과
COMMAND/seq/UUID를 연결하고, `task_smoke_contract.py`가 첫 정상 답변과 별도 진행 중
질문의 명시적 취소·worker 종료·MAIN의 독립 backend 종료 관측을 판정한다.
이 체크포인트의 실제 모델 TaskSmoke는 §6.3의 정상 답변·진행 중 Task 취소·독립 종료 범위에서 PASS다. 반례·fixture와 원본 실행의 판정은 별도로 남긴다.
운영 이미지의 `TaskSmoke` acceptance를 제공한다는 뜻도 아니다. §6.3의 소유 handle 경계를 따른다.

### 보존된 동기 환경 문맥 family

순수 계약·transport 검사는 `test_hosted_space.py`, `test_hosted_inference.py`가,
실제 Linux IPC·관측·질문 전달과 모델을 대신한 fixture 응답은
`test_hosted_agent_runtime.py`와 관련 runtime 검사들이 담당한다. fixture 응답은 실제
모델의 환경 이해 증거가 아니다.

보존된 v0.8 동기 실제 모델 소비 검증은 당시 소스의 `Start-AiosConsole.ps1 -SpaceSmoke`가 실행한다. 고정 질문의
정답을 prompt에 넣지 않고, 실제 문맥에서 실행 디렉터리·CPU·네트워크의 상태와 값을
읽어 답하는지 확인한다. 정상·복구 뒤 요청과 TTL 경과 후 요청을 구분하고,
backend/Cell 교체 시 차단, 명시적 재결속, DNS/HTTPS와 정상 종료도 확인한다.
정확한 명령 계획과 semantic 판정은 `tools/hosted/space_output_contract.py`가 소유한다.
`-GuestTests`를 함께 쓰면 기존 전체 `test_hosted*.py`를 Linux 일반 사용자로 실행한다.
이 전체 검사 checkpoint의 대기 상한은 1200초이며 실행 환경 기록의
`guest_test_timeout_seconds`에 남긴다. 개별 모델 요청·CLI 응답 대기 상한과는 별개다.

이 동기 family의 판정은 `verify_agent.py --space`, 콘솔 실행 판정, 별도 VM 종료 판정을 함께 읽는다.
일반 receipt 재생은 입력·관계·출력 구조를 검사하고 임의 사용자 질문의 의미를 자동 채점하지
않는다. 고정 소비 검증의 PASS를 일반적인 추론 능력이나 임의 도구 실행 지원으로 확대하지 않는다.

## 6. 증거 기록과 배포 경계

- 2026-09-09 Windows host 전체 영향 검사: `767 tests / 211.126s / 70 skipped / exit 0`.
  원문은 `build/hosted-space-host-20260909.log`, 실행 명령·Python 버전·전후 소스 해시는
  같은 이름의 `.json`에 보존했다. 플랫폼 조건 등으로 건너뛴 검사는 통과 수에 포함하지 않는다.
- 전체 검사 뒤 현재 CLI의 recovery 버전 연결 두 파일을 좁게 수정했다. 해당 후속 검사는
  `32 tests / 7.689s / 0 skipped / exit 0`이며 `build/hosted-space-recovery-version-20260909.log`와
  `.json`이 결과·변경 파일·해시를 보존한다. 전체 suite를 이 후속 수정 뒤 다시 실행했다고 쓰지 않는다.
- 이 host 검사는 구버전·현재 fixture의 계약 재생과 변조 거부를 포함한다. 실제 Linux IPC·
  관측·종료의 후속 검사는 아래 `space-03` 기록과 구분한다. 실제 모델의 문맥 소비와
  보존된 실제 이미지 원본의 새 재생은 별도이며 아직 미완료다.
- 같은 날 토크나이저 사전 점검에서는 [공식 Qwen revision](https://huggingface.co/Qwen/Qwen3-0.6B/tree/c1899de289a04d12100db370d81485cdf75e47ca)의
  전체 token ID·merge 순서를 보유 GGUF와 대조했다. 합성한 대표 CURRENT/STALE 입력은
  537~545 tokens, 최대 생성량 포함 729~737 tokens로 1024 이내였다.
  `build/space-tokenizer-preflight-20260909/report.json`과 같은 폴더의 준비·대조 기록이
  소스·다운로드·원본 입력·토큰을 보존한다. 실제 VM 입력이나 모델의 성공 증거는 아니다.
- 첫 개발 VM `build/hosted-space/space-01`은 일부 Linux 검사 원문만 남고 최종 VM·agent
  판정이 없는 미완료 실행이다. 후속 확인에서 실행 프로세스가 없음을 확인했지만 종료 원인은
  확정하지 않았다. 원본을 보존하고 `build/hosted-space/supervision-02/prior-run-observation.json`에
  별도의 host 관측을 남겼다. 이후 실행은 독립 supervisor로 추적하며, 실행 중인 상태나
  supervisor 종료 코드만으로 실제 모델 acceptance를 대신하지 않는다.
- `space-02`는 기존 400초 전체 guest-test checkpoint를 초과해 `guest checkpoint timeout`,
  VM `FAIL / host_killed=true / shutdown_observed=false`로 종료됐다. 원문에는 종료 전
  409개 `ok`와 4개 skip이 있고 개별 실패 표시는 없지만, 전체 suite와 실제 모델 검증은
  완료되지 않았다. `space-02/vm-verdict.json`과 `supervision-02/completion.json`을 보존했다.
- 이 관측 뒤 `qemu_console.py`의 전체 guest-test 대기 상한만 1200초로 조정하고 실행 환경에
  해당 값을 기록했다. 고정 명령·전체 검사 목록·모델 제한·PASS 조건·비정상 종료 처리는
  유지했다. runner 관련 8개 검사는 `0.056s / exit 0`으로 통과했다.
- `space-03`의 Linux 일반 사용자 `test_hosted*.py` 단계는
  `637 tests / 433.684s / 4 skipped / exit 0`으로 완료됐다. 4개 skip은 root 전용 운영 이미지
  검사이므로 해당 이미지 검증 완료로 계산하지 않는다. 정확한 원문과 종료 표식은
  `space-03/linux-serial.log`에 있고 모델 준비 단계로 이어졌다. 이 단계의 통과와 실제 모델
  문맥 소비·전체 VM 정상 종료 판정은 별개다. 나머지 실행은 `supervision-03`의 원본 로그·
  진행·종료 기록으로 추적했다. 아래 최종 원본 판정과 독립 재생을 함께 읽는다.
- 같은 실행의 진행 로그에서 첫 실제 모델 답변은 실행 디렉터리 `CURRENT /root`,
  논리 CPU 수 `CURRENT 2`, 네트워크 `UNKNOWN null`을 반환했다. 실행기 교체 뒤
  `model-not-ready`, Cell 비활성화 뒤 `orphan`, 재활성화 뒤 `stale` 거부와 명시적
  재결속도 관측했다. 이는 진행 중인 `supervision-03/console.log`와 raw serial의 관측이며,
  복구 뒤 두 번째 실제 모델 답변도 같은 상태와 값을 반환했다. TTL 경과 뒤 세 번째 답변은
  경로·CPU를 `STALE null`, 네트워크를 `UNKNOWN null`로 반환했다. DNS·인증서 검증 HTTPS
  요청, MAIN/backend 정지와 CLI 종료까지 진행했다. 이 진행 로그는 최종 판정을 대신하지 않는다.
- `space-03` 원본 최종 판정은 `FAIL / resources_directory`다. VM은
  `vm_exit_code=0 / host_killed=false / shutdown_observed=true`로 정상 종료했고,
  콘솔 26명령·인터넷 판정은 PASS다. MAIN verifier는 파일만 내보낸 산출물에 빈
  `resources` 디렉터리가 없는 것을 거부했다. 첫 MAIN run의 빈 `spaces`도 같은 경계를
  가진다. 원본 source 107개는 전후 동일하며 supervisor exit 1·원본 FAIL을 유지한다.
  보존된 검증기의 별도 `space-03-replay-01`도 같은 실패를 재현하고 원본 bytes 불변을
  확인했다. 빈 inventory의 내보내기 계약 수정과 그 검증기를 통한 독립 재생은 후속으로
  기록하며, 원본 산출물에 디렉터리를 추가하거나 원본 verdict를 바꾸지 않는다.
- 빈 export inventory 계약을 수정한 검증기의 독립 `space-03-replay-02`는 PASS다.
  보존된 v0.8 runtime source 36개를 사용해 실제 CURRENT 답변 2회·STALE 답변 1회,
  무효 대상 거부 3회, MAIN/backend 각 2개 run의 요청·결속·모델 bytes·종료를 대조했다.
  별도 콘솔 26명령·DNS/HTTPS와 raw VM 정상 종료 재생도 통과했다. 원본 223개 파일의
  목록·크기·SHA가 전후 동일하며 원본 pipeline FAIL/exit 1은 그대로 남아 있다.
  `space-03-replay-02/report.json`, `checker-source.json`, `corrected-checker/`와 각
  stdout·stderr가 이 **수정 검증기의 재생 PASS**를 소유한다. 새 VM 실행이나 원본 PASS로
  바꾸어 부르지 않는다. 수정 verifier SHA256은
  `a909bd577933fdaecdd9df1ba115037f404317270543535b569c78e442f61403`이다.
- 수정 검증기의 별도 반례는 `build/space-export-verifier-fix-20260909/`에 보존했다.
  새 16개 검사와 기존 관련 62개 검사가 exit 0이었다. 실제 symlink 생성 12개 subcase는
  Windows 권한 때문에 skip했으며, 해당 논리 분기는 portable filesystem seam 검사로
  별도 확인했다. 이 결과를 실제 Linux symlink 검증으로 표시하지 않는다.
- 실제 세 질문의 guest receipt 소요 시간은 약 1599~1625초이고 각 답변은 48 tokens다.
  backend가 보고한 입력 길이는 525~537 tokens, 입력 처리 시간은 약 1199~1234초다.
  이는 QEMU TCG·2 vCPU·해당 모델/입력의 관측이며 실제 하드웨어 성능이나 효율 개선
  수치가 아니다. 비동기 CLI는 이 긴 대기 중 개입을 가능하게 할 후속이고, 그 자체가
  모델 계산을 빠르게 만들었다는 주장은 아니다.
- 당시 v0.8/v0.9 변경은 beta 작업 트리의 개발 소스였다. 기존 v0.7 모델 이미지·사용자 디스크·history는 보존했으며, 후속 코드의 실행 증거로 재사용하지 않는다.
- 당시 v0.8/v0.9의 이미지 구성 요구는 제품 source 36개였다. 기존 v0.7 이미지의 source 35개와 후속 v0.10의 source 39개는 각 세션·MAIN 계약과 함께 버전별로 분리한다. 이 보존 기록은 새 운영 이미지의 생성·배포 완료를 뜻하지 않는다.

새로운 실행 결과는 source snapshot과 검증 범위를 이 절에 추가하고,
[문서 신선도 원장](../meta/document_freshness_registry_ko.md)과 영향받은 mirror를 함께 갱신한다.

### 6.1. 격리된 후속 상호작용 개발

아래는 v0.8 실행에 사용한 제품 소스를 보존하면서 별도 작업 트리에서 개발한 후속 기록이다.
원본 checkout의 v0.9 로컬 통합과 격리 v0.10 Task 후보를 구분한다. v0.10의 실제 Linux
프로세스 검사는 아래 범위에서 통과했지만 AI 모델을 사용한 Task·새 이미지 acceptance는 아니다.

v0.9 오류 안내 후보는 입력 거부·대상/결속 복구 필요·결과 불명의 상황을 구분하고,
session 9와 해당 source의 literal VERSION을 함께 확인하도록 CLI·검증기를 맞췄다.
Windows 전체 검사는 `787 tests / 212.248s / 70 skipped / exit 0`으로 완료했다.
`build/interaction-isolation-20260909/guidance-integration-02/`의 실행 결과·로그·
`candidate-manifest.json`·전후 source bytes·검토 diff에 보존했다. 최초 검사 01의
Git ownership 오류를 포함한 FAIL도 그대로 유지하며, 해당 작업 트리에 한정한 Git
설정을 자식 프로세스에 적용한 재검사를 별도로 기록했다. `space-03`이 종료된 뒤 원본
checkout에 검토한 15개 파일만 전후 SHA 조건으로 통합했고, 전체 source 109개가 검사한
v0.9와 동일함을 확인했다. 적용 기록은 `guidance-application-01/report.json`이다.
그 뒤의 빈 export inventory 검사 수정은 별도 검증 대상이다. 실제 v0.9 Linux·모델·새
이미지 검증은 완료하지 않았으며, v0.8 원본 FAIL을 이 통합으로 대체하지 않는다.

격리 `interaction-v10-20260909`에는 질문 상태·비동기 worker·동일 CLI 소유 backend의
정지 경계와 daemon·CLI를 연결했다. 후보는 CLI `0.10.0`/session 10/source 35,
MAIN protocol 6/source 28이며 전체 Linux runtime Python 파일은 39개다. 이 수는 새
운영 이미지가 검증됐다는 뜻이 아니다. 원본 checkout의 CLI v0.9 제품 소스는 그대로다.
독립 Task run verifier는 아래 실제 Linux fixture·Windows 재생을 통과했다. 이후 Windows 전체 검사와 Linux 추가 검사는 아래 §6.2의 보존 결과로 확인한다.
대화형 Task의 실제 모델·CLI 종단 실행과 새 이미지 acceptance는 아직 완료하지 않았다.

후보 명령 `ask <질문>`은 UUID를 먼저 만들고 접수를 저장한 뒤 프롬프트로 돌아온다.
`task status <UUID>`, `task result <UUID>`, `task cancel <UUID>`는 같은 질문을 가리킨다.
결과가 확인되지 않아도 자동으로 질문을 다시 보내지 않는다. MAIN 6의 직접 동기 `ask`
IPC는 `request-task-required`로 거부하며, 내부 inference 함수와 과거 protocol 재생은
각자의 기존 계약을 유지한다. 새 Task MAIN RPC는 최종 응답 검증까지 5초로 제한한다.
취소 전체 시간에는 동일 소유 backend의 정지와 별도 상태 조회 시간이 추가된다.

통합 후보와 후속 검증은 다음 사실과 실패 경계를 유지한다.

- 접수한 요청 UUID·원문·실행 대상·관측·관리 결속을 먼저 저장하고 worker를 시작한다.
  시작 함수의 예외만으로 미실행을 판정하지 않는다. 프로세스 생성 뒤 오류가 날 수 있다.
- MAIN의 한 제어 루프가 질문 상태와 source·관리 상태를 변경한다. 계산 중에도 조회를
  처리하며, 조회 자체는 완료 수·event 예산·질문 revision을 증가시키지 않는다.
- 취소 접수 기록을 저장한 뒤 해당 worker에 정지를 요청한다. worker 종료, backend의
  실제 종료, 검증한 모델 답변을 별도로 기록한다. 완료와 취소가 겹치면 이미 확인한 결과를
  보존하고, 확인하지 못한 결과는 `UNKNOWN`으로 남긴다.
  worker가 이미 끝났어도 `UNKNOWN`이면 한 번의 명시적 backend 정지 요청을 허용한다.
  이때 `FINISHED`와 모델 결과는 그대로 보존하며 완료 처리·모델 실행을 반복하지 않는다.
- backend의 retained lease/Popen은 시작한 CLI 프로세스가 소유한다. MAIN daemon이
  같은 파일 경로를 안다고 그 권한을 얻지 않는다. 첫 취소 구현의 범위는 동일 CLI가
  소유한 로컬 실행기 전체의 명시적 정지다. 원래 인스턴스·세대·프로세스 수명과 정상
  종료를 확인하며, 다른 세대의 실행기를 선택하거나 자동 재시도·재시작하지 않는다.
- 저장·완료 처리의 결과가 불확실하면 해당 MAIN 수명을 실패로 끝낸다. 불확실한
  counter 증가나 모델 요청을 다시 수행하지 않는다. 디스크의 접수 기록은 재시작 뒤
  실행을 자동 복원하는 권한이나 작업 연속성 증거가 아니다.
- 한 번에 질문 하나만 실행하고 소유 CLI의 held process 수명을 확인한다. 활성 질문 중
  상태 조회를 유지하며 대상·Cell·자원 관계 변경은 `request-busy`로 거부한다. 접수마다
  최대 6개 질문 event와 전역 종료 여유를 예약한다. 미실행 취소는 자원 창만 닫고
  해당 기간의 CPU 변화를 모델 질문에 귀속시키지 않는다.

**2026-09-09 Task 통합 검증:**

| 증거 | 확인한 범위 | 남은 경계 |
|---|---|---|
| 격리 `build/task-transport-cli-01/report.json` | Windows transport 23 + CLI 14 검사 PASS, skip 0. UUID 보존·재전송 금지·취소 안내·소유 실행기 정지 경로 | 실제 Linux CLI 세션·AI 모델 응답을 증명하지 않음 |
| 격리 `build/task-daemon-integration-01/report.json` | Windows 질문 상태 11 + 실행부 17 + daemon 9 검사 PASS, 총 37/skip 0. 접수 응답 유실·동시 요청 거부·owner 소실·저장 실패·한 번의 완료 처리 | Linux OS 경계·모델은 이 검사에서 fixture |
| 격리 `build/task-linux-probe-01/result.json` | 실제 Linux 3 검사/28.668초/skip 0 PASS. 실행 중 조회, 정상 답변의 반복 조회, worker와 동일 backend의 취소·종료, 교체된 새 backend 보호 | 응답 생성기는 Python fixture. 독립 Task artifact verifier·실제 AI 모델·운영 이미지 acceptance는 별도 |
| 격리 `build/task-linux-probe-02/result.json` 및 `task-linux-probe-02-replay-01/report.json` | 후속 source의 실제 Linux 3 검사/35.831초/skip 0 PASS. 정상 종료한 각 MAIN 기록의 독립 run verifier PASS와 fixture를 live로 요구할 때의 FAIL을 확인. 보존한 3개 기록의 Windows 독립 재생도 같은 판정 | 대화형 CLI 전달·AI 모델·이미지 acceptance와 구분. 실행 중 checker snapshot과 이후 interactive join 보강은 별도 |
| 격리 `build/task-mixed-fixtures-01/report.json` | current CLI 및 과거 CLI·이미지 자료를 다루는 8개 모듈, 203 검사/171.516초/skip 0 PASS. 공용 MAIN 5 fixture의 의미와 기존 이미지 family 보존 | 전체 Linux runtime 검사 전환이나 새 image 39 gate가 아님 |

Linux probe는 146개 전달 파일의 해시와 원문을 보존했다. 전체 VM은 58.204초,
QEMU exit 0, 정상 poweroff, host 강제 종료 없음이며 전달·보존 source가 실행 전후 같았다.
그 뒤 후보 daemon의 `NOT_STARTED` 자원 창 처리를 좁혔다. probe에는 실행 전 취소
사례가 없으며 이 차이를 daemon 보고서의 `current_runtime_delta`에 명시했다. 검사한
source와 이후 변경을 합쳐 같은 실행으로 부르지 않는다. 각 보고서는 해당 격리 작업
트리의 `build/`에 있고, 원본 checkout source 109개와 시작 baseline의 동일성도 대조했다.
probe 02는 좁힌 자원 처리와 독립 run verifier를 포함한 후속 snapshot을 사용했다.
146개 전달 파일, VM 77.704초, exit 0·정상 종료·강제 종료 없음과 세 시험의 원본
regular artifact를 보존했다. Windows 독립 재생은 원본 289개 파일의 해시 불변도 확인했다.

후속은 별도 TaskSmoke lane에서 실제 AI 모델의 접수→실행 중 조회→결과·취소를
한 CLI 세션의 UUID와 연결하는 것이다. 보존된 전체 검사는 새 smoke 도구 변경의
검증을 대신하지 않는다. source 39개 운영 이미지의 생성·acceptance는 그 뒤의 별도 범위다.
기존 동기 `SpaceSmoke`의 고정 명령 배열을 그대로 새 CLI에 실행하면 접수 직후 다음
명령으로 넘어가므로 새 Task 완료의 증거가 되지 않는다. UUID를 따라 완료를 기다리는
별도 실행 계획이 필요하다. 대화 기록·범용 작업 수정·재시작 뒤 자동 재개는 후속이다.

통합 전 원형 검사의 증거는 각 작업 트리의 `build/async-inference-02/`,
`build/backend-fenced-stop-01/`, `build/backend-terminal-binding-01/`에 둔다. 질문 상태·실행부의 보존 검사는 원본 작업 공간의
`build/interaction-v10-isolation-20260909/request-execution-01/`에 있으며,
`27 tests / 0.154s / 0 skipped / exit 0`과 검사한 source bytes를 기록했다.
이 실행 전후 원본 제품 source 107개와 v0.9 기준 source 109개의 불변도 대조했다. fixture와 Windows
검사는 실제 Linux owner·pidfd·IPC·모델 계산 취소를 대신하지 않는다. 위의 후속 Linux
probe가 증명한 프로세스 경계와 별도로, owner 소실·증거 예산·완료 경합의 실제 실행
및 사용자 명령·독립 verifier의 종단 연결을 계속 확인해야 한다.

후보 11개 source의 v0.9 대비 전후 bytes와 각 검사 보고서 SHA는 원본 작업 공간의
`build/interaction-v10-isolation-20260909/candidate-checkpoint-01/manifest.json`에 묶었다.
그 manifest는 통합·실행·배포 완료가 아니라 후속 구현을 이어갈 개발 체크포인트다.

### 6.2. 2026-09-13 인수 시 보존된 Task 결과

아래는 격리 `interaction-v10-20260909`의 종료된 결과를 읽어 확인한 기록이다. 새 실행 결과가
아니며, 각 report가 가리키는 source snapshot을 이후 변경 소스의 검증으로 승계하지 않는다.
ROOT의 날짜별 이관 기록과 로컬 `build/session-handoff-20260913-01`에는 사본과 해시가
보존돼 있다. 이 bundle과 worktree의 `build/` 자료는 Git checkout에 포함되지 않는다.

| 보존 결과 | 확인한 범위 | 경계 |
|---|---|---|
| `build/task-full-host-01/report.json` | Windows 전체 824개 수집·85개 제외·실패 0, exit 0; 검사 전후 123개 Python source 내용·크기 일치 | 824개 전부 실행한 결과가 아니며 Linux·실제 모델·이미지 증거가 아님 |
| `build/task-linux-probe-03/result.json` | 원본 FAIL 보존: 21개 중 기존 discover 전 기대값 `unbound`와 실제 `not-discovered` 차이 | 기대값 수정 뒤 전체 21개 동일 소스 재실행을 주장하지 않음 |
| `build/task-linux-probe-04/result.json` | 수정한 기존 테스트 1개와 BackendMain 1개, Linux 2개 PASS | 실제 프로세스·fixture 응답; 정상 VM 종료, host kill 없음 |
| `build/task-linux-probe-05/result.json` | Cell 2개·Resources 1개, Linux 3개 PASS | 실제 프로세스·fixture 응답; 정상 VM 종료, host kill 없음 |
| `build/task-verifier-02/report.json` | 73개 수집·플랫폼 제한 12개 제외·exit 0 | 원본 `task-verifier-01` FAIL 보존; 제외를 Linux 성공으로 환산하지 않음 |

### 6.3. 2026-09-13 실제 모델 TaskSmoke와 source별 판정

다음은 §6.2의 인수 시 보존 결과 이후 새로 확인한 증거다. 이 체크포인트의 개발 소스는
v0.10이며, 아래 source별 검사·실행 판정은 ROOT 통합이나 beta 게시 완료를 뜻하지 않는다.

| 새 증거 | 확인한 결과 | 경계 |
|---|---|---|
| `build/task-beta-host-01/report.json` | Windows hosted 전체 990개 수집·85개 제외, exit 0, unittest 411.470초; 검사 전후 runtime·계약·도구 소스 150개 변경 없음 | 150개는 Python만이 아니라 `.py/.json/.jsonl/.ps1/.cmd/.sh`를 포함한다. Linux·실제 모델·운영 이미지 acceptance와 별도 |
| `build/task-model-smoke-01/linux-serial.log`의 guest 검사 | 실제 Linux의 `test_hosted_task_linux.py` 3개 PASS, 52.719초. 진행 중 조회·반복 결과 조회의 재전송 방지, worker와 같은 backend의 분리된 종료, 교체 backend 보호를 확인 | Python 응답 fixture를 사용하는 검사다. 뒤이어 진행한 실제 모델 TaskSmoke나 전체 VM 종료의 최종 PASS가 아님 |

**task-model-smoke-01 실행 판정: PASS — 아래의 한정 사용자 흐름과 정상 VM 종료.**
원본 `agent-verdict.json`은 `task_workflow_verified=true`, 질문 2개 중 정상 답변 1개와
진행 중 취소 1개, worker 종료 2개 및 전체 모델 backend의 독립 종료 관측을 확인했다.
MAIN 1회·backend 1회 수명 안에서 같은 CLI의 93개 COMMAND/seq/UUID를 연결했으며
DNS·HTTPS도 통과했다. `vm-verdict.json`은 PASS/exit 0,
`host_killed=false`·`shutdown_observed=true`이고 최종 MAIN 판정의
`vm_shutdown_verified`도 true다. `task-smoke.json`의 driver는
PASS/`termination=exit`/`timed_out=false`였다.

실제 모델은 `aios-qwen3-0.6b-q8_0`이며 원본 판정에서 모델·backend bytes를 확인했다.
첫 답변은 MAIN이 전달한 환경 사실의 정상 결과다. 별도 두 번째 질문은 진행 중 Task 취소,
worker 종료와 전체 모델 backend 종료를 확인했다. 이 lane은 resource/Cell/backend-recover
전체 workflow의 재검증이 아니며 source 39개 운영 이미지 acceptance도 아직 없다.

원본 근거는 `build/task-model-smoke-01/`의 `agent-verdict.json`,
`vm-verdict.json`, `task-smoke.json`, `execution.json`,
`session/session.events.jsonl` 및 MAIN/backend run 기록이다. `environment.json`의
Git HEAD는 `d77b17660562fc41f71571d4b828e1c27ca532b6`이고 작업 트리는 dirty였다.
보존된 `runtime-source/`·`verification-source/`와 원본 manifest/hash를 통해 실행
내용을 이 체크포인트와 대조한다. 이 실행을 이후 생성될 commit SHA의 실행으로 표기하지
않는다. 보존 source를 사용한 독립 재생은 `build/task-model-smoke-01-replay-01/report.json`과
`verdict.json`에서 PASS/exit 0/1.047초였다. 원본 artifact 222개의 목록·크기·SHA는
전후 동일했고, runtime 39개 및 Windows 검사 대상 source 150개가 현재 체크포인트와
일치했다. 재생 판정은 원본 최종 agent 판정과 동일했다. 새 VM·모델 실행을 반복한 결과가 아니다.

별도 `evidence-extract.json`에서 첫 48-token 답변이 `working_directory=CURRENT /root`,
`logical_cpu_count=CURRENT 2`, `network=UNKNOWN null`을 문맥 그대로 반환한 것을
확인했다. 이후 DNS·HTTPS 성공은 이 입력 snapshot의 network 상태와 별도 관측이다.
첫 추론의 2182.573초는 해당 QEMU TCG 실행의 관측이며 하드웨어 성능이나 응답시간 보장이 아니다.

이 PASS의 범위는 같은 CLI가 직접 시작한 backend의 lease/child handle을 보유한 상태에서
MAIN 발견·결속, 첫 질문의 정상 답변·환경 문맥 소비, 별도 두 번째 질문의
`RUNNING` 상태 조회·한 번의 명시적 취소를 연결하는 것이다. 취소 접수, worker 종료,
MAIN의 전체 모델 backend 독립 종료 관측과 최종 `UNKNOWN`을 각각 확인한다. 이미 완료된 두 번째
질문을 대체하려고 새 질문을 반복 접수하지 않는다. `RUNNING`은 제어·worker 상태이며
두 번째 요청의 모델 HTTP 접수나 토큰 생성 시작의 직접 증거가 아니다. 이 시나리오는
진행 중 Task 취소·worker 종료·전체 모델 backend의 독립 종료 관측을 검증하며,
개별 모델 slot의 취소 완료를 증명하지 않는다.

같은 세션의 실제 COMMAND JSON/seq/UUID와 이어지는 prompt를 연결하고,
stdout/stderr/입력·시간·원문 session과 실패 결과도 보존해야 한다. 기존
`SpaceSmoke`와 역사 verifier는 각 source family의 계약을 유지한다.

source 39개 운영 이미지 이행은 별도다. 이미지 부팅 시 backend를 시작하는 주체와 같은
CLI의 실제 소유 handle 유무는 아직 분석하지 않았다. 외부 init이 시작한 backend를 CLI가
자동 소유한다고 가정하지 않으며 기존 image35/36 family와 새 image39 실행을 구분한다.
