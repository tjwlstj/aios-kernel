# AIOS Windows Build Guide

이 문서는 Windows PowerShell 환경에서 AIOS 커널을 빌드하고 점검하는 방법을 정리합니다.

## 1. 요구 도구

다음 도구 조합이 현재 저장소에서 검증되었습니다.

```powershell
winget install --id ezwinports.make
winget install --id BrechtSanders.WinLibs.POSIX.UCRT
winget install --id SoftwareFreedomConservancy.QEMU
```

추가로 다음이 필요합니다.

- Git for Windows
- `x86_64-elf` bare-metal 크로스 툴체인

이 프로젝트는 일반 MinGW `gcc`로는 빌드되지 않습니다. `x86_64-elf-gcc` 계열 툴체인이 반드시 필요합니다.

## 2. x86_64-elf 툴체인 배치

PowerShell 헬퍼 스크립트는 다음 위치를 순서대로 탐색합니다.

1. 환경 변수 `AIOS_X86_64_ELF_ROOT`
2. 저장소 부모 경로의 `tools\x86_64-elf`
3. 저장소 내부 `.toolchain\x86_64-elf`
4. 현재 작업 디렉터리의 `x86_64-elf`

권장 방법은 환경 변수를 지정하는 것입니다.

```powershell
$env:AIOS_X86_64_ELF_ROOT = 'C:\toolchains\x86_64-elf'
```

또는 현재 저장소가 `Z:\aios\aios-kernel`에 있다면 아래 구조도 자동 인식합니다.

```text
Z:\aios\
├── aios-kernel\
└── tools\
    └── x86_64-elf\
```

## 3. 빌드

저장소 루트에서 실행합니다.

```powershell
pwsh -File .\scripts\build-windows.ps1 -Target all
```

이 스크립트는 다음을 자동으로 처리합니다.

- `make`, `nasm`, `qemu-system-x86_64` 탐색
- Git for Windows의 Unix 유틸리티 경로 추가
- `x86_64-elf-gcc`, `x86_64-elf-ld`, `x86_64-elf-objcopy` 연결
- `make CC=... LD=... OBJCOPY=... ASM=nasm <target>` 실행

## 4. 스모크 테스트

```powershell
pwsh -File .\scripts\build-windows.ps1 -Target test
```

성공 시 빌드 산출물은 `build\` 아래에 생성됩니다.

## 5. 주요 주의점

- 일반 WinLibs `gcc`는 Windows 타깃이므로 `-mcmodel=kernel` 커널 빌드에 적합하지 않습니다.
- `make test`는 QEMU를 실행하지만, 환경에 따라 시리얼 로그가 비어 있을 수 있습니다.
- ISO 생성 타깃은 `grub-mkrescue`, `xorriso`, `mtools`가 추가로 필요하므로 Windows에서는 기본 지원 대상으로 보지 않습니다.
