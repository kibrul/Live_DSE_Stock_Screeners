
import streamlit as st
import pandas as pd
import time
from datetime import datetime, timedelta

try:
    from bdshare import get_current_trade_data, get_basic_historical_data, get_market_status, BDShareError
    BDSHARE_AVAILABLE = True
except ImportError:
    BDSHARE_AVAILABLE = False

st.set_page_config(page_title="DSE Live Open Scanner - Real bdshare", layout="wide", page_icon="📈")
from zoneinfo import ZoneInfo
DHAKA_TZ = ZoneInfo('Asia/Dhaka')

st.title("📈 DSE Live Open Scanner v2 - REAL bdshare")
st.caption("Ordinary Common ONLY | Price 3-900 | Gainer >=3% | Live when market open + 15-min VWAP Reclaim")

with st.sidebar:
    st.header("Filters")
    price_min = st.number_input("Price Min", value=3.0, min_value=1.0, max_value=1000.0)
    price_max = st.number_input("Price Max", value=900.0, min_value=1.0, max_value=5000.0)
    gainer_min = st.number_input("Gainer Min %", value=3.0, min_value=0.0, max_value=20.0)
    ordinary_only = st.checkbox("Ordinary Common ONLY", value=True)
    auto_refresh = st.checkbox("Auto-refresh every 5 min", value=False)
    refresh_sec = st.slider("Refresh seconds", 60, 600, 300)
    if st.button("🔄 Refresh Now", use_container_width=True):
        st.rerun()

def is_ordinary(symbol, company=None):
    s = str(symbol).upper()
    bad = ['MF','BOND','PBOND','SUKUK','FUND','1ST','2ND','3RD','DEBENTURE','PREF','MUTUAL']
    for b in bad:
        if b in s:
            return False
    if company and any(x in str(company).upper() for x in ['MUTUAL FUND','BOND','SUKUK']):
        return False
    return True

def get_live_data():
    if not BDSHARE_AVAILABLE:
        st.error("bdshare not installed. Run: pip install bdshare")
        return pd.DataFrame()
    try:
        df = get_current_trade_data()
        if df.empty:
            return df
        # Debug
        st.write("Debug - Raw columns:", df.columns.tolist())
        st.write("Debug - Sample row:", df.head(1).to_dict(orient='records')[0] if not df.empty else {})
        # Normalize
        df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]
        # Price column detection
        price_candidates = ['ltp','last','close','close_price','last_price','price','ltp_price']
        price_col = next((c for c in price_candidates if c in df.columns), None)
        if not price_col:
            # find first numeric col not volume/trade/value
            for c in df.columns:
                if c not in ['volume','trade','value','change'] and pd.api.types.is_numeric_dtype(df[c]):
                    price_col = c
                    break
        if not price_col:
            st.error(f"No price column found. Columns: {df.columns.tolist()}")
            return pd.DataFrame()
        df['ltp'] = pd.to_numeric(df[price_col], errors='coerce')
        df['price'] = df['ltp']
        # Change col
        change_candidates = ['change','change_%','change_percent','percent_change','%change','chg','change_pct']
        change_col = next((c for c in change_candidates if c in df.columns), None)
        if change_col:
            df['change'] = pd.to_numeric(df[change_col], errors='coerce')
        else:
            ycp_candidates = ['ycp','yesterday_close','prev_close','previous_close','yp','yesterday']
            ycp_col = next((c for c in ycp_candidates if c in df.columns), None)
            if ycp_col:
                df['ycp'] = pd.to_numeric(df[ycp_col], errors='coerce')
                df['change'] = ((df['ltp'] - df['ycp']) / df['ycp'] * 100).round(2)
            else:
                df['change'] = 0.0
        # Volume
        vol_col = next((c for c in ['volume','vol','total_volume','qty'] if c in df.columns), None)
        if vol_col:
            df['volume'] = pd.to_numeric(df[vol_col], errors='coerce').fillna(0)
            df['volume_m'] = (df['volume'] / 1_000_000).round(2)
        else:
            df['volume'] = 0
            df['volume_m'] = 0
        # Symbol
        sym_col = next((c for c in ['symbol','trading_code','code','ticker','instrument'] if c in df.columns), None)
        if sym_col and sym_col != 'symbol':
            df['symbol'] = df[sym_col]
        # Ensure trade/value/high/low exist
        for col in ['trade','value','high','low','open']:
            if col not in df.columns:
                df[col] = 0
        return df
    except Exception as e:
        st.error(f"Failed to fetch live data: {e}")
        import traceback
        st.code(traceback.format_exc())
        return pd.DataFrame()

def calculate_tier(row):
    # Use price column (enriched df has price, not ltp)
    price = row.get('price', row.get('ltp', 0))
    vol_m = row.get('volume_m',0)
    rvol = row.get('rvol',1)
    reclaim = row.get('reclaim','NO')
    try:
        price = float(price)
    except:
        price = 0
    if price>=10 and price<=900 and vol_m>1 and rvol>1.8 and reclaim=='YES':
        return 'A+'
    elif reclaim=='YES' and rvol>=1.2:
        return 'B'
    else:
        return 'C'

def enrich_with_history(df):
    enriched = []
    progress = st.progress(0)
    for i, (idx, row) in enumerate(df.iterrows()):
        symbol = str(row['symbol'])
        # row may have ltp or price
        ltp_val = row.get('ltp', row.get('price', 0))
        try:
            end = datetime.now().date()
            start = end - timedelta(days=40)
            hist = get_basic_historical_data(str(start), str(end), symbol)
            if hist is not None and not hist.empty:
                hist.columns = [str(c).lower().strip().replace(' ', '_') for c in hist.columns]
                avg_vol = hist['volume'].mean() if 'volume' in hist.columns else 1
                vol_now = row.get('volume',0)
                rvol = (vol_now / avg_vol) if avg_vol>0 else 1
                if 'high' in hist.columns and 'low' in hist.columns:
                    hist['range'] = hist['high'] - hist['low']
                    adr = hist['range'].tail(14).mean()
                else:
                    adr = 0
                gap = 0
                if 'open' in hist.columns and len(hist)>1 and 'close' in hist.columns:
                    try:
                        gap = ((hist['open'].iloc[-1] - hist['close'].iloc[-2]) / hist['close'].iloc[-2] * 100) if hist['close'].iloc[-2]!=0 else 0
                    except:
                        gap = 0
                vwap = hist['close'].mean() if 'close' in hist.columns else ltp_val
                vwap_dist = ((ltp_val - vwap)/vwap*100) if vwap!=0 else 0
                reclaim = 'YES' if ltp_val > vwap and rvol>1.2 else 'NO'
            else:
                rvol, adr, gap, vwap, vwap_dist, reclaim = 1, 0, 0, ltp_val, 0, 'NO'
        except Exception as ex:
            # st.write(f"Hist error {symbol}: {ex}")
            rvol, adr, gap, vwap, vwap_dist, reclaim = 1, 0, 0, ltp_val, 0, 'NO'
        enriched.append({
            'symbol': symbol,
            'price': float(ltp_val) if pd.notna(ltp_val) else 0,
            'ltp': float(ltp_val) if pd.notna(ltp_val) else 0,
            'change_pct': float(row.get('change',0)),
            'volume_m': float(row.get('volume_m',0)),
            'rvol': round(float(rvol),2),
            'adr': round(float(adr),2),
            'gap_pct': round(float(gap),2),
            'vwap': round(float(vwap),2),
            'vwap_dist': round(float(vwap_dist),2),
            'reclaim': reclaim,
            'high': float(row.get('high', ltp_val)),
            'low': float(row.get('low', ltp_val)),
            'trade': int(row.get('trade',0)),
            'value_mn': round(float(row.get('value',0))/1_000_000,2) if 'value' in row else 0
        })
        progress.progress((i+1)/len(df))
    progress.empty()
    return pd.DataFrame(enriched)

if not BDSHARE_AVAILABLE:
    st.warning("Install first: pip install bdshare streamlit pytz pandas")
    st.code("pip install bdshare streamlit pytz pandas")
    st.stop()

try:
    status = get_market_status()
    st.info(f"Market Status: {status} | Dhaka Time: {datetime.now(DHAKA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
except:
    st.info(f"Dhaka Time: {datetime.now(DHAKA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")

with st.spinner("Fetching LIVE DSE data via bdshare.get_current_trade_data()..."):
    df_live = get_live_data()

if df_live.empty:
    st.stop()

df_live['is_ordinary'] = df_live['symbol'].apply(lambda x: is_ordinary(x))
if ordinary_only:
    df_filtered = df_live[df_live['is_ordinary']]
else:
    df_filtered = df_live

df_filtered = df_filtered[(df_filtered['ltp'] >= price_min) & (df_filtered['ltp'] <= price_max)]
df_filtered = df_filtered[df_filtered['change'] >= gainer_min]

st.success(f"Scanned {len(df_live)} instruments | Filtered {len(df_filtered)} meet Ordinary {price_min}-{price_max} & >= {gainer_min}%")

if df_filtered.empty:
    st.warning("No tickers match filter right now. Try lowering gainer threshold.")
    st.dataframe(df_live.sort_values('change', ascending=False).head(20))
    st.stop()

with st.spinner(f"Calculating RVOL, ADR, Gap, VWAP, Reclaim for {len(df_filtered)} tickers..."):
    df_enriched = enrich_with_history(df_filtered)

df_enriched['tier'] = df_enriched.apply(calculate_tier, axis=1)

c1,c2,c3,c4 = st.columns(4)
with c1: st.metric("Filtered", len(df_enriched))
with c2: st.metric("A+ Tier", len(df_enriched[df_enriched['tier']=='A+']))
with c3: st.metric("Avg Gain %", f"{df_enriched['change_pct'].mean():.2f}%")
with c4: st.metric("Total Vol (M)", f"{df_enriched['volume_m'].sum():.2f}M")

tab1, tab2 = st.tabs(["A+ / B / C Tier Table","All Details"])
with tab1:
    tier_filter = st.selectbox("Tier", ["All","A+","B","C"])
    if tier_filter != "All":
        show_df = df_enriched[df_enriched['tier']==tier_filter]
    else:
        show_df = df_enriched
    show_df = show_df.sort_values(['tier','change_pct'], ascending=[True, False])
    st.dataframe(show_df[['symbol','price','change_pct','volume_m','rvol','adr','gap_pct','vwap','vwap_dist','reclaim','tier']], use_container_width=True, height=600)
    csv = show_df.to_csv(index=False).encode('utf-8')
    st.download_button("Download CSV", csv, "dse_live_filtered.csv", "text/csv")
with tab2:
    st.dataframe(df_enriched.sort_values('change_pct', ascending=False), use_container_width=True, height=600)

st.markdown("### Catalyst")
sector_map = {
    'GENEXIL':'IT + DSES outperformance',
    'BANGAS':'Food & Allied',
    'PENINSULA':'Travel + Hotels +5.1%',
    'SEAPEARL':'Travel leader',
    'SPCERAMICS':'Ceramics +4%',
    'BEXIMCO':'Flagship volume',
    'MHSML':'Textile turnover 18.9%',
    'CONFIDCEM':'Cement',
}
df_enriched['catalyst'] = df_enriched['symbol'].apply(lambda x: sector_map.get(x, 'Broad market bargain hunting'))
st.dataframe(df_enriched[['symbol','price','change_pct','catalyst','tier']].sort_values('change_pct', ascending=False), use_container_width=True)

if auto_refresh:
    st.caption(f"Auto-refreshing every {refresh_sec}s...")
    time.sleep(refresh_sec)
    st.rerun()
