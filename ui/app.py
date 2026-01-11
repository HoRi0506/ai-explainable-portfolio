import json
import pandas as pd
import streamlit as st
from app.graph import run_demo
from app.settings import DEFAULT_SECTORS, DEFAULT_CONSTRAINTS, FEE_BPS, SLIPPAGE_BPS
from reports.render import render_summary_md

st.set_page_config(page_title="AI 설명형 포트폴리오", layout="wide")
st.title("AI 설명형 포트폴리오 — 데모")

with st.sidebar:
    st.header("입력")
    sectors = st.multiselect("섹터", DEFAULT_SECTORS, default=["AI"])
    budget = st.number_input("예산 (KRW)", min_value=0.0, value=1_000_000.0, step=100_000.0)
    n_stocks = st.number_input("종목 수", min_value=1, max_value=20, value=5, step=1)
    max_w = st.slider("최대 비중", 0.0, 1.0, float(DEFAULT_CONSTRAINTS["max_weight"]))
    min_w = st.slider("최소 비중", 0.0, 1.0, float(DEFAULT_CONSTRAINTS["min_weight"]))
    run = st.button("실행")

tabs = st.tabs(["요약", "종목 설명", "리스크", "주문 계획"])  # 자리표시자

if run:
    try:
        with st.spinner("그래프 실행 중..."):
            result = run_demo({
                "sectors": sectors,
                "budget": budget,
                "n": n_stocks,
                "constraints": {"min_weight": min_w, "max_weight": max_w},
            })
        # 요약 탭: 개요 + 다운로드
        with tabs[0]:
            st.subheader("요약")
            st.write(f"선택 섹터: {', '.join(result.get('sectors', []))}")
            st.json({k: v for k, v in result.items() if k in ("tickers", "weights")})
            md = render_summary_md(result)
            st.download_button("요약 Markdown 다운로드", data=md, file_name="summary.md", mime="text/markdown")
            st.download_button("원본 JSON 다운로드", data=json.dumps(result, ensure_ascii=False, indent=2), file_name="result.json", mime="application/json")

        # 종목 설명: facts/valuation/insider
        with tabs[1]:
            st.subheader("종목 설명")
            facts = result.get("facts", {})
            valuation = {x["ticker"]: x for x in result.get("valuation", [])}
            insider = {x["ticker"]: x for x in result.get("insider", [])}
            for t in result.get("tickers", []):
                with st.expander(f"{t}"):
                    v = valuation.get(t)
                    ins = insider.get(t, {})
                    f = facts.get(t, {})
                    if v:
                        st.markdown(v.get("rationale_md", ""))
                    st.caption("출처")
                    srcs = f.get("sources", [])
                    for s in srcs[:5]:
                        st.write(f"- {s}")
                    if ins:
                        st.caption("인사이더 요약")
                        for b in ins.get("bullets", [])[:3]:
                            st.write(f"- {b}")

        # 리스크: 상관행렬/요약 표시
        with tabs[2]:
            st.subheader("리스크")
            risk = result.get("risk", {})
            st.write(risk.get("notes", "-"))
            # corr은 DataFrame 직렬화 어려워 shape만 제공했으므로, 최소 표 형태로 가리기
            st.info("상관행렬은 콘솔/리포트로 확인 (간소화)")

        # 주문 계획: 표 렌더링
        with tabs[3]:
            st.subheader("주문 계획")
            orders = result.get("orders", [])
            if orders:
                df = pd.DataFrame(orders)
                st.dataframe(df, use_container_width=True)
                st.write(f"수수료 가정: {FEE_BPS}bps, 슬리피지: {SLIPPAGE_BPS}bps")
            else:
                st.warning("주문 데이터가 없습니다.")
        st.toast("실행 완료", icon="✅")
    except Exception as e:
        st.error(f"실행 중 오류: {e}")
else:
    st.info("사이드바에서 파라미터를 선택하고 실행하세요.")
