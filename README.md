# tennisweather-radar

기상청(KMA) 레이더 실황(HSP)·초단기 예측을 10분마다 수집해 Web Mercator
투명 PNG + frames.json으로 GitHub Pages에 배포하는 파이프라인.

- 실황: API허브 `rdr_cmp_file.php?data=bin&cmp=hsp` (5분 주기, mm/h)
- 예측: API허브 `nph-qpf_ana_imgp` (+10~+120분, 10분 간격)
- 바람: API허브 `nph-dfs_vsrt_grd` (초단기예보 U·V, 시간당)
- 산출: `https://wogus3602.github.io/tennisweather-radar/frames.json`

로컬 실행: `KMA_APIHUB_KEY=... python3 -m pipeline.run` → `site/`
테스트: `python3 -m unittest discover -s tests -v`

## 라이선스 및 출처

이 저장소는 **상업적 이용을 포함해** 자유롭게 쓸 수 있도록 구성돼 있다.
아래 두 층위가 각각 다른 조건을 따르므로 구분해서 본다.

### 1. 파이프라인 코드 — Apache License 2.0

이 저장소의 소스 코드(`pipeline/`, `tests/`)는 [Apache-2.0](LICENSE)이다.
상업적 이용·수정·재배포가 허용되며, 저작권 고지와 라이선스 사본을 포함하면 된다.

### 2. 기상 데이터 — 기상청, 공공누리 제1유형(출처표시)

파이프라인이 수집·가공하는 레이더 실황·초단기 예측·초단기예보 데이터의 출처는
**기상청 API허브**(<https://apihub.kma.go.kr>)이며, **공공누리 제1유형(출처표시)** 로
개방되어 있다.

공공누리 제1유형은 다음을 허용한다:

- ✅ **상업적 이용 가능**
- ✅ **변형·2차저작물 작성 가능** (본 파이프라인의 재투영·리샘플링·색상 매핑이 여기 해당)
- ⚠️ **출처표시 필수**

따라서 이 데이터를 이용하는 서비스는 **출처를 표시해야 한다.** 표시 예:

> 본 저작물은 기상청에서 공공누리 제1유형으로 개방한 **레이더 영상 합성(HSP)·
> 초단기 예측(QPF)·초단기예보 바람(DFS)** 데이터를 이용하였으며, 해당 데이터는
> 기상청 API허브(<https://apihub.kma.go.kr>)에서 무료로 받을 수 있습니다.

앱(TennisWeather)의 레이더 화면은 지도 하단 어트리뷰션에 **기상청**을 표기해 이 의무를
이행한다.

### 3. 인증키

API허브 인증키(`KMA_APIHUB_KEY`)는 **가입 회원 본인만** 사용할 수 있으며 양도·대여할 수
없다. 따라서 키는 저장소에 커밋하지 않고 GitHub Secrets / 로컬 환경변수로만 주입한다.

### 4. 배경지도는 이 저장소 범위 밖

앱의 레이더 화면이 깔고 쓰는 **배경지도 타일은 이 저장소가 제공하지 않는다**
(별도 저장소 `tennisweather-basemap`, OpenStreetMap 기반). 배경지도는 자체 출처표시
(`© OpenStreetMap contributors`)를 따로 요구한다.

> ⚠️ 과거 앱은 CARTO 호스팅 타일(`basemaps.cartocdn.com`)을 썼는데, CARTO의 호스팅
> 베이스맵은 **상업 이용 시 Enterprise 계약이 필요**하다. 그래서 자체 호스팅으로 교체했다.
> 무료 타일 서비스 상당수(CARTO·MapTiler·Stadia 등)가 "무료 = 비상업"으로 제한하니,
> 타일 제공자를 바꿀 때는 반드시 약관을 먼저 확인할 것.
