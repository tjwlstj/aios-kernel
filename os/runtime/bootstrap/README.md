# AIOS User-Space Bootstrap Lane

이 디렉토리는 `AIOS`가 커널에서 유저공간 OS로 넘어가기 위한
가장 작은 bootstrap 조각을 모아두는 기준 위치다.

## 범위

- ring3 handoff
- static ELF loader
- `aios-init`
- 첫 userspace serial log
- 최소 userspace safety boundary와 연결되는 request 형식

## 현재 상태

2026-04-19 기준 이 디렉토리는 구현체가 아니라 기준 위치다.

이미 커널 쪽에 있는 기반:

- `kernel/user_mode.c`
  - user selector / TSS scaffold
- `runtime/ai_syscall.c`
  - userspace가 eventually 사용할 syscall surface

아직 없는 것:

- 실제 `iretq` handoff
- static ELF loader
- 첫 `aios-init` 바이너리

## 첫 구현 순서

1. userspace entry stub
2. ELF loader
3. `aios-init`
4. invalid pointer safety path

## 완료 기준

- userspace-enter marker가 남는다
- `aios-init`가 serial 로그를 남긴다
- 잘못된 userspace request는 panic 대신 에러로 거부된다
