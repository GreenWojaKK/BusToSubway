"""bts.io — raw 접근의 유일한 관문 (design.md §3).

이 패키지 밖에서의 data/ 직접 읽기(pd.read_csv("data/…"))는 리뷰 거부 대상이다.
`stop_id`/`route_id`라는 컬럼명은 이 패키지 내부에서만 존재할 수 있다 —
개명(route_id→pattern_id)과 stop_id 표준화는 s00 build가 수행한다.
"""


class ContractViolation(Exception):
    """로더의 raw 불변식 위반 — DataFrame을 반환하지 않고 즉시 실패한다."""
