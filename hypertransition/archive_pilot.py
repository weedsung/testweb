from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "archive_cache"
OUT = ROOT / "output_archive"
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

COUNTRIES = ["KR", "JP", "TW", "SG", "US", "GB"]
START_DATE = "2024-11-28"
END_DATE = "2026-01-03"
BASE_URL = (
    "https://huggingface.co/datasets/aurman/GoogleTrendArchive/resolve/"
    "refs%2Fconvert%2Fparquet/trending_queries/train/{name}?download=true"
)
FILES = [f"000{i}.parquet" for i in range(5)]


def download(url: str, destination: Path, attempts: int = 4) -> dict:
    if destination.exists() and destination.stat().st_size > 1_000_000:
        return file_meta(destination, url, cached=True)
    error = None
    for attempt in range(attempts):
        try:
            with requests.get(url, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                temp = destination.with_suffix(destination.suffix + ".part")
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                temp.replace(destination)
                return file_meta(destination, url, cached=False)
        except Exception as exc:
            error = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download failed for {url}: {error}")


def file_meta(path: Path, url: str, cached: bool) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "file": path.name,
        "url": url,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "cached": cached,
    }


def write_rows(path: Path, columns: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def markdown_table(columns: list[str], rows: list[tuple], limit: int | None = None) -> str:
    selected = rows if limit is None else rows[:limit]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in selected:
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> None:
    started = datetime.now(timezone.utc)
    summary: dict = {
        "started_at": started.isoformat(),
        "dataset": "aurman/GoogleTrendArchive",
        "dataset_ref": "refs/convert/parquet",
        "window": [START_DATE, END_DATE],
        "countries": COUNTRIES,
        "downloads": [],
    }

    parquet_paths = []
    for name in FILES:
        url = BASE_URL.format(name=name)
        path = CACHE / name
        meta = download(url, path)
        summary["downloads"].append(meta)
        parquet_paths.append(str(path))
        print(f"downloaded {name}: {meta['bytes']} bytes")

    connection = duckdb.connect(str(ROOT / "archive_pilot.duckdb"))
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='6GB'")
    paths_sql = ",".join(repr(path) for path in parquet_paths)
    countries_sql = ",".join(repr(country) for country in COUNTRIES)

    connection.execute(
        f"""
        CREATE OR REPLACE VIEW archive_source AS
        SELECT * FROM read_parquet([{paths_sql}], union_by_name=true);
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE six_country AS
        SELECT
            trends,
            location,
            episode_id,
            try_cast(start_time AS TIMESTAMP) AS start_time,
            try_cast(end_time AS TIMESTAMP) AS end_time,
            try_cast(collection_date AS DATE) AS collection_date,
            n_days_observed,
            total_occurrences,
            search_volume_lower,
            n_queries,
            trend_breakdown,
            duration_minutes,
            duration_is_estimate,
            duration_hours
        FROM archive_source
        WHERE location IN ({countries_sql})
          AND try_cast(collection_date AS DATE)
              BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}';
        """
    )

    country_query = """
        SELECT
            location,
            count(*) AS episodes_total,
            count(*) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ) AS episodes_complete,
            round(100 * avg(CASE WHEN duration_is_estimate THEN 1.0 ELSE 0.0 END), 3)
                AS estimated_pct,
            round(quantile_cont(duration_hours, 0.25) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS duration_p25_h,
            round(median(duration_hours) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS duration_median_h,
            round(quantile_cont(duration_hours, 0.75) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS duration_p75_h,
            round(quantile_cont(duration_hours, 0.90) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS duration_p90_h,
            round(avg(duration_hours) FILTER (
                WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
            ), 4) AS duration_mean_h,
            round(median(search_volume_lower), 2) AS search_volume_median,
            round(avg(n_queries), 4) AS mean_queries_per_episode,
            count(DISTINCT collection_date) AS observed_days,
            round(count(*)::DOUBLE / count(DISTINCT collection_date), 4)
                AS episodes_per_observed_day
        FROM six_country
        GROUP BY location
        ORDER BY duration_median_h ASC;
    """
    country_cursor = connection.execute(country_query)
    country_columns = [item[0] for item in country_cursor.description]
    country_rows = country_cursor.fetchall()
    write_rows(OUT / "country_duration_summary.csv", country_columns, country_rows)

    daily_query = """
        WITH daily AS (
            SELECT location, collection_date, count(*) AS episodes
            FROM six_country
            GROUP BY location, collection_date
        )
        SELECT
            location,
            count(*) AS observed_days,
            round(avg(episodes), 4) AS daily_mean,
            round(median(episodes), 4) AS daily_median,
            round(stddev_samp(episodes), 4) AS daily_sd,
            round(stddev_samp(episodes) / nullif(avg(episodes), 0), 4) AS daily_cv,
            min(episodes) AS daily_min,
            max(episodes) AS daily_max
        FROM daily
        GROUP BY location
        ORDER BY daily_cv DESC;
    """
    daily_cursor = connection.execute(daily_query)
    daily_columns = [item[0] for item in daily_cursor.description]
    daily_rows = daily_cursor.fetchall()
    write_rows(OUT / "daily_activity_summary.csv", daily_columns, daily_rows)

    pair_query = """
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
            round(median(lag_hours), 4) AS median_lag_h,
            round(quantile_cont(lag_hours, 0.75), 4) AS p75_lag_h,
            round(100 * avg(CASE WHEN lag_hours <= 6 THEN 1.0 ELSE 0.0 END), 3)
                AS within_6h_pct,
            round(100 * avg(CASE WHEN lag_hours <= 24 THEN 1.0 ELSE 0.0 END), 3)
                AS within_24h_pct,
            round(100 * avg(CASE WHEN lag_hours <= 48 THEN 1.0 ELSE 0.0 END), 3)
                AS within_48h_pct
        FROM pairs
        GROUP BY country_a, country_b
        ORDER BY shared_exact_trends DESC;
    """
    pair_cursor = connection.execute(pair_query)
    pair_columns = [item[0] for item in pair_cursor.description]
    pair_rows = pair_cursor.fetchall()
    write_rows(OUT / "pair_sync_summary.csv", pair_columns, pair_rows)

    korea_query = """
        WITH stats AS (
            SELECT
                location,
                median(duration_hours) FILTER (
                    WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48
                ) AS median_duration
            FROM six_country
            GROUP BY location
        ), kr AS (
            SELECT median_duration AS kr_median FROM stats WHERE location = 'KR'
        )
        SELECT
            stats.location,
            round(stats.median_duration, 4) AS country_median_h,
            round(kr.kr_median, 4) AS korea_median_h,
            round(kr.kr_median / nullif(stats.median_duration, 0), 4)
                AS korea_to_country_ratio,
            CASE
                WHEN kr.kr_median < stats.median_duration THEN 'Korea shorter'
                WHEN kr.kr_median > stats.median_duration THEN 'Korea longer'
                ELSE 'Equal'
            END AS direction
        FROM stats CROSS JOIN kr
        WHERE stats.location <> 'KR'
        ORDER BY korea_to_country_ratio ASC;
    """
    korea_cursor = connection.execute(korea_query)
    korea_columns = [item[0] for item in korea_cursor.description]
    korea_rows = korea_cursor.fetchall()
    write_rows(OUT / "korea_comparison.csv", korea_columns, korea_rows)

    totals = connection.execute(
        """
        SELECT
            count(*) AS rows,
            count(DISTINCT trends) AS unique_trends,
            min(collection_date) AS min_date,
            max(collection_date) AS max_date
        FROM six_country;
        """
    ).fetchone()
    summary.update(
        {
            "status": "ok",
            "rows": totals[0],
            "unique_trends": totals[1],
            "min_date": str(totals[2]),
            "max_date": str(totals[3]),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    (OUT / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = f"""# GoogleTrendArchive 파일럿 B 결과

## 범위

- 자료: `aurman/GoogleTrendArchive`
- 고정 기간: {START_DATE}~{END_DATE}
- 국가: KR, JP, TW, SG, US, GB
- 분석 단위: 국가 수준 Google Trending Now 에피소드
- 총 행 수: {totals[0]:,}
- 고유 검색어 수: {totals[1]:,}

## 1. 에피소드 수명

{markdown_table(country_columns, country_rows)}

`duration_median_h`는 관심 반감기 `H`가 아니라 Trending Now에 머문 에피소드 수명의 대리 지표다. 추정 종료시각이 아닌 완결 에피소드만 주 분석에 사용했다.

## 2. 한국 비교

{markdown_table(korea_columns, korea_rows)}

비율이 1보다 작으면 한국의 중앙 에피소드 수명이 비교국보다 짧다.

## 3. 일별 활동 변동성

{markdown_table(daily_columns, daily_rows)}

`daily_cv`는 일별 트렌드 에피소드 수의 변동계수다. 값이 클수록 관심 폭발의 일별 편차가 크다.

## 4. 국가 간 동일 검색어 시차

{markdown_table(pair_columns, pair_rows)}

동일 문자열의 최초 등장 시차만 계산했기 때문에 언어가 다른 국가 간 비교에는 강한 언어 편향이 있다. 이 표는 동기화의 보조 지표로만 사용한다.

## 해석 제한

1. 이 자료는 맞춤 검색어 시계열이 아니라 Trending Now 에피소드 자료다.
2. 에피소드 수명은 `V-H 계산 규칙 v0.1`의 `H`와 동일하지 않다.
3. 국가별 트렌드 선정 기준과 검색시장 규모가 다를 수 있다.
4. 동일 문자열 기반 국가 간 매칭은 번역·표기 차이를 잡지 못한다.
5. 따라서 이 결과는 `초고속 전이 사회 이론`의 확정 증거가 아니라 파일럿 B의 탐색 증거다.
"""
    (OUT / "pilot_b_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
