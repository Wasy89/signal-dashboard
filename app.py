import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone
import time

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BTC Signal Dashboard",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background: #1a1a2e;
        border-radius: 12px;
        padding: 16px 20px;
        border: 1px solid #2a2a4a;
    }
    .conviction-score {
        font-size: 72px;
        font-weight: 800;
        line-height: 1;
    }
    .signal-bull { color: #00c896; }
    .signal-bear { color: #ff4b6e; }
    .signal-neutral { color: #aaaaaa; }
    div[data-testid="stMetric"] {
        background: #1a1a2e;
        border: 1px solid #2a2a4a;
        border-radius: 12px;
        padding: 16px;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ──────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    # Use secret if deployed on Streamlit Cloud, otherwise show input
    default_key = st.secrets.get("GLASSNODE_API_KEY", "") if hasattr(st, "secrets") else ""
    api_key = st.text_input("Glassnode API Key", type="password",
                             value=default_key,
                             help="Get a free key at studio.glassnode.com/settings/api")
    if not api_key:
        st.warning("⚠️ API key required. [Get a free key →](https://glassnode.com)", icon="🔑")
    days = st.selectbox("Lookback period", [7, 14, 30, 90], index=2)
    st.divider()
    st.caption("Data is fetched live from Glassnode on every load.")
    refresh = st.button("🔄 Refresh data")

# ── Helpers ──────────────────────────────────────────────────────────────────────
GLASSNODE = "https://api.glassnode.com/v1/metrics"

@st.cache_data(ttl=3600, show_spinner=False)
def fetch(endpoint: str, asset: str, since: int, until: int, api_key: str = "") -> pd.DataFrame:
    params = {"a": asset, "i": "24h", "s": since, "u": until}
    if api_key:
        params["api_key"] = api_key
    try:
        r = requests.get(f"{GLASSNODE}/{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return pd.DataFrame()
        # SSR returns o.v; everything else returns v
        rows = []
        for item in data:
            t = item["t"]
            v = item.get("v") if item.get("v") is not None else item.get("o", {}).get("v")
            rows.append({"date": datetime.utcfromtimestamp(t), "value": v})
        return pd.DataFrame(rows).dropna()
    except Exception as e:
        st.warning(f"Failed to fetch {endpoint} ({asset}): {e}")
        return pd.DataFrame()

def latest(df: pd.DataFrame) -> float | None:
    return float(df["value"].iloc[-1]) if not df.empty else None

def pct_change(df: pd.DataFrame, n: int = 7) -> float | None:
    if df.empty or len(df) < n:
        return None
    return (df["value"].iloc[-1] - df["value"].iloc[-n]) / df["value"].iloc[-n] * 100

def signal_tag(bullish: bool | None) -> str:
    if bullish is True:  return "🟢 Bullish"
    if bullish is False: return "🔴 Bearish"
    return "⚪ Neutral"

def fmt_large(n: float | None) -> str:
    if n is None: return "N/A"
    if abs(n) >= 1e12: return f"${n/1e12:.2f}T"
    if abs(n) >= 1e9:  return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:  return f"${n/1e6:.2f}M"
    if abs(n) >= 1e3:  return f"{n:,.0f}"
    return f"{n:.4f}"

def fmt_btc(n: float | None) -> str:
    if n is None: return "N/A"
    return f"{n:,.0f} BTC"

def conviction_score(signals: dict) -> tuple[int, list]:
    """Returns (score 1-10, breakdown list of (label, points))."""
    breakdown = []
    total = 0

    def add(label, points, condition):
        nonlocal total
        pts = points if condition else -points
        total += pts
        breakdown.append((label, f"+{pts}" if pts > 0 else str(pts)))

    if signals.get("lth_trend") is not None:
        add("LTH supply rising", 2, signals["lth_trend"] > 0)
    if signals.get("net_flow") is not None:
        add("Exchange net outflow (BTC)", 2, signals["net_flow"] < 0)
    if signals.get("reserve_trend") is not None:
        add("Exchange reserve declining", 2, signals["reserve_trend"] < 0)
    if signals.get("stable_total") is not None:
        add("Stablecoin reserves > $70B", 1, signals["stable_total"] > 70e9)
    if signals.get("usdt_flow") is not None:
        add("USDT flowing into exchanges", 1, signals["usdt_flow"] > 0)
    if signals.get("ssr") is not None:
        add("SSR below 6 (high buying power)", 1, signals["ssr"] < 6)

    # Normalize to 1-10 (max raw = +9, min = -9)
    score = round((total + 9) / 18 * 9 + 1)
    score = max(1, min(10, score))
    return score, breakdown

def score_color(score: int) -> str:
    if score >= 7: return "#00c896"
    if score >= 5: return "#f0c040"
    return "#ff4b6e"

def make_chart(df: pd.DataFrame, title: str, color: str, fmt: str = ",.0f",
               zero_line: bool = False) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.add_annotation(text="No data", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font_color="#666")
    else:
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["value"],
            mode="lines", line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=color.replace(")", ",0.08)").replace("rgb", "rgba"),
            hovertemplate=f"%{{x|%b %d}}: %{{y:{fmt}}}<extra></extra>",
        ))
        if zero_line:
            fig.add_hline(y=0, line_dash="dash", line_color="#444", line_width=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="#ccc")),
        plot_bgcolor="#0f0f1a", paper_bgcolor="#0f0f1a",
        font_color="#ccc", margin=dict(l=0, r=0, t=36, b=0),
        xaxis=dict(showgrid=False, showline=False, color="#555"),
        yaxis=dict(showgrid=True, gridcolor="#1e1e2e", color="#555"),
        height=220,
    )
    return fig

# ── Load data ─────────────────────────────────────────────────────────────────
if refresh:
    st.cache_data.clear()

now    = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp())
since  = now - days * 86400

with st.spinner("Fetching live data from Glassnode…"):
    df_price    = fetch("market/price_usd_close",                          "BTC",  since, now, api_key)
    df_lth      = fetch("supply/lth_sum",                                  "BTC",  since, now, api_key)
    df_flow     = fetch("transactions/transfers_volume_exchanges_net",      "BTC",  since, now, api_key)
    df_reserve  = fetch("distribution/balance_exchanges",                  "BTC",  since, now, api_key)
    df_usdt_bal = fetch("distribution/balance_exchanges",                  "USDT", since, now, api_key)
    df_usdc_bal = fetch("distribution/balance_exchanges",                  "USDC", since, now, api_key)
    df_usdt_flow= fetch("transactions/transfers_volume_exchanges_net",      "USDT", since, now, api_key)
    df_ssr      = fetch("indicators/ssr",                                  "BTC",  since, now, api_key)

# Combined stablecoin total
if not df_usdt_bal.empty and not df_usdc_bal.empty:
    df_stable = df_usdt_bal.copy()
    merged = df_usdt_bal.merge(df_usdc_bal, on="date", suffixes=("_usdt", "_usdc"))
    df_stable = merged.assign(value=merged["value_usdt"] + merged["value_usdc"])[["date","value"]]
else:
    df_stable = pd.DataFrame()

# Signal values
signals = {
    "lth_trend":     pct_change(df_lth),
    "net_flow":      latest(df_flow),
    "reserve_trend": pct_change(df_reserve),
    "stable_total":  latest(df_stable),
    "usdt_flow":     latest(df_usdt_flow),
    "ssr":           latest(df_ssr),
}
score, breakdown = conviction_score(signals)

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📡 BTC On-Chain Signal Dashboard")
st.caption(f"Live data · Last updated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC · {days}-day window")
st.divider()

# ── Top row: conviction score + key metrics ───────────────────────────────────
col_score, col_price, col_lth, col_reserve, col_stable, col_ssr = st.columns([1.2,1,1,1,1,1])

with col_score:
    c = score_color(score)
    st.markdown(f"""
    <div style='background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:16px;text-align:center'>
        <div style='font-size:13px;color:#888;margin-bottom:4px'>Accumulation Conviction</div>
        <div style='font-size:64px;font-weight:800;color:{c};line-height:1'>{score}</div>
        <div style='font-size:16px;color:#555'>/ 10</div>
    </div>
    """, unsafe_allow_html=True)

price_val  = latest(df_price)
lth_val    = latest(df_lth)
res_val    = latest(df_reserve)
stable_val = latest(df_stable)
ssr_val    = latest(df_ssr)

price_delta = pct_change(df_price, 7)
lth_delta   = pct_change(df_lth,   7)
res_delta   = pct_change(df_reserve, 7)

with col_price:
    st.metric("BTC Price",
              f"${price_val:,.0f}" if price_val else "N/A",
              f"{price_delta:+.2f}% (7d)" if price_delta else None)

with col_lth:
    st.metric("LTH Supply",
              fmt_btc(lth_val),
              f"{lth_delta:+.2f}% (7d)" if lth_delta else None)

with col_reserve:
    st.metric("Exchange Reserve",
              fmt_btc(res_val),
              f"{res_delta:+.2f}% (7d)" if res_delta else None,
              delta_color="inverse")

with col_stable:
    st.metric("Stablecoin Reserves",
              fmt_large(stable_val),
              None)

with col_ssr:
    st.metric("SSR",
              f"{ssr_val:.2f}" if ssr_val else "N/A",
              "Low = bullish" if ssr_val and ssr_val < 6 else "High = caution",
              delta_color="inverse" if ssr_val and ssr_val >= 6 else "normal")

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────
st.subheader("📈 BTC On-Chain Metrics")
c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(make_chart(df_price,   "Price (USD)",          "#f7931a"), use_container_width=True)
    st.plotly_chart(make_chart(df_reserve, "Exchange Reserve (BTC)","#ff6b6b",  fmt=",.0f"), use_container_width=True)
with c2:
    st.plotly_chart(make_chart(df_lth,     "LTH Supply (BTC)",     "#00c896",  fmt=",.0f"), use_container_width=True)
    st.plotly_chart(make_chart(df_flow,    "Exchange Net Flow (BTC)","#7eb3ff", fmt="+,.0f", zero_line=True), use_container_width=True)

st.divider()
st.subheader("💵 Stablecoin Exchange Metrics")
c3, c4, c5 = st.columns(3)
with c3:
    st.plotly_chart(make_chart(df_stable,    "Total Stablecoin Reserves (USD)", "#a78bfa", fmt=",.0f"), use_container_width=True)
with c4:
    st.plotly_chart(make_chart(df_usdt_flow, "USDT Net Flow to Exchanges (USD)", "#f472b6", fmt="+,.0f", zero_line=True), use_container_width=True)
with c5:
    st.plotly_chart(make_chart(df_ssr,       "SSR (Stablecoin Supply Ratio)",   "#34d399", fmt=".2f"), use_container_width=True)

st.divider()

# ── Signal table + conviction breakdown ──────────────────────────────────────
col_sig, col_break = st.columns([3, 2])

with col_sig:
    st.subheader("🔎 Signal Summary")
    flow_val   = latest(df_flow)
    usdt_f_val = latest(df_usdt_flow)

    rows = [
        ("BTC LTH Supply (7d change)",      f"{lth_delta:+.2f}%"   if lth_delta  else "N/A", lth_delta and lth_delta > 0),
        ("BTC Exchange Net Flow",            fmt_btc(flow_val),                                flow_val  and flow_val  < 0),
        ("BTC Exchange Reserve (7d change)", f"{res_delta:+.2f}%"   if res_delta  else "N/A", res_delta and res_delta < 0),
        ("Total Stablecoin Reserves",        fmt_large(stable_val),                            stable_val and stable_val > 70e9),
        ("USDT Net Flow to Exchanges",       fmt_large(usdt_f_val) if usdt_f_val else "N/A",  usdt_f_val and usdt_f_val > 0),
        ("SSR",                              f"{ssr_val:.2f}"       if ssr_val    else "N/A",  ssr_val   and ssr_val   < 6),
    ]

    sig_df = pd.DataFrame(rows, columns=["Metric", "Value", "_bull"])
    sig_df["Signal"] = sig_df["_bull"].map(lambda b: signal_tag(b))
    st.dataframe(
        sig_df[["Metric", "Value", "Signal"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Signal": st.column_config.TextColumn(width="small"),
            "Value":  st.column_config.TextColumn(width="medium"),
        }
    )

with col_break:
    st.subheader("🏆 Conviction Breakdown")
    c = score_color(score)
    st.markdown(f"<h1 style='color:{c};font-size:48px;margin:0'>{score}<span style='font-size:24px;color:#555'> / 10</span></h1>", unsafe_allow_html=True)
    st.caption("Based on current signal alignment")
    break_df = pd.DataFrame(breakdown, columns=["Factor", "Points"])
    st.dataframe(break_df, use_container_width=True, hide_index=True)

st.divider()

# ── Watch levels ─────────────────────────────────────────────────────────────
st.subheader("👀 Watch Levels")
w1, w2 = st.columns(2)
with w1:
    st.markdown("**BTC**")
    if res_val:
        breach = "🟢 conviction upgrades" if res_val > 3_000_000 else "🔴 already breached"
        st.markdown(f"- Exchange reserve **< 3.0M BTC** → {breach}")
    if lth_val:
        st.markdown(f"- LTH supply **> 14.75M BTC** → accelerating accumulation")
    if flow_val:
        direction = "outflow" if flow_val < 0 else "inflow"
        st.markdown(f"- Exchange net flow turning positive → distribution caution flag")
with w2:
    st.markdown("**Stablecoins**")
    if stable_val:
        breach = "🟢 buying pressure intensifying" if stable_val > 80e9 else "⚪ not yet reached"
        st.markdown(f"- Total reserves **> $80B** → {breach}")
    if ssr_val:
        breach = "🟢 near peak buying power" if ssr_val < 4 else "⚪ not yet"
        st.markdown(f"- SSR **< 4.0** → {breach}")
    st.markdown("- USDT net flow turning negative → capital rotating out, caution")

st.divider()
st.caption("Built with Streamlit · Data from Glassnode · Refreshes every hour (cached)")
