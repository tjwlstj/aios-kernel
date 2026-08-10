# AI 에이전트 운용 계약 (2026-07-15, 2026-08-10 관리축 정렬)

최종 갱신: 2026-08-11 (K1 hierarchy와 `state room/resource` 반영)

## 1. 목적

AIOS에서 "AI가 편하게 움직인다"는 것은 커널 내부를 자유 형식으로 조작한다는 뜻이 아니다.
에이전트가 현재 상태와 허용 범위를 추측하지 않고 발견하고, 제한된 행동만 제안하며,
결과와 거절 이유를 기계적으로 확인하고, 실패 시 되돌릴 수 있다는 뜻이다.

이 문서는 그 관점에서 실행 중 인터페이스의 최소 계약을 고정한다. Kernel Room의
관리 의미와 우선순위는 [관리 모델](../kernel-room/kernel_room_management_model_ko.md)과
[성숙도 작업흐름](../meta/minimal_io_and_maturity_workflow_ko.md), 검증 진화는
[검증 툴링 진화 설계](../tools/verification_tooling_evolution_design_ko.md), 리소스 정책은
[AI 리소스 관리 계획](ai_resource_management_development_plan_ko.md)이 각각 정본이다.

## 2. 에이전트가 편한 OS의 다섯 조건

1. **발견 가능성:** 명령과 관측 토픽을 먼저 열거할 수 있다.
2. **구조적 관측:** 상태가 한 줄 `key=value` 또는 버전드 레코드로 나온다.
3. **제한된 행동 공간:** target/action/delta/risk가 고정된 값과 범위로 제한된다.
4. **설명 가능한 거절:** 실패가 단순 errno에 그치지 않고 state/reason으로 남는다.
5. **검증과 복구:** before/after 결과와 rollback 여부를 다시 읽을 수 있다.

권한을 넓히는 것보다 이 다섯 조건을 먼저 완성한다.

## 3. 현재 구현 상태

| 단계 | 상태 | 현재 표면 |
|---|---|---|
| 발견 | `CURRENT` | `state list` |
| 커널 관측 | `CURRENT` | `state health/room/mem/sched/nodes/pipeline/resource/pressure/slm/autonomy/user/sec/time/version` |
| Kernel Room K1 계층 | `CURRENT` | schema 1/1024B management-only snapshot + exact boot/summary + `state room` |
| 커널 내부 리소스 ledger | `CURRENT` | schema 1 aggregate snapshot + exact boot summary + `SYS_INFO_RESOURCE=0x706` + `state resource` |
| 자율 제어 관측 | `CURRENT` | `state autonomy` schema 1 |
| 제한된 행동 제안 | `PARTIAL` | `SYS_AUTONOMY_ACTION_PROPOSE`; scheduler만 apply 지원, delta ±32 |
| commit/rollback | `PARTIAL` | `SYS_AUTONOMY_ACTION_COMMIT`, `SYS_AUTONOMY_ROLLBACK`; 상주 userspace agent는 아직 없음 |
| principal authorize | `PLANNED` | K1~K4 관리 identity/binding/attribution 증거 뒤 K5 |
| 재부팅 후 연속성 | `PLANNED` | C1 정책·관리 저널, C2 AI Flow |

현재의 반복 가능한 에이전트 인터페이스는 QEMU COM1에 연결된 host-driven shell이다.
ring3는 두 정적 bootstrap process를 PID 1→PID 2 순서로 각각 동기 실행하는 단계이며,
상주 AI runtime이나 일반 프로세스 모델로 과장하지 않는다.

## 4. `state autonomy` schema 1

응답은 정확히 한 줄이며 값 안에 공백을 넣지 않는다.

```text
[STATE] autonomy schema=1 observation_only=1 safe_mode=0 support_mem=observe-only support_sched=apply support_accel=observe-only support_infer=observe-only ...
```

필드 그룹:

- `schema`: 이 shell record의 버전. 현재 `1`.
- `observation_only`, `safe_mode`: 현재 자율 제어 모드.
- `support_mem/sched/accel/infer`: target별 선언된 지원도
  (`none`, `observe-only`, `apply`).
- `telemetry`, `proposed`, `approved`, `committed`, `rejected`, `rollbacks`:
  누적 통계.
- `queue_depth`, `event_depth`: 현재 action queue와 event log 깊이.
- `last_valid`, `last_action`, `last_target`, `last_state`, `last_reason`:
  마지막 결정 또는 거절. 이벤트가 없으면 `last_valid=0`, 나머지 이름 필드는 `none`이다.

`support_sched=apply`는 현재 모드와 health를 무시하고 즉시 적용 가능하다는 뜻이 아니다.
지원도, `observation_only/safe_mode`, `state health`를 함께 읽어야 한다. 기본값
`observation_only=1`에서는 apply-capable target도 제안 단계에서 차단된다.

## 5. 안전 경계

- `state autonomy` 계약 자체는 read-only 관측면이며 action, syscall 번호, enum 값,
  구조체 ABI를 늘리지 않는다. 별도 AI Resource Ledger는 커널 내부 versioned snapshot만
  추가했고 userspace ABI는 아직 열지 않았다.
- memory/accel/infer target은 계속 `observe-only`다.
- scheduler action만 구현돼 있으며 delta는 `-32..32`로 제한된다.
- raw pointer, register, MMIO 주소를 모델 출력으로 받지 않는다.
- 현재 `SYS_AUTONOMY_MODE_SET`에는 K5 principal/ownership 기반 authorize가 아직 없다.
  따라서 이 단계에서 shell action 명령이나 편의용 apply 우회를 추가하지 않는다.
- Kernel Room gate는 현재 분류 메타데이터이며 per-call authorize로 과장하지 않는다.

## 6. 다음 확장 순서

프로젝트 전체 우선순위는 Kernel Room 관리 K축을 따른다. 에이전트 표면은 아래 관리
증거가 생기는 순서대로 연결한다.

1. K1은 Cell 1 + bound Node 1 + parent-bound NodeBit 2의 management-only
   read-only hierarchy v0를 한 vertical proof로 완성했다. 이 bounded 조각만 `CURRENT`다.
2. K2에서 canonical Node의 source binding과 generation/reconciliation을 확장한다.
3. K3에서 선택한 legacy NodeBit를 namespace adapter로 read-only projection한다.
4. K4에서 resource/pressure를 canonical Cell/Node에 귀속하되
   `observation_only=1`을 유지한다.
5. K5에서 principal, target ownership, stale-generation 거부를 검증한 뒤에만 Kernel
   Room authorize를 mode-set/commit 앞에 강제한다.
6. C1/C2에서 정책·관리 저널과 AI Flow continuation으로 재부팅 경계 연속성을 확보한다.

버전드 `[EVT]` 파일럿, host event parser, read-only autonomy/resource snapshot은 이
순서를 지원하는 검증·관측 작업이다. 병행할 수 있지만 Kernel Room hierarchy
성숙도를 대신하지 않는다.

새 행동을 추가할 때는 항상 `observe → propose → authorize → apply → verify → commit/rollback`
순서를 유지하고, unsupported target은 명시적으로 거부한다.

## 7. 검증 계약

- host unit test는 `state autonomy`의 schema, 기본 mode, support matrix, last-event 없음과
  중복 key 반례를 검사한다.
- strict shell lane은 실제 QEMU에서 이 토픽을 질의하고 같은 response record에서 판정한다.
- 커널 변경은 3개 boot profile, shell clean exit, cppcheck를 통과해야 한다.
- Windows와 Python 정상 verdict는 같은 IDE evidence grammar를 사용해야 한다.
