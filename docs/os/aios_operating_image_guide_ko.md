# AIOS 반복 부팅 운영 이미지 가이드

> 문서 역할: Linux-hosted AIOS 기본 운영 이미지 v0와 후속 모델 profile의 설계·acceptance 정본
>
> 방향: `SUPPORTING` — 검증된 AIOS CLI와 hosted 관리 서비스를 반복 부팅 가능한 사용자 환경에 연결한다.
>
> 성숙도: `PARTIAL` — `image-07`의 같은 완성 디스크로 온라인 두 번·오프라인 한 번의
> 실제 cold boot와 독립 판정을 통과했다. 기본 사용자 디스크를 복제하는 실행기도 검증했다.
> 모델을 포함하지 않는 로컬 QEMU 기본 이미지이며 native·전체 H2/H3 성숙도는 그대로다.
>
> 모델 profile: `SUPPORTING/PARTIAL` — 현재 CLI v0.7의 `model-image-05`로 online·offline
> 두 cold boot, 실제 warmup·질문 각 2회, 명시적 재결속·인터넷·정상 종료와 독립 판정을 통과했다.
> 전용 0.7 launcher의 사용자 사본 실행도 별도 PASS다. v0.6 `model-image-04`와 기존 포인터는 보존한다.
> 별도 장애 복사본 02의 같은 worker recover·즉시 exit·정상 종료도 실제 PASS이며 01의 원본 FAIL은 보존한다.
> 기본 이미지 선택·되돌리기: `SUPPORTING/PARTIAL` — Windows 로컬의 0.7·0.6 실제 기본 부팅과 최종 0.7 재선택 PASS(§9.8).
>
> 작성·공식 자료 검토: 2026-09-08

이 문서의 `build/` 보고서·전용 실행기와 `%LOCALAPPDATA%` 이미지·모델·선택 상태는
로컬 검증 호스트의 비추적 기록이며 Git checkout에 포함되지 않는다. 새 환경은 §7의
basic 또는 §9.2의 모델 Build·Smoke·Run 절차를 사용한다. 과거 실행 ID와 hash는
보존된 증거를 식별하며 새 실행의 성공을 미리 보장하지 않는다.
본문의 현재 소스·snapshot 일치 결과는 각 재검증 시점의 판정이다. 베타 게시 직전
형식 정리로 생긴 4개 파일의 byte 차이와 보존 소스 재생 경계는 §11을 따른다.

## 1. 목표와 정체성

이번 목표는 준비가 끝난 디스크를 부팅하면 AIOS 고유 화면과 CLI에 들어가고,
하드웨어 상태·DNS·인증서 검증 HTTPS를 사용한 뒤 종료하고 다시 부팅할 수 있는
최소 사용자 환경이다. 현재 개발 VM처럼 호스트가 Linux에 로그인하여 패키지를
설치하고 소스를 마운트한 다음 프로그램을 시작하는 절차를 운영 부팅에서 제거한다.

AIOS의 제품 의미와 `Room -> Cell -> Node -> NodeBit`의 identity·generation·binding은
[Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)이 소유한다.
Linux는 드라이버·파일시스템·네트워크·프로세스 실행을 제공하는 특권 커널이다.
이 이미지가 사용하는 Alpine 패키지는 외부 실행 기반이며 AIOS의 관리 정체성이 아니다.
`about`과 실행 증거에서 AIOS 버전과 실제 Linux 커널·외부 패키지 출처를 구분한다.

별도 `kernel/`의 native AIOS 커널과 ABI·검증 경로를 유지한다. 이 이미지를 native
AIOS 커널이 Linux 드라이버를 직접 실행하는 결과, Linux fork 또는 전체 H2/H3 완료로
해석하지 않는다. [Linux substrate 정책](linux_hosted_substrate_and_resource_policy_ko.md)의
source-only 경계와 기존 native qualification을 바꾸지 않는다.

[과거 유저공간 빌드 계획](user_space_os_build_slices_ko.md)과
[과거 방향 문서](user_space_os_direction_ko.md)는 `OLD`다. 현재
[성숙도 작업 순서](../meta/minimal_io_and_maturity_workflow_ko.md)와
[CLI 가이드](aios_cli_internet_guide_ko.md)를 따르며 native process/storage 전체 확장을
이번 이미지의 선행조건으로 추가하지 않는다.

## 2. 기본 이미지의 고정 범위

| 항목 | v0 계약 |
|---|---|
| 대상 | 로컬 QEMU x86_64의 BIOS 부팅 한 profile |
| 디스크 | 새로 생성한 raw 4 GiB 디스크, ext4 root filesystem |
| 외부 기반 | 고정 Alpine 3.24.1 설치 이미지에서 준비한 Linux runtime; 설치된 실제 패키지 버전·hash는 별도로 기록 |
| 제품 파일 | root 소유 `/opt/aios/linux`의 AIOS runtime |
| 제품 source 계약 | 현재 소스는 이미지 35개: CLI v0.7/session schema 7/source 31개와 별도 boot module 4개. model-image-05는 이 소스의 정상 모델 부팅 증거이며 image-07·model-image-04는 당시 v0.6 보존 증거 |
| 부팅 진입점 | `/opt/aios/linux/aios-image-boot.py`; 기존 `aios-boot.py` 관측 진입점과 별도 |
| 이미지 설명 | root 소유 `/usr/share/aios/image.json` |
| 부팅 설정 | `/etc/aios/boot.json`; schema 1, `profile=basic`, `model_config=null` |
| 사용자 | UID 1000의 비특권 `aios`; root 로그인 절차 없이 CLI 진입 |
| 필수 의존성 | Python·시스템 CA·AIOS runtime을 이미지 생성 단계에서 준비 |
| 네트워크 | guest 자체의 제한된 DHCP 시도와 CLI의 명시적 DNS·HTTP(S) 요청 |
| 모델 | 기본 이미지에 모델 bundle과 모델 backend 실행 의존성을 포함하지 않음 |
| 영속 데이터 | 운영 설정과 종료한 부팅의 실행 증거 |
| 실행 상태 | Linux boot ID별 임시 상태; 이전 부팅의 canonical 관리 상태를 복원하지 않음 |
| 종료 | CLI `exit` 뒤 guest supervisor의 고정 cleanup·archive·poweroff 경로 |

기본 이미지의 성공은 CLI와 인터넷 사용 증거다. 모델이 없는 상태의 `agent`·`backend`
조회 또는 요청은 현재 계약에 따라 부재·미설정 결과를 보여야 하며, AI readiness를
만들지 않는다. 모델을 포함하는 별도 profile의 구현·실제 acceptance는 §9에서 관리한다.
기존 개발 VM의 실제 모델·MAIN·Cell·backend 검증은
[MAIN 가이드](aios_agent_binding_guide_ko.md), [Cell 가이드](aios_cell_lifecycle_guide_ko.md),
[backend 가이드](aios_backend_lifecycle_guide_ko.md)에 보존한다.

이미지 builder는 새 산출물 디스크만 준비한다. 운영 boot는 그 디스크 안의 bootloader·
kernel·initramfs로 시작하며, 외부 설치 ISO·호스트의 `-kernel`/`-initrd`·소스 공유 볼륨에
의존하지 않는다. 호스트 launcher는 VM 실행·사용자 serial 입력·외부 증거 수집을 담당한다.
운영 부팅 중 Linux 셸에 설치·시작·종료 명령을 주입하지 않는다.

## 3. 부팅과 종료의 책임

Alpine의 init/OpenRC가 필수 파일시스템과 장치 준비를 마친 뒤 AIOS guest supervisor를
시작한다. OpenRC는 `sysinit`, `boot`, `default` 순서의 runlevel과 별도 shutdown 경로를
제공한다. 이 외부 시작 순서 위에 AIOS 제품의 제한된 supervisor를 연결한다.
[공식 근거: Alpine OpenRC](https://wiki.alpinelinux.org/wiki/OpenRC)

guest supervisor는 이미지 manifest·설정·경로의 소유권과 설치된 제품 source bytes를
확인하고 이번 부팅의 작업 디렉터리를 만든다. 설정은 정확히 `schema_version`,
`profile`, `model_config`를 가진다. 기본 schema 1은 `basic`과 `model_config=null`만
허용하며 모델 schema 2는 §9의 `local-model`과 고정 설정 경로만 허용한다.
그런 다음 고정된 AIOS CLI를 UID 1000으로 실행한다. CLI가 root
코드나 임의 셸 명령을 선택하도록 하지 않는다. serial console은 bootloader 출력,
Linux kernel console, 사용자 프로세스의 터미널 연결을 각각 맞춘다.
[공식 근거: Alpine serial console](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)

DHCP 실패나 외부망 부재를 패키지 설치 대기로 숨기지 않는다. 제한 시간 뒤에도 CLI가
열리며 `net status`, `resolve`, `fetch`에서 관측 상태와 요청 실패를 구분한다.
기존 TLS 인증서·hostname 검증과 입력·출력·worker 제한을 유지한다.

정상 `exit`에서는 다음 순서를 요구한다.

1. CLI session의 terminal 기록과 실제 프로세스 종료를 확인한다.
2. UID 1000 worker가 이번 부팅의 private 상태 경로에 속한 MAIN·backend·CONSOLE_RUNTIME을
   각 제품 client의 고정된 control 경로로 정리한다.
3. root supervisor가 worker 종료 뒤 현재 실행 증거를 검증·archive하고 영속 쓰기를 마친다.
4. guest가 `/sbin/poweroff`로 정상 init/OpenRC 종료를 요청하며 외부 runner가 실제 VM
   종료와 강제 종료 여부를 기록한다. 정상 경로는 `-f`를 사용하지 않는다.

모델이 없는 기본 profile의 정상 service 부재는 허용한다. 실행 중인 서비스가 있다면
저장된 STOPPED 문자열만으로 실제 종료를 대신하지 않는다. cleanup·archive·쓰기 실패는
실패 증거로 남기고 정상 acceptance로 승격하지 않는다. 기존 CLI 세션 상한 도달도
정상 `exit`로 바꿔 표시하지 않는다.

## 4. 영속성과 재부팅 경계

live 상태는 `/run/aios/boots/<Linux-boot-id>` 아래의 `main`, `backend`,
`service`에 둔다. 이 경로의 boot ID는 Linux
실행 출처와 디렉터리 구분자이며 canonical Cell/Node identity가 아니다. CLI session,
MAIN·backend의 source instance와 관리 authority instance는 각 계약의 별도 값이다.

정상 종료한 실행은 root 소유 `/var/lib/aios/history/<Linux-boot-id>`에 보존한다.
현재 부팅의 writable 작업 영역과 확정된 history를 분리하며, CLI 사용자가 과거
archive를 덮어쓰거나 다른 경로를 선택하지 못하게 한다. symlink·비정규 파일·중복
boot ID·경로 이탈·개수/크기 상한 초과는 조용히 생략하지 않고 거부한다.
root는 열린 directory fd와 symlink를 따르지 않는 읽기로 worker 종료 뒤의 일반 파일만
복사한다. archive는 session·MAIN·backend·service 증거와 root·worker receipt를 보존한다.
현재 설계 상한은 history 512회, archive 일반 파일 512개, 원시 데이터 합계 16 MiB,
파일 하나 2 MiB다. 상한을 넘으면 실패하며 이전 history를 삭제하지 않는다.

영속 config와 history가 남는다는 것은 이전 실행의 readiness 또는 결속 신뢰가
살아 있다는 뜻이 아니다. 새 부팅은 새 live 상태에서 시작한다. 저장된 PID·socket·
backend descriptor·binding·resource observation을 복사하여 현재 source로 채택하지 않는다.
부팅 사이 canonical authority·Cell/Node의 동일 인스턴스를 유지하는 기능은 이 v0에 없다.

기본 이미지에는 장기 AI 기억 저장 기능도 없다. history는 실행 증거이며 모델의
장기기억·자동 복구 journal 또는 자동 rebind 명령이 아니다. 보관 한도에 닿았을 때
기존 증거를 지우거나 세대를 초기화하는 정책을 암묵적으로 추가하지 않는다.

이미지의 base 파일 hash와 실제 사용한 디스크의 상태를 구분한다. 영속 쓰기가 발생하면
raw 디스크 전체 hash는 달라질 수 있다. 반복 부팅의 동일성은 같은 디스크의 연속 사용,
image ID·제품 source·설정·이전 history의 대조로 확인한다. 두 부팅 사이에 디스크를
다시 생성하거나 깨끗한 snapshot으로 되돌려 영속성을 통과한 것으로 표시하지 않는다.

## 5. 생성 단계와 upstream 검토

공식 System Disk Mode 문서는 미리 준비하고 마운트한 root를 `setup-disk -m sys`의
대상으로 사용하는 경로를 제공한다. BIOS에서는 MBR과 bootable partition을 확인한다.
패키지 설치에 필요한 네트워크는 이미지 생성 단계의 의존성으로 기록한다.
[공식 근거: Alpine System Disk Mode](https://wiki.alpinelinux.org/wiki/System_Disk_Mode)

2026-09-08에 공식 `3.24-stable` aports의 alpine-conf recipe가 `3.22.0-r0`을 선택하는 것을
확인했다. 이 branch 조회값은 실제 builder가 설치한 package 버전의 증거를 대체하지 않는다.
[공식 recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/alpine-conf/APKBUILD)

해당 `3.22.0`의 `setup-disk.in`을 읽어 아래 계약을 확인했다. 설치 프로그램은 이 문서
검토 과정에서 실행하지 않았다.

- 마운트한 디렉터리 인수는 mounted-root 경로로 들어간다. 자동 디스크 분할 경로의
  `ERASE_DISKS`가 필요하지 않으며 `-m sys`, `-k virt`, `-B syslinux`를 명시할 수 있다.
- `-q`는 모든 질문에 동의하는 옵션이 아니다. 디스크가 없을 때 조용히 종료할 수 있어
  exit code만으로 이미지 생성 성공을 판정하지 않는다.
- `USE_EFI`는 값이 비어 있지 않으면 EFI를 선택한다. BIOS profile에서 `USE_EFI=0`으로
  끄려고 하지 않는다.
- serial kernel 인자는 `KERNELOPTS`로 명시한다. mounted-root의 syslinux 설치는 별도
  MBR 초기화를 보장하지 않으므로 완성 디스크의 부트 sector와 실제 BIOS 부팅을 검증한다.

[공식 setup-disk source](https://raw.githubusercontent.com/alpinelinux/alpine-conf/3.22.0/setup-disk.in)

설치 ISO pin만으로 이후 저장소에서 받은 모든 패키지 바이트가 고정되었다고 주장하지
않는다. builder는 사용한 repository·APK 목록·실제 버전과 검증 가능한 hash,
Linux kernel·initramfs·bootloader·Python·CA·AIOS source의 provenance를 남긴다.
upstream script를 수정하거나 runtime source로 가져오는 작업은 이 설계에 포함하지 않는다.
독립 검증기의 재현 가능성은 bit-for-bit 이미지 재생성과 구분한다.

## 6. acceptance와 증거

기본 이미지의 최소 수락은 같은 완성 디스크의 온라인 cold boot 두 번과 독립 판정이다. `image-07`은
아래 기본 gate와 추가 offline cold boot를 통과했다(§8). 새 이미지에서도 같은 계약을
다시 검사하며, 생성 성공이나 과거 이미지의 PASS로 해당 실행의 결과를 대신하지 않는다.

| Gate | 필요한 증거 | 거부할 반례 |
|---|---|---|
| 1. 완성 디스크 직접 부팅 | BIOS가 디스크의 bootloader·kernel·initramfs를 사용하고 UID 1000 CLI에 도달 | 설치 ISO나 host source 공유·로그인 후 설치/시작 주입에 의존 |
| 2. 같은 디스크 재부팅 | 서로 다른 Linux boot ID·CLI session과 같은 image ID/source; 첫 history 보존 | 두 번째 부팅 전 이미지 재생성·복원, 다른 image/source/run 혼합 |
| 3. 영속 설정과 기록 | 두 부팅의 config 대조 및 첫 archive의 hash·소유권·내용 보존 | 덮어쓰기·중복 boot ID·이전 기록 삭제 또는 부팅별 무관한 샘플 파일로 대체 |
| 4. source 무효화 | 새 boot live 경로와 이전 history 분리; 초기 service 상태를 실제 조회 | 저장된 ready·binding·descriptor를 새 실행으로 승격 |
| 5. 기본 인터넷 | 각 온라인 부팅에서 실제 DNS 응답과 인증서가 검증된 HTTPS 응답 | NIC 존재·고정 문구·TLS 검증 우회만으로 인터넷 PASS |
| 6. guest 정상 종료 | CLI 종료, 서비스 cleanup, archive 완료, 실제 VM exit 0와 host 강제 종료 없음 | 누락·잘린 terminal, cleanup 실패, timeout/host kill을 정상 poweroff로 표시 |
| 추가 offline | NIC/외부 연결 없는 별도 cold boot에서 제한 시간 안에 CLI와 정상 종료 | 네트워크 실패 때문에 무기한 기동 대기하거나 새 패키지 다운로드 요구 |

이미지 manifest에는 제품 image ID·profile·source hashes와 외부 runtime provenance를
기록한다. 실행별 증거에는 실제 VM 명령·버전, 디스크 식별과 크기, Linux boot ID,
CLI 사용자 ID, config hash, raw serial, console artifact, archive 목록·hash와 실제
VM 종료 결과가 필요하다. producer 기록과 외부 verdict는 별도 파일로 보존한다.
독립 verifier는 기존 CLI·boot·service 계약을 재사용하면서 두 부팅의 연속성과 archive
대조를 추가한다. 현재 소스 대조와 보존된 과거 소스의 재생을 혼합하지 않는다.

fixture 검사, 이미지 생성 성공, BIOS 부팅 성공, 실제 인터넷, 두 번째 cold boot,
독립 재검증을 각각 보고한다. 기존 `backend-02`의 모델 검증이나 `cell-01`의 관리 전이를
이번 기본 이미지의 모델·Cell 검증으로 승계하지 않는다.

## 7. Windows에서 생성·검증·사용하기

기본 진입점은 [Start-AiosImage.cmd](../../tools/hosted/Start-AiosImage.cmd)다.
설치된 QEMU와 Python의 `py.exe`, Windows의 `tar.exe`를 사용한다.
`.cmd`는 해당 PowerShell 프로세스에만 `-NoProfile -ExecutionPolicy Bypass`를 적용한다.
시스템이나 사용자 전체의 실행 정책을 변경할 필요는 없다.

이 문서의 로컬 검증 호스트에는 검증된 `image-07`을 가리키는 포인터와 기본 사용자 디스크가
준비돼 있다. 다시 Build할 필요 없이 파일을 실행하거나 저장소 루트에서 다음과 같이
시작한다. `exit`로 종료하면 다음 실행에서도 같은 사용자 디스크를 사용한다.
아래 `local-basic` 기본 위치와 legacy 포인터는 선택 기록이 없을 때의 경로다.
§9.8에서 basic 기본을 선택하면 이후 기본 Run은 저장한 선택을 우선한다.

```powershell
.\tools\hosted\Start-AiosImage.cmd
```

다른 환경에서 이미지를 새로 만들 때는 아래 Build·Smoke 절차를 사용한다. builder는
새 디렉터리만 허용하므로 기존 실패 실행이나 사용자 디스크를 삭제·덮어써서 재시도하지 않는다.

새 이미지 생성 경로를 로컬 디스크에 만들고 Linux 검사도 함께 요청한다.

```powershell
$imageDir = Join-Path $env:LOCALAPPDATA ('AIOS\operating-images\basic-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\tools\hosted\Start-AiosImage.cmd -Action Build -ImageDirectory $imageDir -GuestTests
if ($LASTEXITCODE -ne 0) { throw 'Image build failed; preserve this run and inspect its logs.' }
```

Build가 성공하면 같은 디스크로 운영 부팅을 검증한다. 현재 `Smoke`는 온라인 cold boot
두 번과 offline cold boot 한 번을 실행한 뒤 독립 verifier를 호출한다. 생성 단계의
성공만으로 이 검증을 생략하지 않는다.

```powershell
.\tools\hosted\Start-AiosImage.cmd -Action Smoke -ImageDirectory $imageDir
if ($LASTEXITCODE -ne 0) { throw 'Image acceptance failed; preserve the original evidence.' }
```

Smoke를 통과한 원본을 지정하여 대화형 CLI에 들어간다.

```powershell
.\tools\hosted\Start-AiosImage.cmd -Action Run -ImageDirectory $imageDir
```

`Run`은 `verdict.json`이 있는 검증 원본을 직접 변경하지 않는다. 첫 사용 때 독립 검증을
다시 통과한 원본을 같은 위치의 `<원본-디렉터리>-user`로 복제한다. 이후에는
`working-copy.json`의 원본 경로가 일치하는 사용자 디스크를 재사용하므로 설정과 history가
유지된다. 기존 디렉터리가 불완전하거나 다른 원본의 사용자 복사본이면 보존하고 거부한다.
매 부팅마다 다시 복사하거나 초기 상태로 되돌리지 않는다.

기본 사용자 이미지 위치는 `%LOCALAPPDATA%\AIOS\operating-images\local-basic`이다.
현재 Windows 호스트에서는 C:의 사용자 로컬 캐시 아래에 위치한다. 원본 생성·검증
디스크도 `%LOCALAPPDATA%\AIOS\operating-images\` 아래의 별도 실행 디렉터리에 둔다.
이 기본값은 공유 저장소에 대용량 디스크를 할당하는 경로와 분리된다.

선택 기록과 기본 사용자 디스크가 모두 없으면 launcher는 저장소의
`build/hosted-image/verified-image.json` 포인터를 확인한다. 이 파일의 schema 1과
절대 `image_directory`는 실제 검증을 마친 원본을 가리켜야 한다. 포인터가 있으면 원본을
다시 검증하고 기본 사용자 경로로 처음 한 번만 복제한다. 포인터를 작성했다고 이미지가
검증되는 것은 아니며, 아직 기본 디스크와 유효한 포인터가 없으면 먼저 위 Build·Smoke
절차 또는 명시적인 검증 원본 경로가 필요하다.

기존 사용자 디스크로 외부 네트워크 없이 실행하려면 `-Offline`을 추가한다. 사용자 CLI에서
`exit`하면 guest가 증거를 보존하고 종료하며, 단일 부팅의 독립 판정이 실패하면 launcher도
성공을 반환하지 않는다.

PowerShell 파일을 직접 호출해야 할 때의 동등한 진입점은 다음과 같다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\hosted\Start-AiosImage.ps1 -Action Run
```

## 8. 실제 로컬 검증과 후속

2026-09-08 `image-07`의 원본 `verdict.json`은 `PASS`다. 원본은 로컬 C: 캐시의
`%LOCALAPPDATA%\AIOS\operating-images\image-07`에 보존한다. 설치·운영 부팅의 raw
로그와 manifest, 부팅별 archive 및 외부 판정은 이 디렉터리 안에 있다. 설치 직후의
`build-result.json`은 이미지 생성만 판정하므로 그 안의 `operating_boot_verified=false`는
그대로 보존하며, 실제 운영 성공은 별도 최종 verdict로 읽는다.

| 항목 | 실제 결과 |
|---|---|
| 이미지 | `2dde4a7c-f92b-4895-af48-ce9977d2938e`; basic CLI, raw 4,294,967,296 bytes |
| 설치 기반 | Alpine 3.24.1; 실제 Linux `6.18.49-0-virt` |
| 설치 패키지 | Python `3.14.7-r1`, OpenRC `0.63.2-r0`, CA `20260611-r0`, syslinux `6.04_pre1-r19` |
| 제품 source | 이미지 35개: 기존 CLI 계약의 31개 + `aios-image-boot.py`, `aios_boot/{__init__,archive,runtime}.py` |
| Windows 이미지 검사 | 25개, 24.725초, 21 PASS·4 skip·실패 0; `build/hosted-image/windows-02.log` |
| 실제 Linux 이미지 검사 | 25개, 28.598초, 25 PASS·skip 0; `linux-tests/image-tests.log`, 실제 exit 0 |
| 온라인 부팅 | 같은 디스크로 두 번, 각각 CLI 12명령·DNS 4주소·HTTPS 200/559 bytes·인증서 검증 |
| 오프라인 부팅 | 세 번째 cold boot, CLI 10명령과 서비스 실행·정상 종료 |
| 사용자·종료 | 매 부팅 UID 1000, 새 CONSOLE_RUNTIME 수명·generation 1의 실제 종료/STOPPED, archive 완료, VM exit 0·host kill 없음 |
| AI 범위 | `main_started=false`, `model_bundled=false`; AI readiness·추론 또는 부팅 간 canonical continuity를 만들지 않음 |

| 실행 | Linux boot ID | CLI session ID |
|---|---|---|
| boot-01 online | `88b47c9b-9eb9-4fab-aa27-c0e1f15daae2` | `cf379a61-9190-4b43-8672-59f915d0dbe4` |
| boot-02 online | `0fcecf15-3769-47b7-bce9-607f7f19421d` | `5260099c-26ad-4ce4-bef0-f891db9da28a` |
| boot-03 offline | `2c118acd-45eb-42f2-83ce-671a50748afd` | `fd875c68-6220-40e0-9469-a02f12c95567` |

세 boot의 config SHA-256은
`b876891ae12c4c9fff73ad53bd427628b4786b40bbe3d3629561e038fd49a517`로 같다.
두 번째는 첫 archive를, 세 번째는 앞선 두 archive를 hash로 대조했다. raw 디스크의
부팅 전후 hash 연결도 검증했으며, 세 번째 종료 뒤 원본 디스크 SHA-256은
`2cf3787fff5993d63b91a97b37ed5e5ecac63f70ebf872f47ed2301d1f2c1cd2`다.
이는 해당 사용 시점의 디스크 값이며 새 사용자의 쓰기 뒤에도 같아야 한다는 계약이 아니다.

기본 `.cmd` 실행은 `build/hosted-image/verified-image.json`의 원본을 검증해
`%LOCALAPPDATA%\AIOS\operating-images\local-basic`으로 처음 한 번 복제했다.
실제 대화형 경로로 `about`, `net status`, `exit`를 실행한 단일 부팅의 독립 verdict도
`PASS`다. 이 실행의 boot ID는 `c16b8e1e-5495-4e7b-99d9-ea36b15e25b5`, session은
`bd60cf6e-02cf-4e86-8da7-59d5e68d15dd`이며 UID 1000·VM exit 0·강제 종료 없음과
디스크 bytes 대조를 확인했다. service는 시작하지 않았고 DNS/HTTPS도 요청하지 않았다.
이 단일 사용 판정의 `prior_history_replayed=false`를 세 원본 boot의 history 검증과
혼합하지 않는다. 원본·초기 복사본 hash가 같은 `working-copy.json`을 보존하며 이후 Run은
이 사용자 디스크를 재사용한다.

`current-source-replay.json`의 별도 독립 재검증도 `PASS`다. 이미지의 보존 runtime과
설치 manifest의 source 35개가 이 재검증 수행 시점의 checkout과 일치하며, 원본 증거 281개와 원래 verdict의
SHA-256 `fe9aa143ddfaab2b51172df31ac18b519ffd4e6c8e1d93ad511b1f4b61767e40`을
변경하지 않았음을 확인했다. 최종 디스크 bytes 대조와 원본 verdict 재구성도 통과했다.

종료 뒤 별도 `filesystem-audit.json`은 쓰기 없는 ext4 superblock 감사 `PASS`다.
모든 VM writer가 종료한 뒤 MBR과 두 partition의 superblock만 총 2,560 bytes 읽었고,
각각 `s_state=1`, recovery/orphan-present flag 없음, `s_last_orphan=0`, `s_error_count=0`,
CRC32C 일치를 확인했다. 감사 전후 디스크 크기와 mtime은 같고 parser 반례 6개도
실제 디스크를 바꾸지 않은 fixture로 검사했다. 이는 정상 종료 뒤의 제한된
`SUPPORTING` 관측이다. 전체 filesystem 검사·수리, 전원 손실 내구성 또는 native
파일시스템 구현 증거를 뜻하지 않는다.

초기 실패·진단 실행은 삭제하거나 최종 PASS로 덮어쓰지 않는다.

| 실행 | 현재까지 확인한 결과 |
|---|---|
| image-01 | 공유 저장소의 느린 디스크 할당 단계에서 중단; VM 실행 증거 없음 |
| image-02 | raw drive의 `serial` 인자 위치를 QEMU가 거부하여 guest 부팅 전 실패; device property로 수정 |
| image-03 | filesystem 생성 뒤 `/dev/vda2` mount가 `Invalid argument`로 실패; installer 결과 FAIL, host 강제 종료로 수락 불가 |
| image-04 | 후속 설치 과정 exit 1; installer/build FAIL과 host 강제 종료 기록 보존 |
| image-05 | 설치 대상의 `/usr/local/sbin` 누락으로 boot hook 복사 실패; installer/build FAIL 보존 |
| image-06 | 이미지 생성 PASS 뒤 운영 부팅에서 `worker-evidence` 실패; CLI session·archive 미완료이므로 정상 사용 acceptance 불가 |

최종 image-07은 위 실행을 재사용하거나 판정을 바꾼 결과가 아니라 새 이미지와 세 번의
완결된 운영 증거다. 이전 `backend-02`의 실제 모델·backend 교체·명시적 복구 검증도
별도 역사로 보존하며, 모델 없는 기본 이미지의 기능으로 승계하지 않는다.

로컬 acceptance를 통과해도 범위는 기본 운영 이미지 `PARTIAL`이다. UEFI·Secure Boot,
USB/실기기 설치, 범용 하드웨어 지원, primary Linux exact-reference qualification,
전원 손실·CLI 소실·재부팅 이후 복구, 이미지 내용 업데이트·되돌리기, 부팅 간 canonical 관리 상태 유지,
영속 AI 기억, 전체 H2/H3·ownership·principal·resource apply는 별도 후속 작업이다.
모델 포함 profile은 §9의 `PARTIAL` 구현으로 이어졌으며 v0.6 `model-image-04`와
현재 v0.7 `model-image-05`의 실제 모델·online/offline acceptance를 각각 통과했다.
기본 이미지의 운영 범위를 넓힐 때 해당 producer·독립 verifier·
negative test와 상위 README·PROJECT·CLAUDE·handoff·문서 색인을 함께 갱신한다.

현재 checkout의 v0.7은 동일 CLI가 미리 확보한 child pidfd로 supervisor 소실 뒤
`backend recover`를 수행하고 별도 `RECOVERED` 증거를 남긴다. CLI source 31개의 실제
Linux·모델 기록은 별도 독립 재검증을 통과했다(`PARTIAL`; 원본 FAIL 보존).
복구 직후 `exit`도 backend 종료를 `RECOVERED`로 보존하고, root는 archive에 복사한 receipt·
이전 run hash·동일 UID 1000 worker를 대조한다. 정상 `STOPPED`로 치환하지 않는다.
세부 계약은 [backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)를 따른다. 아래
`model-image-04`와 기존 사용자 디스크는 v0.6의 검증된 보존본이며 이 새 복구 구현의 실제 증거가 아니다.
현재 v0.7/source 35개의 정상 모델 이미지 부팅은 `model-image-05`에서 별도로 통과했다(§9.7).
supervisor 장애 뒤 복구 직후 `exit`하는 경로도 별도 장애 복사본 02에서 실제 통과했다.
정상 원본과 다른 image ID의 시험 복사본에 별도 준비·주입 도구와 독립 verifier를 사용하며,
정상 이미지의 PASS를 장애 경로로 승계하지 않는다. 첫 장애 복사본 01의 원본 FAIL은 보존한다.
기존 v0.6 사용자 디스크나 포인터는 갱신하지 않았다.

## 9. 로컬 모델 이미지 — `SUPPORTING/PARTIAL`

§8의 기본 이미지와 별도 개발 VM의 `backend-02`가 제공한 실행 경로를 연결하는
모델 profile을 구현했다. 설치 자산·manifest·부팅 preflight·명시적 CLI 실행 선택과
독립 verifier가 존재한다. 현재 v0.7 `model-image-05`는 같은 완성 디스크의 online·offline 부팅,
실제 질문·재결속·정상 종료와 최종 독립 판정을 통과했다. 앞선 실패 이미지들의 설치
검사·fixture 결과와 최종 이미지의 실제 모델 질문 성공을 구분한다.

검증한 흐름은 준비된 디스크로 부팅하고 AIOS CLI 명령으로 모델 backend와 MAIN을
시작한 뒤 명시적으로 결속된 MAIN에 질문하는 것이다. 모델의 텍스트 응답은 명령 실행
권한이 아니다. 실패와 보존 위치는 §9.5, v0.6 실제 결과는 §9.6, 현재 v0.7 결과는 §9.7에 기록한다.

방향은 기존 `DIRECT/PARTIAL` MAIN 결속을 반복 부팅 사용자 환경에서 사용하는
`SUPPORTING`이다. 기존 정본의 Linux driver substrate와 독립 AIOS identity 경계를
따르며 native process/storage 확장, 전체 H2/H3 또는 영속 AI 기억을 선행조건이나
이번 완료 범위로 추가하지 않는다. 기본 이미지의 성공 판정과 사용자 디스크는 유지한다.

### 9.1 구현한 profile과 설치 계약

| 항목 | 모델 profile의 고정 계약 |
|---|---|
| 부팅 설정 | schema 2, `profile=local-model`, `model_config=/etc/aios/model.json` |
| 이미지 manifest | schema 2, `profile=local-model-cli`; 제품 runtime source 목록은 기존 35개 유지 |
| 기존 호환성 | basic boot config schema 1·`profile=basic`·`model_config=null`과 basic image schema 1 유지 |
| 가상 하드웨어 | 기존 BIOS/QEMU 경로, CPU 2개, RAM 3,072 MiB, raw 4 GiB 디스크 |
| 외부 설치 자산 | `/opt/aios/inference` 아래 기존 pin의 backend 실행 파일과 GGUF 모델을 root 소유로 설치; UID 1000 사용자가 변경할 수 없음 |
| 모델 설정 | `/etc/aios/model.json`의 로컬 설치 경로·model/backend/provenance hash와 loopback endpoint를 고정 |
| 출처 증거 | 원본 provenance receipt와 공식 metadata·라이선스·문서 11개, 설치된 자산과 증거 파일의 hash를 설치 receipt로 보존 |
| 사용자 상태 | 기존 boot ID별 fresh live 경로와 종료 후 history archive 재사용; 자산 bytes는 archive 밖에 둠 |
| 실행 선택 | launcher의 `-Agent`로 별도 모델 profile 선택; 기본 basic 선택과 `local-basic` 유지 |
| 사용자 디스크 | v0.6 `local-model`과 v0.7 `model-image-05-user`의 당시 실행 PASS를 보존; 명시적 기본 선택·되돌리기는 §9.8의 별도 host 계약 |
| 운영 의존성 | 완성 디스크의 설치 자산만 사용; operating boot 중 모델·패키지 다운로드, host 모델 서버·source 공유 없음 |

모델은 `Qwen/Qwen3-0.6B-GGUF`의 `Qwen3-0.6B-Q8_0.gguf`다. revision
`23749fefcc72300e3a2ad315e1317431b06b590a`, 크기 639,446,688 bytes, SHA-256
`9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`을 유지한다.
backend는 llamafile `0.10.5-thin`, revision
`486e6c5f9356eae50b851b07517bfae1f2420193`, 크기 42,328,074 bytes, SHA-256
`55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611`이다.
그 backend의 llama.cpp pin은 `c588c4f47683e73ad2d69f50480bec6cc85fd0f7`이다.
기존 [prepare_inference.py](../../tools/hosted/prepare_inference.py)의 pin·출처 검사와
[backend 가이드](aios_backend_lifecycle_guide_ko.md)의 실제 실행 계약을 재사용한다.

구현은 [qemu_image.py](../../tools/hosted/qemu_image.py)의 model build·disk-only runner,
[image_finalize.py](../../tools/hosted/image_finalize.py)의 설치 bytes 검증,
[aios_boot/runtime.py](../../hosted/linux/aios_boot/runtime.py)의 부팅 검사,
[verify_image.py](../../tools/hosted/verify_image.py)의 독립 판정이 담당한다. 설치 receipt는
기본 8개 파일에 모델 설정·provenance·설치 자산 검사와 공식 출처 자료 11개를 더한 정확한
22개 파일을 기록한다. 두 대용량 자산은 root UID/GID 0·mode `0444`로 설치한다.

대용량 GGUF·실행 파일은 이미지 설치 자산이다. 파일당 2 MiB·합계 16 MiB의 boot
archive에 복사하지 않으며 원본 bytes와 provenance를 hash로 연결한다. 부팅 때마다
자산을 다시 다운로드하거나 업데이트하지 않는다. 설치된 runtime source·두 자산·설정·
provenance가 일치하지 않으면 모델 준비나 실제 요청을 성공으로 기록할 수 없다.
root preflight는 고정 경로의 일반 파일·소유권·권한·크기를 검사하고 두 자산 전체를
다시 읽어 SHA-256을 대조한다. `boot-read-complete`와 해당 boot ID를 가진
`model_integrity`를 BOOT·RESULT에 남기며 설치 때의 receipt를 현재 부팅의 읽기
증거로 재사용하지 않는다. 실패 시 basic profile로 자동 전환하지 않는다.
로컬 이미지 구성은 `repository_import=false`, `redistribution_approved=false`를 유지한다.

### 9.2 명시적 사용자 실행과 실패 경계

이미지를 준비한 뒤 사용하는 기본 진입점은 다음과 같다. §9.8의 명시적 선택 기록이
있으면 그 사용자 사본을 실행하고, 없으면 기존 `local-model`과 최초 복제용 포인터를
사용한다. 로컬 검증 호스트는 실제 0.7·0.6 기본 부팅과 최종 0.7 재선택을 통과했으며
현재 모델 기본 선택은 0.7이다. 이 호스트의 legacy 포인터는 v0.6 `model-image-04`를
계속 가리킨다. 이전 v0.6 기본 실행은 §9.6, 전용 로컬 실행기와 당시 사용자 사본의
세 명령 실행은 §9.7의 역사적 증거다. 새 checkout에는 이 포인터와 디스크가 없다.

```powershell
.\tools\hosted\Start-AiosImage.cmd -Agent
```

선택 기록이 없을 때 모델 profile은 `%LOCALAPPDATA%\AIOS\operating-images\local-model`과
`build/hosted-image/verified-model-image.json` 포인터를 사용한다. 포인터는
실제 model Smoke와 독립 판정을 통과한 원본이 생긴 뒤에만 작성한다. 현재 포인터는
로컬 검증 호스트에서 보존한 `model-image-04`를 가리킨다. 기본
`local-basic` 디스크·기본 실행과 실패한 모델 이미지 원본을 바꾸지 않는다.

새 모델 이미지의 생성·검증 절차는 다음과 같다. 두 번째 명령은 같은 완성 디스크로
online·offline cold boot를 각각 한 번 수행하며, 어느 부팅이든 실패하면 중단한다.
검증 실패 디렉터리를 지우거나 재사용하지 않는다.

```powershell
$modelImageDir = Join-Path $env:LOCALAPPDATA ('AIOS\operating-images\model-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\tools\hosted\Start-AiosImage.cmd -Action Build -Agent -GuestTests -ImageDirectory $modelImageDir
if ($LASTEXITCODE -ne 0) { throw 'Model image build failed; preserve this run.' }
.\tools\hosted\Start-AiosImage.cmd -Action Smoke -Agent -ImageDirectory $modelImageDir
if ($LASTEXITCODE -ne 0) { throw 'Model image acceptance failed; preserve this run.' }
.\tools\hosted\Start-AiosImage.cmd -Action Run -Agent -ImageDirectory $modelImageDir
```

이 Run은 검증 원본에서 별도 사용자 사본을 만들거나 일치하는 기존 사본을 사용한다.
기본 선택도 바꾸려면 §9.8의 `Select -Agent -ImageDirectory $modelImageDir`를 명시한다.

부팅 뒤 `about`으로 0.7을 확인하고, `help`·하드웨어·인터넷을 조회할 수 있다.
아래는 현재 명령 계약의 사용 예이며 실제 사용자 세 명령 로그의 인용은 아니다.

```text
aios> about
aios> help
aios> hardware all
aios> net status
aios> resolve example.com
aios> fetch https://example.com/
```

모델 질문까지 사용하려면 다음 순서로 진행한다. 모델을 시작하지 않아도 `exit`로
정상 종료할 수 있다.

```text
aios> backend start
aios> agent start
aios> room discover
aios> room bind
aios> ask What is the capital of France?
aios> exit
```

이미지 부팅만으로 backend·MAIN·binding을 자동 생성하지 않는다. CLI의 명시적
`backend start`는 UID 1000의 제품 supervisor와 모델 자식을 시작하고, `agent start`는
현재 backend 실행 증거를 고정한 MAIN과 실제 warmup을 만든다. `room discover/bind`를
통과한 현재 BOUND 상태에서만 `ask`가 가능하다. 상태 표시에서 모델 파일 설치 완료,
backend 준비, MAIN warmup 성공과 canonical 결속을 구분한다. `/health` 성공만으로
MAIN readiness·사용자 질문 성공을 대신하지 않는다.

모델 로딩·CPU 추론에는 시간이 걸린다는 것을 표시한다. 기존 profile의 420초 요청
상한과 짧은 응답 계약을 유지하며, 기다리는 동안 정적 성공 문구를 내지 않는다.
hash 불일치·모델 시작 실패·warmup 실패·backend 교체를 명확한 오류로 돌려주고
일반 CLI의 상태·인터넷 조회와 종료 경로를 보존한다. 임의 backend 인수·모델 교체,
자동 재결속이나 resource action을 추가하지 않는다.

새 cold boot는 새 Linux boot ID, session, source·authority instance와 fresh live
상태에서 시작한다. 이전 history가 남아도 이전 PID·descriptor·binding·ready 상태를
되살리지 않는다. 같은 canonical 숫자 ID가 등장해도 이전 authority의 동일 실행으로
해석하지 않는다. `exit`에서는 MAIN·모델 supervisor/자식·CONSOLE_RUNTIME의 실제 종료,
archive, 정상 `/sbin/poweroff`와 외부 VM 결과까지 확인한다.

### 9.3 실제 acceptance gate

| Gate | 필요한 실제 결과 |
|---|---|
| 설치 자산·직접 부팅 | 현재 제품 source 35개·고정 모델/backend·출처 자료의 설치 hash 일치; 디스크 자체 BIOS 부팅과 UID 1000 CLI; host setup/share/server 의존 없음 |
| 첫 online cold boot | 명시적 backend·MAIN 시작, 실제 warmup·discover/bind·질문 응답; 원시 request/response와 backend execution·binding 일치; DNS와 인증서 검증 HTTPS |
| 같은 디스크의 두 번째 offline cold boot | 설치·다운로드 없이 새 runtime identity로 다시 backend·MAIN 시작; 이전 config/history 보존과 새 live 상태 확인; 실제 모델 질문 성공 |
| 두 번째 부팅의 관리 전이 | Cell 비활성화 뒤 요청 거부, 재활성화만으로 준비/결속 복원 금지, 명시적 발견·재결속 뒤 실제 추론 성공; MAIN/backend 수명과 관리 전이를 구분 |
| 정상 종료·독립 판정 | 두 부팅 각각의 실제 서비스·자식 종료, archive와 정상 poweroff, VM exit 0·host kill 없음; 같은 디스크 hash 연결과 history·source·receipt의 독립 검증 |
| 실패 거부·과거 증거 보존 | missing/변조 자산·잘못된 profile·ready/binding 재사용·관계가 다른 receipt·종료 누락·잘린 archive를 거부; basic schema 1 및 과거 model/CLI 증거 재생 유지 |

fixture 검사와 실제 모델 이미지 verdict를 각각 기록한다. 완료 시 새 실행 디렉터리·
image ID·boot/session/authority·backend identity·실제 질문/시간·종료·외부 verdict를
남긴다. `image-07`이나 `backend-02`의 PASS를 새로운 이미지의 PASS로 복사하지 않는다.
정상 종료 뒤 filesystem 관측도 모델 실행·독립 판정과 별도의 증거로 유지한다.

### 9.4 최신 공식 자료와 기존 pin 비교

2026-09-08 공식 API를 조회했을 때 llamafile latest는 `0.10.5`이고 Qwen GGUF의
현재 revision은 위 기존 pin과 같았다. llamafile release는 llama.cpp `b10103/c588c4f`
연결을 기록한다. 현재 릴리스의 quickstart는 thin이 같은 CPU 실행 코드에서 사전
GPU 라이브러리만 제외한 파일이라고 설명한다. 이번 CPU-only profile은 기존
`--gpu disable`, 로컬 `-m`, loopback 서버의 실행 인자를 재사용하며 GPU toolchain을
운영 의존성에 넣지 않는다.
[공식 release](https://github.com/mozilla-ai/llamafile/releases/tag/0.10.5),
[공식 latest release API](https://api.github.com/repos/mozilla-ai/llamafile/releases/latest),
[고정 revision의 thin 설명](https://raw.githubusercontent.com/mozilla-ai/llamafile/486e6c5f9356eae50b851b07517bfae1f2420193/docs/quickstart.md)

Qwen 공식 GGUF는 Q8_0·Apache-2.0 출처를 제공하고 `/no_think` 전환을 설명한다.
공식 실행 문서는 GGUF가 tokenizer 등 실행 정보를 포함하며 로컬 `-m`으로 실행할 수
있음을 명시한다. 이미 변환된 공식 GGUF와 기존 backend를 사용하므로 모델 변환용
Transformers·PyTorch 설치를 operating boot에 추가하지 않는다. 현재 AIOS의 고정
ChatML·`temperature=0`·짧은 토큰 상한은 검증용 profile이며 최신 Qwen 카드의 일반
대화 sampling 권장값을 모두 구현했다거나 대화 품질·장기 기억을 검증했다는 뜻이 아니다.
[공식 Qwen 모델 카드](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF),
[공식 모델 revision API](https://huggingface.co/api/models/Qwen/Qwen3-0.6B-GGUF),
[공식 Qwen llama.cpp 실행 문서](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html)

llama.cpp의 최신 master는 모델 loading 관련 인자도 계속 바뀐다. 이번 기준은
기존 고정 revision의 로컬 모델·`/health`·`/completion` 계약이다. 최신 master의
`--load-mode` 같은 인자를 이 pin에 임의 적용하지 않는다. 고정 revision에도
`--offline`이 있지만, offline cold boot의 실제 질문 성공을 생략하는 근거가 되지 않는다.
[고정 server 문서](https://raw.githubusercontent.com/ggml-org/llama.cpp/c588c4f47683e73ad2d69f50480bec6cc85fd0f7/tools/server/README.md),
[최신 server 문서 비교](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)

Alpine의 공식 설치 문서는 mounted root에 system 설치·bootloader를 준비하는 기존
경로를 제공한다. 모델 이미지는 이 설치 경로와 기본 이미지의 Python·CA·OpenRC를
재사용하되, 새로 설치한 실제 패키지 버전·kernel/initramfs·설치 파일의 hash를 새 실행의
receipt에 기록한다. Alpine 3.24.1 ISO pin이 이후 저장소의 모든 패키지 bytes까지
고정하거나 primary exact-reference qualification을 완료한다는 뜻은 아니다.
[공식 Alpine 설치 문서](https://docs.alpinelinux.org/user-handbook/0.1a/Installing/manual.html),
[Alpine 3.24.1 릴리스](https://www.alpinelinux.org/posts/Alpine-3.24.1-released.html)

#### 9.4.1 Alpine 설치·serial·정상 종료 계약 재검토 — 2026-09-08

공식 릴리스 표는 `v3.24`를 지원 중인 branch로 표시하며 지원 종료일은 2028-06-01이다.
3.24.1은 이 branch의 공식 maintenance release다. 이번 검토에서는 기존 ISO URL과
SHA-256 pin을 유지했다. 이는 설치 입력을 고정하는 판단이며 AIOS 관리 정체성이나
native qualification을 Alpine 버전으로 정의하는 근거가 아니다.
[공식 지원 branch](https://www.alpinelinux.org/releases/),
[3.24.1 릴리스 기록](https://www.alpinelinux.org/posts/Alpine-3.24.1-released.html)

현재 공식 `3.24-stable`의 alpine-conf recipe는 `3.22.0-r0`이다. 해당 `setup-disk`
소스와 공식 설치 handbook은 이미 mount한 root에 system 설치를 수행하는 경로를
제공한다. 현재 builder는 새 4 GiB target의 serial·크기·쓰기 가능 여부를 확인하고,
DOS partition table과 ext4 `/boot`·root를 준비한 뒤
`setup-disk -m sys -k virt -B syslinux /mnt/target`을 호출한다. 이 검토 범위는
기존 QEMU x86_64 BIOS profile이며 UEFI·Secure Boot·USB/실기기 설치를 검증하지 않는다.
[공식 alpine-conf recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/alpine-conf/APKBUILD),
[고정 버전 setup-disk 소스](https://raw.githubusercontent.com/alpinelinux/alpine-conf/3.22.0/setup-disk.in),
[공식 mounted-root 설치 설명](https://docs.alpinelinux.org/user-handbook/0.1a/Installing/manual.html)

serial 연결은 bootloader·kernel·사용자 터미널의 세 단계다. `setup-disk` 3.22.0은
`KERNELOPTS`의 `console=ttyS0,115200`을 읽고 syslinux의 serial port와 baud 설정을
생성한다. AIOS 설치 hook은 kernel 인자를 유지하고 `/etc/inittab`의 getty 항목을
제거한 뒤 `ttyS0::once:/usr/local/sbin/aios-start-system`을 넣는다. 실제 디스크
부팅에서 고유 CLI까지 도달했다는 판정은 각 이미지의 serial·session 증거로 확인한다.
[setup-disk의 serial 설정](https://raw.githubusercontent.com/alpinelinux/alpine-conf/3.22.0/setup-disk.in)

공식 `3.24-stable` OpenRC recipe는 `0.63.2-r0`이며 Alpine 기본 init을 OpenRC 자체
init으로 바꾸지 않는다. 공식 inittab은 `sysinit → boot → default`와 별도
`openrc shutdown` action을 연결한다. OpenRC의 `mount-ro`는 쓰기를 sync하고 남은
파일시스템을 읽기 전용으로 정리한다. AIOS 운영 경로는 이 설치된 종료 순서를 위해
옵션 없는 `/sbin/poweroff`만 요청한다. 요청 성공만으로 종료를 판정하지 않고 서비스
cleanup·archive·외부 VM exit 0·host kill 없음까지 요구한다.
[공식 OpenRC recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/openrc/APKBUILD),
[Alpine 기본 inittab](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/alpine-baselayout/inittab),
[OpenRC 0.63.2 mount-ro](https://raw.githubusercontent.com/OpenRC/openrc/0.63.2/init.d/mount-ro.in)

임시 설치 VM은 target의 `/boot`·root를 sync·unmount한 뒤 `poweroff -f`로 끝낸다.
이 builder 결과는 완성 디스크에서 실행하는 운영 OS의 정상 종료 증거와 구분한다.
또한 설치 시 `v3.24/main`에서 Python·CA·kernel 등 패키지를 받아 설치하므로 ISO pin이
나중의 package resolution까지 고정하지 않는다. 실제 설치 버전과 kernel/initramfs·
bootloader·inittab·hook hash는 새 설치 receipt가 소유한다. 재현 가능한 package
snapshot, 범용 하드웨어, 이미지 내용 업데이트·되돌리기 및 primary exact-reference qualification은
여전히 별도 후속이다. 이번 검토에서 wiki의 세 페이지는 조회가 차단되어 내용을 새로
확인하지 못했으며, 위 공식 handbook과 버전별 source로 설치·부팅 계약을 대조했다.

### 9.5 보존한 설치·실패 기록

2026-09-08 최종 `model-image-04` 이전 실행들은 아래 실패 기록으로 남긴다. 각 원본 디렉터리는
`%LOCALAPPDATA%\AIOS\operating-images\` 아래에 보존한다. 설치·부팅의 실패 기록을
새 실행의 성공으로 바꾸지 않으며, 후속 수정은 새 이미지에서 검증한다.

| 실행 | 실제 결과와 해석 |
|---|---|
| `model-image-01` | 모델 입력 디스크 추가 뒤 암묵적인 QEMU 장치 순서가 바뀌어 read-only 공유 입력의 `/dev/vdb1` mount가 실패했다. 설치 target 변경 전 실패이며 installer exit 1·host kill·정상 종료 없음이다. target/share/model drive를 명시적 device 순서와 serial로 고정하고 새 실행으로 진행했다. |
| `model-image-02` 설치 | `build-result.json`은 `PASS/local_image_built`, `operating_boot_verified=false`다. installer exit 0·host kill 없음·정상 종료를 기록했다. 설치 Linux에서 이미지 검사 46개가 65.574초에 전부 PASS·skip 0이었고, 모델 두 자산의 실제 bytes·크기·SHA-256·root 소유·`0444` 권한을 확인했다. |
| `model-image-02` 첫 운영 부팅 | 완성 디스크만으로 BIOS 부팅하고 root 자산 전체 읽기 검사와 UID 1000 CLI까지 도달했다. `backend start`가 `backend-listener`로 실패했고 뒤이은 MAIN·질문·결속은 `state-io`로 실패했다. 실제 warmup·사용자 추론·결속 성공은 없다. |
| 같은 부팅의 인터넷·종료 | DNS 4주소와 인증서 검증 HTTPS 200/559 bytes, CONSOLE_RUNTIME 정상 종료, archive 완료, VM exit 0·host kill 없음은 관측했다. 그러나 MAIN/backend cleanup 오류로 worker exit 1·`cleanup_ok=false`, root `FAIL/worker-failed`이므로 전체 acceptance는 FAIL이다. 두 번째 offline boot는 실행하지 않았다. |
| `model-image-03` | 설치 자산 준비 뒤 설치용 Linux에서 검사 51개를 실행했으나 78.465초에 50 PASS·1 ERROR였다. 새 긴 backend 기록 경로 검사의 loopback bind가 `OSError: [Errno 99] Address not available`로 실패했다. 임시 설치 환경의 `lo` 미활성화가 원인이며 builder에 loopback 활성화를 추가했다. 전체 build/installer FAIL·exit 1·host kill·정상 종료 없음이고 운영 부팅은 실행하지 않았다. 설치 단계가 진행됐다는 사실을 build PASS로 해석하지 않는다. |

`model-image-02`의 image ID는 `5afe1080-17fb-4670-9135-efa9695feec4`, 첫 Linux boot ID는
`a2a7b09a-4631-4ade-869e-fdbb0d509d7b`, CLI session ID는
`a53afe2e-a763-4cae-b3aa-4d2e527af325`다. 설치 원본은 `build-result.json`,
`installer-result.json`, `linux-tests/image-tests.log`에, 운영 원본은
`boots/boot-01/archive`와 `boots/boot-01/vm-result.json`에 있다. VM 종료 기록의
`runner_error="guest session or cleanup failed"`를 정상 VM exit와 함께 읽어야 한다.

시작 실패의 진단은 `build/hosted-model-image/model-image-02-diagnosis.json`에 보존했다.
실제 socket은 짧은 별도 backend 경로에 놓이지만 `BackendAttester`가 긴 run 기록
디렉터리에도 control socket 길이 검사를 적용했다. 기록 디렉터리 뒤 가상의
`control.sock`을 붙인 길이는 115 bytes로 계약의 104 bytes 미만 제한을 넘었고,
실제 `backend.sock` 경로는 73 bytes였다. 기록 파일 경로와 실제 socket 경로의
검사 책임을 구분하도록 수정하고 긴 boot/run 경로 회귀 검사를 추가했다. 수정한
당시 v0.6 소스의 실제 모델 이미지 acceptance는 §9.6의 새 실행에서 별도로 통과했다.
이 진단이나 후속 PASS가 과거 FAIL의 판정을 바꾸지 않는다.

별도 `build/hosted-model-image/basic-compatibility.json`은 `image-07`을 보존된 runtime
source로 독립 재생한 `PASS`다. 원본 verdict hash가 전후 동일함을 확인했다. 이는
기본 schema 1과 과거 증거를 유지한 검사이며 변경된 현재 모델 source를 `image-07`이
검증했다는 뜻은 아니다. 최종 사용자용 모델 포인터는 §9.3의 두 cold boot,
실제 추론·재결속·정상 cleanup과 독립 판정을 모두 통과한 `model-image-04`를 가리킨다.

### 9.6 v0.6 모델 이미지의 보존된 실제 acceptance — 2026-09-08

`%LOCALAPPDATA%\AIOS\operating-images\model-image-04`의 원본 `verdict.json`은
schema 2·`profile=local-model-cli`·`PASS`다. `Start-AiosImage.cmd -Action Smoke -Agent`
실행은 exit 0으로 끝났다. 설치부터 두 번의 운영 부팅까지 이 디렉터리의 원본 receipt·
raw serial·부팅별 archive·VM 결과를 보존한다. 설치의 `build-result.json`에 있는
`operating_boot_verified=false`는 생성 단계의 기록이며 최종 verdict로 덮어쓰지 않는다.

| 항목 | 실제 결과 |
|---|---|
| 이미지 | `c86ff2d4-6a3b-4ecf-9fde-1ae3d0cc75fb`; local-model CLI, raw 4,294,967,296 bytes |
| 제품 source | 이미지 35개, CLI v0.6/session schema 6/source 31개와 boot module 4개; 긴 backend 기록 경로 수정 포함 |
| 설치 | build PASS; installer exit 0·host kill 없음·정상 종료; 설치된 고정 모델/backend의 크기·SHA-256·root 소유·`0444` 권한 검사 |
| 실제 Linux 이미지 검사 | 51개, 80.527초, 51 PASS·skip 0; `linux-tests/image-tests.log` |
| 두 운영 부팅 | 같은 디스크의 첫 online 20명령·두 번째 offline 25명령, 총 45명령; 모두 UID 1000 |
| 모델 요청 | 실제 warmup 2회와 사용자 질문 2회; 설치 디스크의 자산 사용, 운영 중 다운로드·host 모델 서버·source 공유 없음 |
| 부팅별 자산 검사 | 두 부팅에서 root의 전체 자산 읽기와 `boot-read-complete` 확인; 설치·config·provenance·source 및 디스크 hash 연결 대조 |
| 인터넷 | online 부팅에서 DNS 4주소, 인증서 검증 HTTPS 200/559 bytes; offline 부팅은 외부 NIC 없이 로컬 모델 질문 성공 |
| 종료 | 각 부팅의 MAIN·backend supervisor/자식·CONSOLE_RUNTIME 종료, worker exit 0·cleanup/archive PASS; 정상 poweroff·VM exit 0·host kill 없음 |
| 판정 범위 | `warmup_requests=2`, `user_requests=2`, `model_bytes_verified=true`, `offline_boot_verified=true`, `stale_rebind_verified=true` |

| 실행 출처 | boot-01 online | boot-02 offline |
|---|---|---|
| Linux boot ID | `fb147593-dbcb-47dc-b8f7-c15aaf0377aa` | `1e43b10e-38dd-440b-a9ef-6854fe707884` |
| CLI session ID | `2ddb4e81-e32d-413a-9684-2f98eade9444` | `2ec4c0e2-2a0f-469c-86be-eff3da694d29` |
| MAIN instance | `0ebcdb5a-99b1-4608-b86e-7c3c84d1e32f` | `a42ff84c-a2b0-41ec-9205-9592efe2c05b` |
| Hosted authority instance | `bb437000-271d-49fb-a8b1-d775e7bd372f` | `91532266-46d3-47f3-973e-6ce4b839bb69` |
| Backend instance | `881b663e-6dc5-48fb-a6d3-c56301fb9d18` | `5350fd2c-6492-4771-a99a-86effc54269f` |
| Backend descriptor source instance | `194e80d8-e787-40c8-85e6-a7481b355bc7` | `e648b134-b95d-4174-ae18-04e87e523765` |

두 cold boot는 각각 MAIN/backend 초기 `ABSENT`에서 명시적으로 시작했다. 설치 설정과
첫 history는 보존하면서 source·authority·session은 새 값으로 생성했다. 각 부팅에서
발견 전 질문은 `not-discovered`로 거부됐다. 이번 검증은 명시적 discover/bind 후
`resources link`로 선택적 자원 관측을 연결하고 질문했다. 자원 관측 연결 자체가 모든
질문의 필수조건인 것은 아니다. 두 번째 부팅에서는 Cell generation 1→2→3과 함께 binding trust가 STALE이
됐으며 재활성화만으로 복구하지 않았다. `ask`의 `stale`와 자원 관계의
`resource-relation-stale`을 거부한 뒤 명시적 discover/reconcile/link로 binding generation과
resource relation generation을 각각 1→2로 바꾸고 실제 질문에 성공했다. 이 관리 전이
동안 MAIN/backend 실행을 유지했으며 부팅 간 canonical authority 연속성을 만들지 않았다.

| 실제 요청 | 모델 응답과 소요 시간 |
|---|---|
| 첫 부팅 warmup | 126.493초 |
| 첫 부팅 `ask What is the capital of France? Answer in one short sentence.` | `The capital of France is Paris.` / 8 tokens / 158.932초 |
| 두 번째 부팅 warmup | 128.903초 |
| 두 번째 부팅 `ask What is the Moon? Answer in one short sentence.` | `The Moon is a natural satellite of Earth, orbiting it in the solar system.` / 18 tokens / 216.137초 |

이 시간은 QEMU TCG CPU 소프트웨어 에뮬레이션의 해당 실행 기록이다. 고정된 두 질문의
성공을 응답 품질·실시간 성능·실기기 성능의 보장으로 일반화하지 않는다.

두 부팅 종료 뒤 원본 verdict SHA-256은
`e654041150d303b1500e4042efc0d3f3d6cbbfda41692689a983a70b22802bb4`, 디스크 SHA-256은
`8f4fc4814c321fb607e1e15b6b57c403da592738f5e83f7490887cc0a2689e26`다. 두 부팅의 boot config는
같은 SHA-256 `916037699ff9be3bb8e0e784cf65062a966c8b78938056aaa3a2e4e2208c3f96`을 사용했다.
`build/hosted-image/verified-model-image.json`은 이 원본과 두 hash를 기록한 별도 포인터다.
사용자 사본의 이후 영속 쓰기까지 이 디스크 hash와 같아야 한다는 계약은 아니다.

`build/hosted-model-image/model-image-04-current-replay.json`의 별도 독립 재검증도
PASS다. 재검증 당시 현재 runtime 35개의 SHA-256이 보존 snapshot·설치 manifest와
일치했고 원본 verdict와 재구성한 판정이 정확히 같았다. 4 GiB 디스크를 포함한 원본
전체 파일의 hash·크기·mtime이 재검증 전후 변하지 않았음을 기록했다. 이는 당시 v0.6 모델
이미지 source의 재검증이며 과거 `backend-02`의 다른 source·교체 시나리오와 구분한다.

실제 사용자 진입점 `Start-AiosImage.cmd -Agent`도
`%LOCALAPPDATA%\AIOS\operating-images\local-model` 사본에서 실행했다. `about`,
`net status`, `exit` 3명령의 단일 부팅 verdict는 PASS이며 UID 1000·모델 자산 전체 읽기
검사·정상 VM exit 0·host kill 없음과 디스크 bytes 대조를 확인했다. boot ID는
`2c4912fe-96ba-4da5-a880-a6fd398de0c0`, session ID는
`e6f85b05-8bd5-450c-883d-03bd15458a98`이다. MAIN/backend는 자동으로 시작하지 않았고
이 사용자 실행에서는 실제 질문·DNS·HTTPS를 요청하지 않았다.

사본의 `boot.json.previous_boots`에 보존된 두 원본 boot의 archive/root-result hash가
원본과 일치함을 별도로 대조했다. 단일 사용자 판정은 `prior_history_replayed=false`이므로
이 확인을 과거 전체 trace의 독립 재생으로 표현하지 않는다. 원본 두 cold boot의 host
재생 검증과 사용자 사본의 history 보존 대조를 구분한다. 로그는
`build/hosted-model-image/user-model-01.log`, 사용자 판정은 사본의
`boots/boot-01/verdict.json`이며 전체 증거 색인은
`build/hosted-model-image/final-summary.json`에 있다.

종료 뒤 `build/hosted-model-image/model-image-04-filesystem-audit.json`은 별도
`SUPPORTING` 읽기 전용 ext4 superblock 감사 PASS다. 디스크 writer 종료를 확인한 뒤
MBR과 두 partition의 superblock만 2,560 bytes 읽어 clean 상태·recovery/orphan flag
없음·error count 0·CRC32C 일치를 확인했고 디스크 크기·mtime은 변하지 않았다.
전체 filesystem 검사·수리, 전원 손실 복구 또는 native filesystem 증거는 아니다.

최종 성숙도는 `SUPPORTING/PARTIAL`이다. 이 이미지의 실제 모델 사용이 Linux 드라이버를
native AIOS 커널에서 직접 실행한 결과, 전체 H2/H3·native/hosted conformance,
ownership·principal·resource apply, 부팅 간 영속 AI 기억, 범용 설치·업데이트·복구,
실기기 호환성 또는 원격 release qualification을 완료했다는 뜻은 아니다.

### 9.7 현재 CLI v0.7 모델 이미지와 전용 사용자 실행 — 2026-09-08

`%LOCALAPPDATA%\AIOS\operating-images\model-image-05`는 현재 CLI 0.7/session schema 7의
제품 source 31개와 boot module 4개를 설치한 새 정상 모델 이미지다. image ID는
`52a12a68-fbbb-4557-b432-bf8550499418`이며 독립 재검증 보고서 (`build/hosted-image-v07/model-image-05-replay/report.json`)는
`PASS`다. 원본 `verdict.json`과 재생 판정이 정확히 같고, 현재 source 35개의 SHA-256이
설치 manifest·runtime snapshot과 일치하며, 원본 전체 파일의 bytes·size·mtime도 보존됐다.

| 항목 | 실제 결과 |
|---|---|
| 설치 | build PASS; installer exit 0, host kill 없음. build의 `operating_boot_verified=false`와 이후 운영 acceptance를 구분 |
| 빌드 당시 Linux 이미지 검사 | 53개, 129.646초, exit 0·skip 0. 이후 추가된 장애 검증기 검사를 포함한 수치가 아님 |
| 당시 Windows 이미지 검사 | 53개 실행, 78.786초, 44 PASS·9 Linux 전용 skip |
| 실제 설치 kernel | `6.18.49-0-virt`; 고정 설치 ISO의 kernel과 갱신 가능한 package repository에서 설치한 kernel을 구분 |
| 두 cold boot | 같은 완성 디스크의 online 20명령·offline 25명령, 총 45명령; 두 START 모두 CLI 0.7.0/schema 7·UID 1000 |
| 실제 모델 | warmup 2회·사용자 질문 2회 PASS, 서로 다른 MAIN/backend/authority 실행 identity |
| 온라인 질문 | `The capital of France is Paris.`; 8 tokens, 261.860325924초 |
| 오프라인 질문 | `The Moon is a natural satellite of Earth, orbiting it in the solar system.`; 18 tokens, 354.830766727초 |
| 관리 전이 | 오프라인 Cell 비활성화·재활성화 뒤 stale 요청 거부, 명시적 발견·재결속으로 binding generation 1→2 |
| 인터넷 | 온라인 DNS 4주소, 인증서 검증 HTTPS 200/559 bytes; 오프라인에서는 외부 요청 없이 설치된 모델 사용 |
| 종료 | 두 부팅의 MAIN·backend·CONSOLE_RUNTIME cleanup, archive, 정상 poweroff와 VM exit 0; host kill 없음 |

이번 검증은 명시적으로 backend·MAIN을 시작하고 discover/bind 뒤 `resources link`를
켜서 질문했다. 응답 시간은 이 로컬 QEMU TCG 실행의 관측값이며 성능 보장이나 모델 품질
평가가 아니다. 두 부팅의 주요 identity는 다음과 같고 전체 요청·실행 identity는 보고서에 있다.

| 항목 | 첫 online boot | 두 번째 offline boot |
|---|---|---|
| Linux boot ID | `a86b2902-9d48-4537-8914-5316c89c1612` | `55bd8162-d8ca-4219-afed-28f365a8db88` |
| CLI session | `813d8bce-7935-4370-8a03-7d8d1e730fae` | `1283b926-cf2e-4f27-9147-cc7db7bd5043` |
| MAIN instance | `7e37e6d9-7306-4066-baf7-67878f7a11d7` | `e72f78c2-ead7-4dac-8b4e-1e636850718b` |
| authority instance | `eb504d8b-d282-45ac-9910-c1148a9ff47d` | `14cd33ae-ba43-40be-8656-3009c0beeef9` |

원본 verdict SHA-256은 `344beb3feea9ba28c328a68004246739cf86a6309e1497944bd867a29cf8277c`,
완료된 disk SHA-256은 `fa8d24d384f30f8ea92305612f434fcbcafde75405d4126e36962a00308b0dd5`다.

전용 Start-Aios-0.7.cmd (`build/hosted-image-v07/Start-Aios-0.7.cmd`)는 검증 원본을
보존하고 `model-image-05-user` 사본을 실행한다. 사용자 실행 receipt (`build/hosted-image-v07/user-launch-receipt.json`)는
`about`, `net status`, `exit` 세 명령, 화면 버전 0.7, UID 1000, model bytes 검사,
launcher exit 0·정상 VM 종료 PASS를 기록한다. 이 실행의 boot ID는
`25c2dbd3-6ff2-46de-908a-7854bbcdbbc9`, session은 `23eff444-943e-4d69-ac04-f7697e243b13`이다.
MAIN/backend를 시작하거나 질문·DNS·HTTPS를 요청한 사용자 세션은 아니다.
단일 사용자 판정의 `prior_history_replayed=false`는 유지하고, 원본 두 부팅의 독립 재생과
구분한다. 기존 `local-model`, `model-image-04`, 기본 모델 포인터는 그대로 보존한다.

정상 이미지와 사용자 실행의 성숙도는 `SUPPORTING/PARTIAL`이다. 별도 개발 VM의
`recovery-model-02`는 원본 FAIL·수정 검증기의 독립 재생 PASS라는 기존 경계를 유지한다.
이번 정상 `model-image-05`에는 장애를 주입하지 않았다. supervisor 소실 뒤 같은 worker가
`backend recover`하고 즉시 `exit`하는 경로는 아래 별도 장애 복사본 02에서 실제 검증했다.
시험 도구의 준비·fixture PASS를 정상 또는 장애 디스크의 실제 PASS로 바꾸지 않는다.
전체 H2/H3·native qualification·ownership·principal·apply·영속 AI 기억·실기기·배포 승격은 없다.

첫 장애 복사본 `fault-01`은 실제 6명령의 `RECOVERED → exit`, root cleanup과
VM exit 0·정상 shutdown·host kill 없음을 기록했지만, 종료 직후 보조 serial socket의
Windows reset 10054를 runner 오류로 처리해 원본 verdict가
`FAIL/image_recovery:operating_boot:['image_contract:vm_exit']`로 남았다.
읽기 전용 진단 (`build/hosted-image-v07/fault-01-readonly-audit/diagnostic.json`)의 나머지
17개 검사 PASS는 `acceptance_granted=false`인 진단이며 원본 FAIL을 바꾸지 않는다.
원본 파일 178개와 완료된 4 GiB 디스크 hash를 보존했다. 정상 `model-image-05` 제품
source 35개와 PASS는 그대로다.

새 `model-image-05-recovery-02`의 준비와 실제 오프라인 장애 acceptance는 모두 PASS다.
image ID는 `fca53c38-a88a-4fe0-b8a9-493e706fe608`, boot ID는
`b6bced22-e52c-4dae-957d-816c0ff0440b`, CLI session은 `75196a24-5355-4464-a999-c950ac894224`다.
실제 6명령에서 recovered backend 1개·정상 종료 backend 0개·MAIN 0개를 기록하고,
같은 worker의 receipt·prior history 재생·source 35개 보존·root cleanup을 통과했다.
`expected_fault_verified=true`, `recovered_only_cleanup_verified=true`이며 VM exit 0·정상 shutdown·
host kill 없음·runner error 없음으로 끝났다. 실제 Windows reset 10054는 정확한
완결 frame 3개·요청 2개·잔여 bytes 0을 확인한 종료 receipt로만 인정했다.
집중 host 검사 27개는 2.655초·skip 0 PASS이며 빌드 당시 Linux 53개와 구분한다.
별도 독립 재생 보고서 (`build/hosted-image-v07/fault-02-replay/report.json`)도 PASS다.
재생 당시 보존한 검증기 27개와 제품 source 35개가 당시 bytes와 일치했고, 별도 프로세스의
9.141초 재생·exit 0 판정이 02 원본과 정확히 같았다. 02 원본 `recovery-verdict.json`의
SHA-256은 `cf6381219075b42bb3ebc7dda4496cd4b962f9e4383894fadd4ec3c00942c218`이다.
디스크를 포함한 정상 원본 396개·
장애 01 원본 179개·장애 02 원본 180개 파일의 hash·size·mtime을 보존하고 세 완료 디스크의
hash를 대조했다. 정상 원본은 준비 당시 보존 map, 01은 최초 실패 map과도 일치했다.
일반 이미지 verifier의 두 공개 진입점은 02를 `expected_fault_image_not_normal`로
거부한다. 이 시험 전용 PASS는 정상 이미지 acceptance나 MAIN·새 질문의 증거를 대신하지 않는다.

### 9.8 기본 사용자 이미지 선택과 이전 선택으로 돌아가기

상태는 Windows 로컬 범위의 `SUPPORTING/PARTIAL`이다. 실제 0.7 선택·기본 부팅,
0.6 Rollback·기본 부팅, 최종 0.7 재선택을 검증했다. 이는 두 번의 실제 부팅과 세 번의
선택 변경이며 마지막 재선택 뒤 세 번째 부팅은 수행하지 않았다. §9.6·§9.7의 이전 실행
PASS와 구분하며 제품 runtime 35개와 CLI 0.7은 바꾸지 않는다.

| 명령 | 사용자에게 보이는 동작 |
|---|---|
| `-Action Selection -Agent` | 현재 기본 모델 사용자 사본과 이전 선택을 표시한다. 선택 기록이 없으면 기존 `local-model`을 표시하며 새로운 선택을 저장하지 않는다. 상태 표시는 새 부팅의 건강 판정이 아니다. |
| `-Action Select -Agent -ImageDirectory <경로>` | 검증된 정상 원본 또는 완성된 사용자 사본을 기본으로 선택한다. 원본을 주면 별도 `-user` 사본을 만들거나 일치하는 기존 사본을 검증해 사용한다. |
| `-Action Rollback -Agent` | 검증을 통과한 이전 사본을 기본으로 돌리고 current·previous를 맞바꾼다. 각 디스크와 history는 그 사본에 남는다. |
| `-Agent` 또는 `-Action Run -Agent` | 선택 기록이 있으면 current를 실행한다. 없으면 기존 `local-model`을 재사용하며, 그것도 없을 때만 legacy verified pointer의 원본을 처음 복제한다. |
| `-Action Run -ImageDirectory <경로>` | 지정한 사본을 한 번 실행하고 기본 선택을 바꾸지 않는다. 기존 호환성으로 명시한 model 이미지의 Run은 `-Agent` 없이도 profile을 읽어 실행한다. |

`-Agent`를 생략한 Selection·Select·Rollback은 모델 없는 basic profile에 적용한다.
사용 예는 다음과 같다. 아래 원본 경로는 로컬 검증 호스트의 예이므로 새 환경에서는
직접 Build·Smoke한 경로로 바꾼다. 이 호스트의 현재 선택은 0.7, 이전 선택은 0.6이다.

```powershell
.\tools\hosted\Start-AiosImage.cmd -Action Selection -Agent
.\tools\hosted\Start-AiosImage.cmd -Action Select -Agent -ImageDirectory "$env:LOCALAPPDATA\AIOS\operating-images\model-image-05"
.\tools\hosted\Start-AiosImage.cmd -Agent
.\tools\hosted\Start-AiosImage.cmd -Action Rollback -Agent
```

선택 성공은 다음 기본 실행 대상을 저장했다는 뜻이다. Rollback은 이전 사본의 선택으로
돌아가며 디스크 bytes를 과거 시점으로 복원하거나 서로 다른 사본의 history를 합치지 않는다.
기존 v0.6 원본·사용자 사본·legacy 포인터는 보존한다. 정상 검증을 통과하지 못한 원본,
시험 전용 fault 이미지, 다른 source의 사본, 부분 복사·완료되지 않은 최신 boot는 거부한다.
선택 대상이 사용 중이면 종료 후 재시도하도록 오류를 표시한다. current의 최신 boot가
실패했어도 디렉터리가 현존하면 건강한 previous로 명시적 Rollback할 수 있다.
current 디렉터리를 삭제·이동한 뒤의 복구는 지원하지 않는다. 선택 기록이 없는 상태에서
기존 legacy 사용자 사본의 검증이 실패한 경우에도 최초 Select로 바로 복구하는 경로는
지원하지 않으며 해당 사본을 보존하고 거부한다.

[image_selection.py](../../tools/hosted/image_selection.py)는 로컬
`%LOCALAPPDATA%\AIOS\image-selections\local-model.json` 또는 `local-basic.json`에
schema 1의 current·previous·revision을 함께 저장한다. 고정 profile lock 안에서 같은
디렉터리의 임시 파일을 flush·fsync·close한 뒤 한 번의 `os.replace`로 교체한다.
[image_host_lock.py](../../tools/hosted/image_host_lock.py)의 별도 OS lock은 파일명이나
저장 PID로 소유권을 주장하지 않는다. 이미지 잠금은 디렉터리의 `st_dev`·`st_ino`를
사용하여 MSIX·일반 경로 같은 실제 동일 디렉터리의 alias도 합친다. `qemu_image.py run`은
자기 프로세스에서 잠금을 얻어 검증·boot 번호 할당·VM 실행·최종 verdict까지 유지한다.

[image_selection_contract.py](../../tools/hosted/image_selection_contract.py)의 검증 범위는
정상 원본 전체 재생과 그 원본 자체의 보존 runtime 35개, 사용자 사본의 최신 boot 전체
재생이다. 더 오래된 사용자 boot는 disk 연결·archive·worker·serial·history metadata를
대조하며 과거 actor 실행 전체를 다시 재생하는 계약은 아니다. 보존 제품 모듈을 host에
import해 실행하지 않으며, 선택 검증을 native·전체 H2/H3 또는 영속 AI 기억으로 승격하지 않는다.

공식 API 근거는 2026-09-08에 대조했다. Python `os.replace`의 같은 filesystem 교체와
buffered 파일의 `flush` 후 `fsync`를 사용하되 전원 손실 내구성이나 다중 파일 transaction을
보장하지 않는다. Windows `msvcrt.locking`은 동일 offset의 1 byte를 nonblocking으로 잠그며,
정상 경로에서는 명시적으로 unlock·close한다. Windows는 프로세스 종료 뒤에도 잠금 해제가
잠시 지연될 수 있으므로 남은 lock 파일이나 저장 PID를 지워 강제로 인수하지 않는다.
[Python 파일 교체·동기화](https://docs.python.org/3/library/os.html),
[Python Windows 파일 잠금](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking),
[Microsoft 잠금 수명](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)

2026-09-08의 실제 선택 acceptance 02 (`build/hosted-image-selection/acceptance-02/result.json`)는
PASS다. revision은 1의 0.7 선택 → 2의 0.6 Rollback → 3의 0.7 재선택으로 진행했고,
최종 current는 `model-image-05-user`(CLI 0.7.0), previous는 `local-model`(CLI 0.6.0)이다.
선택·조회만 수행한 단계는 정상 원본 04·05와 두 사용자 사본의 모든 기존 파일을 보존했다.
두 실제 부팅은 선택된 `system.raw`의 운영 쓰기를 기록하고 새 `boot-02`를 추가했으며,
이전 사용자 증거 파일·다른 이미지를 보존했다. 검증 당시 host 도구의 frozen source도
바뀌지 않았다. 당시 실행 도구 37개·시험 소스 35개와 실제 acceptance helper의 snapshot을
별도로 보존하며 이를 새 독립 배포 패키지로 해석하지 않는다.

| 실제 기본 부팅 | CLI 0.7.0 | CLI 0.6.0 |
|---|---|---|
| 사용자 사본 | `model-image-05-user/boots/boot-02` | `local-model/boots/boot-02` |
| boot ID | `9d5a0891-0c22-4782-87ff-a72272be6909` | `7231291a-e54a-470e-a4a1-b32b62a80910` |
| session ID | `c79ff85e-ff04-488f-98cc-0d1dbf386cb8` | `49278edb-0207-4efe-a066-81ee16ddb99a` |
| 사용자 명령 | `about`, `hardware all`, `net status`, `resolve example.com`, `fetch https://example.com/`, `exit` | 같은 6명령 |
| 실제 결과 | UID 1000, DNS 4주소, HTTPS 200/559 bytes·certificate verified, VM exit 0·정상 shutdown·host kill 없음 | 동일 항목 PASS |

두 실행 모두 모델 bytes를 검사했으며 MAIN/backend·warmup·사용자 모델 질문은 시작하지
않았다. 각 단일 boot verdict의 `prior_history_replayed=false`를 유지하며 source 전체와
사용자 최신 boot·이전 metadata를 대조하는 selector gate의 범위와 구분한다.
결과 파일 SHA-256은 `66475f56c8ce81591d87b7673a2f438e44a58553560b1ed4413cf7064cad65e7`이다.
별도 읽기 전용 독립 재검토 (`build/hosted-image-selection/independent-review/report.json`)도
PASS다. 두 정상 source 전체와 각 사용자 최신 `boot-02`의 전체 재생을 통과했고,
source 04·05의 390·396개 파일과 두 사용자 사본의 각 133개 파일(각 디스크 포함)을
검토 전후 그대로 보존했다. 선택 상태도 current 0.7·previous 0.6·revision 3으로 변하지
않았다. source 05는 현재 runtime 35개와 일치하며, source 04의 7개 차이는 의도적으로
보존한 v0.6 source다. 실제 기본 launcher 인자, 이전 사용자 증거·선택만의 보존과 첫
시험 01의 FAIL 유지도 대조했다. 검토 중 VM은 실행하지 않았고 검증 범위는 위의
`normal-source-full; user-latest-operating-boot-full; earlier-user-boot-chain-metadata`다.

frozen 집중 검사 (`build/hosted-image-selection/focused-tests-discover.log`)는 34개·41.121초·
skip 0·exit 0 PASS다. 이전 focused-tests.log (`build/hosted-image-selection/focused-tests.log`)는
fixture를 package 이름으로 불러온 실행의 import 오류 1개로 22개·1.129초 FAIL이며 보존한다.
첫 acceptance 01 (`build/hosted-image-selection/acceptance-01/result.json`)은 CMD의 UNC 경로·
괄호 해석 때문에 최초 Selection 호출에서 exit 1로 끝났고 선택 변경·VM 실행은 없었다.
기존 Z: 경로가 같은 checkout인지 `Path.samefile`로 확인한 후 새 02에서 실행했다.
앞의 두 실패를 지우거나 PASS로 바꾸지 않았으며 새 정상 부팅을 native·전체 H2/H3·범용
설치 복구·전원 손실 복구 또는 release qualification으로 승격하지 않는다.

## 10. 최초 사용자 환경 목표의 완료 감사 — 2026-09-08

최초 요청의 종료점은 고유 CLI와 부팅 로그·하드웨어 관측을 제공하고, 같은 사용자
환경에서 기본 인터넷 통신을 한 뒤 정상 종료·반복 부팅할 수 있게 하는 것이다.
기존 Linux delivery 방향과 AIOS 독자 관리 의미를 유지한 이 로컬 사용자 환경은
요구사항별 검토와 현재 파일 재검증을 통과했다. 전체 프로젝트와 이 실행 lane의
성숙도는 `SUPPORTING/PARTIAL`로 유지한다.

완료 감사 기록 (`build/hosted-userspace-goal-audit/completion-audit.json`)은 원요청을
8개 항목으로 대조한다. 현재 이미지 재검증 (`build/hosted-userspace-goal-audit/runtime-review/report.json`)과
부팅·하드웨어 부록 (`build/hosted-userspace-goal-audit/runtime-review/runtime-facts.json`)은
기존 PASS 문구만 조회하지 않고 보존된 원본·최신 사용자 부팅과 실제 디스크를 검사했다.

| 요청·사용 계약 | 현재 확인한 증거 |
|---|---|
| 가이드와 공식 자료 검토 | 통합 가이드·Linux 정책·CLI/운영 이미지 정본 대조; Linux PCI sysfs, QEMU network, Python SSL 공식 자료 재확인 |
| AIOS 정체성 유지 | 실제 `about`의 AIOS 관리 의미·Linux 실행 커널·별도 native 구현 구분, `kernel/`·`os/` 변경 없음 |
| 부팅 로그와 고유 CLI | 정상 원본과 최신 사용자 기록의 7개 부팅 묶음이 live/READY이며 고유 banner·`aios>`·UID 1000·디스크 직접 부팅 확인 |
| 하드웨어 인식 | CPU·memory·PCI·USB·block·network 6영역 observed; 개별 장치 usability는 UNTESTED인 Linux-visible QEMU inventory |
| 기본 인터넷 | 현재 model 사용자 boot-02의 같은 세션 DNS·인증서 검증 HTTPS; basic 원본의 online cold boot 두 번 |
| 오류 뒤 계속 사용·정상 종료 | 현재 콘솔 42개·네트워크 19개 host 검사 PASS/skip 0; 실제 사용자 세션·root archive·VM 종료 판정 |
| 반복 사용·기록 보존 | basic 원본 online 2/offline 1, model 원본 online 1/offline 1; 사용자 사본·config/history·디스크 연결 검증과 §9.8 선택/되돌리기 |
| 장기 방향과 제한 보존 | 전체 H2/H3·native conformance·실기기·K5/apply·영속 AI 기억·범용 설치/업데이트/전원 손실 복구는 기존 후속 계획으로 유지 |

감사한 로컬 호스트의 `-Agent` 없는 기본은 보존된 CLI 0.6의 `local-basic`이고, `-Agent` 기본은
CLI 0.7의 `model-image-05-user`다. 모델 선택은 current 0.7/previous 0.6/revision 3을
유지한다. basic 사용자의 기존 boot-01은 `about`, `net status`, `exit` 세 명령이며
DNS·HTTPS 관찰값은 false다. basic 인터넷 증거는 정상 원본의 두 online 부팅에 있고,
현재 모델 사용자 boot-02의 직접 인터넷 증거와 혼합하지 않는다.

현재 모델 원본의 제품 source 35개는 checkout과 일치한다. basic 원본의 source 35개는
의도적으로 보존한 v0.6이다. 재검증 전후 네 이미지의 882개 파일(디스크 4개 포함),
기존 acceptance 02, 당시 host 도구 37개와 선택 상태는 동일했다. 이번 감사에서는 VM을
새로 실행하지 않았으며 과거 사용자 부팅은 이력 연결 검사, 최신 사용자 부팅은 전체
재생이라는 §9.8의 범위를 유지한다.

현재 콘솔 검사 (`build/hosted-userspace-goal-audit/console-behavior.log`)는 42개·6.802초,
네트워크 검사 (`build/hosted-userspace-goal-audit/network-behavior.log`)는 19개·3.068초로
각각 exit 0/skip 0 PASS다. 고정 입력과 로컬 서버 검사는 실제 인터넷 증거와 구분한다.
첫 검사 호출은 로그 디렉터리 준비 누락으로 프로그램이 실행되지 않았고,
초기 준비 오류 (`build/hosted-userspace-goal-audit/initial-test-setup.json`)로 보존했다.
그때의 shell exit 0을 검사 PASS로 사용하지 않았다.

이 감사는 기존 공식 관측·통신 계약을 유지한다. sysfs의 정보 읽기와 장치 제어를
구분하고, user networking의 DNS/외부 요청을 실제 결과로 확인하며, TLS 인증서와
hostname 검증을 우회하지 않는다. 최신 문서 조회는 설치된 버전·upstream pin 변경이나
primary host matrix 지원 승격이 아니다.
[Linux PCI sysfs](https://docs.kernel.org/PCI/sysfs-pci.html),
[QEMU user networking](https://www.qemu.org/docs/master/system/devices/net.html),
[Python SSL client context](https://docs.python.org/3/library/ssl.html)

## 11. 베타 체크포인트 검토 경계 — 2026-09-08

문서·구현 대조 뒤 공용 `qemu_console.py`의 `SerialGuest` 생성 중 reader 시작이 실패하면
이미 생성한 소유 자식을 회수하고 pipe를 닫으며 원래 예외를 보존하도록 보완했다.
이 host 파일의 hash는 §9.8·§10 당시 보존한 37개 도구 snapshot과 달라졌다. 과거 이미지·
선택 acceptance는 당시 도구의 증거로 유지한다. 별도
`build/beta-userspace-checkpoint/independent-evidence-review.json`의 제품 runtime 35개 byte 일치는
아래 게시 형식 정리 전 시점의 판정이다.

최종 index 검사 뒤 `aios_console/__init__.py`·`shell.py`의 CRLF를 LF로 정리하고
`aios_hosted/boot.py`·`aios_service/__init__.py`의 중복 마지막 빈 줄을 하나씩 제거했다.
베타에 포함할 제품 35개 중 31개는 당시 bytes와 같고 4개는 이 형식 차이만 있다.
`build/beta-userspace-checkpoint/publication-format-equivalence.json`은 변경 전후 Python AST가
행·열 위치까지 같음을 확인했다. 기존 이미지와 당시 source·acceptance는 보존했으며,
형식 정리 뒤 새 이미지 부팅은 재실행하지 않았다. 과거 실행은 그 실행의 `runtime-source/`로
재생하고, 현재 `hosted/linux`를 지정해 과거 source hash와 같다고 판정하지 않는다.

로컬 Windows hosted 전체 검사는 보완 전 698개(실행 630 PASS·68 skip, 178.076초)다.
보완 뒤 별도 생성 실패 검사 3개·0.061초와 기존 runner 검사 19개·0.273초는 skip 없이 PASS다.
실제 QEMU 자식의 생성 실패 주입도 회수·pipe 종료·원래 예외 보존을 통과했지만 OS 부팅 시험은 아니다.
로그와 `qemu-constructor-failure.json`은 같은 로컬 `build/beta-userspace-checkpoint/`에 보존한다.
게시 파일만 꺼낸 첫 별도 검사는 `.git` 기록이 없어 H1 provenance 검사에서 실패했다
(701개 중 실패 3·오류 17·skip 68). 원본 로그는
`build/beta-userspace-checkpoint/hosted-clean-export-windows-tests.log`에 보존한다.
이 소스 복사는 정상 Git checkout 검증을 대신하지 않으며, 실제 커밋을 clone한 뒤 다시 검사한다.
이 검토 시점에는 새 커밋 SHA의 원격 CI 결과가 없으며 게시 후 terminal 결과를 별도로 확인한다.

범위를 넓히지 않고 남긴 보완은 깊은 JSON에 대한 `verify_boot.py`의 structured FAIL 출력과,
MAIN·CONSOLE_RUNTIME client에서 stdout 파일을 연 뒤 stderr 파일 열기가 실패할 때 첫 파일을
닫는 처리다. 전자는 현재 비정상 종료로 거부되어 false PASS는 없고, 후자는 실패 경로의 자원 정리다.
제품 client 수정과 그 소스에 대한 실행 검증은 별도 후속이며 기존 실제 PASS를 변경하지 않는다.

### 11.1 첫 원격 베타 검증의 Windows 경로 차이

첫 체크포인트 `a5cd44c`를 실제 Git clone한 Windows 로컬 전체 검사는
701개(633 실행 PASS·68 skip, 136.208초)로 통과했다. 앞의 `.git` 없는 복사본 실패와 구분한다.
[첫 원격 실행](https://github.com/tjwlstj/aios-kernel/actions/runs/34240648311)의
Windows hosted 검사에서는 임시 경로의 `RUNNER~1`과 `runneradmin` 표기 차이로 33개가 실패했다
(701개·66 skip). 실제 runner와 verifier는 canonical 경로를 사용하지만 일부 fixture가
해석 전 경로를 기록하거나 mock의 예상 인자로 사용했다. 원본 job 로그와 실패 목록은
`build/beta-userspace-checkpoint/remote-windows-job-original.log` 및
`remote-windows-first-failure.json`에 보존한다.

이 회귀는 fixture 세 곳의 경로 정규화와 portable alias 반례 세 개로 보완했다.
로컬 관련 suite는 이미지 31개·선택 14개·복구 18개, 합계 63개를 skip 없이 통과했다.
새 반례만 별도로 다시 실행한 3개·4.662초 PASS 로그는 `path-alias-regressions.log`에 보존한다.
실제 제품 runtime과 verifier의 디스크
판정 계약은 유지하며, 수정 커밋은 새로운 SHA의 전체 원격 CI 결과로 별도 판정한다.
첫 실행의 Linux·H1 검증 성공을 Windows 실패나 새 SHA의 성공으로 대신하지 않는다.
