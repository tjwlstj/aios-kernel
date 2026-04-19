# AIOS User-Space Service Plane

이 디렉토리는 `AIOS`의 유저공간 코어 서비스를 정리하는 기준 위치다.

## 대상 서비스

- `aios-osd`
- `aios-agentd`
- `aios-modeld`
- `aios-memd`
- `aios-kvcached`
- `aios-compatd`

## 역할

- 커널의 read-only snapshot과 bounded UAPI를 읽는다
- 메인 AI supervisor와 하위 node tree를 관리한다
- 모델, 기억, KV-cache, bundle/component 실행을 나눠 맡는다

## 현재 상태

2026-04-19 기준 이 디렉토리는 책임 경계를 먼저 정하는 문서 위치다.
아직 실제 userspace service 구현체는 없다.

## 설계 원칙

- service 하나가 너무 많은 정책을 갖지 않는다
- 커널은 메커니즘만 제공하고, 정책은 service가 맡는다
- 메인 AI와 SLM 후보 운영은 service plane에서 관리한다
- degraded / rollback / quarantine 기준을 각 service가 설명 가능하게 유지한다

## 첫 구현 후보

가장 먼저 구현할 service는 아래 둘 중 하나가 적합하다.

1. `aios-osd`
   - boot mode, health, service lifecycle 관점에서 가장 중심이 된다
2. `aios-compatd`
   - loader / launcher / host 역할이 있어 userspace substrate 구축에 직접 연결된다
