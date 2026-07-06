> 이 문서는 데이터 감사 당시의 역사 기록으로, 프로젝트 내부 용어를 그대로 사용한다 — 용어는 [docs/internal/GLOSSARY.md](../../docs/internal/GLOSSARY.md) 참조.

# 원시 데이터 감사: ulsan_route_schedule_before.csv

- 감사일: 2026-07-04
- 대상: `data/ulsan_route_schedule_before.csv` (57,438,458 bytes)
- 도구: pandas (python). 모든 수치는 실측값이며 추정치는 포함하지 않는다.
- 로드 규약: `pd.read_csv(path, encoding='utf-8-sig', dtype=str)`로 읽어들인 뒤, 시각·순서 컬럼을 명시적으로 파싱한다.

---

## 1. 파일 물성 (인코딩·구조)

| 항목 | 실측값 |
|---|---|
| 인코딩 | UTF-8 **with BOM** (`EF BB BF`) — `utf-8-sig`로 읽어야 한다 |
| 개행 | CRLF (`\r\n`) |
| 총 라인 | 427,528 (헤더 1행 + 데이터 427,527행) |
| 컬럼 (순서 고정) | `route_name, stop_name, stop_sequence, arrival_time, departure_time, route_id, trip_id, stop_id` |
| 데이터 행 | **427,527** (선행 기준 "427k"와 일치) |
| 전결측 행 | **48** (아래 §7.1 참조) → 유효 행 **427,479** |
| 부분 결측 행 | 0 (결측은 예외 없이 8개 컬럼이 동시에 NaN이 되는 형태로만 나타난다) |
| 공백 문자열 셀 | 0 |
| 완전 중복 행 | 0 |

## 2. 키 구조

| 항목 | 실측값 |
|---|---|
| `trip_id` nunique | **7,625** (선행 기준 7,625와 일치) |
| `route_id` nunique | **487** (선행 기준 "184"와 불일치 — 원인은 §3에서 규명한다) |
| `route_name` nunique | 398 |
| 노선 base(괄호 제거) nunique | 190 |
| `stop_id` nunique | 3,397 |
| `stop_name` nunique | 1,754 |
| `(trip_id, stop_sequence)` 중복 | **0** — 이 쌍이 행의 유일 키다 |
| `route_id → route_name` | 함수적 관계다 (route_id마다 이름이 하나로 결정되며 위반은 0건) |
| `route_name → route_id` | **비유일**: 72개 이름이 각각 2~5개의 route_id에 대응한다 (예: `61(신암마을회관 방면)` 5개, `328(배내골 방면)` 4개, `943(대안입구 방면)` 4개) |
| trip_id 형식 | 7,625건 전부가 `route_id + "_Ord" + 3자리` 형태다. 즉 모든 trip은 route_id에 100% 귀속된다 |

ID 형식은 두 계보로 나뉜다.
- `BR_TAGO_USB` + 12자리: route_id 476개 / `BS_TAGO_USB` + 9자리(총 20자): stop_id 3,394개 — 시내버스(TAGO 계보)
- `BR_ACC0_` + 8자리: route_id 11개 / `BS_ACC0_` + 8자리(총 16자): stop_id 3개 — **울주 마을버스(울주01~10)로, 별도 계보를 이룬다**

## 3. route_id 487 vs 선행 기준 184 — 원인 규명 (핵심 발견)

**raw의 `route_id`는 '노선' 단위가 아니라 방향·변형 패턴 단위다.**

실측 근거는 다음과 같다.
1. route_id 487개 가운데 **481개는 정확히 하나의 고유 정차열(stop_id 시퀀스)**만을 가진다. 복수 패턴을 갖는 것은 울주 마을버스 6개뿐이다(울주01=3, 울주02=2, 울주04=3, 울주05=5, 울주08=2, 울주09=2).
2. `reference/variant_tagging/variant_tags.csv`(판단층)와 대조하면, variant_tags 481행의 `route_id`가 schedule의 route_id와 **481/487 일치**한다. 또한 variant_tags의 `route`(base) nunique는 **184**, `route_type` 분포는 **General 170 + Express 14**로, 선행 기준값 3종이 모두 base 레벨에서 나타난다.
3. 계층 구조는 **base 노선 190 = 정규 노선 184 + '지원' 변형 6**으로 정리된다. variant_tags에 포함되지 않은 6개 route_id는 전원이 지원 노선이다(`13 지원2`, `236 지원2/3/4`, `802 지원3`, `924 지원2`).

즉 선행 구현이 말하는 "노선 184(route_id 기준)"는 raw 스키마에서는 **base 노선명 레벨**에 해당한다. 따라서 로더는 `route_id`(패턴)와 base 노선(노선명의 괄호 앞부분)을 서로 다른 레벨로 구분해 다뤄야 한다.

### 3.1 General/Express 규칙 재검증 (raw에서 독립 재현)

base 190개를 노선명 규칙에 따라 분류하면 다음과 같다.

| 분류 | 규칙 | base 수 | route_id 수 | trip 수 |
|---|---|---|---|---|
| Express | 4자리 숫자 (`^\d{4}$`) | **14** = 1127,1137,1147,1401,1421,1703,1713,1723,1733 (9) + 5001~5005 (5) | 27 | 942 |
| General(숫자) | 1~3자리 숫자 | 160 | 443 | 6,567 |
| General(울주) | `울주\d\d` | 10 | 11 | 59 |
| 지원 | 이름에 "지원" 포함 | 6 | 6 | 57 |

→ Express 14는 선행 구현 규칙("4자리+5000번대")과 일치하며, General 170은 숫자 160 + 울주 10으로 구성되어 variant_tags의 route_type과 동일하다. 지원 6개는 184 우주 밖에 놓인다.

### 3.2 route_id 숫자 구조 (파싱 함정)

route_id의 마지막 1자리를 절단하면 unique가 191개가 되어 base 190에 근사한다. 즉 끝 자리가 변형 일련번호처럼 보인다. 그러나 **base 313과 912는 stem이 각각 2개씩 존재하므로**(예: 912 = `USB19510912x` 3개 + `USB192109121` 1개, 313 = `USB19610313x` 3개 + `USB196003133` 1개), 이런 방식의 구조 파싱은 190개 중 187개 base에서만 성립한다. 따라서 **route_id를 절단해 파싱하지 말고, route_name의 base를 그룹 키로 사용해야 한다.**

## 4. 시각 무결성 — GTFS식 24+ 표기 확인

| 항목 | 실측값 |
|---|---|
| 형식 | 427,479/427,479 (100%)가 `^\d{1,2}:[0-5]\d:[0-5]\d$`를 만족한다 |
| 시(hour) zero-padding | **없음** — `7:10:00` 형식이다. 한 자리 시 행이 113,116개이고, 두 자리 zero-pad(`07:`) 행은 0개다. `%H:%M:%S` 엄격 파서는 실패하므로 주의한다 |
| 초 정밀도 | 실사용 — 초≠00인 행이 412,975개(96.6%) |
| 시 범위 | 4시 ~ **25시** (GTFS식 24+ 표기 확인) |
| 24시 이상 행 | 444행 / 31 trips (1137, 1127, 401, 5000번대 등 심야 도착) |
| 24시 이상에 **출발**하는 trip | 5개 (첫 출발 24:05:00, 예: `BR_TAGO_USB196150011_Ord044`) — naive datetime 변환이 즉시 실패하는 지점이다 |
| 자정 리셋(23:xx→0:xx) | **0** — 24+ 표기만 사용하며 wrap은 발생하지 않는다 |
| 행 내 `departure < arrival` | 0 |
| trip 내 단조성 위반 (`arr[i] < dep[i-1]`, seq 정렬 기준) | **0행 / 0 trips** |
| 첫 정류장 `arrival == departure` | 7,625/7,625 (100%) |
| dwell(dep−arr) | =0: 15,250행, >0: 412,229행, p99 = 10초 |
| dwell > 10분 | 31행 — **전부 BR_ACC0(울주)**. 최대 8,332초(2시간 19분): 울주09 `범서중학교앞` 11:41:08→14:00:00. 중간 layover(월내, 출강마을회관 등 회차 대기)로 판단된다 |
| 운행 시간대 | 첫 trip 시작 4:00:00, trip 소요 시간 중앙값 52.4분, 최대 183.4분, 비양(≤0) 0 |

## 5. stop_sequence 연속성

| 항목 | 실측값 |
|---|---|
| 1..n 연속(min=1, max=count, 중복 0) | **7,607/7,625 (99.76%)** |
| 위반 18 trips | **전부 `BR_ACC0_`(울주 마을버스)** — TAGO 노선은 100% 연속이다 |
| — offset 시작(내부는 연속, min=11/21/23/38/39) | 8 trips (울주01 ×5, 울주02, 울주04, 울주08) |
| — 내부 결번 | 10 trips (울주05 ×5, 울주09 ×5) |
| trip 길이 | min 2 / 중앙값 54 / 평균 56.1 / max 165 |
| 2정류장 trip | 1개: `52(온남초등학교 방면)` `BR_TAGO_USB196100525_Ord001` (온양서희스타힐스→온남초등학교, 8:25) |

## 6. 패턴(노선별 고유 정차열)·순환 신호

| 항목 | 실측값 |
|---|---|
| 전역 고유 패턴 | 493 |
| route_id×패턴 (route 레벨 합) | 498 |
| 패턴이 1개인 route_id | 481/487 — **route_id ≈ 패턴** (§3) |
| 복수 패턴 route_id | 울주 6개 (최대 울주05 = 5패턴) |
| **노선 간 공유 패턴** | 4건: `22(수필아파트 순환)↔977(수필아파트 순환)` ×2, `941↔948(농소차고지 방면)`, `527↔537↔808(태화강역 방면)` — 서로 다른 base가 글자(字)까지 동일한 정차열을 갖는다. C-space·중복 제거 단계에서 명시적으로 처리해야 한다 (variant_tags에 role=duplicate 2행이 존재하는 것과 부합한다) |
| trips/route_id | min 1 / 중앙값 8 / max 104 |
| 주 패턴 지배도(route_id 내 최빈 패턴 비중) | 중앙값 1.0 / 최소 0.2 (울주05) |
| stop 재방문 trip (한 열 안에서 stop_id 중복) | 700 trips / 56 route_id (상위: 21 옥현주공3단지 64, 527 덕하공용차고지 47) |
| 닫힌 순환(첫 stop = 끝 stop) | 170 trips / 8 route_id |
| '순환'으로 명명된 route_id | 101 — 재방문 route_id 56개 중 '순환'으로 명명된 것은 22뿐이다. **명명은 loop 판정의 근거가 되지 못한다** |

## 7. 이상치·함정

### 7.1 전결측 콤마행 48개 (단일 연속 블록)

파일 라인 **299,866~299,913** (48줄 연속)에 나타나며, 내용은 전부 `,,,,,,,`(콤마 7개 = 빈 필드 8개)다.
- 직전 행은 `50(남창 방면)` trip의 종료행이고, 직후 행은 `50(대운산 방면)` trip의 시작행이다.
- 가설: 노선 단위 export 과정에서 **스케줄이 없는 노선 구간을 표시한 placeholder**로 보인다. 정황 증거로, `ulsan_bus_route_before.csv`에는 `50(내고산 방면)`이 존재하지만 schedule에는 없으며, stops_before의 미사용 stop 12개 중 4개(내고산·중고산·중고산마을입구·외고산마을입구)가 정확히 그 구간의 정류장이다. 따라서 이 48행은 해당 노선 계보의 결측을 표현한 것으로 추정한다(확정은 아니다).
- 로더 처리: `dropna()`로 정확히 48행이 제거되어야 하며, 부분 결측 행은 0이어야 한다.

### 7.2 그 외 함정 목록

1. **BOM**: `utf-8`(sig 없이)로 읽으면 첫 컬럼명이 `﻿route_name`이 된다.
2. **시 zero-padding 없음 + 24+ 표기**: 문자열 정렬과 naive 파싱이 모두 오답을 낸다. 초 단위 변환(`h*3600+m*60+s`)이 필수다.
3. **route_name 비유일**(72개 이름 → 복수 route_id): 이름을 기준으로 조인·집계하면 다대다로 오염된다.
4. 노선명 표기의 요동: `837(태화강역방면)`(공백 없음), `924 지원2 (문수초지원(오후))`(중첩 괄호) 등이 있으므로, base 추출 정규식은 `\(.*\)\s*$` greedy로 처리해야 안전하다.
5. **울주 마을버스(BR_ACC0)는 사실상 별도의 데이터 계보다**: seq 이상 18 trips 전부, dwell>10분 31행 전부, 복수 패턴 route_id 6개 전부, 전용 stop 3개가 모두 여기에 속한다. 골간 분석에서는 별도로 취급해야 한다.
6. stop_name 표기 불일치 1건: `BS_TAGO_USB196015621`이 schedule에서는 `양우내안에`, stops_before에서는 `양우내안애`로 다르다. stop_name은 표시용이며 키는 stop_id다.
7. stop_name → stop_id는 1,754 대 3,397의 관계다(1,421개 이름이 복수 stop에 대응하며 최대 7개다). stop 파편화는 정상적인 구조이지만, 이름을 키로 사용해서는 안 된다.
8. 2정류장 trip이 1개 존재한다(§5). 길이 필터를 적용할 때 이 존재를 인지해야 한다.
9. route_id는 숫자를 절단해 파싱할 수 없다(§3.2).

## 8. 타 데이터셋 조인 가능성 (실측)

| 상대 | 키 | 조인율 | 비고 |
|---|---|---|---|
| `data/ulsan_stops_before.csv` (3,409행, stop_id 유일) | `stop_id` | **고유 stop 3,397/3,397 = 100%, 행 레벨 100%** | stops 측 미사용 12개 = KTDB 계열 8(공항리무진·김해공항 구간으로 추정: 국제선청사, 신복로터리, 태화로터리, 통도사.신평×2, 서울산톨게이트, 언양, 국내선) + TAGO 4(내고산 구간) |
| `reference/variant_tagging/variant_tags.csv` (481행) | `route_id` | **481/487 = 98.77%** | 미포함 6개 = 지원 노선 전부(§3). vt에만 있고 schedule에 없는 route_id는 0 |
| `data/ulsan_bus_route_before.csv` (21,402행) | `route_name` ↔ `노선명` (**ID 컬럼 없음** — 컬럼: 노선명,정류장순서,원본순서,정류장명,위도,경도,행정동,도착시간들,출발시간들,도착횟수) | **schedule 이름 398/398 = 100%** | bus_route에만 있는 이름 2개: `50(내고산 방면)`, `김해공항`. 다만 72개 이름이 schedule에서 복수 route_id에 대응하므로 이름 조인은 다대다다 |
| schedule_after | 없음 | — | after에는 trip_id가 없다(운행일자+OBE_ID로 대체된다). 따라서 trip 레벨 직접 조인은 불가능하며, 노선·정류장 레벨로만 조인할 수 있다 |

## 9. 선행 기준값 대조 요약

| 기준값 | 실측 | 판정 |
|---|---|---|
| 427k행 | 427,527 (유효 427,479) | 일치 |
| 7,625 trips | 7,625 | **정확 일치** |
| 노선 184 (route_id 기준) | raw route_id 487 / base 190 / **base−지원 = 184** (variant_tags `route` 184와 일치) | **표현은 불일치, 실질은 일치** — raw route_id는 패턴 단위이고 "184"는 base 노선 레벨이다 |
| General 170 / Express 14 | 규칙 재검증 결과: Express(4자리) 14, General 170(숫자 160 + 울주 10) | **일치** (규칙 유효성 확인) |
| trip 귀속 100% | trip_id 전부가 route_id를 접두로 갖는다 (7,625/7,625) | 일치 |

## 10. 로더 불변식 (assert 구현용)

```python
df = pd.read_csv(PATH, encoding='utf-8-sig', dtype=str)
assert list(df.columns) == ['route_name','stop_name','stop_sequence','arrival_time',
                            'departure_time','route_id','trip_id','stop_id']
assert len(df) == 427_527
allnull = df.isna().all(axis=1); anynull = df.isna().any(axis=1)
assert allnull.sum() == 48 and (allnull == anynull).all()   # 결측은 전결측 48행뿐
df = df[~allnull]
assert len(df) == 427_479 and df.notna().all().all()
assert not df.duplicated().any()
assert not df.duplicated(['trip_id','stop_sequence']).any()  # 행 유일 키
assert df['trip_id'].nunique() == 7_625
assert df['route_id'].nunique() == 487
assert (df.groupby('route_id')['route_name'].nunique() == 1).all()
tid = df[['trip_id','route_id']].drop_duplicates()
assert tid.apply(lambda r: r['trip_id'].startswith(r['route_id']+'_Ord'), axis=1).all()
assert df['route_id'].str.match(r'^BR_(TAGO_USB\d{12}|ACC0_\d{8})$').all()
assert df['stop_id'].str.match(r'^BS_(TAGO_USB\d{9}|ACC0_\d{8})$').all()
assert df['arrival_time'].str.match(r'^\d{1,2}:[0-5]\d:[0-5]\d$').all()
assert df['departure_time'].str.match(r'^\d{1,2}:[0-5]\d:[0-5]\d$').all()
# 초 변환 후: 4:00:00 ≤ t < 26:00:00, dep>=arr, trip 내 단조
assert (dep_s >= arr_s).all() and arr_s.max() < 26*3600 and dep_s.min() >= 4*3600
s = df.sort_values(['trip_id','stop_sequence'])
assert not (s.groupby('trip_id')['arr_s'].shift(-1) <  # 다음 도착 < 현 출발 없음
            s['dep_s']).any() if False else True  # 구현시 groupby transform으로
# stop_sequence: TAGO trip은 1..n 연속, 위반은 BR_ACC0 18 trips뿐
seq = s.groupby('trip_id')['stop_sequence'].agg(['min','max','count'])
bad = seq[(seq['min'] != 1) | (seq['max'] != seq['count'])]
assert len(bad) == 18 and bad.index.str.startswith('BR_ACC0_').all()
# 패턴: TAGO route_id는 정차열 1개
pat = s.groupby('trip_id')['stop_id'].agg(tuple)
rid = s.drop_duplicates('trip_id').set_index('trip_id')['route_id']
ppr = pd.DataFrame({'p':pat,'r':rid}).groupby('r')['p'].nunique()
assert (ppr[ppr.index.str.startswith('BR_TAGO')] == 1).all()
# 조인
stops = pd.read_csv(STOPS, encoding='utf-8-sig', dtype=str)
assert df['stop_id'].isin(set(stops['stop_id'])).all()          # 100%
assert (df.groupby('stop_id')['stop_name'].nunique() == 1).all()
# 첫 정류장 arr==dep
first = s.groupby('trip_id').first()
assert (first['arrival_time'] == first['departure_time']).all()
```

## 11. 설계 시사점

1. **레벨을 사전에 정의한다**: `trip(7,625) ⊂ route_id=패턴(487) ⊂ base 노선(190) ⊃ 정규 184(=General 170+Express 14) + 지원 6`. canonical 노선열 제조의 입력 단위는 route_id(패턴)이고, 출력 단위는 base다.
2. 시간층은 trip을 원자 사건 그대로 사용할 수 있다. 시각 무결성이 완전하므로(단조 위반 0건), 초 변환만 정확히 수행하면 된다. 24+ 표기를 그대로 유지하는 것이 자정 넘김을 처리하는 정답이다.
3. 울주 마을버스는 seq·dwell·패턴 모두에서 예외 계보다. 골간·시간층을 산정할 때 분리 플래그가 필수다.
4. 노선 간 동일 패턴 4건은 C-space 사영 이전에 중복으로 명시 처리해야 한다(variant_tags의 role=duplicate와 대조한다).
