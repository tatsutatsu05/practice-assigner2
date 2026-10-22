"""練習振り分けロジック。

元のスクリプトを「ファイルを受け取って結果を返す関数」に整理したもの。
Streamlit(app.py)からも、コマンドライン(python assign.py)からも使える。
"""

import csv
import io
import random
import sys
import unicodedata
from collections import Counter, defaultdict, deque
from pathlib import Path

# 列は見出しの完全一致ではなく「キーワードを含むか」で見つける
NAME_KEYWORD = "名前"
LINE_NAME_KEYWORD = "LINE名"
SCHEDULE_KEYWORD = "練習日程"

DEFAULT_CAPACITIES = {
    "成城": 9,
    "高島平": 9,
    "学芸": 17,
}
DEFAULT_SEED = 42
TIE_BREAK_RANGE = 1000


# ---------------------------------------------------------------
# 入力の読み込み
# ---------------------------------------------------------------
def read_text(source):
    """パス / アップロードファイル / bytes / str のどれでも文字列にする。"""
    if source is None:
        return None
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    elif hasattr(source, "getvalue"):
        data = source.getvalue()
    elif hasattr(source, "read"):
        data = source.read()
    else:
        data = source

    if isinstance(data, str):
        return data

    # GoogleフォームのCSVはUTF-8、Excelで保存し直すとShift_JIS(cp932)になりがち
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "CSVの文字コードを判定できませんでした。UTF-8で保存し直してください。"
    )


def load_raw_rows(source):
    """アップロードされたCSVを、加工せずそのまま表示用に読む。"""
    reader = csv.DictReader(io.StringIO(read_text(source), newline=""))
    return list(reader)


def split_choices(text):
    return [
        item.strip()
        for item in (text or "").split(",")
        if item.strip()
    ]




def _normalize_header(text):
    # 全角英数・全角スペースなどを半角に揃え、大文字小文字も無視する
    return unicodedata.normalize("NFKC", text or "").upper()


def find_column(fieldnames, keyword, label, exclude=None):
    """見出しにkeywordを含む列を1つだけ見つけて、その見出しを返す。"""
    key = _normalize_header(keyword)
    ex = _normalize_header(exclude) if exclude else None

    matches = [
        name
        for name in fieldnames
        if key in _normalize_header(name)
        and not (ex and ex in _normalize_header(name))
    ]

    if not matches:
        raise ValueError(
            f"回答CSVに「{label}」の列が見つかりません"
            f"(見出しに「{keyword}」を含む列が必要です)。"
        )
    if len(matches) > 1:
        raise ValueError(
            f"「{keyword}」を含む列が複数あります："
            + "、".join(matches)
        )
    return matches[0]


def load_responses(source):
    schedule_applicants = defaultdict(list)
    line_names = {}
    schedule_seen = defaultdict(set)

    reader = csv.DictReader(io.StringIO(read_text(source), newline=""))

    fieldnames = [f for f in (reader.fieldnames or []) if f]

    # 「LINE名」の列は「名前」を含まないが、念のため名前の列からは除外する
    name_column = find_column(
        fieldnames, NAME_KEYWORD, "名前", exclude=LINE_NAME_KEYWORD
    )
    line_name_column = find_column(
        fieldnames, LINE_NAME_KEYWORD, "LINE名"
    )
    schedule_column = find_column(fieldnames, SCHEDULE_KEYWORD, "練習日程")

    for row in reader:
        name = (row.get(name_column) or "").strip()
        line_name = (row.get(line_name_column) or "").strip()

        if not name:
            continue
        if not line_name:
            raise ValueError(f"{name}さんのLINE名が空欄です。")

        if name in line_names and line_names[name] != line_name:
            raise ValueError(
                f"{name}さんに異なるLINE名があります："
                f"「{line_names[name]}」「{line_name}」"
            )
        line_names[name] = line_name

        for schedule in split_choices(row.get(schedule_column)):
            if name not in schedule_seen[schedule]:
                schedule_applicants[schedule].append(name)
                schedule_seen[schedule].add(name)

    return schedule_applicants, line_names


def load_manual_assignments(source):
    manual = defaultdict(list)
    text = read_text(source)

    if not text or not text.strip():
        return manual

    reader = csv.DictReader(io.StringIO(text, newline=""))

    if set(reader.fieldnames or []) != {"schedule", "name"}:
        raise ValueError(
            "手動確定CSVの見出しは「schedule,name」にしてください。"
        )

    for row in reader:
        schedule = (row.get("schedule") or "").strip()
        name = (row.get("name") or "").strip()

        if not schedule and not name:
            continue
        if not schedule or not name:
            raise ValueError("手動確定CSVに空欄の項目があります。")

        manual[schedule].append(name)

    return manual


def validate_manual(schedule_applicants, manual, capacities):
    for schedule, names in manual.items():
        if schedule not in schedule_applicants:
            raise ValueError(
                f"手動確定の日程が応募データにありません：{schedule}"
            )
        if len(names) != len(set(names)):
            raise ValueError(
                f"同じ人が重複して手動確定されています：{schedule}"
            )

        applicants = set(schedule_applicants[schedule])
        for name in names:
            if name not in applicants:
                raise ValueError(
                    f"{name}さんは「{schedule}」に応募していません。"
                )

        if len(names) > get_capacity(schedule, capacities):
            raise ValueError(
                f"手動確定者が定員を超えています：{schedule}"
            )


# ---------------------------------------------------------------
# 最小費用流
# ---------------------------------------------------------------
class Edge:
    def __init__(self, to, reverse_index, capacity, cost):
        self.to = to
        self.reverse_index = reverse_index
        self.capacity = capacity
        self.cost = cost


class MinCostMaxFlow:
    def __init__(self, node_count):
        self.graph = [[] for _ in range(node_count)]

    def add_edge(self, source, target, capacity, cost):
        forward = Edge(
            target,
            len(self.graph[target]),
            capacity,
            cost,
        )
        backward = Edge(
            source,
            len(self.graph[source]),
            0,
            -cost,
        )
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def run(self, source, sink, required_flow):
        flow = 0
        cost = 0
        node_count = len(self.graph)

        while flow < required_flow:
            distance = [None] * node_count
            previous_node = [-1] * node_count
            previous_edge = [-1] * node_count
            in_queue = [False] * node_count

            distance[source] = 0
            queue = deque([source])
            in_queue[source] = True

            while queue:
                node = queue.popleft()
                in_queue[node] = False

                for edge_index, edge in enumerate(self.graph[node]):
                    if edge.capacity <= 0:
                        continue

                    new_distance = distance[node] + edge.cost
                    if (
                        distance[edge.to] is None
                        or new_distance < distance[edge.to]
                    ):
                        distance[edge.to] = new_distance
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index

                        if not in_queue[edge.to]:
                            queue.append(edge.to)
                            in_queue[edge.to] = True

            if previous_node[sink] == -1:
                break

            added = required_flow - flow
            node = sink

            while node != source:
                prev = previous_node[node]
                edge = self.graph[prev][previous_edge[node]]
                added = min(added, edge.capacity)
                node = prev

            node = sink

            while node != source:
                prev = previous_node[node]
                edge = self.graph[prev][previous_edge[node]]
                edge.capacity -= added
                self.graph[node][edge.reverse_index].capacity += added
                node = prev

            flow += added
            cost += distance[sink] * added

        return flow, cost


# ---------------------------------------------------------------
# 振り分け本体
# ---------------------------------------------------------------
def optimize_assignments(schedule_applicants, manual, capacities, seed):
    selected = defaultdict(list)
    rejected = defaultdict(list)
    counts = Counter()
    methods = {}
    oversubscribed = {}

    # 0回の人も公平性評価に含める
    for applicants in schedule_applicants.values():
        for name in applicants:
            counts[name] = 0

    # 定員内の日程は全員参加として先に確定
    for schedule, applicants in schedule_applicants.items():
        manual_names = set(manual.get(schedule, []))

        if len(applicants) <= get_capacity(schedule, capacities):
            selected[schedule] = applicants.copy()

            for name in applicants:
                counts[name] += 1
                methods[(schedule, name)] = (
                    "手動確定"
                    if name in manual_names
                    else "定員内確定"
                )
        else:
            oversubscribed[schedule] = applicants.copy()

    # 定員超過日程の手動確定者を固定
    for schedule in oversubscribed:
        for name in manual.get(schedule, []):
            selected[schedule].append(name)
            counts[name] += 1
            methods[(schedule, name)] = "手動確定"

    remaining_capacity = {}
    candidate_schedules = defaultdict(list)

    # 定員超過の全日程を、まとめて最適化する準備
    for schedule, applicants in oversubscribed.items():
        fixed = set(manual.get(schedule, []))
        remaining_capacity[schedule] = (
            get_capacity(schedule, capacities) - len(fixed)
        )

        if remaining_capacity[schedule] <= 0:
            continue

        for name in applicants:
            if name not in fixed:
                candidate_schedules[name].append(schedule)

    required_flow = sum(remaining_capacity.values())

    if required_flow:
        people = sorted(candidate_schedules)
        schedules = sorted(remaining_capacity)

        source = 0
        person_start = 1
        schedule_start = person_start + len(people)
        sink = schedule_start + len(schedules)

        person_node = {
            name: person_start + index
            for index, name in enumerate(people)
        }
        schedule_node = {
            schedule: schedule_start + index
            for index, schedule in enumerate(schedules)
        }

        solver = MinCostMaxFlow(sink + 1)

        # 公平性を抽選用乱数より必ず優先させる
        fairness_base = required_flow + 1
        tie_scale = required_flow * TIE_BREAK_RANGE + 1

        # 1人の1回目、2回目、3回目…ほど費用を大きくする。
        # これにより、まず参加0回の人を減らし、
        # 次に1回の人を減らす、という順で全体を公平化する。
        for name in people:
            current_count = counts[name]
            possible_count = len(candidate_schedules[name])

            for extra_index in range(possible_count):
                level = current_count + extra_index
                fairness_cost = fairness_base ** level

                solver.add_edge(
                    source,
                    person_node[name],
                    1,
                    fairness_cost * tie_scale,
                )

        random_generator = random.Random(seed)
        pairs = [
            (name, schedule)
            for name, schedules_for_name in candidate_schedules.items()
            for schedule in schedules_for_name
        ]
        random_generator.shuffle(pairs)

        assignment_edges = {}

        for name, schedule in pairs:
            edge = solver.add_edge(
                person_node[name],
                schedule_node[schedule],
                1,
                random_generator.randrange(TIE_BREAK_RANGE),
            )
            assignment_edges[(name, schedule)] = edge

        for schedule in schedules:
            solver.add_edge(
                schedule_node[schedule],
                sink,
                remaining_capacity[schedule],
                0,
            )

        actual_flow, _ = solver.run(source, sink, required_flow)

        if actual_flow != required_flow:
            raise RuntimeError(
                "全枠を割り当てられませんでした。"
                "応募データを確認してください。"
            )

        for schedule, applicants in oversubscribed.items():
            selected_set = set(selected[schedule])

            for name in applicants:
                edge = assignment_edges.get((name, schedule))

                if edge is not None and edge.capacity == 0:
                    selected[schedule].append(name)
                    selected_set.add(name)
                    counts[name] += 1
                    methods[(schedule, name)] = "全体最適化"

            rejected[schedule] = [
                name
                for name in applicants
                if name not in selected_set
            ]

    for schedule, applicants in oversubscribed.items():
        if schedule not in rejected:
            selected_set = set(selected[schedule])
            rejected[schedule] = [
                name
                for name in applicants
                if name not in selected_set
            ]

    return selected, rejected, counts, methods


def validate_results(
    schedule_applicants,
    selected,
    rejected,
    manual,
    counts,
    capacities,
):
    recalculated = Counter()

    for applicants in schedule_applicants.values():
        for name in applicants:
            recalculated[name] = 0

    for schedule, applicants in schedule_applicants.items():
        selected_names = selected[schedule]
        rejected_names = rejected[schedule]

        if len(selected_names) > get_capacity(schedule, capacities):
            raise RuntimeError(f"定員超過が発生しています：{schedule}")
        if set(selected_names) & set(rejected_names):
            raise RuntimeError(
                f"参加者と落選者が重複しています：{schedule}"
            )
        if set(selected_names) | set(rejected_names) != set(applicants):
            raise RuntimeError(
                f"応募者と結果が一致しません：{schedule}"
            )
        if not set(manual.get(schedule, [])) <= set(selected_names):
            raise RuntimeError(
                f"手動確定者が参加者にいません：{schedule}"
            )

        for name in selected_names:
            recalculated[name] += 1

    if recalculated != counts:
        raise RuntimeError("参加回数の集計が一致しません。")


# ---------------------------------------------------------------
# 出力(CSVはファイルに書かず、bytesで返す)
# ---------------------------------------------------------------
def to_csv_bytes(rows, fieldnames):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    # Excelで文字化けしないようBOM付きUTF-8
    return buffer.getvalue().encode("utf-8-sig")


PRACTICE_FIELDS = [
    "日程",
    "結果",
    "本名",
    "LINE名",
    "選出方法",
    "最終練習参加回数",
]


def build_practice_rows(
    schedule_applicants, selected, rejected, counts, line_names, methods
):
    rows = []
    for schedule in schedule_applicants:
        for name in selected[schedule]:
            rows.append(
                {
                    "日程": schedule,
                    "結果": "参加",
                    "本名": name,
                    "LINE名": line_names[name],
                    "選出方法": methods[(schedule, name)],
                    "最終練習参加回数": counts[name],
                }
            )
        for name in rejected[schedule]:
            rows.append(
                {
                    "日程": schedule,
                    "結果": "落選",
                    "本名": name,
                    "LINE名": line_names[name],
                    "選出方法": "全体最適化で未選出",
                    "最終練習参加回数": counts[name],
                }
            )
    return rows


# ---------------------------------------------------------------
# 入口
# ---------------------------------------------------------------
def run(response_file, manual=None, capacities=None, seed=DEFAULT_SEED):
    """回答CSVを受け取り、結果をまとめて返す。

    manual は {日程: [名前, ...]} の辞書、手動確定CSV、または None。
    """
    capacities = dict(capacities or DEFAULT_CAPACITIES)

    schedule_applicants, line_names = load_responses(response_file)
    if isinstance(manual, dict):
        manual = defaultdict(
            list, {k: list(v) for k, v in manual.items() if v}
        )
    else:
        manual = load_manual_assignments(manual)
    validate_manual(schedule_applicants, manual, capacities)

    selected, rejected, counts, methods = optimize_assignments(
        schedule_applicants, manual, capacities, seed
    )
    validate_results(
        schedule_applicants, selected, rejected, manual, counts, capacities
    )

    practice_rows = build_practice_rows(
        schedule_applicants, selected, rejected, counts, line_names, methods
    )

    schedules = []
    for schedule in schedule_applicants:
        schedules.append(
            {
                "schedule": schedule,
                "capacity": get_capacity(schedule, capacities),
                "applicant_count": len(schedule_applicants[schedule]),
                "selected": [
                    (n, line_names[n], methods[(schedule, n)])
                    for n in selected[schedule]
                ],
                "rejected": [
                    (n, line_names[n]) for n in rejected[schedule]
                ],
            }
        )

    return {
        "schedules": schedules,
        "counts": [
            {"本名": n, "LINE名": line_names[n], "参加回数": c}
            for n, c in sorted(counts.items())
        ],
        "count_distribution": dict(sorted(Counter(counts.values()).items())),
        "practice_csv": to_csv_bytes(practice_rows, PRACTICE_FIELDS),
    }


def main():
    """従来どおり data/ のCSVを読んで output/ に書き出すCLI。"""
    base = Path(__file__).parent
    manual_path = base / "data" / "manual_assignments.csv"
    result = run(
        base / "data" / "responses.csv",
        manual_path if manual_path.exists() else None,
    )

    for item in result["schedules"]:
        print()
        print(item["schedule"])
        print(f"定員：{item['capacity']}人 / 応募者：{item['applicant_count']}人")
        for name, line, method in item["selected"]:
            print(f"  参加  {name}（LINE名：{line}）［{method}］")
        for name, line in item["rejected"]:
            print(f"  落選  {name}（LINE名：{line}）")


    out = base / "output"
    out.mkdir(exist_ok=True)
    (out / "assignment_results.csv").write_bytes(result["practice_csv"])
    print(f"保存しました：{out}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(f"エラー：{error}")
        sys.exit(1)
