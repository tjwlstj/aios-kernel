# AIOS User-Space Policy Plane

이 디렉토리는 메인 AI와 SLM 후보군을 운영하는
userspace 정책 계층의 기준 위치다.

## 범위

- seed SLM
- candidate registry
- observer service
- builder service
- promotion policy
- rollback / quarantine policy

## 현재 상태

2026-04-21 기준 이 위치는 policy broker의 초기 골격과 설계 문서용 뼈대다.

현재 커널에서 이미 제공하는 연결점:

- `Kernel Room snapshot`
- health / stability summary
- memory fabric domain / window
- SLM seed plan
- `SYS_SLM_HW_SNAPSHOT`의 NodeBit catalog
- `SYS_SLM_NODEBIT_LOOKUP` 단건 NodeBit 조회

아직 없는 것:

- candidate registry runtime
- observer / builder runtime
- userspace promotion engine

현재 userspace 쪽에 있는 최소 구현:

- `os/examples/nodebit_catalog.sample.json`
  QEMU boot snapshot의 runtime overlay NodeBit 형태를 흉내 낸 샘플 catalog
- `os/tools/evaluate_nodebit_policy.py`
  `node_id + action + observe/apply mode`를 받아 allow/deny, reason,
  support_state, risky gate 상태를 구조화해서 출력하는 policy broker 판정 도구

이 도구는 실제 syscall 호출자가 아니라, 이후 `aios-osd`나
policy broker가 따라야 할 판정 규칙을 고정하는 host-side smoke이다.

## 핵심 원칙

- 커널은 모델을 선택하지 않는다
- 커널은 관측과 격리, 전달 primitive만 제공한다
- 후보 평가, 승급, 강등은 userspace policy가 맡는다
- base model 직접 수정은 늦추고 adapter/distillation artifact부터 다룬다

## 최소 운영 모델

1. `seed`
2. `experimental`
3. `bounded`
4. `stable`

stable 상태의 후보만 더 안쪽 역할과 더 높은 신뢰 경로로 올린다.

## NodeBit 판정 흐름

초기 userspace policy broker는 아래 순서를 따른다.

1. `SYS_INFO_BOOTSTRAP` 또는 `SYS_SLM_HW_SNAPSHOT`으로 snapshot을 읽는다
2. 필요한 노드는 `SYS_SLM_NODEBIT_LOOKUP`으로 단건 재확인한다
3. `present`, `user_visible`, `required_capability_bits`를 확인한다
4. observe 요청은 `observe_only_bits | allow_bits`를 본다
5. apply 요청은 `allow_bits`, `SLM_NODEBIT_F_APPLY_ALLOWED`, risky gate를 본다
6. 커널에 제출하는 plan은 여전히 `SYS_SLM_PLAN_SUBMIT`의 verifier를 통과해야 한다

이 단계에서는 userspace가 직접 MMIO 주소나 커널 포인터를 만들지 않는다.
