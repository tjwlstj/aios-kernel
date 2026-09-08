# AIOS hosted Cell 수명과 명시적 재결속

이 문서의 실행 ID별 `build/` 자료는 로컬 비추적 증거이며 Git checkout에 포함되지 않는다.
과거 결과의 재검증 명령은 해당 원본을 보유한 환경용이며 새 실행의 결과 경로와 구분한다.

> 방향: `DIRECT` — Cell 1 / MAIN Node 101의 관리 수명을 실행 중인 source에 연결한다.
> 성숙도: `PARTIAL` — 로컬 실제 Linux·모델·인터넷·정상 종료 검증 완료.
> 전체 Cell lifecycle, H2/H3 및 resource apply 완료를 뜻하지 않는다.
> 작성: 2026-09-07

이 문서의 `cell-01` PASS는 보존된 CLI v0.5/session schema 5/source 25개와
MAIN run schema 3/source 18개의 실행 증거다. 현재 CLI v0.7/schema 7/source 31개,
MAIN protocol/run 4/source 24개는 receipt 2의 backend 관리·실행 결속을 유지한다.
[backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)의 v0.6 `backend-02`는 로컬 Linux·실제 모델·
교체 후 요청 거부·명시적 복구·자원 관측·인터넷·정상 종료와 당시 소스 재검증을 통과했다.
`backend-02`는 보존된 소스의 증거이며 이후 변경된 현재 소스의 실행 검증을 대신하지 않는다.
성숙도는 `PARTIAL`을 유지하며 이 backend 시나리오가 Cell 시나리오 전체의 재실행을 뜻하지 않는다.

## 계약과 범위

AIOS hosted authority가 Cell의 active 상태와 세대를 소유한다. Linux PID, cgroup,
backend의 프로세스 상태를 Cell ID나 Cell generation으로 사용하지 않는다.
현재 조각은 이미 선언된 Cell 1만 다루며 생성·삭제·이동·격리·소유권 기능을 추가하지 않는다.
관리 정본은 [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md),
기존 결속·관측 계약은 [MAIN](aios_agent_binding_guide_ko.md)과
[자원 관측](aios_resource_observation_guide_ko.md) 가이드를 따른다.

`cell status`는 복사된 관리 상태를 보여 준다. `cell deactivate`는 Cell을 비활성화하고
parent와 canonical Node generation을 각각 하나 올린다. 기존 binding record는 남기되
trust, discovery, binding confirmation을 폐기한다. MAIN과 backend는 계속 살아 있다.
새 `ask`는 `orphan`으로 거부하며 실제 모델 요청 receipt나 완료 횟수를 만들지 않는다.
기존 자원 관계는 현재 결속 조건을 만족하지 못하므로 관측을 거부한다.

`cell activate`는 두 관리 generation을 다시 하나 올린다. 재활성화 직후에도 기존 결속은
stale이다. 사용자가 `room discover`, `room reconcile`, `resources link`를 순서대로
실행해야 현재 MAIN과 backend의 관계가 다시 유효해진다. 같은 active 상태를 반복해서
요청하면 세대와 snapshot이 바뀌지 않는다. 세대 상한에 도달한 전이는 `overflow`로
거부하고 결속 신뢰를 폐기한다.

| 단계 | Cell/Node generation | binding generation | resource relation generation |
|---|---:|---:|---:|
| 처음 명시적으로 결속 | 1 | 1 | 1 |
| 비활성화 | 2 | 1 (stale) | 1 (stale) |
| 재활성화 | 3 | 1 (stale) | 1 (stale) |
| discover/reconcile/link | 3 | 2 | 2 |

Cell 상태 변경은 MAIN source instance/generation, readiness, 완료 횟수를 바꾸지 않는다.
현재 MAIN은 명령 처리 전에 별도로 backend 생존을 확인하므로, backend 종료·교체가
발견되면 Cell 전이와 구분된 source 무효화가 먼저 기록될 수 있다.
비활성 Cell 안에서 같은 MAIN instance가 종료하면 검증된 source의 종료 상태는 복사하되
관리 신뢰를 부여하지 않는다. 새 MAIN instance는 명시적 discovery 전에 도입하지 않는다.
Cell active/generation은 private management state에 저장하며 MAIN 재시작에도 유지한다.
상태는 동일 사용자의 private 디렉터리와 단일 daemon lock 안에서만 신뢰한다. 사용자에 의한
전체 증거 디렉터리 재작성에 대한 인증 저장소·cross-reboot 보안은 별도 범위다.

## 사용과 버전

Windows 진입점 `tools/hosted/Start-AiosConsole.ps1 -Agent`에서 다음 명령을 사용한다.

```text
agent start
room discover
room bind
resources link
cell status
cell deactivate
ask Say hello.
cell activate
room discover
room reconcile
resources link
ask What is the capital of France? Answer in one short sentence.
```

Cell 도입 당시 CLI v0.5 / session schema 5는 source 25개를 기록했다. MAIN protocol 및 run/event/result는
schema 3, source 18개였다. Cell/Node/source/resource persisted record의 기존 schema 1과
native K1 1024B/K2-a 256B/H1 ABI는 유지한다. 현재 독립 검사기는 과거 CLI 1–6 및 MAIN run 1–3을
당시 source snapshot으로 계속 재생한다. 실행 중인 구버전 daemon과 새 protocol을 혼용하지
않으며, 이번 개발 guest는 매번 새 private state에서 시작한다.

## 검증 계약

`-CellSmoke -GuestTests`는 실제 Linux 사용자·모델과 고정 명령 순서로 검증한다.
독립 `verify_agent.py --cells`는 CLI 출력, per-run event, source hash, 세대 변화,
자원 관측, 모델 요청·provenance, DNS/인증서 검증 HTTPS, MAIN/backend/VM 정상 종료를 묶는다.
Cell 전이 없이 parent가 바뀌거나 전이 중 source/binding 기록이 바뀌면 실패한다.
비활성·재활성 직후 요청 거부는 fixture 성공과 별개로 실제 실행에서도 확인한다.

Linux 통합 반례는 fixture 모델임을 명시하고 실제 MAIN 프로세스와 IPC를 사용한다.
비활성 Cell에서 MAIN을 종료·재시작한 뒤에도 Cell 상태와 세대가 보존되는지 확인한다.
이 fixture를 실제 모델이나 native kernel 실행의 PASS로 승격하지 않는다.

## 외부 근거와 다음 범위

2026-09-07에 Linux 공식 [proc 문서](https://docs.kernel.org/filesystems/proc.html)와
[cgroup v2 문서](https://docs.kernel.org/admin-guide/cgroup-v2.html)를 재확인했다.
proc의 PID·시작 시각·프로세스 상태는 실행 source의 관측값이며 AIOS Cell의 관리 수명은
이 프로젝트의 독립 계약이다. 이 단계는 cgroup 생성, 프로세스 이동, freeze/kill, quota를
호출하지 않는다. backend 자체의 교체·종료 세대와 MAIN 실행 연결은 별도 backend 수명
가이드의 `backend-02`에서 로컬 실제 실행과 당시 소스 재검증을 통과했다(`PARTIAL`).
현재 `backend recover`는 동일 CLI가 생존 중 확보한 child pidfd를 사용한 supervisor 소실 뒤
명시적 정리다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존). 별도 `RECOVERED` 증거와 명시적
MAIN 재시작·재결속은 [backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)를 따른다.
CLI 소실·재부팅 이후 복구, 모델 이미지의 범용 설치·실기기 부팅,
다중 Cell 및 principal/ownership/authorize는 후속 범위다.

모델 없는 기본 운영 이미지의 반복 부팅·설정/history 보존은 별도
[운영 이미지 가이드](aios_operating_image_guide_ko.md)의 `image-07`에서 검증했다
(`SUPPORTING/PARTIAL`). 부팅 간 canonical Cell 상태를 복원하거나 이 문서의 실제
모델·Cell 관리 전이를 기본 이미지에서 재실행한 결과는 아니다.

## 실행 증거

Windows 최종 host suite는 486개 중 446개 통과, Linux 전용 및 symlink 40개 skip,
실패 0개, 78.524초, 실제 종료 코드 0이다. `build/hosted-cells/windows-final.log`의
SHA-256은 `32c6553955b22012ea239a4aa4f8bed6ed2206775b06fa655177dfa1fc9c152d`이다.
이 검사는 실제 Linux 프로세스나 실제 모델 실행 증거를 대신하지 않는다.

새 `cell-01`의 Linux suite는 354개 모두 통과, skip 0개, 239.480초다. 같은 MAIN의
Cell 전이 및 비활성 MAIN 재시작을 포함한다. 이어 실제 Qwen3-0.6B-Q8_0와 pinned
llamafile CPU backend로 고정한 CLI 26개 명령을 실행했다. 비활성 요청 `orphan`,
활성화 직후 요청 `stale`, discovery 없는 reconcile `not-discovered`, 두 시점의
`resource-relation-stale`를 확인했다. 이 구간의 MAIN instance, source generation 1,
readiness와 완료 횟수는 변하지 않았다.

| 증거 | cell-01 결과 |
|---|---|
| CLI session | `75282574-5549-4195-be99-c9a1b169e540` / schema 5 / source 25개 |
| MAIN instance | `ee39af23-d1b8-4108-ae1c-7b18c329bd11` / run schema 3 / source 18개 |
| hosted authority | `f30a739b-87a8-4d9c-8d4e-fda9566dface` |
| Cell·Node 세대 | 1 → 2 → 3; 같은 상태 재요청은 snapshot 불변 |
| binding·자원 관계 세대 | 각각 1 → 2; 관계 ID `9602fb51-5f9d-4018-8d7d-02ec9c1faac5` 유지 |
| 실제 모델 실행 | warmup 1회 + 재결속 후 사용자 질문 1회; 답변 `The capital of France is Paris.` |
| 사용자 요청 | `7a57ada6-6870-4a0f-bcb5-2358e1355be5`; 184,766,870,128 ns |
| 요청 관측 | `19a9b8c2-e3ec-4ee5-8d50-4442fe612b0e`; MAIN CPU 90,000,000 ns / backend CPU 335,260,000,000 ns |
| 요청 이후 RSS 근사치 | MAIN 21,921,792 bytes / backend 830,713,856 bytes; 합산 ownership 주장 없음 |
| 인터넷 | 실제 DNS 4개 주소, 인증서 검증 HTTPS 200 / 559 bytes |
| 종료 | MAIN STOPPED, backend exit 0, VM exit 0, host_killed=false, shutdown_observed=true |

`build/hosted-cells/cell-01/agent-verdict.json`과 `vm-verdict.json`은 최초 실행부터 PASS다.
`linux-serial.log`, `session/`, `agent/runs/`, `model-backend/`에 원자료를 보존했다.
당시 v0.5 runtime source를 직접 대조한 후속 독립 재생도 PASS이며, 별도
`current-source-replay.json`에 일곱 검사기 hash와 원래 verdict hash를 기록했다.
이 파일명은 당시 대조 시점을 뜻하며 현재 v0.7 소스와 일치한다는 주장이 아니다.
마지막 재생은 필요한 stale 관계 거부를 단순 RPC 오류로 바꾼 두 반례도 거부했다.
관측 오류를 Cell 무효화 증거로 대신할 수 없다.

MAIN/backend는 재결속 전후 동일 process identity와 backend descriptor를 유지했고,
두 번의 관측 중 마지막은 실제 모델 요청 receipt에 연결됐다. Cell에서 거부한 질문은
receipt를 만들지 않았다. 실제 fixture HTTP 요청 로그와 프로세스 생존·종료를 검사한
Linux 통합 증거와 실제 모델 실행 증거를 각각 보존한다.
이전 resource-02 PASS는 관측 단계의 역사 증거로 남는다.
