# 하드웨어 코어 기반 + SLM 최적화 메모

## 이번에 들어간 핵심

- `kernel/acpi.c`
  - Multiboot2 ACPI tag 우선 파싱
  - tag가 없으면 BIOS low memory에서 RSDP fallback scan
  - RSDP/RSDT/XSDT/MCFG/MADT/FADT 최소 검증
- `drivers/pci_core.c`
  - PCI legacy config access 공통화
  - ACPI MCFG가 있으면 ECAM 사용 가능 여부 판단
  - capability list, BAR, IRQ, MSI/MSI-X, PCIe link 상태 공통 파싱
- `drivers/platform_probe.c`
  - 개별 드라이버가 아닌 PCI 코어를 통해 장치 inventory 생성
  - BAR/IRQ/MSI/MSI-X/64-bit BAR 정보를 장치 메타데이터로 보존
- `runtime/slm_orchestrator.c`
  - ACPI/PCI fabric profile 생성
  - compatibility score 계산
  - `core-audit` plan 자동 시드
  - I/O profile이 fabric 상태를 반영해 queue/poll/DMA 힌트를 조정

## 현재 의미

이제 AIOS는 단순히 `장치가 보인다` 수준이 아니라 아래를 같이 판단합니다.

- 펌웨어가 ACPI를 얼마나 제대로 노출하는지
- PCI fabric이 legacy config만 되는지, ECAM까지 가능한지
- PCIe/MSI/MSI-X 기반 장치가 얼마나 있는지
- 그 결과를 기준으로 SLM이 얼마나 공격적으로 I/O plan을 잡아도 되는지

즉, SLM이 드라이버를 추천할 때 `장치 종류`뿐 아니라 `버스/펌웨어 품질`도 같이 보게 됐습니다.

## 현재 부팅 검증 기준

Windows QEMU smoke test 기준으로 다음을 확인합니다.

- ACPI RSDP 발견
- PCI core 초기화
- peripheral probe 완료
- health `stable`
- `AIOS Kernel Ready`

## 다음 우선순위

1. ACPI MADT를 읽어 IRQ/APIC topology를 공통 계층으로 올리기
2. PCI bridge traversal과 bus topology graph 추가
3. DMA allocator를 공통 API로 분리
4. SLM이 fabric score를 바탕으로 `driver template` 우선순위를 자동 조정
5. 이후 `AHCI`, `xHCI transfer ring`, `e1000 RX` 같은 실제 I/O 경로 확장
