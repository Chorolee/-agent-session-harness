# 릴리즈 모델

언어: [English](release-model.md) | 한국어

이 문서는 `agent-session-harness`의 공개 릴리즈가 무엇을 의미하는지 설명합니다.

완전한 제품 지원 약속보다는 좁고,
그냥 코드 스냅샷보다는 넓은 범위의 약속입니다.

## 현재 단계

현재 목표 단계:
- public beta

의미:
- 이 저장소는 단순 참고용이 아니라 다른 repo가 채택할 수 있는 상태를 목표로 합니다
- 공개 런타임 surface는 tag 사이에서 일관되게 유지되어야 합니다
- install/bootstrap, adoption docs, compatibility 문서가 지원 surface에 포함됩니다

의미하지 않는 것:
- 내부 helper 이름이 모두 안정화되었다는 뜻은 아닙니다
- 모든 workspace 구조를 지원한다는 뜻은 아닙니다
- 모든 third-party CLI와의 호환이 보장된다는 뜻은 아닙니다

## 공개 Surface

beta 기준 공개 surface는 다음입니다.
- `scripts/start_worker_session`
- `scripts/ai_worker`
- `scripts/bootstrap_consumer`
- `docs/public-contract.md`
- `docs/adoption-guide.md`
- `docs/compatibility.md`
- `docs/migration-guide.md`
- `examples/sample-consumer/`

다운스트림 소비자는 우선 이 surface를 기준으로 붙이는 것이 맞습니다.

## 안정성 수준

### beta tag 사이에서 안정적으로 유지할 것

다음 개념은 beta 동안 안정적으로 유지되는 것이 목표입니다.
- head session과 worker session 분리
- binding-first worker launch
- bounded, metadata-first resume
- explicit task identity와 lineage
- doc-basis-aware executable continuity
- ergonomic launch path로서의 `ai_worker`
- 최소 install/bootstrap 경로로서의 `bootstrap_consumer --copy-runtime`

### beta 중에도 바뀔 수 있는 것

다음은 public breaking regression으로 보지 않고 바뀔 수 있습니다.
- `tools/harness/` 내부 module 이름
- reducer/helper 구성
- 정확한 내부 cache shape
- 런타임 contract를 바꾸지 않는 범위의 example/scaffolding 세부사항

## Beta 종료 기준

다음이 충족되면 beta를 벗어날 준비가 된 것입니다.
- 서로 다른 consumer repo 형태 최소 2개 이상 검증
- first-time adoption이 가능할 정도로 install/bootstrap 흐름이 명확함
- 공개 tag 간 migration guide가 존재함
- compatibility 기대치가 명시되어 있음
- runtime contract와 release model이 tag마다 크게 흔들리지 않음

## 태그 의미

권장 의미:
- `alpha`: 공개 shape가 빠르게 바뀌는 단계
- `beta`: adoption path가 있고, 공개 런타임 surface가 문서화된 단계
- `stable`: migration 기대치와 지원 경계가 더 예측 가능한 단계

## 소비자 가이드

beta 동안 이 저장소를 채택한다면:
- 임의의 `main` 커밋보다 tagged docs를 우선 보십시오
- `docs/public-contract.md`를 런타임 권위 문서로 취급하십시오
- 강한 vendoring 흐름이 이미 없다면 첫 채택은 `scripts/bootstrap_consumer --copy-runtime`을 쓰는 편이 안전합니다
- tag 업그레이드 전에는 `CHANGELOG.md`와 `docs/migration-guide.md`를 확인하십시오
