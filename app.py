import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# ==========================================
# 1. 経過時間から温度を判定する関数
# ==========================================
def get_temperature(elapsed_min):
    if elapsed_min <= 20: return 100
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
# 2. 転化率・選択率の計算関数（エクセル基準に修正）
# ==========================================
def calc_metrics(row, co2_in):
    co_conc = row['CO_Conc']
    ch4_conc = row['CH4_Conc']
    
    # 転化率: (生成したCO + CH4) / 原料のCO2濃度 * 100
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

# --- 検量線設定エリア ---
st.sidebar.header("⚙️ 計算設定")

# 🌟【追加】原料のCO2濃度を設定できるようにしました（エクセルの計算用）
co2_in_conc = st.sidebar.number_input("原料の初期CO2濃度 (%)", value=10.0, step=1.0)
st.sidebar.write("---")

st.sidebar.write("※ 濃度(%) = Area × Slope + Intercept")

# デフォルトの検量線データ
DEFAULT_CALIB = pd.DataFrame({
    'Gas': ['CO2', 'CO', 'CH4'],
    'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
    'Intercept': [-0.0387, 0.1918, 0.0]
})

# セッションステートの初期化
if 'calib_df' not in st.session_state:
    st.session_state['calib_df'] = DEFAULT_CALIB.copy()

# 係数ファイルの読み込み
uploaded_calib = st.sidebar.file_uploader("📂 保存した係数(CSV)を読込", type=['csv'])
if uploaded_calib is not None:
    try:
        st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
        st.sidebar.success("係数を読み込みました！")
    except Exception as e:
        st.sidebar.error("ファイルの読み込みに失敗しました。")

# 表形式で係数を編集可能にする
edited_calib_df = st.sidebar.data_editor(
    st.session_state['calib_df'], 
    num_rows="dynamic", 
    hide_index=True,
    use_container_width=True
)

calib_csv = edited_calib_df.to_csv(index=False).encode('utf-8')
st.sidebar.download_button("💾 現在の係数を保存", data=calib_csv, file_name="calib_settings.csv", mime="text/csv")
calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')

# --- メインエリア（データ処理） ---
st.write("Micro GCの `.Area` ファイル（Channel 1 と Channel 2）をアップロードしてください。")

col1, col2 = st.columns(2)
with col1:
    file_ch1 = st.file_uploader("Channel 1 のファイル (.Area)", type=['Area', 'txt', 'csv'])
with col2:
    file_ch2 = st.file_uploader("Channel 2 のファイル (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 is not None and file_ch2 is not None:
    if st.button("データ処理開始", type="primary"):
        try:
            # ファイルの読み込み
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')

            # 🌟【修正】時刻のズレでデータが消えないよう、行番号で強制的に横に結合する
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df_ch2_unique = df_ch2.drop(columns=cols_to_drop)
            df = pd.concat([df_ch1, df_ch2_unique], axis=1)
            
            # 文字列になってしまった空白データを数値(0)に強制変換
            for col in ['CO2', 'CO', 'CH4', 'N2']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
            # 🌟【修正】不正な時刻データ（空白行など）を弾いて計算する
            df['Datetime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'], errors='coerce')
            df = df.dropna(subset=['Datetime']).reset_index(drop=True)

            start_time = df['Datetime'].iloc[0]
            df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0

            # 温度割り当て
            df['Temperature'] = df['Elapsed_min'].apply(get_temperature)

            # 濃度計算（編集された検量線係数を使用）
            df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)

            # 定常状態の抽出
            result_df = df.dropna(subset=['Temperature']).groupby('Temperature').apply(lambda x: x.tail(3).mean(numeric_only=True)).reset_index()

            # 🌟【修正】エクセルの式に合わせた転化率・選択率計算（co2_in_concを使用）
            metrics = result_df.apply(lambda row: calc_metrics(row, co2_in_conc), axis=1)
            result_df = pd.concat([result_df, metrics], axis=1)

            # 出力列の整理
            output_columns = ['Temperature', 'Elapsed_min', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = result_df[output_columns]

            st.success("✅ 処理が完了しました！")
            
            # 結果のプレビュー表示
            st.dataframe(final_result.style.format("{:.3f}"))

            # CSVダウンロードボタンの作成
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(
                label="📥 評価結果をCSVでダウンロード (Igor用)",
                data=csv,
                file_name='RWGS_Result_Summary.csv',
                mime='text/csv',
            )

        except Exception as e:
            st.error(f"エラーが発生しました。\n詳細: {e}")
