## Commercial Stability Baseline

### 목표

AIOS를 상용 수준으로 끌어올리려면 기능 수보다 먼저 다음 조건이 필요하다.

1. 커널이 자기 상태를 스스로 평가할 수 있어야 한다.
2. 열화된 경로에서 위험한 자동 동작이 차단되어야 한다.
3. 부팅 테스트가 "커널이 떴다"를 넘어서 "상태 요약이 나왔다"까지 확인해야 한다.

이번 단계에서는 그 기반을 먼저 추가했다.

### 현재 도입된 안정화 기반

- kernel health registry
  - subsystem별 `unknown / ok / degraded / failed` 상태 관리
- stability summary
  - `unsafe / degraded / stable` 판정
- stability gate
  - health가 열화되면 autonomy를 safe mode로 고정
  - risky I/O SLM plan을 validation 단계에서 차단
- stricter smoke test
  - 준비 로그뿐 아니라 `[HEALTH] stability=` 출력까지 확인

핵심 파일:

- [include/kernel/health.h](/Z:/aios/aios-kernel/include/kernel/health.h)
- [kernel/health.c](/Z:/aios/aios-kernel/kernel/health.c)
- [kernel/main.c](/Z:/aios/aios-kernel/kernel/main.c)
- [runtime/slm_orchestrator.c](/Z:/aios/aios-kernel/runtime/slm_orchestrator.c)
- [runtime/autonomy.c](/Z:/aios/aios-kernel/runtime/autonomy.c)

### 현재 기준 해석

현재 QEMU 부팅 로그 기준으로 AIOS는 `degraded`가 아니라 `stable`이다.

이유:

- `e1000`가 MMIO 경로로 정상 초기화됨
- `link up`과 `TX smoke PASS`를 확인함
- USB `xHCI` capability probe와 IDE channel status probe도 정상 출력됨

다만 이건 어디까지나 현재 QEMU 기준이다.
상용 안정도 관점에서는 "QEMU에서 stable"과 "실기기에서 commercial-ready"는 다르다.

반대로 다음은 정상으로 본다.

- IDT
- time source
- boot selftest
- tensor memory manager
- scheduler
- PCI probe
- USB bootstrap
- storage bootstrap
- syscall layer
- autonomy control plane
- SLM orchestrator

### 왜 중요한가

이제 커널은 "문제가 있다"를 단순 로그가 아니라 정책 입력으로 사용한다.

예:

- network I/O가 열화되면 `e1000 tx smoke` 같은 risk 1 plan은 자동으로 reject
- autonomy는 safe mode로 강등
- system info dump와 smoke test 모두 같은 health 모델을 본다
- 반대로 I/O 경로가 정상으로 검증되면 SLM risk 1 plan도 더 이상 자동 차단되지 않는다

이 구조가 있어야 이후 드라이버가 늘어나도 상용 안정성 기준을 유지할 수 있다.

### 아직 상용 수준이 아닌 이유

아래는 아직 명확한 blocker다.

1. 실제 데이터 경로 부족
- network: TX smoke는 되지만 RX 경로와 일반 패킷 경로는 미완
- USB: capability probe는 되지만 transfer ring 없음
- storage: IDE channel probe는 되지만 read/write 경로 없음

2. 지속성 부족
- crash reason 저장
- persistent event log
- boot 실패 후 forensic path

3. 회귀 검증 부족
- 단위 테스트
- 장치별 시나리오 테스트
- Linux/Windows/QEMU 편차 검증

4. 격리와 복구 부족
- fault containment
- subsystem restart
- watchdog

### 다음 우선순위

1. `e1000` RX 경로와 일반 TX 경로
2. storage read path 또는 AHCI reset path
3. USB transfer ring 최소 골격
4. persistent health/event log
5. watchdog + fatal reason capture
