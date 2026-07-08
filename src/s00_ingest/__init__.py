"""s00_ingest — 원시 데이터 로딩과 형태 정규화 (design.md §5 s00).

이 단계는 원시 파일을 읽고 컬럼명, 타입, 시간 표현을 표준화하는 데만 머문다.
BOM, dtype, 전결측 제거, 시각→service_s, 컬럼 표준화, name→stop 해소만 수행한다.
role 해석, base 정규화, canonical 선정, 병합 같은 의미 판단은 s01 이후 단계의 책임이다.
"""
