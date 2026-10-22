import streamlit as st

import assign

st.set_page_config(page_title="練習振り分けツール", page_icon="📋", layout="wide")

st.title("📋 練習振り分けツール")
st.caption(
    "回答CSVをアップロードして「振り分け実行」を押すだけ。"
    "参加回数ができるだけ公平になるように抽選します。"
)

with st.expander("使い方", expanded=False):
    st.markdown(
        """
1. Googleフォームの回答を **CSVでダウンロード** する
2. 下の枠にそのCSVをドラッグ＆ドロップ
3. 「振り分け実行」を押す
4. 結果を確認して、**結果CSVをダウンロード**

**手動確定(任意)**：CSVをアップロードすると、定員を超えた日程ごとに応募者の一覧が出ます。
必ず参加させたい人がいれば、そこで選んでください(選ばなければ全員が公平に抽選されます)。

アップロードしたデータはこのページ上で処理するだけで、サーバーには保存していません。
        """
    )

# ---------------- サイドバー(設定) ----------------
with st.sidebar:
    st.header("設定")
    st.subheader("会場ごとの定員")
    st.caption("日程の文字列に会場名が含まれていれば、その会場の定員が使われます。")
    capacities = {
        venue: st.number_input(venue, min_value=1, value=default, step=1)
        for venue, default in assign.DEFAULT_CAPACITIES.items()
    }
    st.subheader("乱数シード")
    seed = st.number_input(
        "同じシード＋同じ入力なら同じ結果になります",
        value=assign.DEFAULT_SEED,
        step=1,
    )
    st.caption("結果を引き直したいときはシードを変えてください。")

# ---------------- 入力 ----------------
response_file = st.file_uploader("回答CSV", type="csv")

manual = {}
inputs_ok = False

if response_file is not None:
    try:
        raw_rows = assign.load_raw_rows(response_file)
        with st.expander(f"📄 アップロードしたCSVの中身を見る（{len(raw_rows)}行）"):
            st.caption("加工していない元データです。列名のクリックで並び替え、右上の虫めがねで検索できます。")
            st.dataframe(raw_rows, width="stretch")
    except ValueError as error:
        st.error(str(error))

if response_file is not None:
    try:
        schedule_applicants, line_names = assign.load_responses(
            response_file
        )
        over = {
            schedule: applicants
            for schedule, applicants in schedule_applicants.items()
            if len(applicants) > assign.get_capacity(schedule, capacities)
        }
        inputs_ok = True
    except ValueError as error:
        st.error(str(error))

if inputs_ok:
    st.subheader("手動確定(任意)")
    if not over:
        st.info("定員を超えている日程はないので、全員参加になります。")
    else:
        st.caption(
            "定員を超えた日程だけ表示しています。"
            "必ず参加させたい人がいれば選んでください。"
        )
        for schedule, applicants in over.items():
            capacity = assign.get_capacity(schedule, capacities)
            chosen = st.multiselect(
                f"{schedule}　(定員{capacity} / 応募{len(applicants)})",
                options=applicants,
                format_func=lambda n: f"{n}（{line_names[n]}）",
                max_selections=capacity,
                key=f"manual_{response_file.file_id}_{schedule}_{capacity}",
            )
            if chosen:
                manual[schedule] = chosen

if st.button("振り分け実行", type="primary", disabled=not inputs_ok):
    try:
        st.session_state["result"] = assign.run(
            response_file, manual, capacities, int(seed)
        )
    except (ValueError, RuntimeError) as error:
        st.session_state.pop("result", None)
        st.error(str(error))

# ---------------- 結果表示 ----------------
result = st.session_state.get("result")

if result:
    st.success("振り分けが完了しました。")

    st.download_button(
        "⬇ 結果CSVをダウンロード",
        result["practice_csv"],
        file_name="assignment_results.csv",
        mime="text/csv",
    )

    tab_practice, tab_counts = st.tabs(["練習の結果", "参加回数"])

    with tab_practice:
        if not result["schedules"]:
            st.info("練習への応募はありません。")
        for item in result["schedules"]:
            rejected_n = len(item["rejected"])
            label = (
                f"{item['schedule']}　"
                f"(定員{item['capacity']} / 応募{item['applicant_count']}"
                + (f" / 落選{rejected_n}" if rejected_n else "")
                + ")"
            )
            with st.expander(label, expanded=bool(rejected_n)):
                st.markdown("**参加者**")
                st.dataframe(
                    [
                        {"本名": n, "LINE名": line, "選出方法": method}
                        for n, line, method in item["selected"]
                    ],
                    hide_index=True,
                    width="stretch",
                )
                if rejected_n:
                    st.markdown("**落選者**")
                    st.dataframe(
                        [
                            {"本名": n, "LINE名": line}
                            for n, line in item["rejected"]
                        ],
                        hide_index=True,
                        width="stretch",
                    )

    with tab_counts:
        st.markdown("**参加回数ごとの人数**")
        st.dataframe(
            [
                {"参加回数": c, "人数": n}
                for c, n in result["count_distribution"].items()
            ],
            hide_index=True,
        )
        st.markdown("**一人ひとりの参加回数**")
        st.dataframe(result["counts"], hide_index=True, width="stretch")
