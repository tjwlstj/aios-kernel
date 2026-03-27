# SLM 최적화 및 학습 강화

## 목적

AIOS의 SLM(Small Language Model) 하드웨어 오케스트레이터가 단순한 장치 목록 수집기를 넘어서,
부팅 중 관측한 시스템 상태와 실제 I/O 결과를 바탕으로 더 안전하고 효율적인 계획을 제안하도록 강화한다.

이번 단계의 목표는 다음과 같다.

- 장치 존재 여부를 기준으로 잘못된 계획을 초기에 차단
- 하드웨어 호환성과 건강 상태를 반영한 confidence 점수 계산
- plan 제출/거절/적용 결과를 학습 프로필에 누적
- queue depth, poll budget, DMA window를 학습 피드백으로 재조정

## 현재 구조

SLM은 부팅 시 다음 입력을 결합해 학습 프로필을 만든다.

- ACPI/PCI fabric 상태
- 부팅 성능 tier 및 메모리 selftest 결과
- 커널 health gate
- e1000, USB, storage bootstrap 준비 상태
- platform probe로 발견된 실제 장치 수

이 정보를 바탕으로 `slm_learning_profile_t`를 구성하고, 각 action마다 개별 confidence를 계산한다.

## 학습 프로필

`slm_learning_profile_t`는 다음 상태를 유지한다.

- 전역 confidence
- I/O aggressiveness bias
- 자동 조정된 queue depth / poll budget / DMA window
- action별 attempts / successes / failures / timeouts / rejections
- 마지막 latency와 status

이 프로필은 `slm_hw_snapshot_t`에 포함되어 syscall로 외부 SLM 런타임에 그대로 전달된다.

## 강화 루프

강화 루프는 다음 단계로 동작한다.

1. 부팅 관측값으로 기본 confidence와 I/O bias 계산
2. action과 target 장치 존재 여부를 기준으로 plan confidence 계산
3. confidence가 낮은 plan은 validation 단계에서 reject
4. apply 결과가 성공하면 confidence를 올리고 bias를 조금 더 공격적으로 조정
5. apply 결과가 실패하거나 timeout이면 confidence를 낮추고 bias를 보수적으로 조정
6. 조정된 bias와 성공/실패 누적으로 queue/poll/DMA 기본값을 다시 계산

즉, 같은 부팅 세션 안에서 SLM은 더 자주 성공한 I/O 시퀀스에는 조금 더 적극적인 기본값을 주고,
실패와 거절이 누적된 경로에는 더 보수적인 기본값을 준다.

## 안전 장치

학습 강화가 곧 무제한 자동 실행을 의미하지는 않는다.

- target 장치가 없으면 discovery 외 action은 reject
- risky I/O는 kernel health gate가 허용할 때만 validation
- confidence가 너무 낮으면 risk가 있는 plan은 reject
- 실제 MMIO/PCI 접근은 기존 커널 드라이버와 allowlist 경로 안에서만 수행

## 현재 한계

- 학습 상태는 현재 부팅 세션 메모리 안에서만 유지된다
- 장기 학습을 위한 persistent store는 아직 없다
- action confidence는 휴리스틱 기반이며, 실제 장치별 quirk table과 완전히 통합되진 않았다
- USB/storage는 아직 bootstrap/dump 위주라 학습에 반영되는 실제 I/O 이벤트가 제한적이다

## 다음 단계

- 학습 프로필을 persistent log 또는 storage snapshot에 저장
- action별 timeout/latency 분포를 기반으로 더 정교한 confidence 모델 도입
- device/vendor-specific quirk score를 학습 프로필에 통합
- e1000 RX, storage read, USB transfer ring 결과를 실제 reinforcement source로 연결
