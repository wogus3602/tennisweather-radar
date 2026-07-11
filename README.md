# tennisweather-radar

기상청(KMA) 레이더 실황(HSP)·초단기 예측을 10분마다 수집해 Web Mercator
투명 PNG + frames.json으로 GitHub Pages에 배포하는 파이프라인.

- 실황: API허브 `rdr_cmp_file.php?data=bin&cmp=hsp` (5분 주기, mm/h)
- 예측: API허브 `nph-qpf_ana_imgp` (+10~+120분, 10분 간격)
- 산출: `https://wogus3602.github.io/tennisweather-radar/frames.json`

로컬 실행: `KMA_APIHUB_KEY=... python3 -m pipeline.run` → `site/`
테스트: `python3 -m unittest discover -s tests -v`
데이터 출처: 기상청 API허브(apihub.kma.go.kr)
