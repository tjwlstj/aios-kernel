# AIOS 유저스페이스 부팅과 하드웨어 관측 가이드

> 문서 역할: H2-a 작업 준비서·운영 가이드
>
> 권위 범위: Linux-hosted AIOS 초기화, 읽기 전용 하드웨어 inventory,
> 실행별 artifact와 외부 검증의 첫 구현 계약
>
> 상위 정본: [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md),
> [Linux-hosted substrate 정책](linux_hosted_substrate_and_resource_policy_ko.md),
> [도메인 맵](../../PROJECT.md),
> [검증 도구 진화 설계](../tools/verification_tooling_evolution_design_ko.md)
>
> 구현 경계: H2-a bootstrap은 `PARTIAL`이며 실제 Linux 개발 VM에서 로컬 검증했다.
> 별도 MAIN의 bounded binding/reconcile은 `PARTIAL`이며 전체 H2/H3 acceptance는 `PLANNED`.
> 실행 결과와 재검증 명령은 §8에 기록한다. 원격 exact-SHA acceptance와
> Linux primary exact reference 지원은 아직 확인하지 않았다.
>
> 문서 수명주기: 활성
>
> 공식 문서 검토·최종 갱신: 2026-09-03

후속 사용자 환경은 [CLI·인터넷](aios_cli_internet_guide_ko.md), [MAIN 결속](aios_agent_binding_guide_ko.md),
[자원 관측](aios_resource_observation_guide_ko.md), [Cell 수명](aios_cell_lifecycle_guide_ko.md) 가이드를 따른다.
현재 CLI v0.7/session schema 7/source 31개와 MAIN protocol/run 4/source 24개는
v0.6에서 도입한 `backend status/start/stop/restart`와 receipt schema 2의 실제 요청 대상 결속을 유지한다.
[backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)의 확장은 `backend-02`에서 로컬 Linux·
실제 모델·교체 후 요청 거부·명시적 복구·자원 관측·인터넷·정상 종료와 당시 소스 재검증을
통과했으며 `PARTIAL`을 유지한다. 이 문서 §8의 bootstrap 및 후속 v0.5 cell-01
증거는 각각 당시 보존 소스의 검증으로 남는다(운영 mirror 갱신: 2026-09-08).
`backend-02`도 보존된 소스의 증거이며 이후 변경된 현재 소스의 실행 검증을 대신하지 않는다.
현재 `backend recover`는 동일 CLI의 미리 확보한 child pidfd를 사용한 supervisor 소실 뒤
명시적 정리다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존). `RECOVERED`의 별도 증거와 MAIN의
명시적 재시작·재결속 계약은 위 backend 수명 가이드를 따른다.

별도 [운영 이미지 가이드](aios_operating_image_guide_ko.md)는 source 35개의 기본 설치
이미지를 다룬다. `image-07`에서 같은 디스크로 온라인 두 번·오프라인 한 번의 cold boot,
UID 1000 CLI, 설정/history 보존과 정상 종료를 검증했다(`SUPPORTING/PARTIAL`).
`Start-AiosImage.cmd`는 검증 원본에서 만든 사용자 디스크를 실행한다. 이 모델 없는
기본 이미지와 본 문서 §8의 초기 개발 VM 관측, 별도 실제 모델 증거를 구분한다.

현재 v0.7/session 7/source 35개의 모델 이미지는 `model-image-05`에서 online·offline
두 부팅 45명령, 실제 질문·재결속·인터넷·정상 종료와 독립 재생을 통과했다.
전용 0.7 실행기 (`build/hosted-image-v07/Start-Aios-0.7.cmd`)의 별도 사용자 사본도 세 명령과
정상 종료를 실제 검증했다. 기존 v0.6 디스크·포인터와 bootstrap 증거는 보존하며,
별도 장애 복사본 02도 같은 worker의 recover·즉시 exit·정상 종료를 실제 검증했으며,
첫 복사본 01의 원본 FAIL은 보존한다.
운영 이미지 가이드 §9.7이 이 `SUPPORTING/PARTIAL` 결과의 정본이다.
새 기본 사용자 사본 선택·되돌리기는 §9.8의 Windows 로컬 host 기능으로 `SUPPORTING/PARTIAL`이다.
0.7·0.6의 실제 기본 부팅·하드웨어 조회·인터넷·정상 종료와 최종 0.7 재선택을 검증했고
기존 boot/hardware 증거를 변경하지 않는다.

후속 [CLI·인터넷 가이드](aios_cli_internet_guide_ko.md)는 고유 대화형 콘솔과
사용자 요청 DNS·HTTP(S) I/O를 다룬다. 이 문서의 boot-inventory 관측 전용 계약은
그대로이며, 전체 콘솔 세션의 네트워크 I/O까지 관측 전용이라고 부르지 않는다.

## 1. 독자적인 AIOS와 Linux의 역할

AIOS의 운영 핵은 `Room -> Cell -> Node -> NodeBit`의 정체성, 관계, 상태,
세대와 검증 규칙을 소유하는 독자적인 관리 커널·런타임이다. Linux를 사용해도
이 의미를 Linux PID, cgroup, 디바이스 경로 또는 배포판 설정으로 바꾸지 않는다.

현재 선택한 Linux-hosted 실행에서는 하드웨어 특권 커널이 Linux다. Linux가
드라이버, 인터럽트, 실제 메모리 관리와 장치 I/O를 수행하고, AIOS는 공개된
사용자 공간 인터페이스를 통해 그 결과를 관측한다. 이 가이드의 실행 파일이
Linux를 대신해 CPU 특권 모드에서 부팅하거나 Linux 드라이버를 직접 실행한다고
설명하지 않는다.

저장소의 `kernel/`은 AIOS가 직접 작성한 별도 x86_64 네이티브 커널이다.
해당 커널의 부팅·실행·관리 계약 증거는 그대로 유지하며, 이번 hosted 조각은
그 public ABI와 기존 부트 marker를 바꾸지 않는다. Linux 배포판 fork나 Linux
커널 module을 만드는 작업도 아니다. Linux `.ko` 파일을 네이티브 AIOS 커널에
직접 적재하는 호환 기능은 포함하지 않는다.

Linux 공식 문서는 사용자 공간 syscall 인터페이스와 내부 드라이버 인터페이스를
구분한다. 내부 드라이버 API·바이너리 ABI는 안정된 공통 인터페이스가 아니며
커널 구성과 내부 자료구조에 의존한다. 따라서 Linux 드라이버를 네이티브 AIOS에서
실행하려면 별도의 이식 또는 호환 실행 환경이 필요하다는 것이 이 문서의 기술적
판단이다. 현재는 Linux userspace 경계를 이용한다.
[공식 근거: Linux Kernel Driver Interface](https://docs.kernel.org/process/stable-api-nonsense.html)

```text
AIOS 독자 관리 계약                  기존 AIOS 네이티브 커널
Room -> Cell -> Node -> NodeBit       kernel/의 x86_64 reference/proof 경로
          |                          자체 부팅·ABI·검증을 별도로 유지
Linux-hosted AIOS runtime
          |
Linux userspace 인터페이스
          |
Linux 하드웨어 커널과 드라이버
          |
Linux가 현재 볼 수 있는 장치
```

H2-a inventory는 위 관리 계약에 결속하기 위한 입력 자료다. 장치 목록이
출력됐다는 사실만으로 managed Cell/Node/NodeBit가 만들어졌다고 보지 않는다.

## 2. 이번 조각의 종료점과 남는 단계

이번 조각의 목표는 **AIOS 런타임 시작 로그를 출력하고, 실행 중인 Linux가
노출하는 CPU·메모리·PCI·USB·block·network 정보를 제한된 크기로 읽어,
동일 실행의 증거를 남기고 정상 종료하는 것**이다.

이 문서에서 `boot`는 **AIOS 유저스페이스 런타임 초기화**를 뜻한다.
펌웨어·부트로더를 거치는 native cold boot나 Linux kernel boot log와 구분한다.
Linux 부팅 로그를 가져와 AIOS가 자체 하드웨어 부팅을 완료한 것처럼 표시하지 않는다.

| 항목 | 이 조각의 계약 | 성숙도·한계 |
|---|---|---|
| H1 contract/replay | 기존 계약과 native semantic oracle를 유지 | `CURRENT` 범위는 기존 정본의 host-only acceptance |
| H2-a runtime bootstrap | 시작·inventory·종료, 실행별 artifact, 외부 verifier | `PARTIAL`; 실제 Linux 개발 VM 로컬 검증은 §8에 기록 |
| Linux-visible 하드웨어 관측 | procfs/sysfs source-only snapshot | 장치 열거·읽기 증거이며 실제 데이터 경로 지원 전체가 아님 |
| bound `AI_SERVICE` | producer-owned instance/generation과 canonical binding | 별도 MAIN은 `PARTIAL`; H2-a inventory는 계속 UNBOUND, 전체 H2/H3는 후속 |
| live lifecycle/reconciliation | exit/recreate/restart/host reboot와 explicit rebind | bounded MAIN·Cell 1 관리는 `PARTIAL`; 전체 source coverage와 host reboot는 후속 |
| per-owner 자원 귀속 | 유효한 binding을 통한 Cell/Node 관측 | `PLANNED` |
| authorize/apply/rollback | principal·ownership·권한 분리·별도 적용 검증 | `PLANNED` |
| Linux primary exact reference 지원 | 정확한 커널·구성·패키지·hash와 정규 acceptance | 이번 개발 호스트 실행만으로 승격하지 않음 |

첫 단계는 H2의 실행 기반을 여는 `SUPPORTING` 조각이다. 전체 Kernel Room topology,
K2 lifecycle, H2 service binding 또는 resource enforcement 완료를 대신하지 않는다.

## 3. 파일 책임과 의존 방향

첫 runtime 구현은 **Python 3.11 이상과 표준 라이브러리**로 구성한다.
외부 패키지 설치나 배포판별 장치 조사 명령의 출력 파싱을 기본 전제로 두지 않는다.

| 위치 | 책임 |
|---|---|
| `hosted/linux/aios-boot.py` | 사용자가 실행하는 bootstrap 진입점 |
| `hosted/linux/aios_hosted/` | 제한된 procfs/sysfs reader, inventory, runtime 초기화와 artifact 생성 |
| `hosted/contracts/` | backend-neutral 계약; 기존 H1과 후속 계약의 소유 도메인 |
| `tools/hosted/verify_boot.py` | runtime 밖에서 artifact 일관성과 종료 조건을 판정하는 verifier |
| `tools/hosted/`의 관련 테스트 | fixture, malformed input, false-PASS 방지 검증 |
| `build/hosted-boot/<unique-run>/` | 한 실행의 결과; 이전 실행과 섞지 않는 비추적 산출물 |

허용되는 방향은 `tools/ -> hosted/` 검증과
`hosted/linux/ -> hosted/contracts/` 공개 계약 소비다. runtime은 `tools/`를
import하지 않으며 `kernel/` private header, 내부 배열, lock, native ring3 구현에
의존하지 않는다. 기존 AIOS syscall opcode를 Linux syscall 번호로 사용하지 않는다.

이번 코드는 공개된 인터페이스 의미를 참고하여 AIOS에서 독립 구현한다.
공식 문서 조사·source catalog 등록은 upstream 코드 import 승인이 아니다.
source-only 및 `code_import=0` 정책과 upstream exact pin은
[기존 정책과 manifest](../../tools/platform/resources/linux_substrate_resources.json)를
따르며 이 가이드가 변경하지 않는다.

## 4. 하드웨어 관측 계약

### 4.1 관측 범위와 읽기 경계

live 실행의 기본 원본은 `/proc`, `/sys`다. fixture 테스트는 분리된 임시 디렉터리를
명시적으로 입력해 수행하되, fixture 산출물과 live Linux 산출물을 구분한다.
fixture를 읽은 결과를 실제 하드웨어 인식 증거로 표시하지 않는다.

일반 사용자 권한으로 문서화된 속성을 읽고 디렉터리를 열거한다. runtime이 sysfs를
mount하거나 driver bind/unbind, PCI enable/reset/rescan, USB configuration,
network 설정, CPU hotplug, 디스크 mount를 수행하지 않는다. PCI config space,
BAR, ROM과 block device의 내용 자체를 열어 장치를 시험하지도 않는다.

PCI 문서는 식별용 ASCII 속성과 쓰기·mmap이 가능한 제어/자원 파일을 구분한다.
H2-a는 식별용 속성의 관측만 소비한다.
[공식 근거: PCI sysfs](https://docs.kernel.org/PCI/sysfs-pci.html)

| 분류 | 첫 관측 대상 | 의미와 제약 |
|---|---|---|
| CPU | `/proc/cpuinfo`, 필요 시 `/sys/devices/system/cpu`의 문서화된 속성 | Linux-visible CPU 정보. logical CPU 개수·topology·host 물리 socket 수를 혼동하지 않음 |
| 메모리 | `/proc/meminfo`의 `MemTotal`, `MemAvailable` 등 지원된 항목 | Linux가 사용 가능한 RAM과 추정 가용량. 설치 DIMM 용량 또는 AIOS 예약량으로 해석하지 않음 |
| PCI | `/sys/bus/pci/devices`, `vendor`, `device`, `class`, subsystem ID, 해당 객체의 driver 링크 | 열거된 function과 그 객체의 binding 관측 |
| USB | `/sys/bus/usb/devices`, `idVendor`, `idProduct`, class, bus/device 번호와 해당 객체의 driver 링크 | USB 장치와 인터페이스 객체를 구분하며 같은 장치를 중복 물리 장치로 집계하지 않음 |
| Block | `/sys/class/block`, 객체 이름·종류·문서화된 크기 속성 | 디스크·파티션·가상 block 객체를 구분. 실제 읽기/쓰기·마운트 가능성은 별도 |
| Network | `/sys/class/net`, `ifindex`, `type`, `operstate` 등 | loopback·가상 NIC도 inventory에 포함될 수 있음. link 상태는 외부 통신 성공 증거가 아님 |

CPU topology ID는 아키텍처·플랫폼에 따라 의미가 다르고, block의 논리/물리
블록 크기는 서로 다른 값이다. 제공되지 않은 속성을 기본값으로 채워 물리 특성을
만들지 않는다.
[공식 근거: Stable ABI descriptions](https://docs.kernel.org/admin-guide/abi-stable.html)

USB 식별자와 network 상태 속성은 각각의 ABI 의미에 맞게 해석한다.
network `speed`처럼 일부 인터페이스만 구현하는 속성의 부재는 정상 0으로 바꾸지 않는다.
관측값은 해당 Linux 객체의 현재 정보이며 영구적인 AIOS identity가 아니다.
[공식 근거: Testing ABI descriptions](https://docs.kernel.org/admin-guide/abi-testing.html)

`/proc` 항목의 존재 여부는 Linux 구성과 모듈에 따라 달라질 수 있다.
`MemTotal`은 Linux가 사용할 수 있는 RAM이고 `MemAvailable`은 새 프로그램에 쓸 수
있는 양의 추정값이다. 합계·추정값·누락 여부를 보존하며 정확한 할당 보증으로 바꾸지 않는다.
[공식 근거: proc filesystem](https://docs.kernel.org/filesystems/proc.html)

### 4.2 sysfs 객체와 driver binding

분류 디렉터리의 symlink는 `/sys/devices`의 실제 장치 경로로 해석한다.
driver 이름은 **그 객체에 존재하는** `driver` 링크에서만 읽는다.
부모에 driver가 있고 자식에는 없는 경우 부모 driver를 자식의 driver 값으로
복사하지 않는다. 부모 맥락을 표시하려면 parent/source 관계를 별도 정보로 남긴다.

sysfs의 내부 디렉터리 깊이 또는 `../` 개수에 기대어 부모를 찾지 않는다.
장치 경로는 해당 시점의 source 식별자이고 재부팅·재연결·이름 재사용을 견디는
영구 canonical ID가 아니다. 오류는 가능한 한 관측 결과로 전파한다.

Linux 문서는 직접 sysfs를 읽을 때 추상화 규칙을 따르고, 가능한 경우 udev 같은
기존 추상화를 사용하도록 권고한다. H2-a의 일회성 snapshot 이후 hotplug·장기
서비스 단계에서는 이 경로를 별도로 검토한다. 이를 위해 이번 runtime이 udev rule이나
장치 권한을 변경할 필요는 없다.
[공식 근거: sysfs access rules](https://docs.kernel.org/admin-guide/sysfs-rules.html)

관측 수준은 아래처럼 구분한다.

1. **열거됨:** Linux가 노출한 객체와 식별 속성을 읽었다.
2. **드라이버 결속 관측:** 해당 객체의 driver 링크를 읽었다.
3. **실제 사용 검증:** 요구된 I/O·통신·가속 연산을 별도 실행하여 검증했다.

H2-a는 1과 가능한 2까지만 다룬다. 결과가 성공이어도 3을 자동으로 참으로
만들지 않는다. 드라이버 결속 관측만으로 `usable`, production support 또는
AIOS의 장치 제어 권한을 선언하지 않는다.

### 4.3 실패와 제한

각 분류는 정상적으로 읽은 빈 목록, 원본 경로 부재, 권한 오류, 잘못된 값,
수집 도중 장치 소멸을 구분한다. 사용자가 결과에서 어떤 정보가 없고 왜 없는지
판단할 수 있어야 한다. 지원되지 않는 subsystem을 전체 하드웨어가 없는 것으로
표현하지 않는다.

디렉터리 열거 개수, 한 속성의 읽기 byte 수, 문자열·기록 크기에 상한을 둔다.
상한 초과는 명시적인 불완전 관측이며 조용히 자른 뒤 완전한 inventory로
표시하지 않는다. 정렬 후 작은 결과만 취하는 방식도 전체 디렉터리를 먼저 무제한
읽는다면 bounded 열거가 아니다.

서로 다른 source를 순서대로 읽는 snapshot은 best-effort다. 여러 subsystem이
동시에 같은 순간에 고정됐다는 원자성이나 모든 장치를 빠짐없이 읽었다는 보증은
별도 일관성 계약 없이는 주장하지 않는다.

## 5. 실제 Linux 실행과 개발 호스트

fixture reader와 외부 verifier의 테스트는 Windows와 Linux에서 수행한다.
그러나 Windows에서 fixture 테스트를 통과한 것은 live Linux runtime 증거가 아니다.
실제 `/proc`·`/sys`를 읽는 정상 실행과, 같은 실행의 정상 종료까지 추가로 필요하다.

현재 작업은 WSL 설치를 전제로 하지 않고 **격리된 QEMU Linux VM을
테스트 호스트로 사용한다**. 이 Linux VM은 AIOS runtime을 실행·검증하기 위한
개발 도구이며 AIOS 배포판 이미지 산출물이 아니다. guest image의 출처·hash,
실제 Linux kernel release, Python 버전과 QEMU 실행 조건을 실행 증거에 남긴다.

VM에서 관측한 PCI·USB·디스크·NIC는 그 Linux guest에 노출된 장치다.
에뮬레이션·가상화·명시적으로 전달된 장치와 Windows 호스트의 물리 장치 전체를
동일시하지 않는다. 물리 PC의 모든 하드웨어를 직접 검증하는 단계는 따로 둔다.

향후 WSL2를 사용할 때도 같은 원칙을 적용한다. WSL2는 관리되는 VM 안에서
실제 Linux 커널을 실행하므로 결과는 WSL guest-visible inventory다.
USB 지원은 별도의 USBIPD-WIN 경로가 제공되며, 이번 collector가 그 연결을
자동 수행하거나 물리 passthrough를 검증했다고 표시하지 않는다.
[공식 근거: Comparing WSL versions](https://learn.microsoft.com/en-us/windows/wsl/compare-versions)

개발 호스트의 한 번의 성공을 Linux primary exact reference 지원으로 확장하지 않는다.
승인된 upstream pin의 소유자는 기존 resource manifest다. 해당 exact kernel,
구성·패키지·integrity 정보와 정규 host acceptance가 없으면 지원 자격은 미확정이다.
이번 공식 문서 검토일은 기존 exact source pin 갱신일을 대체하지 않는다.

## 6. 실행 명령과 산출물

다음은 저장소 루트를 기준으로 한 실행 명령이다. Python 3.11 이상이 필요하다.
실제 실행 환경과 검증 범위는 §8에 별도로 기록한다.

```bash
python3 hosted/linux/aios-boot.py --artifact-dir build/hosted-boot/<unique-run>
python3 tools/hosted/verify_boot.py build/hosted-boot/<unique-run> --require-live
python3 tools/hosted/boot_smoke.py --artifact-dir build/hosted-boot/<new-smoke-run>
```

`<unique-run>`은 실행마다 새 이름으로 치환한다. 이미 다른 실행의 결과가 들어 있는
디렉터리를 재사용하지 않는다. Windows의 fixture·검증 호출에서는 `py -3`을
사용할 수 있지만, Windows 프로세스를 live Linux runtime으로 판정하지 않는다.

Windows에서 실제 Linux 개발 VM을 부팅해 확인하려면 다음을 실행한다.

```powershell
powershell.exe -NoProfile -File tools/hosted/Start-AiosBootDemo.ps1
```

이 도구는 설치된 QEMU와 Python을 사용하며, 공식 Alpine 3.24.1 virt 이미지의
고정 SHA-256을 확인한 뒤 임시 VM을 띄운다. VM 내부에만 Python을 준비하고,
AIOS 소스는 읽기 전용 가상 디스크로 전달한다. 실제 실행 로그·장치 요약을 출력한 뒤
VM을 정상 종료한다. Linux 이미지는 사용자 로컬 캐시에 보관하는 개발 도구다.
호스트의 디스크·드라이버 설정·WSL 구성은 변경하지 않는다.

`boot_smoke.py`는 실제 process exit, stdout/stderr와 `verdict.json`을 별도 보존한다.
`qemu_boot_demo.py`는 Linux serial, ISO/kernel/initramfs hash, QEMU 버전과
VM 정상 종료 여부를 `environment.json`·`demo-verdict.json`에 추가한다.
실행에 전달한 runtime 소스도 `runtime-source/`에 보존한다. 보존된 실행 전체를
재검증할 때는 `--execution`으로 실제 process 종료·stdout/stderr를 함께 검사하고,
`--source-root`로 당시 소스의 hash를 대조한다(§8 명령).
직접 runtime의 종료 코드는 READY=0, FAILED=1, DEGRADED=2, UNSUPPORTED=3이며
기존 artifact 디렉터리나 artifact 쓰기 오류는 4다. 외부 verifier/runner는 PASS만 0이다.

| 파일 | 소유자와 의미 |
|---|---|
| `boot.log` | runtime가 실제로 기록한 사람이 읽는 시작·관측·종료 로그 |
| `events.jsonl` | 같은 실행의 순서가 있는 구조화된 runtime 이벤트 |
| `inventory.json` | 원본 범위·관측 상태·제한과 함께 기록한 하드웨어 snapshot |
| `result.json` | 같은 실행의 runtime 완료 상태; 외부 verifier의 판정 자체를 대체하지 않음 |

네 파일은 같은 run identity와 관측 범위를 가져야 한다. 실행이 실패하면 성공 marker와
이전 `result.json`을 남겨 성공처럼 보이게 하지 않는다. 초기에 artifact 생성 자체가
실패한 경우도 정상 실행으로 판정할 수 없다. 외부 verifier는 runtime의 결과 문자열을
그대로 믿지 않고 파일 사이의 연관성, 필수 단계·순서와 실패 정보를 다시 검사한다.

runtime의 정상 완료 기록과 외부 launcher가 본 실제 프로세스 exit code는 별도 증거다.
`result.json`에 정상 종료라고 써 있어도 실제 exit 관측이 없으면 그 사실을 확인한 것이
아니다. outer runner/CI는 exit code와 timeout·강제 종료 여부를 보존해야 한다.

프로젝트 SHA와 dirty state, runtime 실행 조건, Python·Linux 버전, 개발 VM 출처를
같은 실행의 provenance로 보존한다. artifact hash가 있더라도 수집한 raw 관측값의
정확성을 hash 자체가 증명하는 것은 아니다.

## 7. 다섯 가지 acceptance

아래 다섯 조건은 H2-a의 최소 gate다. 정상 inventory 한 번만 출력해서 완료 처리하지 않는다.

| Gate | 요구 증거 | 반드시 거부할 반례 |
|---|---|---|
| 1. 실패하면 READY 없음 | 필수 초기화·관측이 실패하면 실패 결과와 비정상 종료를 남기며 READY를 내지 않음 | 필수 입력 누락·malformed 값·쓰기 실패 뒤 READY 또는 성공 result |
| 2. 오류와 빈 목록 구분 | 읽을 수 있는 빈 subsystem은 빈 목록으로, 읽기 실패는 오류/불완전 관측으로 기록 | 권한 거부·경로 부재·hot-unplug를 정상 0개로 위장 |
| 3. 개수와 읽기량 제한 | 열거·속성 읽기·이벤트 크기의 상한과 초과 이유를 보존 | 무제한 열거/읽기, 조용한 truncation, 상한 초과인데 완전한 성공 |
| 4. binding과 사용 가능성 분리 | 객체별 driver 유무와 source 관계를 보존하고 실제 I/O 사용 가능성은 미검증으로 유지 | 부모 driver 복사, driver 이름만으로 usable/authorize 선언 |
| 5. 같은 실행의 시작·inventory·종료 일치 | `boot.log`, `events.jsonl`, `inventory.json`, `result.json`과 실제 종료의 일치 | 다른 run 파일 혼합, 중복/역순 terminal, 누락·잘린 artifact, 성공 뒤 실패, timeout을 정상 종료로 처리 |

verifier는 exact record와 구조를 검사해야 한다. 인용문 속 READY, 접두어가 같은
다른 token, 중복 JSON key, 서로 모순되는 count와 상태가 성공 근거가 되면 안 된다.
runtime 결과·외부 판정·CI terminal outcome을 분리하고 원본 로그를 보존한다.

검증 순서는 좁은 테스트부터 실제 실행으로 넓힌다.

1. 임시 procfs/sysfs fixture로 정상·빈 subsystem·누락·잘못된 값·읽기 상한을 검증한다.
2. 실제 sysfs와 같은 symlink/부모 관계를 fixture로 만들어 driver 귀속 오류를 검증한다.
3. 생성 artifact를 변경하여 다른 run 혼합, 순서 위반, 누락과 뒤늦은 실패를 verifier가 거부하는지 확인한다.
4. Windows와 Linux에서 fixture·verifier 테스트를 수행한다.
5. 실제 Linux guest에서 runtime를 실행하고 live inventory, 네 artifact, 실제 정상 종료를 대조한다.

Windows에서 symlink fixture를 생성할 수 없어 해당 테스트가 skip되면 그대로 기록한다.
Windows 전체 테스트 성공이라는 한 줄로 이 skip을 감추지 않고 Linux에서 해당 반례를
실제로 검증한다. 기존 native 커널을 변경하지 않은 이 조각의 테스트를 위해 native
QEMU baseline을 갱신하지 않는다.

## 8. 실행 결과 기록과 성숙도 승격

2026-09-03 로컬 검증 결과다. checkout은 `beta`, 기반 HEAD는
`2b4649b7db1a061d63dd1e9d9e778ebc7939c7a3`이며 이번 변경은 커밋 전 상태다.
실행 시점의 HEAD·dirty 목록은 `environment.json`, 정확한 runtime 파일 hash는
`run/result.json`, 실행에 사용한 runtime 복사본은 `runtime-source/`에 남아 있다.

| 확인 대상 | 확인 결과 | 플랫폼·범위·증거 |
|---|---|---|
| Windows hosted 전체 테스트 | `PASS` | Python 3.11.9, 166 tests / 25.901 s, symlink 권한 관련 1개 skip |
| Linux H2-a fixture·verifier | `PASS` | Linux 6.18.35-0-virt / Python 3.14.7, 25 tests / 2.485 s, symlink 반례 포함·skip 없음 |
| 실제 Linux guest bootstrap | `PASS` | Alpine virt 3.24.1 x86_64 개발 guest, QEMU 10.2.0 q35/TCG, AIOS `READY`, runtime exit 0 |
| artifact 외부 재검증 | `PASS` | 같은 run의 process exit 0·빈 stderr·stdout/boot.log 일치·실행 소스/산출물 hash 검증 |
| 최종 verifier 보강 재검증 | `PASS` | 관련 Windows 테스트 34개(1 skip), `--execution --run-id`의 일치 실행 승인·다른 실행 ID 거부 확인 |
| VM 정상 종료 | `PASS` | serial `reboot: Power down`, VM exit 0, `host_killed=false`, `shutdown_observed=true` |
| 기존 platform resource guard | `PASS` | source-only 13개 resource guard와 platform tests 26개 통과, 기존 pin/contract 변경 없음 |
| H2-a 원격 exact-SHA acceptance | 미실행 | 같은 SHA의 named job·terminal outcome·artifact |
| primary exact reference qualification | 미검증 | canonical exact kernel/config/package/hash와 정규 지원 gate |

실행 디렉터리는 `build/hosted-boot/qemu-07/`, run identity는
`269c3249-d529-4b9f-b5a8-38db2590584e`다. 이 디렉터리는 로컬 비추적 증거이며
저장소를 clone한 환경에 포함되지 않는다. GitHub Actions의 Linux 실행·artifact
업로드 단계는 추가했지만 원격 실행하지 않았다. PyYAML이 없어 공식 YAML 검증은
실행하지 않았으며, 기존 native 커널을 바꾸지 않아 native QEMU baseline도 갱신하지 않았다.

```powershell
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -q
powershell.exe -NoProfile -File tools/hosted/Start-AiosBootDemo.ps1
py -3 tools/hosted/verify_boot.py build/hosted-boot/qemu-07 --execution --require-live --run-id 269c3249-d529-4b9f-b5a8-38db2590584e --source-root build/hosted-boot/qemu-07/runtime-source
```

두 번째 명령은 새 실행 디렉터리를 자동으로 만들며 끝에 경로를 출력한다.
세 번째 명령의 경로는 보존된 `qemu-07` 증거를 재검증하는 예다. 새 실행을 검증할
때는 출력된 경로로 두 경로 인자를 함께 치환하고 새 `run_id`를 사용한다.

| 관측 분류 | `qemu-07` 실제 결과 | 해석 |
|---|---|---|
| CPU | logical 2개, QEMU Virtual CPU version 2.5+ | guest에 노출된 CPU |
| 메모리 | `MemTotal=754503680` bytes, `MemAvailable=539205632` bytes | 약 719.55 MiB의 Linux 사용 가능 RAM, 가용량은 추정치 |
| PCI | function 8개, 직접 driver binding 5개 | `bochs-drm`, `e1000`, `xhci_hcd`, `virtio-pci`, `ahci` |
| USB | 장치 객체 3개 | root hub 2개와 에뮬레이션 키보드 1개, interface 중복 제외 |
| Block | 객체 11개 | loop 8개·`sr0`·`vda`·`vda1`; 물리 디스크 11개라는 뜻이 아님 |
| Network | `eth0`, `lo` 2개 | `operstate`는 각각 `up`, `down`; 통신 성능·지원 판정은 아님 |

여섯 관측 분류는 모두 `observed`다. 모든 장치의 실제 사용 가능성은 여전히
`UNTESTED`, canonical binding은 `UNBOUND`, action은 `UNSUPPORTED`다.
이번 성공은 guest-visible 읽기·초기화·정상 종료까지의 증거다.

로컬 실제 Linux 실행까지 통과해도 bootstrap 범위는 `PARTIAL`이다. 이 bootstrap 자체에는
장기 서비스·source binding이 없으며, 후속 서비스·MAIN의 별도 증거와 구분한다.
remote acceptance와 primary exact qualification도 이 실행으로 완료되지 않는다.
일반 Linux guest 성공은 H1의 기존 원격 acceptance를 새 H2 runtime에 승계시키지 않는다.

현재 H1이 신뢰하는 source namespace는 native source 계약에 한정된다. H2-a가 Linux
device/PID를 그 namespace에 넣거나 native처럼 꾸민 lifecycle trace를 생성해서
H1 replay를 통과시키면 안 된다. hosted namespace와 producer-owned instance/generation,
semantic-kind gate를 실제로 구현·검증한 후에만 H1 연동을 확장한다.

상위 문서에는 이 가이드의 제한된 범위를 링크하고, Linux-hosted bootstrap과 전체
bound H2를 구별한다. [H1 작업 준비서](h1_binding_trace_replay_workplan_ko.md)의 기존
host-only contract/replay 상태와 H0 resource catalog의 상태를 변경 근거로 재사용하지 않는다.

## 9. 장기 작업의 후속 단계

장기 개발은 각 단계의 증거가 다음 단계를 여는 순서로 진행한다. 부팅 로그가 생겼다는
이유만으로 자원 제어·배포 범위를 한꺼번에 넓히지 않는다.

| 순서 | 다음 구현 조각 | 단계 종료 증거 |
|---|---|---|
| 1 | H2-a bootstrap·하드웨어 관측 | 로컬 완료(§8); 원격 acceptance와 exact qualification은 별도 |
| 2 | 장기 service lifecycle | 별도 CONSOLE_RUNTIME과 MAIN은 `PARTIAL`; 시작·정상 종료·실패·재시작 및 CLI 재접속의 보존 실행 증거 |
| 3 | bound `AI_SERVICE` source | MAIN·Cell 1의 명시적 binding/reconcile `PARTIAL`; backend 관리·실제 요청 대상 결속은 backend-02의 로컬 실제 실행 및 당시 소스 재검증 통과, 보존된 소스 증거로 `PARTIAL` 유지 |
| 4 | per-Cell/per-Node 자원 관측 | MAIN/backend 각각의 CPU/RSS와 unattributed system PSI는 `PARTIAL`; 전체 per-owner 귀속은 별도 후속 |
| 5 | proposal·authorize·bounded apply | K5 principal/ownership와 별도 승인·권한 분리, before/after, timeout·partial apply·rollback 증거 |

service process가 있다는 사실만으로 canonical `AI_SERVICE`를 충족하지 않는다.
PID·cgroup 경로·host boot ID는 source lifecycle의 증거이고, AIOS가 소유하는
canonical identity/generation과 구분해야 한다. 재시작 후 이전 binding을 암묵적으로
복원하지 않는다.

후속 release 판단에는 설치·시작·중지·제거 경험, 지원하는 Linux 기준선, 실제 workload,
업데이트와 회귀 증거가 추가로 필요하다. QEMU 개발 VM이나 현재 한 번의 bootstrap
결과는 AIOS 배포판, 설치 이미지 또는 production release 산출물로 표시하지 않는다.

## 10. 변경·인수인계 규칙

각 조각에서 runtime producer, 외부 verifier, negative test와 운영 문서를 같은 변경으로
맞춘다. source reader를 넓히면 새로운 장치 class의 빈 목록·오류·상한과 사용 가능성
경계도 함께 정의한다. 새로운 관측 분류가 canonical NodeBit 또는 apply 권한을
자동 생성하게 만들지 않는다.

완료 보고에는 구현한 범위, 사용한 Linux guest, 정확한 run artifact, 실제 종료,
외부 verifier 결과, 미실행 lane을 나누어 적는다. `README.md`, `CLAUDE.md`,
`PROJECT.md`, `hosted/README.md`, 관련 정본·roadmap과 `docs/README.md`의
같은 상태 표현을 동기화한다. 검증되지 않은 전체 H2를 `CURRENT`로 승격하지 않는다.

이번 문서의 공식 링크는 인터페이스 검토 근거다. 움직이는 최신 문서 URL은 실행
artifact의 immutable source pin이나 특정 Linux 조합의 지원 증거를 대신하지 않는다.
upstream 기준선 변경은 기존 source policy의 별도 검토·guard 경로를 따른다.
