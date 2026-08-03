from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from archive_pilot import BASE_URL, CACHE, COUNTRIES, END_DATE, FILES, OUT, START_DATE, download

SENSITIVITY_OUT = OUT / "sensitivity"
SENSITIVITY_OUT.mkdir(parents=True, exist_ok=True)
THRESHOLDS = [0, 200, 500, 1000, 2000, 5000, 10000]


def write_rows(path: Path, columns: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def markdown_table(columns: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows:
        formatted = []
        for value in row:
            if isinstance(value, float):
                formatted.append(f"{value:.4f}")
            else:
                formatted.append(str(value))
        lines.append("| " + " | ".join(formatted) + " |")
    return "\n".join(lines)


def distribution(connection: duckdb.DuckDBPyConnection, location: str) -> list[tuple[int, int]]:
    return connection.execute(
        """
        SELECT cast(round(duration_minutes) AS INTEGER) AS duration_min, count(*) AS n
        FROM six_country
        WHERE location = ?
          AND NOT duration_is_estimate
          AND duration_hours BETWEEN 0 AND 48
        GROUP BY duration_min
        ORDER BY duration_min
        """,
        [location],
    ).fetchall()


def effect_against_korea(kr: list[tuple[int, int]], other: list[tuple[int, int]]) -> tuple:
    other_total = sum(count for _, count in other)
    kr_total = sum(count for _, count in kr)
    other_map = {duration: count for duration, count in other}
    durations = sorted(set([d for d, _ in kr] + [d for d, _ in other]))
    cumulative_less = {}
    running = 0
    for duration in durations:
        cumulative_less[duration] = running
        running += other_map.get(duration, 0)
    shorter_pairs = 0
    longer_pairs = 0
    tie_pairs = 0
    for duration, count in kr:
        less = cumulative_less.get(duration, 0)
        equal = other_map.get(duration, 0)
        greater = other_total - less - equal
        shorter_pairs += count * greater
        longer_pairs += count * less
        tie_pairs += count * equal
    total_pairs = kr_total * other_total
    return (
        kr_total,
        other_total,
        shorter_pairs / total_pairs,
        longer_pairs / total_pairs,
        tie_pairs / total_pairs,
        (shorter_pairs - longer_pairs) / total_pairs,
    )


def main() -> None:
    parquet_paths = []
    for name in FILES:
        path = CACHE / name
        download(BASE_URL.format(name=name), path)
        parquet_paths.append(str(path))

    connection = duckdb.connect(str(Path(__file__).resolve().parent / "archive_sensitivity.duckdb"))
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='6GB'")
    paths_sql = ",".join(repr(path) for path in parquet_paths)
    countries_sql = ",".join(repr(country) for country in COUNTRIES)
    connection.execute(
        f"CREATE OR REPLACE VIEW source AS SELECT * FROM read_parquet([{paths_sql}], union_by_name=true)"
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE six_country AS
        SELECT
            trends,
            location,
            try_cast(start_time AS TIMESTAMP) AS start_time,
            try_cast(collection_date AS DATE) AS collection_date,
            search_volume_lower,
            duration_minutes,
            duration_hours,
            duration_is_estimate
        FROM source
        WHERE location IN ({countries_sql})
          AND try_cast(collection_date AS DATE)
              BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'
        """
    )

    threshold_values = ",".join(f"({value})" for value in THRESHOLDS)
    threshold_query = f"""
        WITH thresholds(min_volume) AS (VALUES {threshold_values}), medians AS (
            SELECT
                t.min_volume,
                s.location,
                count(*) AS n,
                median(s.duration_hours) AS median_duration_h
            FROM thresholds t
            JOIN six_country s
              ON s.search_volume_lower >= t.min_volume
             AND NOT s.duration_is_estimate
             AND s.duration_hours BETWEEN 0 AND 48
            GROUP BY t.min_volume, s.location
        )
        SELECT
            min_volume,
            location,
            n,
            round(median_duration_h, 4) AS median_duration_h,
            rank() OVER (PARTITION BY min_volume ORDER BY median_duration_h ASC) AS shortest_rank
        FROM medians
        ORDER BY min_volume, shortest_rank, location
    """
    cursor = connection.execute(threshold_query)
    threshold_columns = [item[0] for item in cursor.description]
    threshold_rows = cursor.fetchall()
    write_rows(SENSITIVITY_OUT / "volume_thresholds.csv", threshold_columns, threshold_rows)

    estimate_query = """
        SELECT
            location,
            round(median(duration_hours) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS complete_only_median_h,
            round(median(duration_hours) FILTER (
                WHERE duration_hours BETWEEN 0 AND 48
            ), 4) AS including_estimates_median_h,
            round(median(duration_hours) FILTER (
                WHERE duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS estimates_only_median_h
        FROM six_country
        GROUP BY location
        ORDER BY complete_only_median_h
    """
    cursor = connection.execute(estimate_query)
    estimate_columns = [item[0] for item in cursor.description]
    estimate_rows = cursor.fetchall()
    write_rows(SENSITIVITY_OUT / "estimate_sensitivity.csv", estimate_columns, estimate_rows)

    monthly_query = """
        WITH monthly AS (
            SELECT
                date_trunc('month', collection_date)::DATE AS month,
                location,
                count(*) AS n,
                median(duration_hours) AS median_duration_h
            FROM six_country
            WHERE NOT duration_is_estimate
              AND duration_hours BETWEEN 0 AND 48
            GROUP BY month, location
        )
        SELECT
            month,
            location,
            n,
            round(median_duration_h, 4) AS median_duration_h,
            rank() OVER (PARTITION BY month ORDER BY median_duration_h ASC) AS shortest_rank
        FROM monthly
        ORDER BY month, shortest_rank, location
    """
    cursor = connection.execute(monthly_query)
    monthly_columns = [item[0] for item in cursor.description]
    monthly_rows = cursor.fetchall()
    write_rows(SENSITIVITY_OUT / "monthly_ranks.csv", monthly_columns, monthly_rows)

    shortest_counts = defaultdict(int)
    observed_months = defaultdict(int)
    for _, location, _, _, rank in monthly_rows:
        observed_months[location] += 1
        if rank == 1:
            shortest_counts[location] += 1
    monthly_rank_rows = [
        (
            location,
            observed_months[location],
            shortest_counts[location],
            round(100 * shortest_counts[location] / observed_months[location], 3),
        )
        for location in COUNTRIES
    ]
    monthly_rank_rows.sort(key=lambda item: (-item[2], item[0]))
    monthly_rank_columns = ["location", "observed_months", "months_shortest", "months_shortest_pct"]
    write_rows(SENSITIVITY_OUT / "monthly_shortest_frequency.csv", monthly_rank_columns, monthly_rank_rows)

    kr_dist = distribution(connection, "KR")
    effect_rows = []
    for location in COUNTRIES:
        if location == "KR":
            continue
        n_kr, n_other, p_shorter, p_longer, p_tie, delta = effect_against_korea(
            kr_dist, distribution(connection, location)
        )
        effect_rows.append(
            (
                location,
                n_kr,
                n_other,
                round(p_shorter, 6),
                round(p_longer, 6),
                round(p_tie, 6),
                round(delta, 6),
            )
        )
    effect_rows.sort(key=lambda item: item[-1], reverse=True)
    effect_columns = [
        "comparison_country",
        "n_korea",
        "n_country",
        "p_korea_shorter",
        "p_korea_longer",
        "p_tie",
        "cliffs_delta_shorter_positive",
    ]
    write_rows(SENSITIVITY_OUT / "korea_effect_sizes.csv", effect_columns, effect_rows)

    ascii_query = """
        WITH earliest AS (
            SELECT
                lower(trim(trends)) AS normalized_trend,
                location,
                min(start_time) AS first_start
            FROM six_country
            WHERE trends IS NOT NULL
              AND length(trim(trends)) >= 3
              AND start_time IS NOT NULL
            GROUP BY normalized_trend, location
        ), pairs AS (
            SELECT
                a.location AS country_a,
                b.location AS country_b,
                a.normalized_trend,
                regexp_full_match(a.normalized_trend, '^[ -~]+$') AS ascii_only,
                abs(date_diff('minute', a.first_start, b.first_start)) / 60.0 AS lag_hours
            FROM earliest a
            JOIN earliest b
              ON a.normalized_trend = b.normalized_trend
             AND a.location < b.location
            WHERE abs(date_diff('day', a.first_start, b.first_start)) <= 7
        )
        SELECT
            country_a,
            country_b,
            count(*) AS shared_exact_trends,
            count(*) FILTER (WHERE ascii_only) AS ascii_shared,
            round(100 * count(*) FILTER (WHERE ascii_only) / count(*), 3) AS ascii_share_pct,
            round(median(lag_hours), 4) AS median_lag_all_h,
            round(median(lag_hours) FILTER (WHERE ascii_only), 4) AS median_lag_ascii_h
        FROM pairs
        GROUP BY country_a, country_b
        ORDER BY shared_exact_trends DESC
    """
    cursor = connection.execute(ascii_query)
    ascii_columns = [item[0] for item in cursor.description]
    ascii_rows = cursor.fetchall()
    write_rows(SENSITIVITY_OUT / "pair_language_bias.csv", ascii_columns, ascii_rows)

    kr_threshold = [row for row in threshold_rows if row[1] == "KR"]
    report = f"""# GoogleTrendArchive 파일럿 B 민감도 검증

## 검색량 임계값별 한국 순위

{markdown_table(threshold_columns, kr_threshold)}

`shortest_rank=1`이 가장 짧은 에피소드 수명이다. 검색량 임계값을 올려도 한국의 순위가 일관되는지 확인한다.

## 종료시간 추정치 포함 여부

{markdown_table(estimate_columns, estimate_rows)}

## 월별 최단 수명 빈도

{markdown_table(monthly_rank_columns, monthly_rank_rows)}

## 한국과 비교국의 효과크기

{markdown_table(effect_columns, effect_rows)}

`cliffs_delta_shorter_positive`가 양수면 임의의 한국 에피소드가 비교국 에피소드보다 짧을 가능성이 더 크고, 음수면 더 길 가능성이 크다.

## 동일 문자열 매칭의 언어편향

{markdown_table(ascii_columns, ascii_rows)}

ASCII 비율이 높을수록 국가 간 동시성 결과가 국제 고유명사·영문 표기에 편향됐을 가능성이 크다.

## 판정 원칙

- 한국의 최단 수명이 검색량 임계값과 월별 분석에서 반복되지 않으면 `한국 특수형`의 지지로 해석하지 않는다.
- 효과크기가 작거나 방향이 국가마다 다르면 한국 단독 효과보다 영역·시장 구조 차이를 우선 검토한다.
- 동일 문자열 동시성은 언어편향이 크므로 보조 증거로만 남긴다.
"""
    (SENSITIVITY_OUT / "sensitivity_report.md").write_text(report, encoding="utf-8")
    summary = {
        "status": "ok",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": THRESHOLDS,
        "countries": COUNTRIES,
        "outputs": [path.name for path in sorted(SENSITIVITY_OUT.iterdir())],
    }
    (SENSITIVITY_OUT / "sensitivity_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
