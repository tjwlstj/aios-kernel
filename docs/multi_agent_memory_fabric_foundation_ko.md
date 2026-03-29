# 멀티 AI 에이전트용 Memory Fabric 기초안

## 왜 이 기반이 필요한가

현재 AIOS는 다음 강점을 이미 갖고 있다.

- 부팅 가능한 x86_64 베어메탈 커널
- `Tensor MM` 기반의 AI 친화 메모리 풀
- `AI Scheduler`와 `SLM Orchestrator`
- ACPI / PCI / 기본 I/O probe
- 유저 공간 OS 계층과 메인 AI / 하위 노드 설계 문서

하지만 멀티 AI 에이전트를 병렬로 실제 기동하려면, 단순한 텐서 할당기만으로는 부족하다.  
메인 AI, 하위 worker, memory distiller, device node가 같은 텐서와 같은 KV 영역을 서로 다른 정책으로 함께 접근해야 하기 때문이다.

즉 필요한 것은 다음이다.

- 동일 텐서를 여러 에이전트가 공유할 수 있는 커널 측 추상화
- 복사보다 `공유 창(window)`을 우선하는 zero-copy 경로
- CPU-only PC부터 PCIe 중심 시스템, 장차 NUMA / CXL 계열까지 확장 가능한 호환성 모델
- 드라이버/런타임/유저 공간이 모두 읽을 수 있는 공통 메모리 토폴로지 힌트

이번 기초안은 이 요구를 위해 `Tensor MM` 위에 `Memory Fabric` 층을 추가하는 방향이다.

## 이번에 추가한 기반

코드 기준으로 다음 축을 새로 추가했다.

- [include/mm/memory_fabric.h](Z:\aios\aios-kernel\include\mm\memory_fabric.h)
- [mm/memory_fabric.c](Z:\aios\aios-kernel\mm\memory_fabric.c)

보조적으로 함께 넣은 것:

- [include/mm/tensor_mm.h](Z:\aios\aios-kernel\include\mm\tensor_mm.h)
- [mm/tensor_mm.c](Z:\aios\aios-kernel\mm\tensor_mm.c)
  `tensor_info()` 추가
- [include/runtime/slm_orchestrator.h](Z:\aios\aios-kernel\include\runtime\slm_orchestrator.h)
- [runtime/slm_orchestrator.c](Z:\aios\aios-kernel\runtime\slm_orchestrator.c)
  `slm_hw_snapshot_t`에 `memory_fabric` 프로필 연결

## 핵심 아이디어

### 1. Tensor MM는 그대로 둔다

기존 `Tensor MM`는 여전히 실제 텐서 물리/가상 할당의 기초다.

- model
- inference
- DMA
- KV-cache

이건 유지한다.  
즉 `Memory Fabric`는 allocator를 대체하는 것이 아니라, 그 위에서 **누가 어떤 텐서를 어떤 정책으로 공유하는지**를 다루는 계층이다.

### 2. 에이전트는 Memory Domain으로 본다

멀티 AI 에이전트를 커널 입장에서는 우선 `Memory Domain`으로 단순화한다.

- `main`
- `worker`
- `memory`
- `device`

각 도메인은 다음 속성을 가진다.

- priority
- realtime 여부
- zero-copy 선호 여부
- local budget
- inflight budget

즉 프로세스보다 먼저, **메모리 접근과 공유 정책 단위**를 만든 것이다.

### 3. 텐서 공유는 Shared Window로 본다

같은 텐서를 여러 노드가 쓰는 경우, 바로 복사하지 않고 `shared window`로 표현한다.

공유 창은 다음을 추적한다.

- owner domain
- reader mask
- writer mask
- access mode
- size
- DMA 가능 여부
- pin 여부
- logical map count

이렇게 하면 메인 AI와 여러 worker가 같은 텐서를 읽거나, 특정 노드만 쓰기를 허용하는 정책을 커널 레벨에서 붙일 수 있다.

## 메모리 다중접근 모델

이번 설계에서 말하는 `메모리주소 다중접근`은 아직 완전한 하드웨어 SVA(Shared Virtual Addressing)가 아니다.  
현재 단계에서는 다음 순서로 간다.

### 단계 0. 논리적 다중접근

현재 구현된 `Memory Fabric`는 같은 `tensor_id`를 여러 domain이 공유하는 **논리적 다중접근 모델**이다.

- 하나의 backing tensor
- 여러 domain mask
- zero-copy 우선
- DMA staging 필요 시 별도 region 사용

즉, 현재는 "같은 메모리 객체를 여러 주체가 복사 없이 가리킬 수 있게 하는 기초"다.

### 단계 1. user/kernel shared mapping

다음 단계에서는 ring3 도입 후 다음이 필요하다.

- user-space agent runtime에 shared tensor window export
- 읽기 전용 weight window
- KV / activation shared read-mostly window
- worker별 private scratch + shared hotset

이 단계부터 진짜 주소 공간 aliasing이 커널-유저 공간 사이에 생긴다.

### 단계 2. device shared addressing

하드웨어가 허용하는 경우에만 다음을 붙인다.

- IOMMU
- PASID
- PRI
- ATS
- SVA

하지만 이건 호환성 문제가 크기 때문에 기본 전제로 두지 않는다.  
기본 경로는 여전히 `pinned DMA staging + bounce fallback` 이어야 한다.

## 호환성 중심 설계 원칙

이번 기초안은 처음부터 "최신 기능이 있는 시스템"을 기준으로 설계하지 않았다.  
호환성을 위해 다음 4단계 모델을 둔다.

### Level 0. Uniform fallback

가장 넓은 호환성 경로다.

- 단일 메모리 관점
- ACPI 힌트 부족
- NUMA/HMAT 없음
- IOMMU/SVA 없음
- device DMA는 pinned/staging 기반

이 단계는 QEMU와 일반 PC에서 반드시 살아야 한다.

### Level 1. Segmented PCIe-aware

현재 AIOS가 가장 먼저 잘할 수 있는 경로다.

- ACPI + PCI core
- ECAM/MCFG가 있으면 더 좋음
- PCIe cap, MSI/MSI-X, BAR 정보 활용
- device-local staging window 크기 조절

즉 지금 저장소가 이미 갖고 있는 강점을 활용하는 단계다.

### Level 2. NUMA-hinted

향후 필요한 단계다.

- ACPI SRAT
- SLIT
- HMAT

이 단계부터는 메인 AI hotset을 CPU-local node에 붙이고, memory journal이나 cold prefix는 더 먼 memory node에 내리는 식의 정책이 가능하다.

### Level 3. Fabric-expandable

장기 방향이다.

- CXL memory expander
- CXL memory pooling
- CXL memory sharing
- CDAT / HMAT류 힌트

이 단계는 현재 AIOS에서 아직 구현 대상이 아니지만, 구조적으로 미리 막히지 않게 만드는 것이 중요하다.

## 현재 구현한 Memory Fabric의 역할

이번 코드의 역할은 다음 3가지로 제한했다.

### 1. 토폴로지/호환성 프로필 생성

커널은 다음 정보를 묶어서 `memory_fabric_profile_t`를 만든다.

- ACPI readiness
- ECAM availability
- invariant TSC
- PCIe presence
- compatibility score
- locality score
- recommended agent slots
- recommended hotset / staging / zero-copy window 크기

즉 OS는 이걸 보고 "지금 이 머신에서 몇 개 worker를 적극적으로 돌릴지"를 초기에 판단할 수 있다.

### 2. 기본 domain seed

부팅 시 다음 도메인을 기본 seed 한다.

- `main`
- `memory`
- `device`
- 일부 `worker`

이건 실제 유저 공간 프로세스가 아니라, **커널이 추천하는 병렬 실행 슬롯 구조**다.

### 3. shared tensor window 관리

`tensor_id`를 기준으로 reader/writer mask와 access mode를 가진 공유 창을 만들 수 있게 했다.

이건 아직 유저 공간 export는 없지만, 이후:

- worker간 shared KV
- read-only model weights
- device staging tensors
- memory journal buffers

같은 경로를 붙이는 기초가 된다.

## 왜 이 구조가 오버헤드를 줄이는가

멀티 에이전트에서 오버헤드는 주로 여기서 나온다.

- 텐서 복사
- KV 복제
- agent별 scratch 재할당
- PCIe 왕복 DMA staging 남용
- cache hotset 오염

이번 구조는 이를 다음처럼 줄인다.

- 기본은 복사보다 `shared window`
- write가 필요한 경우에만 writer mask로 분리
- device 쪽은 별도 `device domain`과 staging budget 사용
- hardware quality가 낮으면 conservative hotset / staging 정책으로 자동 축소

즉 커널에서 먼저 오버헤드 상한을 잡아두는 방식이다.

## 실제 다음 단계

이번 기초안 다음으로 바로 이어져야 할 작업은 이 순서가 적절하다.

1. `ring3 + shared tensor window export`
2. `agent-tree-aware scheduler`가 `memory domain`을 소비하게 만들기
3. `Tensor MM`에 hot/warm/cold tier metadata 추가
4. `KV tier manager`와 `Memory Fabric` 연결
5. `IOMMU / ATS / PASID`는 가능 하드웨어에서만 선택적으로 연결

즉 지금은 아직 "정책층"과 "공유층"을 만든 단계이고, 다음은 "진짜 주소 공간"과 "런타임"에 연결하는 단계다.

## 외부 기준과의 연결

이번 방향은 다음 공식 자료와 맞닿아 있다.

- Intel SDM은 IA-32 / Intel 64의 프로그래밍 환경과 메모리 아키텍처를 OS가 정확히 이해해야 함을 전제로 한다.  
  출처: [Intel 64 and IA-32 Architectures Software Developer's Manual Volume 1](https://www.intel.com/content/www/us/en/content-details/825744/intel-64-and-ia-32-architectures-software-developer-s-manual-volume-1-basic-architecture.html)

- ACPI 6.5는 System Address Map과 NUMA(HMAT/SRAT/SLIT 포함) 정보를 OSPM이 읽는 구조를 정의한다.  
  출처: [ACPI Specification 6.5](https://uefi.org/specs/ACPI/6.5/index.html), [ACPI 6.5 PDF](https://uefi.org/sites/default/files/resources/ACPI_Spec_6_5_Aug29.pdf)

- CXL 공식 자료는 memory pooling / sharing / coherency가 AI 워크로드에서 특히 중요하다고 설명한다.  
  출처: [Boosting AI Performance with CXL](https://computeexpresslink.org/blog/boosting-ai-performance-with-cxl-3818/)

이 자료들을 AIOS에 바로 그대로 가져오는 것은 아니다.  
다만 위 자료들을 바탕으로, **호환성은 fallback-first로 유지하면서도 향후 NUMA/CXL까지 막히지 않는 구조를 미리 준비하는 것이 안전하다**고 판단했다. 이 판단은 위 자료들에 대한 설계적 추론이다.

## 한 줄 결론

이번 `Memory Fabric` 기초안은 `Tensor MM`를 대체하지 않는다.  
대신 멀티 AI 에이전트 병렬 실행을 위해, **같은 텐서를 여러 주체가 낮은 오버헤드로 공유하고, 하드웨어 수준이 달라도 같은 정책 계층으로 운영할 수 있게 만드는 첫 커널 기반**이다.
