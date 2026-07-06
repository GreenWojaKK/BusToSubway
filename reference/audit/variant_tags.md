> 이 문서는 데이터 감사 당시의 역사 기록으로, 프로젝트 내부 용어를 그대로 사용한다 — 용어는 [docs/internal/GLOSSARY.md](../../docs/internal/GLOSSARY.md) 참조.

# 원시 데이터 감사 — reference/variant_tagging (variant_tags.csv + evidence/ + evidence_compact/)

감사일: 2026-07-04 · 도구: pandas (python) · 모든 수치는 실측이며 추정은 없다.

대상:
- `reference/variant_tagging/variant_tags.csv` (298,272 bytes)
- `reference/variant_tagging/evidence/` (184 JSON)
- `reference/variant_tagging/evidence_compact/` (184 JSON)

교차 대조에 사용한 파일: `data/ulsan_route_schedule_before.csv`, `data/ulsan_stops_before.csv`, `data/ulsan_bus_route_before.csv`

---

## 1. variant_tags.csv — 스키마·인코딩·행수

- **인코딩**: 이 파일은 UTF-8 **with BOM**이다(`EF BB BF` 시그니처로 확인했다). 따라서 로더는 반드시 `encoding='utf-8-sig'`로 읽어야 한다. 줄바꿈은 **CRLF**이고 총 482개이므로, 헤더를 포함한 482라인에서 데이터는 **481행**이다.
- **시각 컬럼 없음**: 판단층에는 시간 정보가 전혀 담겨 있지 않으며 빈도 `frequency`만 존재한다. 따라서 시각 포맷과 관련한 이슈는 이 파일에는 해당하지 않는다.
- **컬럼은 총 18개**이며, 그 내역은 다음과 같다.

| 컬럼 | 결측 | 내용 (실측) |
|---|---|---|
| route | 0 | 노선 단명(예: `10`, `울주01`). distinct 184개 = General 170 + Express 14 |
| route_type | 0 | `General` 454행 / `Express` 27행 |
| route_id | 0 | **PK, 481개 전부 유일**. 470개는 `BR_TAGO_USB…`(20자), 11개는 `BR_ACC0_…`(16자, 울주01~10의 DRT형) |
| dir_label | 0 | 방면 라벨. `(none)` 11건(전부 울주 BR_ACC0). (route, dir_label) 조합은 키가 아니다(중복 88행) |
| n_stops | 0 | 2~165. evidence의 stop 리스트 길이와 전 행에서 일치 |
| frequency | 0 | 1~104. **schedule_before의 route_id별 distinct trip_id 수와 481행 전부 정확히 일치(불일치 0)** |
| is_loop | 0 | True 138 / False 343 (문자열 `True`/`False`) |
| first, last | 0 | 기점·종점 정류장명. evidence의 first_raw/last_raw와 전 행에서 일치 |
| role | 0 | **8종**: main 187, short_turn 112, circular 103, detour 51, branch 14, extension 10, duplicate 2, anomaly 2 |
| direction_group | 0 | `A:…`/`B:…`(그 밖에 C, D, E, L, Loop) 프리픽스가 있는 행 325개, 프리픽스 없는 자유라벨 156행 |
| base_route_id | 163 | 관계 참조(§3). 전부 같은 route 내부에 실존하는 route_id를 가리킨다(dangling 0) |
| confidence | 0 | high 436 / medium 43 / low 2 |
| source | 0 | agent 357 / auto 124 |
| verified | 0 | `True` 357 / `False` 124 — **source=agent ⇔ verified=True로 완전히 동치** |
| verifier_agree | 124 | agent 행은 전부 `True`, auto 행은 전부 NaN |
| added_or_removed | 199 | 기준 대비 추가·삭제된 정류장에 대한 서술(자유 텍스트) |
| evidence | 0 | 판정 근거에 대한 자유 텍스트(길이 52~548, 공백 행 0) |

## 2. 판정 키와 역할 체계

- **판정 단위는 `route_id`이며, 이는 before의 trip-pattern(변형) 하나에 대응한다.** 즉 키는 노선(route)×패턴이 아니라 패턴 그 자체다. evidence의 `gtfs_note`가 이 점을 명시한다: "each route_id is a distinct trip-pattern (variant); frequency = number of distinct planned trips on it. **PRE-MERGE resolution: tag each variant, do NOT merge**".
- **역할은 4종이 아니라 8종이다.** 선행 구현 명세가 정의한 4종(main/circular/branch/short_turn) 외에 `detour`(51행/230 trips), `extension`(10행/59), `duplicate`(2행/2), `anomaly`(2행/2)가 추가로 존재한다. 로더가 4종만 허용하도록 작성되어 있다면 이 파일에서 즉시 깨진다.
- **행·trip 분포**는 다음과 같다(frequency 가중치는 실측 schedule의 trip 수와 동일하다).

| role | 행수 | trips | General trips | Express trips |
|---|---|---|---|---|
| main | 187 | 4,648 | 3,742 | 906 |
| circular | 103 | 2,275 | 2,239 | 36 |
| short_turn | 112 | 319 | 319 | 0 |
| detour | 51 | 230 | 230 | 0 |
| extension | 10 | 59 | 59 | 0 |
| branch | 14 | 33 | 33 | 0 |
| duplicate | 2 | 2 | 2 | 0 |
| anomaly | 2 | 2 | 2 | 0 |
| **계** | **481** | **7,568** | | |

- **main 유일성**: (route, direction_group)당 main은 1개 이하이며 위반 사례는 0이다. main이 없는 노선 85개는 전부 circular를 보유하므로, **184개 노선이 모두 main 또는 circular를 1개 이상 가진다**(공간 골간 universe가 무결하다).
- **auto 층**: source=auto인 124행은 79개 노선에 걸쳐 있으며, 그 79개 노선은 전부 auto만으로 구성된다(agent와 섞인 노선은 0). role은 main(90)과 circular(34)뿐이고, confidence는 전부 high, verified는 전부 False다. 즉 변형이 자명한(단순 왕복이거나 단일 순환인) 노선을 대상으로 한 기계 태깅 층이다.
- **is_loop × role**: circular 103건 가운데 100건이 loop다. 예외는 is_loop=False인 circular 3건으로, 22/977 `수필아파트 순환`(first≠last인 개방형 순환)과 707 `울산대공원 순환`이 이에 해당한다. 반대로 role이 main이면서 is_loop=True인 경우도 11건(예: 101 꽃바위순환의 2방향, 233의 왕복 편도운행) 있다. 따라서 **is_loop만으로는 circular 여부를 판정할 수 없다**.

## 3. base_route_id 의미론 (실측)

| role | base 있음 | base 의미 |
|---|---|---|
| main | 92/187 | **전부 자기참조(base == 자기 route_id)** — "no base"를 다르게 표기한 것에 불과하다. 로더는 base==self를 결측과 동일하게 취급해야 한다 |
| circular | 37/103 | 34건은 자기참조, 3건은 타 행 참조(102→main, 357→main, 707→다른 circular) |
| short_turn | 112/112 | main 32 / circular 79 / **short_turn 1(체인: 147의 …1476→…1475)** |
| detour | 51/51 | main 35 / circular 13 / branch 2 / short_turn 1 (자기참조 0) |
| extension | 10/10 | main 7 / circular 3 (자기참조 0) |
| branch | 14/14 | main 6행(15 trips) / circular 8행(18 trips) |
| duplicate | 2/2 | 같은 route(323)의 main을 참조 |
| anomaly | 0/2 | NaN (955의 고립된 5-stop 파편, evidence에 "data anomaly; defer"로 명시) |

- dangling 참조는 0건이고, 타 노선을 가리키는 참조도 0건이다(base는 항상 같은 route 내부를 가리킨다).
- 파생 행(short_turn·branch·detour·extension)의 base를 역참조했을 때 **base가 canonical(main/circular)이 아닌 경우가 4건**(체인 1 + detour→branch 2 + detour→short_turn 1) 있으므로, 이를 재귀적으로 해소하는 로직이 필요하다.

## 4. schedule_before와의 조인 (canonical 제조의 접점)

조인 키는 **`route_id`** 단일 컬럼이며, 별도 가공 없이 그대로 조인할 수 있다.

- schedule_before는 427,527행이고, distinct route_id는 **487**(여기에 더해 route_id/route_name/trip_id가 모두 NaN인 완전 공백행 48개가 별도로 있다), distinct trip_id는 **7,625**, distinct route_name은 398이다.
- **tags 481은 schedule 487의 부분집합이며(교집합 481), tags → schedule 방향으로는 100% 커버된다.**
- 행 조인율은 425,783/427,527 = **99.59%**, trip 조인율은 7,568/7,625 = **99.25%**다.
- **미태깅된 6개 패턴은 전부 "지원"(등하교 지원) 패턴으로, 총 57 trips**다.

| route_id | route_name | trips |
|---|---|---|
| BR_TAGO_USB103000132 | 13 지원2 (함월고등학교 지원) | 44 |
| BR_TAGO_USB103002362 | 236 지원2 (용연한국가스공사 출발) | 5 |
| BR_TAGO_USB103002363 | 236 지원3 (농소 출발) | 3 |
| BR_TAGO_USB103002364 | 236 지원4 (변전소 출발) | 3 |
| BR_TAGO_USB103008023 | 802 지원3 (우미린2차 푸르지오2차 출발) | 1 |
| BR_TAGO_USB103009242 | 924 지원2 (문수초지원(오후)) | 1 |

  (이들 route_id의 세 번째 블록은 `103…`으로, 본선의 `19x…`와 채번 체계가 다르다 — 지원 계통을 별도로 채번한 것으로 보인다.)
- **frequency 검증: 481행 전부가 schedule의 route_id별 distinct trip 수와 일치하며 불일치는 0이다** — frequency는 신뢰할 수 있는 파생값이다.
- **패턴 결정성**: BR_TAGO(비ACC0) route_id 476개는 **모든 trip의 stop 시퀀스가 완전히 동일하다(가변 사례 0건)**. 즉 route_id가 곧 결정적 패턴이다. 반면 **BR_ACC0(울주 DRT) 11개 중 6개는 trip마다 stop 리스트가 다르다**(울주01의 경우 7 trips에 3종 리스트, 길이 32~42). 이때 n_stops(= evidence의 stop_ids 길이)는 개별 trip의 길이가 아니라 **합집합 순회 시퀀스의 길이**다(울주01: 52 엔트리 / distinct 44 = schedule 합집합 44와 정확히 일치).
- schedule의 route_name은 96.7%(465/481)가 `route + "(" + dir_label + ")"` 재구성과 일치한다. 예외 16건은 다음과 같다: 울주 11건(괄호가 없고 dir_label=`(none)`), 233 3건(dir_label에 주석이 부가됨), 857(내안**에** vs 내안**애** 표기 차이), 928(schedule 쪽 노선명 내부에 후행 공백 `법원순환 `).
- stop_sequence는 숫자(1~165)이며, NaN 48건은 위의 공백행에 해당한다.

## 5. evidence / evidence_compact 구조

- **파일 단위는 노선(route)** 이며 총 184개다. CSV의 route 184개와 **정확히 1:1로 대응한다**(파일명 = route, 누락·잉여 0). BOM은 없다(UTF-8).
- top-level 키는 184개 파일 전부에서 동일하다: `route, route_type, gtfs_note, n_variants, direction_groups, variants, pairs`.
- `Σ n_variants = 481 = CSV 행수`다. 노선별 variants의 route_id 집합은 CSV의 해당 노선 route_id 집합과 일치하며(불일치 0), variant의 frequency·n_stops 역시 CSV와 전 행에서 일치한다(불일치 0).
- variant 키는 `route_id, route_name, dir_label, n_stops, frequency, is_loop, first_raw/first_norm, last_raw/last_norm, stop_names_raw, stop_ids`다. CSV의 first/last는 raw와 일치한다. raw≠norm인 variant는 247/481이다(정규화 흔적).
- **compact은 full에서 `stop_ids`만 제거한 형태**다(variant에서 stop_ids만 빠지고 pairs 등 나머지는 동일함을 확인했다).
- `pairs`는 **"무엇을 근거로 판정했는가"에 대한 답**으로, 같은 노선 내 변형 쌍의 구조 신호를 담으며 총 534쌍이다. 키는 `a, b, name_jaccard, seam_a_to_b, seam_b_to_a, reverse_pair, relationship`다. relationship 분포는 divergent 288, b_short_turn_of_a 111, a_short_turn_of_b 11, b_subset_detour_of_a 32, a_subset_detour_of_b 29, b_subset_unordered_of_a 25, a_subset_unordered_of_b 21, identical_set 12, b_short_turn_of_a_interior 5다. 요컨대 evidence는 "패턴 쌍 관계 신호 + 방향군 + 원 시퀀스"로 role 판정을 뒷받침하는 입력 스냅샷이다.
- **stop_ids 조인**: evidence 전체의 distinct stop_id는 3,389개이며, `ulsan_stops_before.csv`(3,409)에 **100.00% 존재한다**(미존재 0). stops 쪽에 남는 20개는 before 패턴에서 사용되지 않은 정류장이다.
- variant 내부에서 stop_id가 **중복되는 경우가 55건**(그중 first==last인 순환 폐합이 8건) 있으므로, "시퀀스 내 정류장은 유일하다"는 가정을 두어서는 안 된다.
- 주의: `ulsan_bus_route_before.csv`는 한국어 컬럼(노선명, 정류장순서, 원본순서, 정류장명, 위도, 경도, 행정동, 도착시간들, 출발시간들, 도착횟수)으로 구성되어 **route_id/stop_id가 없다**. 따라서 판단층과 ID로 조인할 수 없고 이름 기반 조인만 가능하다. ID 조인의 접점은 schedule_before와 stops_before 두 파일이다.

## 6. 선행 기준값 대조 (diff 신호)

| 기준값 | 선행 구현 | 실측 | 판정 |
|---|---|---|---|
| before General 노선수 | 170 | **170** (+Express 14) | 일치 |
| canonical 379행 | 379 | **main 187 + base-less circular 66 + short_turn 112 + branch 14 = 379** | **정확 일치 — 379의 정체 규명**: base_route_id가 없는 circular만 canonical에 포함(자기참조 34 + 타 행 참조 3 = 37행 제외) |
| main trips | 4,648 | **4,648** (Express 906 포함) | 정확 일치 |
| circular trips | 2,271 | 2,275 | **+4 불일치** |
| short_turn trips | 227 | 319 | **+92 불일치** |
| branch trips | 31 | 33 | **+2 불일치** |
| trip 귀속 100% (7,625) | 100% | 판단층 단독 99.25%(7,568) | **57 trips(지원 6패턴) 미커버** — 선행 구현의 100%는 지원 패턴을 별도로 귀속(모선 13/236/802/924로의 병합 등)한다는 전제 위에 성립 |

불일치의 원인에 대한 가설은 다음과 같다(circ +4 / st +92 / br +2, 합 +98).
1. **후단 재판정 가설**: 선행 구현 로더가 이 판단층 위에 자체 재분류를 얹는다는 가설이다(예: base가 canonical이 아닌 파생 행의 재귀 해소, circ-with-base 37행의 trip 재귀속). 다만 어떤 단일한 자연 분할(confidence·source·base_role·is_loop 기준)로도 92/4/2를 재현하지 못함을 확인했다 — confidence별 st trips(high 258 / medium 60 / low 1)도, base_role별(circular 231 / main 87 / chain 1)도 모두 어긋난다.
2. **스냅샷 버전 가설**: 선행 기준값을 채집한 variant_tags 버전과 이 이식본이 서로 다르다는 가설이다(역할 재태깅 이력이 존재한다). main은 정확히 일치하면서 파생 role만 어긋나는 양상은, 파생 role 경계(특히 short_turn↔detour)의 재판정과 정합적이다.
- 행 379의 규칙(base-less circular)과 trip 2,271의 규칙(전 circular −4)은 **서로 모순**이므로, 선행 구현의 두 기준값은 서로 다른 파이프라인 단계에서 산출된 수치일 가능성이 크다. 재구축 시에는 본 이식본을 기준으로 재정의하고 그 diff를 문서화해야 한다.

Express 판별 규칙 재검증: schedule route_name 기준 4자리 숫자 노선 = {1127, 1137, 1147, 1401, 1421, 1703, 1713, 1723, 1733, 5001~5005} 14개로, tags의 Express 14개와 정확히 일치한다. General에는 4자리 숫자 노선명이 없다. 따라서 **"노선명이 4자리 숫자면 Express"라는 규칙이 raw에서 성립함을 확인했다.**

## 7. 이상치·함정 목록

1. CSV는 BOM+CRLF이므로, `utf-8-sig`를 쓰지 않으면 첫 컬럼명이 `﻿route`가 된다.
2. role은 4종이 아니라 8종이다. duplicate(323)와 anomaly(955) 각 2행은 canonical 제조에서 명시적으로 제외해야 한다.
3. main의 base_route_id 92건은 **자기참조**로, 결측과 혼용된 이중 표기다.
4. short_turn 체인이 1건 있다(147: short_turn의 base가 다시 short_turn을 가리킨다). 1단계 역참조만 가정하면 위반된다.
5. is_loop=False인 circular 3건, is_loop=True인 main 11건이 있다 — is_loop과 circular는 동치가 아니다.
6. 울주(BR_ACC0) 11패턴: trip마다 stop 리스트가 가변적이고(6/11), n_stops는 trip 길이가 아니라 합집합 순회 길이이며, dir_label=`(none)`, route_id는 16자 별도 체계다.
7. variant 내부 stop_id 중복이 55건이다(순환 폐합 8건 포함).
8. schedule_before에 완전 공백행이 48개 있다(route_id·trip_id·stop_sequence 모두 NaN).
9. 미태깅된 지원 패턴이 6개(57 trips) 있다 — 판단층 커버리지는 패턴 기준 481/487(98.8%), trip 기준 99.25%다.
10. direction_group 표기가 혼재한다: `A:`류 프리픽스 325행 vs 자유라벨 156행(한 노선 안에서 두 표기가 혼재하는 경우는 206 단 1개다).
11. dir_label 표기가 흔들린다: 857의 양우내안**애**(tags) vs 내안**에**(schedule), 928의 schedule 노선명 후행 공백이 그 예다.
12. verifier_agree는 auto 124행에서 NaN이다 — boolean으로 파싱할 때 NaN을 허용해야 한다.

## 8. 로더 불변식 제안 (assert 구현용)

§10의 StructuredOutput contract_invariants와 동일하다 — 핵심은 다음과 같다: route_id의 PK·프리픽스, role enum 8종, frequency와 trip 수의 완전 일치, base 참조 무결성 및 main 자기참조, evidence 1:1 대응, stop_ids ⊂ stops_before, 비ACC0 패턴의 결정성.

## 9. 재현 코드 (요지)

```python
import pandas as pd, json, os
BTS = "C:/Users/whtnm/Documents/BTS"
df = pd.read_csv(BTS+"/reference/variant_tagging/variant_tags.csv",
                 encoding="utf-8-sig", dtype=str)          # BOM 필수 처리
assert df["route_id"].is_unique and len(df)==481
sch = pd.read_csv(BTS+"/data/ulsan_route_schedule_before.csv",
                  usecols=["route_id","trip_id"], dtype=str)
trips = sch.dropna().groupby("route_id")["trip_id"].nunique()
cmp = df.set_index("route_id")["frequency"].astype(int).to_frame().join(trips.rename("t"))
assert (cmp["frequency"]==cmp["t"]).all()                   # 실측: 불일치 0
# 379행 재현
canon = df[(df.role=="main") |
           ((df.role=="circular") & df.base_route_id.isna()) |
           (df.role.isin(["short_turn","branch"]))]
assert len(canon)==379
```

감사 스크립트 원본은 세션 스크래치(`audit_vt1.py`~`audit_vt8.py`)에서 실행했으며, 본 문서의 수치는 전부 그 출력이다.
