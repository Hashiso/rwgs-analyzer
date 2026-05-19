import streamlit as st
import pandas as pd
import numpy as np

# ==========================================
# 1. 経過時間から温度を判定する関数
# ==========================================
def get_temperature(elapsed_min):
    if elapsed_min < 0: return np.nan
    elif elapsed_min <= 20: return 100
    elif elapsed_min <= 40: return 200
    elif elapsed_min <= 60: return 300
    elif elapsed_min <= 80: return 350
    elif elapsed_min <= 103: return 400
    elif elapsed_min <= 126: return 450
    elif elapsed_min <= 149: return 500
    elif elapsed_min <= 172: return 550
    elif elapsed_min <= 195: return 600
    else: return np.nan

# ==========================================
# 2. 転化率・選択率の計算関数
# ==========================================
def calc_metrics(row, co2_in):
    co_conc = row.get('CO_Conc', 0)
    ch4_conc = row.get('CH4_Conc', 0)
    
    conversion = (co_conc + ch4_conc) / co2_in * 100 if co2_in > 0 else 0
    prod_c = co_conc + ch4_conc
    sel_co = co_conc / prod_c * 100 if prod_c > 0 else 0
    sel_ch4 = ch4_conc / prod_c * 100 if prod_c > 0 else 0
        
    return pd.Series([conversion, sel_co, sel_ch4], index=['CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# 3. WebアプリのUIと処理
# ==========================================
st.set_page_config(page_title="RWGS 活性自動評価アプリ", layout="wide")
st.title("RWGS 触媒活性 自動評価ツール 🧪")

# --- サイドバー設定 ---
st.sidebar.header("⏱️ タイムライン補正")
offset_min = st.sidebar.number_input("昇温開始の遅れ (分)", value=0.0, step=1.0)
gc_interval = st.sidebar.number_input("GCの平均測定間隔 (分/回)", value=2.45, step=0.05)

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 計算設定")
co2_in_conc = st.sidebar.number_input("原料の初期CO2濃度 (%)", value=10.0, step=1.0)

DEFAULT_CALIB = pd.DataFrame({
    'Gas': ['CO2', 'CO', 'CH4'],
    'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
    'Intercept': [-0.0387, 0.1918, 0.0]
})

if 'calib_df' not in st.session_state:
    st.session_state['calib_df'] = DEFAULT_CALIB.copy()

uploaded_calib = st.sidebar.file_uploader("📂 保存した係数(CSV)を読込", type=['csv'])
if uploaded_calib is not None:
    try:
        st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
    except:
        pass

edited_calib_df = st.sidebar.data_editor(st.session_state['calib_df'], num_rows="dynamic", hide_index=True, use_container_width=True)
calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
st.sidebar.download_button("💾 現在の係数を保存", data=edited_calib_df.to_csv(index=False).encode('utf-8'), file_name="calib_settings.csv", mime="text/csv")

# --- メインエリア ---
col1, col2 = st.columns(2)
with col1:
    file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2:
    file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 is not None and file_ch2 is not None:
    if st.button("データ処理開始", type="primary"):
        try:
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns = df_ch1.columns.str.strip()
            df_ch2.columns = df_ch2.columns.str.strip()

            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df_ch2_unique = df_ch2.drop(columns=cols_to_drop)
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2_unique.reset_index(drop=True)], axis=1)
            
            for col in ['CO2', 'CO', 'CH4', 'N2']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
            df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
            
            # 🌟【修正箇所】Indexエラーが出ないように安全なSeries形式（数値の列）に変換
            fallback_time = df.index.to_series() * gc_interval
            
            if df['Datetime'].isna().sum() < len(df) * 0.5:
                start_time = df['Datetime'].dropna().iloc[0]
                df['GC_Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
                df['GC_Elapsed_min'] = df['GC_Elapsed_min'].fillna(fallback_time)
            else:
                df['GC_Elapsed_min'] = fallback_time

            df['Furnace_Time_min'] = df['GC_Elapsed_min'] - offset_min
            df['Temperature'] = df['Furnace_Time_min'].apply(get_temperature)

            df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)

            metrics_all = df.apply(lambda row: calc_metrics(row, co2_in_conc), axis=1)
            df = pd.concat([df, metrics_all], axis=1)

            result_df = df.dropna(subset=['Temperature']).groupby('Temperature').apply(lambda x: x.tail(3).mean(numeric_only=True)).reset_index()
            output_columns = ['Temperature', 'Furnace_Time_min', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = result_df[output_columns]

            st.success("✅ 処理が完了しました！")
            
            st.subheader("📊 1. 抽出された各温度の定常状態")
            st.dataframe(final_result.style.format("{:.2f}"))

            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(label="📥 定常状態の結果をCSVでダウンロード", data=csv, file_name='RWGS_Result_Summary.csv', mime='text/csv')

            st.markdown("---")
            st.subheader("🔍 2. 全データの推移（検証用）")
            preview_cols = ['GC_Elapsed_min', 'Furnace_Time_min', 'Temperature', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)']
            st.dataframe(df[preview_cols].style.format("{:.2f}"))

        except Exception as e:
            st.error(f"エラーが発生しました。\n詳細: {e}")
