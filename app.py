import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BTC Signal Dashboard",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    div[data-testid="stMetric"] {
        background: #1a1a2e;
        border: 1px solid #2a2a4a;
        border-radius: 12px;
        padding: 16px;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    days = st.selectbox("Lookback period", [7, 14, 30, 90], index=2)
    refresh = st.button("🔄 Refresh data")
    st.divider()
    st.caption("Data: [CoinMetrics Community API](https://coinmetrics.io) — free, no key needed.")

# ── Data fetching ─────────────────────────────────────────────────────────────
COINMETRICS = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_cm(assets: str, metrics: str, start: str, end: str) -> pd.DataFrame:
    """Fetch from CoinMetrics Community API. Returns wide DataFrame indexed by date."""
    try:
        r = requests.get(COINMETRICS, params={
            "assets": assets,
            "metrics": metrics,
            "frequency": "1d",
            "start_time": start,
            "end_time": end,
            "page_size": 10000,
        }, timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").apply(pd.to_numeric, errors="coerce")
        return df
    except Exception as e:
        st.error(f"CoinMetrics fetch failed: {e}")
        return pd.DataFrame()

if refresh:
    st.cache_data.clear()

end_dt   = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
start_dt = end_dt - timedelta(days=days)
start_s  = start_dt.strftime("%Y-%m-%dT00:00:00Z")
end_s    = end_dt.strftime("%Y-%m-%dT00:00:00Z")

with st.spinner("Fetching data from CoinMetrics…"):
    # BTC: price, exchange supply, exchange flows, 1yr+ held supply, market cap
    df_btc = fetch_cm(
        "btc",
        "PriceUSD,SplyExNtv,FlowInExNtv,FlowOutExNtv,SplyActEver1yr,CapMrktCurUSD",
        start_s, end_s
    )
    # Stablecoins: exchange supply + market cap for SSR
    df_usdt = fetch_cm("usdt", "SplyExNtv,CapMrktCurUSD", start_s, end_s)
    df_usdc = fetch_cm("usdc", "SplyExNtv,CapMrktCurUSD", start_s, end_s)

# ── Derived series ────────────────────────────────────────────────────────────
def col(df, name):
    return df[name] if (not df.empty and name in df.columns) else pd.Series(dtype=float)

btc_price    = col(df_btc, "PriceUSD")
btc_reserve  = col(df_btc, "SplyExNtv")
btc_inflow   = col(df_btc, "FlowInExNtv")
btc_outflow  = col(df_btc, "FlowOutExNtv")
btc_lth      = col(df_btc, "SplyActEver1yr")
btc_mcap     = col(df_btc, "CapMrktCurUSD")
usdt_bal     = col(df_usdt, "SplyExNtv")
usdc_bal     = col(df_usdc, "SplyExNtv")
usdt_mcap    = col(df_usdt, "CapMrktCurUSD")
usdc_mcap    = col(df_usdc, "CapMrktCurUSD")

# Net flow = inflow - outflow (positive = net inflow to exchanges, bearish)
btc_net_flow = (btc_inflow - btc_outflow).rename("NetFlow")

# Stablecoin total on exchanges (USD value — USDT/USDC are ~$1 each)
stable_total = (usdt_bal + usdc_bal).rename("StableTotal")

# SSR = BTC market cap / total stablecoin market cap
stable_mcap  = (usdt_mcap + usdc_mcap)
ssr          = (btc_mcap / stable_mcap).rename("SSR")

# ── Latest values + signals ───────────────────────────────────────────────────
def latest(s): return float(s.iloc[-1]) if not s.empty else None
def trend7(s):
    if s.empty or len(s) < 7: return None
    return float(s.iloc[-1] - s.iloc[-7])

v_price      = latest(btc_price)
v_reserve    = latest(btc_reserve)
v_net_flow   = latest(btc_net_flow)
v_lth        = latest(btc_lth)
v_usdt       = latest(usdt_bal)
v_usdc       = latest(usdc_bal)
v_stable     = latest(stable_total)
v_ssr        = latest(ssr)

t_reserve    = trend7(btc_reserve)   # negative = declining = bullish
t_lth        = trend7(btc_lth)       # positive = rising = bullish

# ── Conviction score ──────────────────────────────────────────────────────────
def conviction(signals):
    pts, rows = 0, []
    def add(label, val, bull):
        nonlocal pts
        p = val if bull else -val
        pts += p
        rows.append((label, f"+{p}" if p > 0 else str(p)))
    if t_lth        is not None: add("LTH supply (1yr+ held) rising",    2, t_lth > 0)
    if v_net_flow   is not None: add("BTC exchange net outflow",          2, v_net_flow < 0)
    if t_reserve    is not None: add("Exchange reserve declining",        2, t_reserve < 0)
    if v_stable     is not None: add("Stablecoin reserves > $70B",        1, v_stable > 70e9)
    if v_ssr        is not None: add("SSR below 6 (high buying power)",   1, v_ssr < 6)
    score = max(1, min(10, round((pts + 8) / 16 * 9 + 1)))
    return score, rows

score, breakdown = conviction({})

def score_color(s):
    if s >= 7: return "#00c896"
    if s >= 5: return "#f0c040"
    return "#ff4b6e"

def fmt_btc(v): return f"{v:,.0f} BTC" if v else "N/A"
def fmt_usd(v):
    if v is None: return "N/A"
    if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"

def sig(bull): return "🟢 Bullish" if bull else "🔴 Bearish"

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📡 BTC On-Chain Signal Dashboard")
st.caption(f"CoinMetrics Community · {days}-day window · Updated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
st.divider()

# ── Top metrics row ───────────────────────────────────────────────────────────
c0, c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1, 1])

with c0:
    c = score_color(score)
    st.markdown(f"""
    <div style='background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;
                padding:16px;text-align:center;height:100%'>
      <div style='font-size:13px;color:#888;margin-bottom:4px'>Accumulation Conviction</div>
      <div style='font-size:64px;font-weight:800;color:{c};line-height:1'>{score}</div>
      <div style='font-size:16px;color:#555'>/ 10</div>
    </div>""", unsafe_allow_html=True)

p7 = (float(btc_price.iloc[-1]) / float(btc_price.iloc[-7]) - 1) * 100 if len(btc_price) >= 7 else None
r7 = (t_reserve / float(btc_reserve.iloc[-7]) * 100) if t_reserve and not btc_reserve.empty and len(btc_reserve) >= 7 else None
l7 = (t_lth / float(btc_lth.iloc[-7]) * 100) if t_lth and not btc_lth.empty and len(btc_lth) >= 7 else None

with c1: st.metric("BTC Price",         f"${v_price:,.0f}" if v_price else "N/A", f"{p7:+.1f}% (7d)" if p7 else None)
with c2: st.metric("LTH Supply (1yr+)", fmt_btc(v_lth),  f"{l7:+.2f}% (7d)" if l7 else None)
with c3: st.metric("Exchange Reserve",  fmt_btc(v_reserve), f"{r7:+.2f}% (7d)" if r7 else None, delta_color="inverse")
with c4: st.metric("Stablecoin Reserves", fmt_usd(v_stable), None)
with c5: st.metric("SSR", f"{v_ssr:.2f}" if v_ssr else "N/A", "High buying power" if v_ssr and v_ssr < 6 else "Lower buying power", delta_color="normal" if v_ssr and v_ssr < 6 else "inverse")

st.divider()

# ── Chart helper ──────────────────────────────────────────────────────────────
def chart(series: pd.Series, title: str, color: str, zero_line=False, fmt=".2f") -> go.Figure:
    fig = go.Figure()
    if series.empty:
        fig.add_annotation(text="No data available", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_color="#666")
    else:
        fill_color = color[:-1] + ",0.08)" if color.startswith("rgb") else color
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            mode="lines", line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=fill_color,
            hovertemplate=f"%{{x|%b %d}}: %{{y:{fmt}}}<extra></extra>",
        ))
        if zero_line:
            fig.add_hline(y=0, line_dash="dash", line_color="#444", line_width=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="#ccc")),
        plot_bgcolor="#0f0f1a", paper_bgcolor="#0f0f1a",
        font_color="#ccc", margin=dict(l=0, r=0, t=36, b=0),
        xaxis=dict(showgrid=False, color="#555"),
        yaxis=dict(showgrid=True, gridcolor="#1e1e2e", color="#555"),
        height=230,
    )
    return fig

# ── BTC charts ────────────────────────────────────────────────────────────────
st.subheader("📈 BTC On-Chain")
a, b = st.columns(2)
with a:
    st.plotly_chart(chart(btc_price,    "Price (USD)",              "#f7931a", fmt="$,.0f"), use_container_width=True)
    st.plotly_chart(chart(btc_reserve,  "Exchange Reserve (BTC)",   "#ff6b6b", fmt=",.0f"),  use_container_width=True)
with b:
    st.plotly_chart(chart(btc_lth,      "LTH Supply — held 1yr+ (BTC)", "#00c896", fmt=",.0f"), use_container_width=True)
    st.plotly_chart(chart(btc_net_flow, "Exchange Net Flow (BTC)",  "#7eb3ff", fmt="+,.0f", zero_line=True), use_container_width=True)

st.divider()

# ── Stablecoin charts ─────────────────────────────────────────────────────────
st.subheader("💵 Stablecoin Metrics")
d, e, f_ = st.columns(3)
with d:  st.plotly_chart(chart(stable_total, "Total Stablecoin Reserves on Exchanges", "#a78bfa", fmt=",.0f"), use_container_width=True)
with e:  st.plotly_chart(chart(usdt_bal,     "USDT on Exchanges",  "#f472b6", fmt=",.0f"), use_container_width=True)
with f_: st.plotly_chart(chart(ssr,          "SSR (BTC mcap / Stablecoin mcap)", "#34d399", fmt=".2f"), use_container_width=True)

st.divider()

# ── Signal table + breakdown ──────────────────────────────────────────────────
left, right = st.columns([3, 2])

with left:
    st.subheader("🔎 Signal Summary")
    rows = [
        ("LTH Supply (1yr+ held, 7d change)", f"{t_lth:+,.0f} BTC" if t_lth else "N/A",   sig(t_lth and t_lth > 0)    if t_lth is not None else "⚪ N/A"),
        ("BTC Exchange Net Flow (latest day)", fmt_btc(v_net_flow),                          sig(v_net_flow and v_net_flow < 0) if v_net_flow is not None else "⚪ N/A"),
        ("Exchange Reserve (7d change)",       f"{t_reserve:+,.0f} BTC" if t_reserve else "N/A", sig(t_reserve and t_reserve < 0) if t_reserve is not None else "⚪ N/A"),
        ("Total Stablecoin Reserves",          fmt_usd(v_stable),                            sig(v_stable and v_stable > 70e9) if v_stable is not None else "⚪ N/A"),
        ("USDT on Exchanges",                  fmt_usd(v_usdt),                              "⚪ Reference"),
        ("USDC on Exchanges",                  fmt_usd(v_usdc),                              "⚪ Reference"),
        ("SSR",                                f"{v_ssr:.2f}" if v_ssr else "N/A",           sig(v_ssr and v_ssr < 6)    if v_ssr is not None else "⚪ N/A"),
    ]
    st.dataframe(
        pd.DataFrame(rows, columns=["Metric", "Value", "Signal"]),
        use_container_width=True, hide_index=True,
        column_config={
            "Signal": st.column_config.TextColumn(width="small"),
            "Value":  st.column_config.TextColumn(width="medium"),
        }
    )

with right:
    st.subheader("🏆 Conviction Breakdown")
    c = score_color(score)
    st.markdown(f"<h1 style='color:{c};font-size:52px;margin:0'>{score}"
                f"<span style='font-size:22px;color:#555'> / 10</span></h1>",
                unsafe_allow_html=True)
    st.caption("Signal alignment score")
    st.dataframe(pd.DataFrame(breakdown, columns=["Factor", "Points"]),
                 use_container_width=True, hide_index=True)

st.divider()

# ── Watch levels ──────────────────────────────────────────────────────────────
st.subheader("👀 Watch Levels")
w1, w2 = st.columns(2)
with w1:
    st.markdown("**BTC**")
    st.markdown(f"- Exchange reserve **< 3.0M BTC** → {'🟢 already below' if v_reserve and v_reserve < 3e6 else '⚪ not yet reached'}")
    st.markdown(f"- Net flow turning **positive** for 3+ days → distribution warning 🔴")
    st.markdown(f"- LTH supply **> 14.75M BTC** → accelerating accumulation {'🟢' if v_lth and v_lth > 14.75e6 else '⚪'}")
with w2:
    st.markdown("**Stablecoins**")
    st.markdown(f"- Total reserves **> $80B** → {'🟢 already above' if v_stable and v_stable > 80e9 else '⚪ not yet'}")
    st.markdown(f"- SSR **< 4.0** → near peak buying power {'🟢' if v_ssr and v_ssr < 4 else '⚪'}")
    st.markdown(f"- SSR **> 8.0** → buying power depleted 🔴")

st.divider()
st.caption("Data: [CoinMetrics Community API](https://docs.coinmetrics.io/api/v4) · Free, no API key required · Cached 1hr")
