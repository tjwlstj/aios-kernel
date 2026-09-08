# AIOS MAIN 자원 관측 가이드

이 문서의 실행 ID별 `build/` 자료는 로컬 비추적 증거이며 Git checkout에 포함되지 않는다.
과거 결과의 재검증 명령은 해당 원본을 보유한 환경용이며 새 실행의 결과 경로와 구분한다.

기준일: 2026-09-07. 이 문서는 `aios_agent_binding_guide_ko.md`의 실제 MAIN
추론·명시적 binding 이후의 작은 자원 관측 조각을 정의한다. 관측 단계는
**로컬 실제 실행 검증 완료(DIRECT/PARTIAL)** 이다. 아래 `resource-02`가 보존된 v0.4
관측 단계 소스의 CPU/RSS·system PSI·실제 모델·인터넷·정상 종료 증거를 소유한다.
이후 CLI v0.5의 Cell 수명 확장은 [Cell 가이드](aios_cell_lifecycle_guide_ko.md)를 따른다.
v0.6에서 도입한 [backend 수명·MAIN 실행 결속](aios_backend_lifecycle_guide_ko.md)은 `PARTIAL`
구현이며 `backend-02`에서 로컬 Linux·실제 모델·교체 후 요청 거부·명시적 복구·자원 관측·
인터넷·정상 종료와 당시 소스 재검증을 통과했다. `backend-02`를 포함한 이전 실행은
각각 당시 보존 소스의 증거이며 이후 변경된 현재 소스의 실행 검증을 대신하지 않는다.
backend-02가 자원 관측 시나리오 전체의 재실행을 뜻하지 않는다.
전체 H2/H3, 자원 ownership·principal·apply 및 설치형 OS 완료를 뜻하지 않는다.

## 목표와 정체성

AIOS 관리 계층이 현재 MAIN Node와 실제 모델 backend 사이의
`uses-model-backend` 관계를 명시적으로 승인하고, 한 번의 추론 전후에
두 프로세스의 CPU 시간과 RSS를 별도로 관측한다. 독자적인 Cell/Node,
관계, 세대, 판단 계약의 주체는 AIOS이다. Linux의 PID, 시작 tick,
부팅 UUID와 `/proc` 자료는 그 계약에 연결하는 외부 source이다.

이 단계는 native K1/K2 ABI, frozen H1 trace, Linux 코드 import 및
resource manifest를 바꾸지 않는다. native registry와 hosted authority의
인스턴스는 다르며 Node 101은 typed semantic counterpart이다.
`observation_only=true`, `ownership_valid=false`, `resource_actions=UNSUPPORTED`를
항상 유지한다. quota, scheduler 변경, 자동 정책 적용은 후속 작업이다.

## 관측 계약

관측 대상은 `main-control-process`와 `model-backend-process` 두 개로 고정한다.
모델 호출 worker나 자식 프로세스 전체를 포함한다고 주장하지 않는다.
v0.4 증거의 backend는 개발 launcher가 시작했고, 현재는 제품 backend supervisor가
별도 모델 자식을 관리한다. 이름 검색이나 PID 파일만으로 MAIN에 연결하지 않는다. private Unix socket,
실제 peer 자격, 자식의 시작 tick 및 그 자식이 소유한 loopback LISTEN socket을
확인한 뒤 `resources link` 요청으로 관계를 만든다.

`resources status`는 관계의 현재 유효성과 마지막 관측을 표시한다.
`resources sample`은 짧은 두 시점의 자료를 읽는다. 명시적으로 연결된 상태의
`ask`는 실제 요청 전후 자료를 receipt의 request UUID에 연결한다.
관측 실패는 추론 결과와 따로 표시하며 MAIN의 model-ready 또는 source 세대를
변경하지 않는다. 연결이 없는 기존 `ask`도 기존 계약대로 동작한다.
이미 현재인 관계에 대한 `resources link`는 같은 관계·세대를 반환한다.
재시작·교체 또는 Cell 수명 변경 후의 명시적 재연결만 관계 세대를 하나 올린다. 이전 관계에 남은
canonical/parent/binding/source 세대와 요청 counter보다 낮은 관리 상태로
되돌아가 재연결할 수 없다.

각 프로세스는 pidfd와 `/proc/<pid>` 디렉터리 handle을 보유하고, 읽기 전후의
boot UUID/PID/start tick/UID 및 생존 여부를 확인한다. stat 원문은 4 KiB,
status는 8 KiB, PSI는 종류별 1 KiB로 제한한다. 잘림, 타입 오류, 접근 실패,
프로세스 교체는 오류이며 이전 수치를 현재 수치로 재사용하지 않는다.

CPU는 stat의 utime+stime을 실제 `SC_CLK_TCK`로 환산한다. 자식 시간이나
이미 utime에 포함된 guest 시간을 더하지 않는다. 전후 identity·관계·binding이
같고 누적 counter가 감소하지 않을 때만 차이를 계산한다. 이는 요청을 둘러싼
프로세스 관측 구간의 값이며 요청의 독점적 실행 비용이라는 뜻은 아니다.

RSS는 stat의 페이지 수와 실제 page 크기의 곱인 근사치이다. 감소는 정상이다.
서로 다른 파일의 비동기 읽기 값을 같은 순간의 정밀한 값으로 간주하지 않는다.
공유 페이지 때문에 두 RSS를 합쳐 독점 물리 메모리라고 표시하지 않는다.

PSI는 `/proc/pressure/{cpu,memory,io}`의 Linux 전체 관측만 제공한다.
`scope=linux-system`, `attribution=unattributed`이며 MAIN Node의 pressure가
아니다. 없는 값은 UNAVAILABLE로 표시한다. system CPU full은 유효한 측정으로
사용하지 않는다. cgroup 생성·이동이나 PSI trigger 등록은 수행하지 않는다.
모델을 준비하는 개발 VM은 Linux의 공식 `psi=1` 부팅 옵션으로 system PSI 관측을
활성화하도록 요청한다. 해당 kernel이 제공하지 않으면 계속 UNAVAILABLE이며,
옵션을 전달한 사실만으로 측정 가능하다고 판단하지 않는다.

## 증거와 acceptance

자원 관측 도입 단계는 런타임 공개 응답 schema 2, CLI session schema 4에 자원 자료를
별도 versioned payload로 추가했다. 현재 backend 확장은 MAIN protocol/run schema 4와
CLI v0.7/session schema 7을 사용한다. 과거 MAIN 1–3 및 CLI 1–6의 저장된 증거는 당시
source snapshot으로 계속 재검증한다.

현재 CLI source는 31개, MAIN 개별 run source는 24개이다. receipt schema 2의
`backend_execution`은 선택적 자원 관측과 별도로 실제 모델 요청 대상의 연속성을 검증한다. run schema 2–4의
event는 nullable `resource_file`을 포함하고 `resources/<UUID>.json`을 terminal
hash 목록에 포함한다. authority가 잠근 private `resource-state.json`에는 관계와
선택한 backend 디렉터리만 저장하며, 마지막 측정값은 재시작 후 복원하지 않는다.
관측 하나는 128 KiB, MAIN 응답은 256 KiB, CLI session journal과 transcript는
각 4 MiB로 제한한다. MAIN은 최대 64 events와 64 requests, backend attestation은
최대 128건이며 초과는 성공 증거가 아니다. 각 요청 receipt의 실행 시간도 두
프로세스 관측 구간 안에 들어오는지 독립 검증한다.

대화형 실행은 `tools/hosted/Start-AiosConsole.ps1 -Agent`로 시작한다.

```text
agent start
room discover
room bind
resources link
resources sample
ask What is the capital of France? Answer briefly.
resources status
resolve example.com
fetch https://example.com/
agent stop
exit
```

`-ResourceSmoke -GuestTests`는 고정한 실제 모델 시나리오와 Linux 검사를 실행한다.
저장 결과는 `py -3 tools/hosted/verify_agent.py <결과 디렉터리> --resources`로
재검증한다. 저장한 source snapshot을 기본 사용하며 `--source-root hosted/linux`를
더하면 현재 소스와 같은지도 검사한다.

1. 실제 Linux의 같은 사용자로 동작하는 MAIN과 backend에서 listener 소유권,
   peer, 시작 tick을 확인하고 명시적 link가 성공한다.
2. 같은 binding 아래 실제 모델 응답과 request UUID를 공유하는 전후 raw 자료가
   남고, 독립 verifier가 CPU 환산·차이와 RSS·PSI 파싱을 재계산한다.
3. 잘못된 peer/listener, 죽은 프로세스, 교체된 시작 tick, stale authority/binding,
   counter 역행 및 변조된 raw 자료를 거부한다. RSS 감소는 허용한다.
4. 관측 오류는 MAIN readiness·source 세대를 바꾸지 않는다. MAIN/backend 교체는
   기존 relation을 stale로 만들며 명시적 재연결 전에는 유효한 차이를 계산하지 않는다.
5. 실제 CLI 출력, 각 요청 결과, per-run hash 목록, backend attestation과 종료,
   VM 정상 종료를 독립 검증한다. fixture 통과는 실제 모델 실행 증거와 구별한다.

## 확인한 외부 근거

- [Linux proc 문서](https://docs.kernel.org/filesystems/proc.html): 열린 proc handle의
  수명, stat/status 의미, RSS의 비동기 근사치.
- [Linux PSI 문서](https://docs.kernel.org/accounting/psi.html): system/cgroup 범위,
  some/full와 평균·누적 시간, system CPU full의 제약.
- [Linux man-pages pidfd_open](https://man7.org/linux/man-pages/man2/pidfd_open.2.html):
  프로세스 handle과 종료 관측. pidfd만으로 proc 내용의 identity 검증을 대체하지 않는다.
- [Linux man-pages proc_pid_stat](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html):
  CPU tick, starttime, virtual size 및 RSS 단위.
- [Linux kernel parameters](https://docs.kernel.org/admin-guide/kernel-parameters.html):
  `psi`의 pressure 추적 활성화·비활성화 의미.

## 2026-09-07 실행 증거

`build/hosted-resources/resource-01`은 첫 실제 모델·자원·인터넷 통합 실행이며 PASS다.
Windows 호스트의 QEMU TCG, Linux 일반 사용자(uid 1000), 고정 Qwen3-0.6B Q8_0와
llamafile 0.10.5 CPU backend를 사용했다. 이 실행의 source snapshot은 이후 rollback
하한 보강 이전의 `aios_management/resources.py`를 보존한다. 당시 자료를 덮어쓰지
않았고 보강한 독립 verifier로 같은 자료를 재생해도 PASS다.

- Linux hosted 검사 302개, 188.202초, skip 없이 통과.
- 실제 warmup 1회와 사용자 질문 1회. 답변은 `The capital of France is Paris.`.
- CLI session `a9d98eb4-af6a-4fe4-b04b-8fe0eb21d3c6`, MAIN instance
  `b6a69c8d-273d-4e13-a53d-2d27ee1cf663`, hosted authority
  `741aa2bd-2cf2-4322-954c-83119e2cb784`.
- 명시적 관계 `b4632501-cf59-4407-91df-b327cf5be333`, 관계·binding 세대 모두 1.
  사용자 request `eb8e9905-4b3e-4700-9564-61143c73487f`와 관측
  `dbf73884-78cb-47af-bb78-142b65c8ea6d`가 결속됐다.
- 요청 elapsed 192,460,428,719 ns. 관측 창에서 MAIN CPU 60,000,000 ns,
  backend CPU 336,640,000,000 ns. backend는 여러 thread의 CPU 누적이므로
  wall time보다 클 수 있다. 이후 RSS는 각각 21,811,200 / 830,423,040 bytes이다.
- 기본 부팅에서 PSI 세 파일은 없었고 모두 UNAVAILABLE/pressure-missing으로
  기록됐다. 이는 압박 0 또는 관측 가능한 system PSI라는 증거가 아니다.
- DNS 실제 4개 주소, 인증서 검증 HTTPS 200/559 bytes, MAIN/backend/VM 정상 종료.
  backend와 VM exit 0, host_killed=false. 새 표본과 요청 표본 2개를 독립 재생했다.

기본 부팅의 PSI 부재를 확인한 뒤 최종 실행에서는 공식 `psi=1` 옵션으로
활성화를 요청했고, 아래와 같이 실제 세 종류의 system PSI를 읽었다.

Windows 전체 hosted 검사는 최종 guard 적용 후 `windows-guarded.log`에서
444개/74.216초, 실행 406개 통과·38개 skip·실패/오류 0, 실제 process exit 0이다.
skip은 Linux 기능이 필요한 36개와 Windows symlink 권한 제약 2개다.
이전 `windows-final.log`의 434개 결과는 그대로 보존했다.

### 관측 단계 최종 소스: resource-02 (CLI v0.4 기록)

`build/hosted-resources/resource-02`는 관측 단계의 최종 소스와 `psi=1`을 사용한 별도 실행이다.
Linux hosted 검사 **312개/159.890초/skip 없이 모두 통과**, CLI 12개 명령,
실제 warmup 1회·질문 1회, 자원 표본 2개, MAIN/backend/VM 정상 종료를 확인했다.
저장 결과를 `--resources --source-root hosted/linux`로 독립 재생해도 PASS다.
실행에 보존한 runtime source 25개는 당시 최종 소스와 일치했다. 이후 Cell 수명 확장으로
현재 runtime은 v0.7이므로 이 실행은 보존 snapshot으로 재생한다.

| 항목 | 실제 결과 |
|---|---|
| CLI session | `77d8e180-166d-49c3-bc8a-6208cd0a6cd6` |
| MAIN source / instance | `0c12d38b-967d-45b8-a265-099647b2aa8c` / `09df4270-ad82-4e9f-b928-76df6887ba80` |
| hosted authority | `3d7fe600-109f-4e40-bbab-c824a4c7a079` |
| 관계 / binding 세대 | `37a2a3f2-6586-495a-b185-a253655764bd` / 모두 1 |
| 실제 사용자 request | `0389351e-9eff-4c79-85d7-a87d1fb662db` |
| 요청 관측 UUID | `8f773a26-160b-4e00-ad45-490a1edcd1b7` |
| 실제 모델 답변 / 시간 | `The capital of France is Paris.` / 184,305,660,746 ns |
| MAIN 관측 | CPU 60,000,000 ns / 창 184,600,727,326 ns / 이후 RSS 21,331,968 bytes |
| backend 관측 | CPU 330,170,000,000 ns / 창 184,600,188,781 ns / 이후 RSS 828,047,360 bytes |
| system PSI | CPU/memory/io 모두 AVAILABLE, 요청 이후 some avg10은 9.66% / 0.00% / 0.00%; CPU full_valid=false |
| 실제 인터넷 | DNS 4개 주소 / 인증서 검증 HTTPS 200, 559 bytes |
| 종료 | MAIN STOPPED, backend exit 0, VM exit 0, host_killed=false |

`linux-serial.log`에 실제 검사·부팅·출력·종료가 있고, `session/`에는 CLI 출력과
구조화된 명령 결과가 있다. `agent/runs/<instance>/resources/`는 별도 관측 기록,
`model-backend/backend-attestations.jsonl`은 launcher가 내보낸 peer/listener proof를
보존한다. `agent-verdict.json`과 `vm-verdict.json`은 원래 실행 판정,
`current-source-replay.json`은 당시 소스·독립 검사기 hash를 기록한 후속 재생 판정이다.

이번 Linux 통합 반례에는 실제 MAIN 재시작 후 stale 관계 거부와 명시적 재연결 세대 2,
관측기만 중지한 뒤 추론 성공·readiness 유지·관측 ERROR가 포함된다. 이전 세대 관리
복원, PID/start identity 교체, CPU 감소, 잘못된 listener, 변조 후 재해시와 관측창보다
긴 receipt도 거부한다. 기존 v0.1/0.2 CLI와 v0.3 MAIN 증거 재생은 유지했다.

### 다음 범위

이 조각의 범위는 정확히 두 프로세스의 관측이다. Cell 수명과 명시적 재결속은
[Cell 가이드](aios_cell_lifecycle_guide_ko.md)의 v0.5 증거로 이어진다. 현재 backend
교체·종료의 관리와 MAIN 실행 결속은 backend 수명 가이드의 `backend-02`에서 로컬 실제
실행과 당시 소스 재검증을 통과했다(`PARTIAL`). 이 기록은 보존된 소스의 증거다.
현재 v0.7의 `backend recover`는 같은 CLI가 미리 확보한 child pidfd를 사용한 supervisor
소실 뒤 명시적 정리다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존). 별도 `RECOVERED` 증거와
명시적 MAIN 재시작·재결속은 [backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)를 따른다.
CLI 소실·재부팅 이후 복구와 필요한 worker 및 cgroup의 **명시적인 읽기 전용 관계**는 후속이다.
그 뒤에도 관측과 ownership·principal·quota
적용은 별도 acceptance가 필요하다. native K1/K2-a ABI, H1 trace 및 Linux resource
manifest는 이번 작업에서 바꾸지 않았다. QEMU TCG의 약 3분짜리 추론은 개발 검증 시간이며,
기본 CLI/인터넷은 모델을 준비하지 않고도 사용할 수 있다. 모델 없는 기본 설치 이미지는
[운영 이미지 가이드](aios_operating_image_guide_ko.md)의 `image-07`에서 반복 부팅과
설정/history 보존을 검증했다(`SUPPORTING/PARTIAL`). 현재 모델 포함 `model-image-05`도
별도 실제 모델·반복 부팅 acceptance를 통과했다. 실기기 부팅은 후속이며,
저장된 resource observation을 새 boot의 현재 관계로 복원하지 않는다.
