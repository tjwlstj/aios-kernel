# Gemini CLI 활용 방향 정리

## 목적

이 문서는 현재 `AIOS` 저장소에서 `Gemini CLI`를 어떻게 활용할지, 그리고 실제 사용 가능 상태가 어느 정도인지 기록하기 위한 운영 메모이다.

핵심 목표는 다음과 같다.

- 외부 시각으로 커널/OS 아키텍처 검토
- 문서와 구현 간 불일치 탐지
- 커널 부족점 우선순위 점검
- 크로스 OS 빌드/테스트 흐름에 대한 제3자 검토
- 메인 AI / 하위 노드 / 유저 공간 구조에 대한 설계 조언 수집

## 현재 확인된 상태

2026-03-30 기준 이 머신에서는 전역 설치된 `Gemini CLI` 실행 파일을 확인했다.

- `gemini.cmd`: `C:\Users\tjwls\AppData\Roaming\npm\gemini.cmd`
- `node.exe`: `C:\Program Files\nodejs\node.exe`
- `gemini --version`: 성공
- `gemini --help`: 성공
- `gemini --list-sessions`: 성공
- 프로젝트 루트에서 세션 캐시 로드: 성공

실제 확인 명령 예시는 다음과 같다.

```cmd
set PATH=C:\Program Files\nodejs;C:\Users\tjwls\AppData\Roaming\npm;%PATH%
gemini.cmd --version
gemini.cmd --help
```

다만 실제 모델 호출은 아직 계정 검증 단계에서 막혀 있다.

- `gemini -p "..." -o text`: 실행됨
- 결과: `403 Verify your account to continue`

즉 현재 상태는 다음과 같이 정리할 수 있다.

- CLI 실행 자체: 가능
- 로컬 세션 및 명령 조회: 가능
- 실제 모델 응답 호출: 계정 검증 완료 전까지 제한

## 현재 프로젝트에서의 권장 사용 방향

Gemini CLI는 이 프로젝트에서 "직접 수정자"보다 "보조 검토자"로 두는 편이 가장 안전하다. 특히 현재 AIOS는 커널/OS/런타임/문서가 동시에 움직이고 있으므로, Gemini는 다음 역할에 집중시키는 것이 좋다.

### 1. 아키텍처 검토

다음 주제에 대한 외부 검토를 요청하기 좋다.

- `ring3 + syscall + ELF loader` 우선순위 검토
- `메인 AI / 하위 노드 / 유저 공간 daemon` 분리 적절성
- `정적-혼돈 연산자` 기반 메인 AI 구조의 안정성 검토
- `KV tier manager`와 `TurboQuant / KVTC` 병행 설계 검토

### 2. 문서 감리

Gemini가 특히 잘할 가능성이 높은 작업이다.

- README와 실제 구현 범위 비교
- 설계 문서 간 용어 불일치 찾기
- 커널/OS 경계 설명 보강
- 외부 기여자 온보딩 문서 개선안 제안

### 3. 테스트 전략 검토

현재 저장소에는 Windows/Linux 기준 빌드 및 스모크 테스트 축이 이미 있으므로, Gemini에게는 "새 테스트 아이디어 제안" 역할을 맡기는 것이 좋다.

- 부팅 회귀 테스트 케이스 제안
- 드라이버 smoke -> 실 I/O 테스트 단계 분해
- 커널/OS 올인원 테스트 툴 개선 포인트
- GitHub Actions matrix 확장 아이디어

### 4. 구현 전 설계 피드백

실제 코드 생성보다 다음 단계에 대한 설계 초안 피드백이 더 유효하다.

- `agent-tree-aware scheduler`
- `user-space init / loader`
- `persistent memory journal`
- `hardware compatibility quirk table`
- `main AI state machine`

## 권장 운영 원칙

Gemini CLI를 이 프로젝트에 붙일 때는 다음 원칙을 지키는 것이 좋다.

### 읽기 우선

처음에는 코드 변경 요청보다 다음 형태가 안전하다.

- "현재 구조의 가장 큰 리스크 5개"
- "이 설계에서 빠진 전제"
- "README와 구현 간 충돌"
- "다음 3개 우선순위"

### 커밋 전 검토자 역할

Gemini를 커밋 생성기보다 사전 검토자처럼 사용하는 편이 좋다.

- 설계 리뷰
- 문서 품질 점검
- 변경 후 회귀 가능성 질문
- 테스트 누락 탐지

### 위험 구간은 별도 프롬프트로 분리

한 번에 모든 걸 물어보기보다, 다음처럼 나누는 편이 결과 품질이 좋다.

- 커널 코어
- 유저 공간 OS 구조
- AI 런타임
- 모델 메모리 구조
- 드라이버/호환성

## 권장 프롬프트 유형

### 커널 부족점 점검

```text
현재 저장소는 AI 전용 커널/OS를 목표로 한다.
다음 관점에서 가장 치명적인 부족점 5개를 우선순위로 정리해줘.
- ring3 / syscall / ELF loader
- memory model
- scheduler / timer
- hardware compatibility
- driver maturity
가능하면 지금 당장 해야 할 것과 나중에 미뤄도 되는 것을 분리해줘.
```

### 문서-구현 차이 점검

```text
README와 docs를 기준으로 프로젝트가 약속하는 기능과 실제 구현 상태가 어긋나는 부분을 찾아줘.
특히 '이미 구현된 것처럼 보이지만 실제로는 bootstrap 또는 skeleton 수준'인 항목을 찾아줘.
```

### 메인 AI 구조 검토

```text
이 프로젝트는 메인 AI 1개와 다수의 소형 하위 노드 AI를 운영체제 수준에서 관리하려 한다.
정적-혼돈 연산자를 메인 AI의 상태 제어 축으로 사용하려는데,
실제 구현 관점에서 어떤 상태 머신과 안전장치가 필요할지 검토해줘.
```

### 호환성 전략 검토

```text
이 커널은 x86_64 베어메탈, Multiboot2, Windows/Linux 빌드 테스트를 함께 가져가려 한다.
실기기 호환성을 높이기 위해 ACPI/PCI/DMA/driver 순서를 어떻게 잡아야 하는지 제안해줘.
```

## 실제 사용 전 체크리스트

실제 모델 호출을 다시 쓰기 전에는 다음을 먼저 확인하는 편이 좋다.

1. Google 계정 검증을 완료해 `403 Verify your account` 상태를 해소할 것
2. 셸 `PATH`에 아래 두 경로가 포함되어 있을 것
   - `C:\Program Files\nodejs`
   - `C:\Users\tjwls\AppData\Roaming\npm`
3. 프로젝트 루트에서 실행할 것
4. 첫 사용은 읽기/검토 중심 프롬프트부터 시작할 것
5. 자동 편집 전에는 현재 브랜치와 변경 범위를 분리할 것

## 추천 결론

현재 AIOS에서 Gemini CLI는 충분히 유용할 수 있다. 다만 지금 단계에서는 "코드 자동 작성기"보다 다음 역할이 더 적합하다.

- 설계 리뷰어
- 문서 감사자
- 테스트 전략 보조자
- 변경 전 리스크 탐지기

즉, 현 시점의 최적 사용 방식은 다음 한 줄로 정리된다.

> Gemini CLI는 현재 AIOS에서 "구현 주체"보다 "외부 검토자" 역할로 먼저 붙이는 것이 가장 효율적이다.
