# 마이그레이션 가이드

언어: [English](migration-guide.md) | 한국어

이 문서는 alpha tag에서 첫 beta tag로 넘어갈 때의 공개 마이그레이션 경로를 설명합니다.

## 범위

이 가이드는 공개 하네스를 이미 다른 repo에 복사하거나 vendoring한 소비자를 위한 문서입니다.

대상 시작점:
- `v0.1.0-alpha1`
- `v0.1.0-alpha2`
- `v0.1.0-alpha3`

## Beta로 올라갈 때 바뀌는 것

beta의 핵심 변화는 새로운 런타임 엔진이 아닙니다.
설치, 채택, 릴리즈 기대치를 둘러싼 공개 계약이 더 강해진다는 점입니다.

다음을 1급 public artifact로 취급해야 합니다.
- `CHANGELOG.md`
- `docs/public-contract.md`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- `docs/release-model.md`

## 권장 업그레이드 경로

1. 새 runtime path를 다시 vendor/copy 합니다.
2. 소비자 repo의 disposable clone에서 `scripts/bootstrap_consumer --copy-runtime --force`를 다시 실행합니다.
3. canonical docs를 검토합니다.
   - `docs/specs/project-roadmap/decision-log.md`
   - `docs/specs/task-spec.md`
4. 공개 launch path를 확인합니다.
   - `scripts/ai_worker --help`
   - `scripts/start_worker_session --help`
   - `scripts/bootstrap_consumer --help`
5. print-only worker launch를 한 번 돌립니다.
   - `scripts/ai_worker codex docs/specs/task-spec.md --print-command -- --model gpt-5.4`

## Alpha 버전별 메모

### alpha1에서 올라오는 경우

다음이 빠져 있습니다.
- `scripts/bootstrap_consumer`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- sample consumer workspace

이 경우 개별 파일을 손으로 골라 넣기보다 beta tag 기준 공개 runtime을 다시 vendor하는 편이 낫습니다.

### alpha2에서 올라오는 경우

기본 채택 경로는 이미 있습니다.
다만 아래를 다시 점검해야 합니다.
- `scripts/bootstrap_consumer --force`
- nested directory에서의 `scripts/ai_worker` 실행

이 부분에 로컬 패치가 있었다면 업그레이드 전에 충돌을 신중히 정리해야 합니다.

### alpha3에서 올라오는 경우

런타임 shape는 이미 beta에 가깝습니다.
주요 추가점은 다음입니다.
- release model 문서
- migration guide
- changelog 기반 공개 릴리즈 기대치

## 그대로 유지되어야 하는 것

beta로 올라가도 다음 운영 개념은 바뀌지 않아야 합니다.
- head session은 얇게 유지
- worker continuity는 conversational continuity보다 더 엄격하게 유지
- `ai_worker`는 여전히 권장 ergonomic path
- `start_worker_session`은 저수준 authority boundary 유지

## 업그레이드 후 다시 확인할 것

업그레이드 후에는 아래를 다시 점검하십시오.
- 복사된 스크립트의 실행 비트
- canonical doc 위치
- `ai_worker`를 호출하는 로컬 alias/wrapper
- alpha 전용 동작을 아직 설명하고 있는 downstream 문서
