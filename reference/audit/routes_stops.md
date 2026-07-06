> 이 문서는 데이터 감사 당시의 역사 기록으로, 프로젝트 내부 용어를 그대로 사용한다 — 용어는 [docs/internal/GLOSSARY.md](../../docs/internal/GLOSSARY.md) 참조.

# 원시 데이터 감사 — routes_stops

- 감사 일자: 2026-07-04
- 대상: `data/ulsan_bus_route_before.csv`, `data/ulsan_bus_route_after.csv`, `data/ulsan_stops_before.csv`, `data/ulsan_stops_after.csv` (교차검증용으로 `data/ulsan_route_schedule_before.csv` 포함)
- 도구: `python` + pandas. 모든 수치는 추정 없이 실측한 값이다.

---

## 1. 파일 개요 (실측)

| 파일 | 행수 | 컬럼수 | 인코딩 | 기본키(실측상 유일) |
|---|---|---|---|---|
| ulsan_bus_route_before.csv | 21,402 | 10 | UTF-8 **BOM** | (노선명, 정류장순서) — 중복 0 |
| ulsan_bus_route_after.csv | 17,841 | 11 | UTF-8 **BOM** | (route_id, stop_sequence) — 중복 0 |
| ulsan_stops_before.csv | 3,409 | 4 | UTF-8 **BOM** | stop_id — 중복 0 |
| ulsan_stops_after.csv | 3,224 | 6 | UTF-8 **BOM** | stop_id — 중복 0 |
| ulsan_route_schedule_before.csv | 427,527 (원시) | 8 | UTF-8 **BOM** | — (아래 NaN 블록 참조) |

**네 파일 모두 UTF-8 BOM으로 인코딩되어 있다.** 따라서 로더는 반드시 `encoding='utf-8-sig'`로 읽어야 하며, 그렇지 않으면 첫 컬럼명이 `﻿노선명`으로 깨진다.

### 스키마

- **bus_route_before** — 헤더가 전부 한국어로 되어 있다: `노선명, 정류장순서, 원본순서, 정류장명, 위도, 경도, 행정동, 도착시간들, 출발시간들, 도착횟수`. route_id와 stop_id는 어느 것도 존재하지 않는다.
- **bus_route_after** — 영문과 한국어가 혼합되어 있다: `route_id, route_name, stop_sequence, stop_name, stop_lat, stop_lon, 권역, 행정동, 도착시간들, stop_id, 도착횟수`. 출발시간은 없고 도착시간만 제공된다.
- **stops_before** — `stop_id, stop_name, stop_lat, stop_lon`
- **stops_after** — `stop_id, stop_name, stop_lat, stop_lon, zone, admin_dong`

### 결측

- bus_route_before: `행정동`이 NaN인 행이 351행(55개 노선)인데, 이는 전부 **울산 밖에 위치한 정류장**이다(모화=경주, 웅상·서창·덕계=양산 등). 그 밖의 컬럼에서는 결측이 0이다.
- bus_route_after / stops_before / stops_after: 결측 0, 빈 문자열 0이다.
- schedule_before: **전체가 NaN인 행 48개가 연속 블록으로 존재한다**(index 299,864~299,911, `50(남창 방면)`의 마지막 trip 직후). 이는 원본 CSV에 들어 있던 빈 줄 블록으로, 이를 제거하면 부분 NaN이 0이 되고 데이터는 427,479행이 된다.

---

## 2. 구조 차이: bus_route before vs after (초점 1)

| 항목 | before | after |
|---|---|---|
| 노선 키 | **노선명 문자열** (방면 포함, 400개) | **route_id 숫자** (348개, 패턴 단위) |
| 노선명 | `"10(성안청구 방면)"` 형태가 389/400, 괄호 없는 것 11개(울주01~10, 김해공항) | `"중구01"`, `"1114"` 등 base명만 (184개) |
| 정류장 키 | **없음** (정류장명과 좌표뿐) | stop_id (정수 문자열) |
| 순서 | 정류장순서(1..N 연속 100%)와 **원본순서** | stop_sequence (1..N 연속 100%) |
| 시각 | 도착시간들 + 출발시간들 (계획 스케줄 집계) | 도착시간들만 (실운행 로그 집계) |
| 지역 | 행정동 | 권역 + 행정동 |

- after에서 `route_id → route_name` 대응은 함수적이다(위반 0). 그러나 반대 방향은 1:N이어서, **84개 route_name이 각각 2~7개의 route_id**를 가진다(예: 북구13 → 7개). 즉 after의 route_id는 방향·변형에 따른 **패턴 단위 id**다. route_name당 패턴 수 분포는 {1:100, 2:28, 3:44, 4:4, 5:5, 6:2, 7:1}이다.
- before의 노선명(방면) 역시 유사한 패턴 축을 이루지만, 한 단계 더 굵은 단위다. schedule_before를 기준으로 보면 route_id 487개 ↔ 노선명 398개 ↔ base 190개로 대응한다. 즉 **계층 구조는 base 노선(190) > 방면 노선명(398) > route_id 패턴(487) > trip(7,625)** 순이다.

### before의 `원본순서`의 정체 (실측 규명)

- 전체의 87.4% 행에서 `원본순서 == 정류장순서`가 성립한다. 불일치는 2,696행(78개 노선)이고, `(노선명, 원본순서)` 조합의 중복은 392행(54개 노선)이다.
- 예를 들어 `101(꽃바위순환)`의 (정류장순서, 원본순서)는 (1,1),(2,2),(3,2),(4,3),(5,3),… 로 이어져, 원본순서가 둘씩 반복된다.
- 그 원인은 §3의 핵심 발견과 동일하다. before 시퀀스는 여러 운행 패턴을 병합한 것이고, 원본순서는 병합 전 각 원 패턴 내부 순서가 남긴 흔적이다.

---

## 3. 핵심 발견: bus_route_before 시퀀스 = trip 패턴들의 합집합 (canonical 아님)

schedule_before(공통 교집합 398개 노선명)와 before의 정류장열을 대조한 결과는 다음과 같다.

- **311/398**: before 시퀀스가 schedule의 특정 trip 패턴과 **완전히 일치**한다(대부분 단일 패턴 노선이다).
- 나머지 **87/398 전부**: before의 정류장 **집합이 해당 노선명의 전체 trip 패턴 정류장 합집합과 동일**하다(87/87, 예외 0).
  - 예를 들어 `101(꽃바위순환)`은 schedule의 최빈 패턴이 33개 정류장인 데 비해 before는 73개 정류장으로, 3개 패턴의 합집합에 해당한다.
  - 유형별로 분해하면, 최빈 패턴이 before의 비연속 부분열인 경우 39개, before가 최빈 패턴의 부분열인 경우 32개, 순서가 상이한 경우 14개, 연속 부분열인 경우 2개다.

**설계 함의: bus_route_before의 정류장열을 canonical main 시퀀스로 그대로 사용해서는 안 된다.** 그것은 변형(단축·지선·순환)을 모두 합쳐 놓은 합집합 마스터이기 때문이다. canonical은 schedule의 trip 패턴과 `reference/variant_tagging/variant_tags.csv` 판단층으로부터 제조해야 한다. (이는 선행 구현 정본 로직의 "canonical 379행"과 정합하는 실측 근거다.)

### 도착횟수 집계 대조 (before ↔ schedule)

- before의 `도착횟수` 총합은 **427,732**인 반면, schedule의 데이터 행수는 **427,479**다.
- 그 차이 253은 (schedule에 없는 2개 노선의 400회)에서 (147회 누락)을 뺀 값이다.
- 노선×정류장 셀 17,675개 중 **17,661개(99.92%)가 완전히 일치**한다. 불일치 14셀은 모두 `33(현대백화점 순환)` 계열로, schedule은 60 trips 분량인 데 비해 before는 47 trips 분량이다(13 trips ≈ 147행 차이). 즉 before 집계가 이 노선의 일부 trip을 누락한 것이다.
- 결론적으로 **bus_route_before는 schedule_before를 집계한 산물**이며(동일 원천), 교차검증에서의 가치는 "합집합 마스터 + 방면 노선명 카탈로그" 역할에 있다. 시각 레벨 정보는 schedule이 정본이다.

### 노선 집합 대조

- schedule 노선명 398개는 before 노선명 400개의 부분집합(⊂)이다. **before에만 있는 노선은 2개**다:
  - `50(내고산 방면)` — 정류장 6개, 도착횟수 48. schedule에는 50번의 다른 3개 방면만 존재한다.
  - `김해공항` — 정류장 8개, 도착횟수 352. 공항 리무진이다(stop_id 체계도 별도이며, §5 참조).
- trip_id는 7,625개로 선행 기준값 7,625와 일치한다. trip_id 형식은 `route_id + "_OrdNNN"`이며(100%), trip_id → route_id 대응은 함수적이다(위반 0).
- schedule의 route_id 접두사는 `BR_TAGO_USB*`(427,479행 중 424,831행)와 `BR_ACC*`(2,648행 = **울주01~10 마을버스 전부**)로 나뉜다.

---

## 4. General/Express 명명 규칙 재도출 (초점 4)

before의 base 노선명(방면 괄호를 제거한 것) **191개**의 형태 분포는 다음과 같다.

| 형태 | 수 | 목록/비고 |
|---|---|---|
| 숫자 1~3자리 | 160 | 10~995, 번대 분포 {0:20, 100:21, 200:15, 300:14, 400:22, 500:3, 700:16, 800:9, 900:40} |
| **숫자 4자리** | **14** | 1127, 1137, 1147, 1401, 1421, 1703, 1713, 1723, 1733 (1000번대 9개) + **5001~5005** (5000번대 5개) |
| 울주+2자리 | 10 | 울주01~10 (마을버스, route_id 접두사 BR_ACC) |
| "N 지원M" 변형 | 6 | `13 지원2`, `236 지원2/3/4`, `802 지원3`, `924 지원2` (지원운행 변형 — 공백이 포함된 이름이므로 주의) |
| 기타 | 1 | 김해공항 (리무진) |

급행 = 4자리 숫자 = 정확히 **14개**다(5000번대는 4자리의 부분집합이므로, "4자리 + 5000번대"는 사실상 "4자리" 하나로 환원된다). General = 191 − 급행14 − 지원6 − 김해공항1 = **170**이다(= 숫자 1~3자리 160 + 울주 10). 즉 선행 구현의 General 170에는 울주 마을버스 10개가 포함된다.

**단, 이 규칙은 after로 이식할 수 없다.** after의 4자리는 26개인데(1114~1733 17개, 2100/2300 좌석형, 3000(4정류장)/3100(8정류장) 셔틀형, 5001~5005), 여기에 더해 구명 지선 74개(남구16·동구7·북구13·중구9·울주23·순환6)가 신설되었다. 따라서 개편 후 급행 판정에는 별도의 규칙이 필요하다.

### 개편 전후 노선 대응 (부수 실측)

- before base 191개와 after route_name 184개의 **교집합은 56개뿐**이다. 노선번호 체계가 개편 과정에서 대부분 교체된 것이다.
- route_id 체계 역시 단절되었다. schedule_before의 숫자 코어와 after route_id의 교집합은 1개(`196003133`, before `313(삼동초 방면)` ↔ after `313`)에 불과하며, 이것이 유일하게 살아남은 id다.

---

## 5. stops 조인율 (초점 2·3)

### schedule_before → stops_before: **100%**

- schedule의 stop_id 3,397개가 전부 stops_before에 존재한다(행 기준 조인율 1.0).
- stops_before에서 사용되지 않는 12개는 김해공항 노선 정류장 8개(`BS_KTDB_*`)와 `50(내고산 방면)` 전용 4개로, schedule에 없는 2개 노선의 전용 정류장과 정확히 일치한다.
- stops_before의 stop_id 접두사 분포는 `BS_TAGO_` 3,398개, `BS_KTDB_` 8개(공항 리무진), `BS_ACC0_` 3개(울주06·07이 사용하는 지곡회관·내기마을·초전마을)다.

### bus_route_before → stops_before: stop_id가 없어 **이름 + 최근접 좌표 조인이 필수**

- 이름 완전일치 기준으로, 유니크 1,760개 중 **1,759개가 매칭된다(99.94%)**(행 기준 99.995%).
- 유일한 실패는 `양우내안에`(308(봉계 방면), stop 1개)와 stops_before의 **`양우내안애`** 사이의 대응인데, 좌표 거리는 **0.9m**에 불과하다. 오탈자에서 비롯된 이형(異形)이며, 별칭 1건을 하드코딩하면 해소할 수 있다.
- **좌표를 exact 문자열로 조인하면 63.5%만 성공한다.** before 좌표에 1~5자리로 반올림 자릿수가 섞여 있기 때문이다(5자리 19,150행 / 4자리 2,043행 / 1~3자리 209행). 따라서 exact 좌표 조인은 금지한다.
- 같은 이름 내 최근접 거리는 **최대 0.91m**다(p99 0.91m, 10m 초과 0건). 따라서 `(정류장명 일치) AND (거리 ≤ 1m 최근접)` 조건으로 **3,409개 stop 전부를 일의적으로 매핑할 수 있다**.
- **bus_route_before의 유니크 (정류장명, 위도, 경도) 조합은 3,409개로 stops_before의 행수와 같다.** 즉 stops_before는 정확히 bus_route_before의 stop 우주다. 이름당 stop 수 분포는 {1:335, 2:1297, 3:57, 4:56, 5:7, 6:6, 7:2}로, 2개가 방향쌍으로서 지배적이며, 이는 stop/place 2레벨 문법의 실측 근거가 된다.
- stops_before의 stop_name 유니크는 1,759개로, 선행 구현의 place 총수 기준값 1,759와 같다(place 병합 결과 수와 이름 유니크 수가 일치한다는 신호다).

### bus_route_after → stops_after: **float 함정 통과 후 100%**

- **stops_after의 stop_id는 3,224행 전부가 `"N.0"` 형태의 float 문자열이다**(예: `5067.0`). CSV 생성 시 float dtype가 남긴 잔재다. 반면 bus_route_after는 정수 문자열(`14824`)이다.
- raw 문자열을 그대로 조인하면 **0%**다. `.0`을 제거해 정규화하면 **3,224/3,224 = 100%(양방향 집합 동일)**이 되어, after의 route 파일과 stops 파일이 완전히 정합한다.
- 조인 후 stop_name은 100% 일치하고, 좌표 거리는 최대 1.37m(p99 1.1m)다.

### stops_before ↔ stops_after: id 단절

- id 체계가 완전히 단절되어 있고(`BS_TAGO_USB…` vs 숫자), 숫자 꼬리만 뽑아 대조해도 교집합은 **0**이다.
- 이름 교집합은 1,525개로 before 이름의 86.7%에 해당한다. 개편 전후 정류장 대응은 이름과 좌표로만 가능하다.

---

## 6. 좌표 품질 (초점 5)

| 파일 | NaN | 0값 | 위경도 스왑 | 한국 밖 | 울산 bbox 밖* |
|---|---|---|---|---|---|
| bus_route_before | 0 | 0 | 0 | 0 | 2 |
| bus_route_after | 0 | 0 | 0 | 0 | 0 |
| stops_before | 0 | 0 | 0 | 0 | 2 |
| stops_after | 0 | 0 | 0 | 0 | 0 |

*bbox = lat 35.2~35.85, lon 128.9~129.6. bbox 밖 2건은 **김해공항 국제선청사·국내선**(lon≈128.95, 리무진 노선)으로, 오류가 아니라 실재하는 시외 정류장이다. before 좌표 범위는 lat [35.17079, 35.71386], lon [128.94754, 129.47401]다.

- stops_after의 `zone`에 **`양산시`가 1건**(방기, stop_id 5067) 있다. 시외 인접 정류장이며, admin_dong은 "울산광역시 울주군 삼남읍"으로 채워져 있다(zone과 admin_dong이 불일치하는 사례다).
- 좌표 정밀도는 before가 5자리 위주이고(4자리 이하 2,252행이 혼재), after route는 6자리, stops_after는 8자리인데, 여기에 더해 **14~15자리 부동소수점 잔재가 20행** 있다(예: `35.552508270000004`). float 재직렬화 흔적이므로 round(6)으로 정규화할 것을 권장한다.

---

## 7. 시각 포맷 (초점: 인코딩·포맷 함정)

| 원천 | 포맷 | 시 범위 | 자정 넘김 규약 |
|---|---|---|---|
| bus_route_before 도착/출발시간들 | `"H시 M분 S초"`, 파이프(`\|`) 구분 | 4~**25** | **24·25시 연장 표기** (도착 440, 출발 444 토큰) |
| schedule_before arrival/departure | `"H:MM:SS"` — **zero-pad가 비일관적(73.5%만 2자리)** | 4~**25** | 24시 이상 440행 (before route 파일과 동수 = 동일 원천이라는 방증) |
| bus_route_after 도착시간들 | `"HH:MM:SS"`, 파이프 구분 (단, 아래 예외 존재) | 0~23 | **0시(359토큰)·1시(8토큰) wrap-around 표기** — before와 규약이 다름! |

- **bus_route_after에서 도착횟수==1인 2,318행은 전부 `"H시 M분 S초"` 한국어 포맷**이고, 나머지 15,523행은 전부 `HH:MM:SS`다(혼합 행 0). 생성 파이프라인의 dtype 분기(단일값 vs 리스트)가 남긴 흔적이다. 따라서 파서는 두 포맷을 모두 처리해야 한다.
- `도착횟수 == 시간 토큰 수` 관계는 before·after 모두에서 **100% 일치**한다(불일치 0행).
- before는 도착과 출발을 분리해 제공하는데, 토큰의 96.4%가 서로 달라 정차시간이 실존함을 보여 준다. schedule에서 arrival==departure인 비율은 3.6%다.
- **경계 함정**: before(24시+)와 after(0시 wrap)의 심야 규약이 서로 다르므로, 시간층을 비교할 때 서비스데이 정규화(예: 03시를 기준으로 절단한 뒤 +24h) 없이 비교하면 심야 배차가 왜곡된다.

---

## 8. 선행 기준값 대조 요약

| 기준값 | 실측 | 판정 |
|---|---|---|
| trip 7,625 | 7,625 | **일치** |
| before General 170 | 191 base − 급행14 − 지원6 − 김해공항1 = 170 | **재현** (울주01~10 포함이 전제) |
| 급행 14 (4자리+5000번대) | 4자리 숫자 = 14 (5000번대 ⊂ 4자리) | **일치** (규칙은 "4자리"로 환원 가능) |
| place 1,759 | stops_before stop_name 유니크 = 1,759 | **동수** (병합 결과가 이름 유니크 수와 일치한다는 신호) |

---

## 9. 로더 계약 (invariant — assert 구현용)

1. 4개 CSV 모두 `encoding='utf-8-sig'`로 읽는다(BOM). 첫 컬럼명에 `﻿`가 없음을 assert한다.
2. `bus_route_before`: `(노선명, 정류장순서)`가 유일하고, 노선명별 정류장순서가 1..N 연속이며, 행수 21,402, 노선명 400개다.
3. `bus_route_after`: `(route_id, stop_sequence)`가 유일하고, route_id별로 1..N 연속이며, `groupby(route_id).route_name.nunique()==1`, 행수 17,841, route_id 348개, route_name 184개다.
4. `stops_before`: stop_id가 유일(3,409개)하고, `stops_after`: stop_id가 유일(3,224개)하며, 좌표에 NaN·0이 없고, lat∈[35.17,35.72], lon∈[128.94,129.48]다.
5. `stops_after.stop_id`는 전행이 `^\d+\.0$` 형태다. 로더는 `.0`을 strip해 정규화하며, 정규화 후 `set(bus_route_after.stop_id) == set(stops_after.stop_id)`(양방향 100%)를 만족한다.
6. `schedule_before`: 전체 NaN 행 정확히 48개(연속 블록)를 제거한다. 제거 후 부분 NaN은 0이고 행수는 427,479, `trip_id == route_id + "_Ord\d{3}"`가 전행에서 성립하며, trip_id는 7,625개다.
7. `set(schedule_before.stop_id) ⊆ set(stops_before.stop_id)`이고, 차집합(stops_before 측)은 정확히 12개 = 김해공항 8(KTDB) + 50(내고산) 전용 4다.
8. `bus_route_before`의 정류장명 → stops_before 이름 조인 실패는 `양우내안에`(→`양우내안애`) 1건뿐이며, 같은 이름 내 최근접 좌표 거리 ≤ 1m로 전 stop을 매핑한다(초과 시 fail).
9. `bus_route_before`의 유니크 (정류장명, 위도, 경도) 수 == len(stops_before) == 3,409다.
10. `도착횟수 == len(도착시간들.split('|'))`가 before·after 전행에서 성립한다.
11. 시각 파서: before/schedule은 시 4~25를 허용하고(24+ 연장 표기), after는 0~23을 허용한다. after 파서는 `HH:MM:SS`와 `H시 M분 S초`를 겸용해야 한다(한국어 포맷 = 도착횟수 1 행).
12. `set(schedule_before.route_name) ⊂ set(bus_route_before.노선명)`이고, 차집합 == {`50(내고산 방면)`, `김해공항`}이다.
13. canonical을 제조할 때 bus_route_before 시퀀스를 main으로 사용하지 않는다(패턴 합집합이기 때문 — §3).

---

## 부록: 재현 코드 (핵심 스니펫)

```python
import pandas as pd, numpy as np, re

# 로딩 규약
rb = pd.read_csv("data/ulsan_bus_route_before.csv", encoding="utf-8-sig", dtype=str)
sa = pd.read_csv("data/ulsan_stops_after.csv",      encoding="utf-8-sig", dtype=str)
sch = pd.read_csv("data/ulsan_route_schedule_before.csv", encoding="utf-8-sig", dtype=str)
sch = sch[~sch.isna().all(axis=1)]           # 전체-NaN 48행 제거 (idx 299864~299911)
sa["stop_id"] = sa["stop_id"].str.replace(r"\.0$", "", regex=True)  # float 함정 정규화

# before stop -> stops_before: 이름 blocking + 최근접(≤1m)
# (같은 이름 내 최근접 거리 max 0.91m 실측 — exact 좌표 조인은 63.5%에서 실패)

# 급행 재도출: 4자리 숫자 base 노선명 == 14개
base = rb["노선명"].str.replace(r"\(.*\)$", "", regex=True).str.strip().unique()
express = [n for n in base if re.fullmatch(r"\d{4}", n)]   # 14개, 5000번대 포함

# rb 시퀀스 = trip 패턴 합집합 검증: 불일치 87개 노선 전부에서
# set(rb 정류장) == union(모든 trip 패턴 정류장) (87/87)
```
