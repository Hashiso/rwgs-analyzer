"""RWGS Catalyst Analytics（単一ファイル版・ログインなし）

GitHub には app.py と requirements.txt だけ置けば動きます。
上半分が解析ロジック、「# ===== UI =====」より下が Streamlit の画面です。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# ======================================================================
# 定数・既定値（恒久的に変えたいときはここを編集）
# ======================================================================
GAS_COLS = ["CO2", "CO", "CH4"]
OPTIONAL_COLS = ["N2", "O2"]
DEFAULT_GC_INTERVAL_MIN = 2.45
FILENAME_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})")

STATUS_STEADY = "✅ 定常状態"
STATUS_EARLY = "⚠️ プラトー（採用外）"
STATUS_WAIT = "❌ 昇温・降温・安定化待ち"

DEFAULT_CALIB = pd.DataFrame(
    {"Gas": ["CO2", "CO", "CH4"], "Slope": [4.75e-07, 1.44e-06, 1.29e-06], "Intercept": [0.0, 0.0, 0.0]}
)
DEFAULT_SCHEDULE = pd.DataFrame(
    {
        "start_min": [0.0, 20, 40, 60, 80, 103, 126, 149, 172],
        "end_min": [20.0, 40, 60, 80, 103, 126, 149, 172, 195],
        "temp": [100.0, 200, 300, 350, 400, 450, 500, 550, 600],
    }
)
SUMMARY_VALUE_COLS = [
    "C-Balance(%)", "CO2_Conc", "CO_Conc", "CH4_Conc",
    "CO2_Conversion(%)", "CO_Selectivity(%)", "CH4_Selectivity(%)",
]


@dataclass
class AnalysisConfig:
    schedule: pd.DataFrame = field(default_factory=lambda: DEFAULT_SCHEDULE.copy())
    ignore_mins: float = 0.0          # 測定開始から除外する時間
    settle_min: float = 8.0           # 各ステージ開始後の昇温/安定化時間
    n_steady: int = 3                 # 各温度で定常とみなす点数
    tail_margin_min: float = 2.0      # ステージ終了のこの時間前以降の点は除外（次の昇温の立ち上がり対策）
    offset_mode: str = "auto"         # "auto" | "manual"
    offset_manual: float = 0.0        # 手動指定時のオフセット（分）
    offset_min: float = 0.0           # 自動探索の範囲（分）
    offset_max: float = 40.0
    baseline_mode: str = "p95"        # "p95" | "manual"
    baseline_manual: float | None = None
    overflow_threshold: float = 5e7   # N2/O2 のオーバーフロー値判定


@dataclass
class AnalysisResult:
    df: pd.DataFrame
    summary: pd.DataFrame
    offset: float
    offset_score: float
    gc_interval: float
    baseline: float
    warnings: list[str]
    offset_scan: pd.DataFrame | None = None
    offset_mode: str = "auto"


# ======================================================================
# 読み込み
# ======================================================================
def read_area_file(raw: bytes, encodings=("cp932", "utf-8-sig", "utf-8")) -> pd.DataFrame:
    """GC の .Area ファイル（タブ区切り・先頭2行スキップ）を読み込む。文字コードは順に試す。"""
    last_err: Exception | None = None
    for enc in encodings:
        try:
            df = pd.read_csv(io.BytesIO(raw), sep="\t", skiprows=2, encoding=enc)
            df.columns = df.columns.astype(str).str.strip()
            return df
        except UnicodeDecodeError as e:
            last_err = e
    raise ValueError(f"文字コードを判定できませんでした（試行: {', '.join(encodings)}）: {last_err}")


def merge_channels(df1: pd.DataFrame, df2: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warns: list[str] = []
    if len(df1) != len(df2):
        n = min(len(df1), len(df2))
        warns.append(
            f"Channel 1（{len(df1)}行）と Channel 2（{len(df2)}行）の行数が異なります。"
            f"先頭 {n} 行のみ結合しました。ファイルの組み合わせを確認してください。"
        )
        df1, df2 = df1.iloc[:n], df2.iloc[:n]
    dup = [c for c in df2.columns if c in df1.columns]
    merged = pd.concat(
        [df1.reset_index(drop=True), df2.drop(columns=dup).reset_index(drop=True)], axis=1
    )
    return merged, warns


def prepare_dataframe(df: pd.DataFrame, overflow_threshold: float) -> tuple[pd.DataFrame, list[str]]:
    warns: list[str] = []
    df = df.copy()
    has_fn = "File Name" in df.columns
    has_dt = {"Date", "Time"}.issubset(df.columns)
    if not (has_fn or has_dt):
        raise ValueError("時刻を決定できません。'File Name' 列、または 'Date' と 'Time' 列が必要です。")

    df = df.dropna(subset=["File Name"] if has_fn else ["Date", "Time"])

    missing = [c for c in GAS_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"必要な列がありません: {', '.join(missing)}（検出された列: {list(df.columns)}）")

    for col in GAS_COLS + OPTIONAL_COLS:
        if col not in df.columns:
            continue
        s = df[col].astype(str).str.strip()
        num = pd.to_numeric(s, errors="coerce")
        bad = num.isna() & s.ne("") & s.str.lower().ne("nan")
        if bad.any():
            warns.append(f"列 '{col}' に数値化できない値が {int(bad.sum())} 件あり、0 として扱いました。")
        blank = int((num.isna() & ~bad).sum())
        if blank and col in GAS_COLS and blank < len(num):
            warns.append(f"列 '{col}' に空欄が {blank} 件あり、0 として扱いました（GCの未検出/ピーク無し）。")
        num = num.fillna(0)
        if col in OPTIONAL_COLS:
            num = num.mask(num > overflow_threshold, 0)
        df[col] = num

    # 測定日時は File Name 内のタイムスタンプを優先（Date/Time はエクスポート時刻のため）
    dt = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if has_fn:
        ts = df["File Name"].astype(str).str.extract(FILENAME_TS_RE)[0]
        dt = pd.to_datetime(ts, format="%Y-%m-%d %H-%M-%S", errors="coerce")
    if has_dt:
        fallback = pd.to_datetime(
            df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip(), errors="coerce"
        )
        if dt.isna().any() and has_fn:
            warns.append("File Name から測定日時を取得できない行があり、Date/Time で代用しました。")
        dt = dt.fillna(fallback)
    df["Datetime"] = dt

    n_before = len(df)
    df = df.dropna(subset=["Datetime"]).sort_values("Datetime").reset_index(drop=True)
    if len(df) < n_before:
        warns.append(f"日時を取得できない {n_before - len(df)} 行を除外しました。")
    if df.empty:
        raise ValueError("有効なデータが見つかりませんでした。")
    return df, warns


# ======================================================================
# 設定の検証
# ======================================================================
def normalize_calibration(calib_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warns: list[str] = []
    c = calib_df.copy()
    c["Gas"] = c["Gas"].astype(str).str.strip()
    c = c[c["Gas"].ne("") & c["Gas"].str.lower().ne("nan")]
    c["Slope"] = pd.to_numeric(c["Slope"], errors="coerce")
    c["Intercept"] = pd.to_numeric(c["Intercept"], errors="coerce").fillna(0.0)
    if c["Slope"].isna().any():
        raise ValueError("キャリブレーションの Slope に数値でない（または空の）値があります。")
    dup = c["Gas"].duplicated(keep="last")
    if dup.any():
        warns.append(f"キャリブレーションに重複した Gas 行があります（最後の行を採用）: {sorted(set(c.loc[dup, 'Gas']))}")
        c = c[~dup]
    missing = [g for g in GAS_COLS if g not in set(c["Gas"])]
    if missing:
        raise ValueError(f"キャリブレーション係数がありません: {', '.join(missing)}")
    if (c.loc[c["Gas"].isin(GAS_COLS), "Slope"] <= 0).any():
        raise ValueError("Slope は正の値にしてください。")
    return c.set_index("Gas")[["Slope", "Intercept"]], warns


def validate_schedule(schedule: pd.DataFrame) -> pd.DataFrame:
    s = schedule.copy()
    for col in ("start_min", "end_min", "temp"):
        if col not in s.columns:
            raise ValueError(f"加熱スケジュールに '{col}' 列がありません。")
        s[col] = pd.to_numeric(s[col], errors="coerce")
    s = s.dropna(subset=["start_min", "end_min", "temp"]).sort_values("start_min").reset_index(drop=True)
    if s.empty:
        raise ValueError("加熱スケジュールが空です。")
    if (s["end_min"] <= s["start_min"]).any():
        raise ValueError("加熱スケジュールの end_min は start_min より大きくしてください。")
    if (s["start_min"].iloc[1:].to_numpy() < s["end_min"].iloc[:-1].to_numpy()).any():
        raise ValueError("加熱スケジュールの区間が重なっています。")
    if s["temp"].duplicated().any():
        raise ValueError("加熱スケジュールに同じ温度が複数あります（温度ごとの集計が混ざるため）。")
    return s


# ======================================================================
# ステージ割当とオフセット推定
# ======================================================================
def assign_stage(furnace_time, schedule: pd.DataFrame, settle_min: float):
    """電気炉時間 → (温度, ステージ名)。ベクトル化済み。"""
    t = np.asarray(furnace_time, dtype=float)
    temp = np.full(t.shape, np.nan)
    label = np.full(t.shape, "降温・測定終了", dtype=object)
    label[np.isnan(t) | (t < 0)] = "待機"
    for start, end, tmp in schedule[["start_min", "end_min", "temp"]].itertuples(index=False):
        in_stage = (t >= start) & (t < end)
        settling = in_stage & (t < start + settle_min)
        hold = in_stage & ~settling
        label[settling] = f"昇温/安定化 (→{tmp:g}℃)"
        label[hold] = f"維持 ({tmp:g}℃)"
        temp[hold] = tmp
    return temp, label


def _stage_groups(t: np.ndarray, schedule: pd.DataFrame) -> np.ndarray:
    """炉時間 → グループ番号。0: 開始前, 1: 終了後・区間外, 2+k: k番目のステージ区間（昇温/安定化を含む）"""
    g = np.ones(t.shape, dtype=int)
    g[t < schedule["start_min"].iloc[0]] = 0
    for k, (start, end) in enumerate(schedule[["start_min", "end_min"]].itertuples(index=False)):
        g[(t >= start) & (t < end)] = 2 + k
    return g


def scan_offsets(elapsed: np.ndarray, co: np.ndarray, schedule: pd.DataFrame, offsets) -> pd.DataFrame:
    """各オフセットで「ステージ区間ごとの平均値で CO を説明した残差（全分散で正規化）」を計算する。

    小さいほど、CO の階段状の変化とスケジュールの区切りが一致している。
    全データ（昇温中の点も含む）を評価するので、高CO側を“除外”して有利になることがない。
    covered: データが1点以上あるステージ数（測定がスケジュールをどこまで覆っているか）。
    """
    n_stages = len(schedule)
    ss_tot = float(((co - co.mean()) ** 2).sum())
    rows = []
    for off in offsets:
        g = _stage_groups(elapsed - off, schedule)
        cnt = np.bincount(g, minlength=n_stages + 2)
        mean = np.bincount(g, weights=co, minlength=n_stages + 2) / np.maximum(cnt, 1)
        ss = float(((co - mean[g]) ** 2).sum())
        rows.append((float(off), ss / ss_tot if ss_tot > 0 else np.nan, int((cnt[2:] >= 1).sum())))
    return pd.DataFrame(rows, columns=["offset", "score", "covered"])


def pick_offset(scan: pd.DataFrame, n_stages: int) -> tuple[float, float, bool]:
    """最良のオフセットを選ぶ。全ステージを測定が覆うオフセットを優先する（約23分周期の取り違え防止）。"""
    valid = scan.dropna(subset=["score"])
    if valid.empty:
        return 0.0, float("nan"), False
    full = valid[valid["covered"] == n_stages]
    cand = full if not full.empty else valid
    best = cand.loc[cand["score"].idxmin()]  # 同点なら小さいオフセット
    return float(best["offset"]), float(best["score"]), not full.empty


# ======================================================================
# メイン解析
# ======================================================================
def run_analysis(
    df_ch1: pd.DataFrame, df_ch2: pd.DataFrame, calib_df: pd.DataFrame, cfg: AnalysisConfig
) -> AnalysisResult:
    warns: list[str] = []
    schedule = validate_schedule(cfg.schedule)
    calib, w = normalize_calibration(calib_df)
    warns += w

    df, w = merge_channels(df_ch1, df_ch2)
    warns += w
    df, w = prepare_dataframe(df, cfg.overflow_threshold)
    warns += w

    start_time = df["Datetime"].min()
    df["Elapsed_min"] = (df["Datetime"] - start_time).dt.total_seconds() / 60.0
    df = df[df["Elapsed_min"] >= cfg.ignore_mins].reset_index(drop=True)
    if df.empty:
        raise ValueError("除外時間を長く設定しすぎたため、データが全て消えました。設定を見直してください。")

    # 濃度
    for gas in GAS_COLS:
        df[f"{gas}_Conc"] = (df[gas] * calib.at[gas, "Slope"] + calib.at[gas, "Intercept"]).clip(lower=0)
    df["Total_Carbon_Conc(%)"] = df["CO2_Conc"] + df["CO_Conc"] + df["CH4_Conc"]

    # 基準炭素量
    if cfg.baseline_mode == "manual":
        if not cfg.baseline_manual or cfg.baseline_manual <= 0:
            raise ValueError("基準炭素量（手動入力）は正の値にしてください。")
        baseline = float(cfg.baseline_manual)
    else:
        baseline = float(np.percentile(df["Total_Carbon_Conc(%)"], 95))
        if baseline <= 0:
            baseline = 10.0
            warns.append("基準炭素量を算出できなかったため 10.0% を使用しました。")

    df["C-Balance(%)"] = df["Total_Carbon_Conc(%)"] / baseline * 100
    df["CO2_Conversion(%)"] = (df["CO_Conc"] + df["CH4_Conc"]) / baseline * 100
    prod_c = df["CO_Conc"] + df["CH4_Conc"]
    # 生成物が検出されない点の選択率は定義できないので NaN（0 にすると定常平均が不当に下がる）
    df["CO_Selectivity(%)"] = np.where(prod_c > 0, df["CO_Conc"] / prod_c * 100, np.nan)
    df["CH4_Selectivity(%)"] = np.where(prod_c > 0, df["CH4_Conc"] / prod_c * 100, np.nan)

    # タイムライン同期
    gc_interval = float(df["Elapsed_min"].diff().median())
    if not np.isfinite(gc_interval) or gc_interval <= 0:
        gc_interval = DEFAULT_GC_INTERVAL_MIN
    elapsed, co = df["Elapsed_min"].to_numpy(), df["CO_Conc"].to_numpy()
    if cfg.offset_max <= cfg.offset_min:
        raise ValueError("オフセット探索の上限は下限より大きくしてください。")
    scan = scan_offsets(elapsed, co, schedule, np.arange(cfg.offset_min, cfg.offset_max + 0.25, 0.5))
    n_stages = len(schedule)
    if cfg.offset_mode == "manual":
        offset = float(cfg.offset_manual)
        score = float(scan_offsets(elapsed, co, schedule, [offset])["score"].iloc[0])
    else:
        offset, score, full = pick_offset(scan, n_stages)
        if not np.isfinite(score):
            warns.append("オフセットを自動推定できませんでした（CO濃度が一定、または点数不足）。0分を使用しています。")
        else:
            if not full:
                warns.append("測定がスケジュール全体を覆うオフセットがありませんでした（測定が途中で終わっている可能性）。"
                             "ステージの対応を探索グラフで確認してください。")
            cand = scan[scan["covered"] == n_stages] if full else scan
            alt = cand[(cand["score"] <= score * 1.5 + 1e-12) & ((cand["offset"] - offset).abs() >= 5)]
            if not alt.empty:
                warns.append(f"オフセット {alt.iloc[0]['offset']:g}分 でも同程度に合います（階段状データは周期的に似るため）。"
                             "結果が不自然なら手動指定してください。")
            if offset <= cfg.offset_min or offset >= cfg.offset_max:
                warns.append("最良のオフセットが探索範囲の端です。範囲を広げて確認してください。")

    df["Furnace_Time_min"] = df["Elapsed_min"] - offset
    temp, stage = assign_stage(df["Furnace_Time_min"].to_numpy(), schedule, cfg.settle_min)
    df["Temperature"], df["Stage"] = temp, stage

    # 定常状態：維持区間のうち「ステージ終了の tail_margin_min 分前まで」の最後の n_steady 点
    df["Status"] = STATUS_WAIT
    hold = df["Temperature"].notna()
    df.loc[hold, "Status"] = STATUS_EARLY
    stage_end = df["Temperature"].map(schedule.set_index("temp")["end_min"])
    eligible = hold & (df["Furnace_Time_min"] < stage_end - cfg.tail_margin_min)
    steady_idx = df[eligible].groupby("Temperature").tail(cfg.n_steady).index
    df.loc[steady_idx, "Status"] = STATUS_STEADY

    counts_hold = df[hold].groupby("Temperature").size()
    absent = sorted(set(schedule["temp"]) - set(counts_hold.index))
    if absent:
        warns.append(f"データに現れなかった温度ステージ: {', '.join(f'{t:g}℃' for t in absent)}")
    counts = df[eligible].groupby("Temperature").size().reindex(counts_hold.index, fill_value=0)
    short = counts[counts < cfg.n_steady]
    if not short.empty:
        warns.append(
            f"定常状態の点数が {cfg.n_steady} 点に満たない温度: "
            + ", ".join(f"{t:g}℃ ({n}点)" for t, n in short.items())
            + "（「昇温/安定化に除く時間」や「終了側で除く時間」を小さくすると増えます）"
        )

    steady = df.loc[steady_idx]
    grp = steady.groupby("Temperature")
    summary = grp[SUMMARY_VALUE_COLS].mean().reset_index()
    summary["CO2_Conversion_SD(%)"] = grp["CO2_Conversion(%)"].std().to_numpy()
    summary["N_points"] = grp.size().to_numpy()

    return AnalysisResult(df, summary, offset, score, gc_interval, baseline, warns, scan, cfg.offset_mode)


# ===== UI =====（ここから下が Streamlit の画面）
def main() -> None:
    st.set_page_config(page_title="RWGS Analyzer", layout="wide")
    st.markdown(
        "<style>.stButton>button, .stDownloadButton>button { border-radius: 8px; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("## 🧪 RWGS Catalyst Analytics")

    # ---------------- サイドバー ----------------
    with st.sidebar:
        st.markdown("### ✂️ データトリミング")
        ignore_mins = st.number_input(
            "測定開始から除外する時間 (分)", min_value=0, max_value=200, value=0, step=5,
            help="GCの挙動が不安定な最初の時間を切り捨てます",
        )

        st.markdown("### ⚙️ キャリブレーション")
        edited_calib = st.data_editor(
            DEFAULT_CALIB, num_rows="dynamic", hide_index=True, width="stretch", key="calib_editor",
            column_config={
                "Gas": st.column_config.TextColumn("Gas", required=True),
                "Slope": st.column_config.NumberColumn("Slope", format="%.3e", required=True),
                "Intercept": st.column_config.NumberColumn("Intercept", format="%.4f"),
            },
        )
        st.caption("ここでの変更はこの画面を開いている間だけ有効です。恒久的に変えるには app.py の DEFAULT_CALIB を編集してください。")

        with st.expander("🌡️ 加熱スケジュール / 判定条件"):
            schedule_df = st.data_editor(
                DEFAULT_SCHEDULE, num_rows="dynamic", hide_index=True, width="stretch",
                key="schedule_editor",
                column_config={
                    "start_min": st.column_config.NumberColumn("開始 (分)", format="%.1f"),
                    "end_min": st.column_config.NumberColumn("終了 (分)", format="%.1f"),
                    "temp": st.column_config.NumberColumn("温度 (℃)", format="%.0f"),
                },
            )
            settle_min = st.number_input("昇温/安定化に除く時間 (分)", 0.0, 60.0, 8.0, 1.0)
            n_steady = st.number_input("定常状態とみなす点数", 1, 20, 3, 1)
            tail_margin = st.number_input(
                "ステージ終了側で除く時間 (分)", 0.0, 10.0, 2.0, 0.5,
                help="各温度の終了直前は次の昇温の立ち上がりが混ざることがあるため、この時間内の点は定常に含めません。",
            )
            offset_label = st.radio(
                "オフセット（GC開始と炉スケジュールのずれ）", ["自動推定", "手動指定"], horizontal=True,
                help="炉時間 = GC経過時間 − オフセット。正の値は、GCの測定開始より炉のスケジュール開始（＋応答の遅れ）が後であることを表します。",
            )
            offset_min, offset_max, offset_manual = 0.0, 40.0, 0.0
            if offset_label == "手動指定":
                offset_manual = st.number_input("オフセット (分)", -120.0, 120.0, 10.0, 0.5)
            else:
                oc1, oc2 = st.columns(2)
                offset_min = oc1.number_input("探索 下限 (分)", -120.0, 120.0, 0.0, 5.0)
                offset_max = oc2.number_input("探索 上限 (分)", -120.0, 120.0, 40.0, 5.0)

        with st.expander("🧮 基準炭素量"):
            baseline_label = st.radio(
                "算出方法", ["全データの95パーセンタイル", "手動入力"], index=0,
                help="転化率・炭素収支の分母（入口CO2相当の炭素濃度）です。バイパス測定値があれば手動入力が最も正確です。",
            )
            baseline_manual = (
                st.number_input("基準炭素量 (%)", min_value=0.0, value=10.0, step=0.1, format="%.3f")
                if baseline_label == "手動入力" else None
            )

    # ---------------- ファイル入力と解析 ----------------
    st.markdown("#### 📂 Raw Data ファイルのアップロード")
    col1, col2 = st.columns(2)
    with col1:
        file_ch1 = st.file_uploader("Channel 1 (.Area)", type=["area", "txt", "csv"])
    with col2:
        file_ch2 = st.file_uploader("Channel 2 (.Area)", type=["area", "txt", "csv"])

    sample_name = st.text_input("サンプル名（出力ファイル名に使用）", value="", placeholder="例: LZMO_run1")

    if file_ch1 and file_ch2:
        if st.button("🚀 全自動で解析を実行", type="primary", width="stretch"):
            try:
                with st.spinner("データを処理しています..."):
                    cfg = AnalysisConfig(
                        schedule=schedule_df, ignore_mins=float(ignore_mins), settle_min=float(settle_min),
                        n_steady=int(n_steady), tail_margin_min=float(tail_margin), offset_min=float(offset_min), offset_max=float(offset_max),
                        offset_mode="manual" if offset_label == "手動指定" else "auto", offset_manual=float(offset_manual),
                        baseline_mode="manual" if baseline_manual else "p95", baseline_manual=baseline_manual,
                    )
                    result = run_analysis(
                        read_area_file(file_ch1.getvalue()),
                        read_area_file(file_ch2.getvalue()),
                        edited_calib, cfg,
                    )
                st.session_state["result"] = result
                st.session_state["sample"] = re.sub(r"[^\w.\-]", "_", sample_name.strip()) or "RWGS"
            except ValueError as e:
                st.session_state.pop("result", None)
                st.error(str(e))
            except Exception as e:  # 想定外のフォーマット等
                st.session_state.pop("result", None)
                st.error("ファイルを解析できませんでした。形式（タブ区切り・先頭2行ヘッダ）をご確認ください。")
                with st.expander("詳細"):
                    st.code(repr(e))

    # ---------------- 結果表示（session_state から描画 → ダウンロード操作で消えない） ----------------
    result: AnalysisResult | None = st.session_state.get("result")
    if result is None:
        return
    sample = st.session_state.get("sample", "RWGS")
    df, summary = result.df, result.summary

    st.success(f"🎯 解析完了！ (オフセット: {result.offset:.1f}分{'（手動）' if result.offset_mode == 'manual' else ''}"
               f" / 基準炭素量: {result.baseline:.2f}%)")
    if result.warnings:
        with st.expander(f"⚠️ 警告 {len(result.warnings)} 件", expanded=True):
            for w in result.warnings:
                st.warning(w)

    if not summary.empty:
        top = summary.loc[summary["Temperature"].idxmax()]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"転化率 ({top['Temperature']:g}℃)", f"{top['CO2_Conversion(%)']:.2f} %")
        m2.metric("測定間隔", f"{result.gc_interval:.2f} min")
        m3.metric("平均C-Balance", f"{summary['C-Balance(%)'].mean():.2f} %")
        m4.metric("同期スコア", "-" if pd.isna(result.offset_score) else f"{result.offset_score:.3f}",
                  help="ステージ区間ごとの平均で説明できなかった分/全体のばらつき。小さいほど温度ステージとデータが良く同期しています。")

    with st.expander("🔍 オフセット探索の詳細（同期スコア）"):
        scan = result.offset_scan
        if scan is not None and not scan.empty:
            line = alt.Chart(scan).mark_line().encode(
                x=alt.X("offset:Q", title="オフセット (分)"),
                y=alt.Y("score:Q", title="同期スコア（小さいほど良い）"),
                tooltip=[alt.Tooltip("offset:Q", title="オフセット (分)"),
                         alt.Tooltip("score:Q", title="スコア", format=".4f"),
                         alt.Tooltip("covered:Q", title="カバーするステージ数")],
            )
            rule = alt.Chart(pd.DataFrame({"offset": [result.offset]})).mark_rule(color="#ef4444").encode(x="offset:Q")
            st.altair_chart(line + rule, width="stretch")
        st.caption("赤線が採用値。階段状のデータは約20〜23分周期で似た形になるため、谷が複数できることがあります。"
                   "採用値が実験記録と合わないときは、サイドバーで「手動指定」にしてください。")

    tab_chart, tab_summary, tab_all = st.tabs(["📈 グラフ", "📋 定常状態の平均値", "🗂️ 全データ"])

    with tab_chart:
        y_options = {
            "CO 濃度 (%)": "CO_Conc", "CO2 濃度 (%)": "CO2_Conc", "CH4 濃度 (%)": "CH4_Conc",
            "CO2 転化率 (%)": "CO2_Conversion(%)", "炭素収支 (%)": "C-Balance(%)",
            "CO 選択率 (%)": "CO_Selectivity(%)",
        }
        y_label = st.selectbox("縦軸", list(y_options))

        def safe(c: str) -> str:  # Altair のフィールド名エスケープ問題を回避
            return re.sub(r"[^0-9A-Za-z_]", "_", c)

        plot_df = df.rename(columns=safe)
        yf = safe(y_options[y_label])
        base = alt.Chart(plot_df).encode(
            x=alt.X("Furnace_Time_min:Q", title="電気炉稼働時間 (分)"),
            y=alt.Y(f"{yf}:Q", title=y_label),
        )
        line = base.mark_line(color="#cbd5e1")
        points = base.mark_circle(size=80).encode(
            color=alt.Color(
                "Status:N", title="状態",
                scale=alt.Scale(domain=[STATUS_STEADY, STATUS_EARLY, STATUS_WAIT],
                                range=["#10b981", "#f59e0b", "#ef4444"]),
            ),
            tooltip=[
                alt.Tooltip("Temperature:Q", title="温度 (℃)"),
                alt.Tooltip("Stage:N", title="ステージ"),
                alt.Tooltip("Furnace_Time_min:Q", title="炉時間 (分)", format=".1f"),
                alt.Tooltip(f"{yf}:Q", title=y_label, format=".3f"),
            ],
        )
        st.altair_chart((line + points).properties(height=400).interactive(), width="stretch")

    with tab_summary:
        fmt = {c: "{:.3f}" for c in summary.columns if c not in ("Temperature", "N_points")}
        fmt.update({"Temperature": "{:g}", "N_points": "{:d}"})
        st.dataframe(summary.style.format(fmt, na_rep="-"), width="stretch", hide_index=True)
        st.download_button(
            "📥 綺麗なCSVをダウンロード",
            data=summary.to_csv(index=False).encode("cp932", errors="replace"),
            file_name=f"{sample}_RWGS_Clean_Result.csv", mime="text/csv",
        )

    with tab_all:
        show_cols = ["Datetime", "Elapsed_min", "Furnace_Time_min", "Temperature", "Stage", "Status",
                     "CO2_Conc", "CO_Conc", "CH4_Conc", "C-Balance(%)", "CO2_Conversion(%)",
                     "CO_Selectivity(%)", "CH4_Selectivity(%)"]
        st.dataframe(df[show_cols], width="stretch", hide_index=True)
        st.download_button(
            "📥 全データCSVをダウンロード",
            data=df[show_cols].to_csv(index=False).encode("cp932", errors="replace"),
            file_name=f"{sample}_RWGS_All_Data.csv", mime="text/csv",
        )


main()
