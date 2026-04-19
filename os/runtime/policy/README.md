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

2026-04-19 기준 이 위치는 설계 문서용 뼈대다.

현재 커널에서 이미 제공하는 연결점:

- `Kernel Room snapshot`
- health / stability summary
- memory fabric domain / window
- SLM seed plan

아직 없는 것:

- candidate registry runtime
- observer / builder runtime
- userspace promotion engine

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
