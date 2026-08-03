from __future__ import annotations

import bisect
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output_domains"
DB = ROOT / "domain_pilot.duckdb"
DOMAINS = ["technology", "investment", "food", "entertainment", "politics", "sports"]
COUNTRIES = ["KR", "JP", "TW", "SG", "US", "GB"]
MIN_COUNTRY_DOMAIN_N = 300
MIN_MONTH_DOMAIN_N = 100


def write_csv(path: Path, columns: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def fetch(connection: duckdb.DuckDBPyConnection, query: str):
    cursor = connection.execute(query)
    return [item[0] for item in cursor.description], cursor.fetchall()


def effect(kr_values: list[float], peer_values: list[float]):
    peer = sorted(peer_values)
    shorter = longer = ties = 0
    for value in kr_values:
        lo = bisect.bisect_left(peer, value)
        hi = bisect.bisect_right(peer, value)
        longer += lo
        ties += hi - lo
        shorter += len(peer) - hi
    total = len(kr_values) * len(peer)
    if not total:
        return None, None, None, None
    p_shorter = shorter / total
    p_longer = longer / total
    p_tie = ties / total
    return p_shorter, p_longer, p_tie, p_shorter - p_longer


def main() -> None:
    connection = duckdb.connect(str(DB))
    connection.execute(
        """
        CREATE OR REPLACE TABLE strict_validated AS
        SELECT
            *,
            CASE
                WHEN strict_domain = 'food'
                     AND regexp_matches(trend_text, '김치프리미엄', 'i')
                    THEN 'unclassified'
                WHEN strict_domain = 'technology'
                     AND regexp_matches(trend_text, '(사이버대학교|사이버대학|민방위.*사이버교육)', 'i')
                    THEN 'unclassified'
                WHEN strict_domain = 'politics'
                     AND regexp_matches(trend_text, '^정부\s*24$', 'i')
                    THEN 'unclassified'
                ELSE strict_domain
            END AS validated_domain
        FROM classified;
        """
    )

    coverage_query = """
        SELECT location, count(*) AS episodes_total,
            count(*) FILTER (WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')) AS assigned,
            round(100.0 * assigned / episodes_total, 3) AS assigned_pct,
            count(*) FILTER (WHERE validated_domain = 'ambiguous') AS ambiguous,
            count(*) FILTER (WHERE validated_domain = 'unclassified') AS unclassified
        FROM strict_validated
        GROUP BY location ORDER BY location;
    """
    coverage_columns, coverage_rows = fetch(connection, coverage_query)
    write_csv(OUT / "strict_coverage_country.csv", coverage_columns, coverage_rows)

    stats_query = """
        SELECT validated_domain AS domain, location,
            count(*) AS episodes_total,
            count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
            round(median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS median_h,
            round(avg(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS mean_h,
            round(quantile_cont(duration_hours, 0.90) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS p90_h,
            round(median(search_volume_lower), 2) AS median_volume
        FROM strict_validated
        WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')
        GROUP BY validated_domain, location
        ORDER BY domain, median_h;
    """
    stats_columns, stats_rows = fetch(connection, stats_query)
    write_csv(OUT / "strict_domain_stats.csv", stats_columns, stats_rows)

    ranks_query = f"""
        WITH stats AS (
            SELECT validated_domain AS domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM strict_validated
            WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY validated_domain, location
        ), eligible AS (
            SELECT * FROM stats WHERE n_complete >= {MIN_COUNTRY_DOMAIN_N}
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY domain ORDER BY median_h ASC) AS short_rank,
                count(*) OVER (PARTITION BY domain) AS eligible_countries
            FROM eligible
        )
        SELECT domain, location, n_complete, round(median_h, 4) AS median_h,
            short_rank, eligible_countries,
            CASE WHEN eligible_countries = 6 THEN 'comparable' ELSE 'incomplete_coverage' END AS status
        FROM ranked ORDER BY domain, short_rank, location;
    """
    ranks_columns, ranks_rows = fetch(connection, ranks_query)
    write_csv(OUT / "strict_domain_ranks.csv", ranks_columns, ranks_rows)

    korea_query = f"""
        WITH stats AS (
            SELECT validated_domain AS domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM strict_validated
            WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY validated_domain, location
        ), eligible AS (
            SELECT * FROM stats WHERE n_complete >= {MIN_COUNTRY_DOMAIN_N}
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY domain ORDER BY median_h ASC) AS short_rank,
                count(*) OVER (PARTITION BY domain) AS eligible_countries
            FROM eligible
        )
        SELECT domain, n_complete AS korea_n, round(median_h, 4) AS korea_median_h,
            short_rank AS korea_rank, eligible_countries,
            CASE
                WHEN eligible_countries = 6 AND short_rank = 1 THEN 'Korea shortest'
                WHEN eligible_countries = 6 THEN 'Korea not shortest'
                ELSE 'Insufficient peer coverage'
            END AS result
        FROM ranked WHERE location = 'KR' ORDER BY domain;
    """
    korea_columns, korea_rows = fetch(connection, korea_query)
    write_csv(OUT / "strict_korea_domain_ranks.csv", korea_columns, korea_rows)

    month_query = f"""
        WITH monthly AS (
            SELECT date_trunc('month', collection_date) AS month,
                validated_domain AS domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM strict_validated
            WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY month, validated_domain, location
        ), eligible AS (
            SELECT * FROM monthly WHERE n_complete >= {MIN_MONTH_DOMAIN_N}
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY month, domain ORDER BY median_h ASC) AS short_rank,
                count(*) OVER (PARTITION BY month, domain) AS eligible_countries
            FROM eligible
        )
        SELECT domain,
            count(*) FILTER (WHERE location = 'KR' AND eligible_countries = 6) AS comparable_months,
            count(*) FILTER (WHERE location = 'KR' AND eligible_countries = 6 AND short_rank = 1) AS shortest_months,
            round(100.0 * shortest_months / nullif(comparable_months, 0), 3) AS shortest_pct
        FROM ranked GROUP BY domain ORDER BY domain;
    """
    month_columns, month_rows = fetch(connection, month_query)
    write_csv(OUT / "strict_monthly_korea_shortest.csv", month_columns, month_rows)

    audit_query = """
        WITH ranked AS (
            SELECT validated_domain AS domain, location, trends, trend_breakdown,
                search_volume_lower, duration_hours,
                row_number() OVER (PARTITION BY validated_domain, location ORDER BY search_volume_lower DESC NULLS LAST, episode_id) AS volume_rank,
                row_number() OVER (PARTITION BY validated_domain, location ORDER BY hash(trends, episode_id)) AS sample_rank
            FROM strict_validated
            WHERE validated_domain IN ('technology','investment','food','entertainment','politics','sports')
        )
        SELECT domain, location,
            CASE WHEN volume_rank <= 15 THEN 'top_volume' ELSE 'deterministic_sample' END AS sample_type,
            trends, trend_breakdown, search_volume_lower, round(duration_hours, 4) AS duration_hours
        FROM ranked
        WHERE volume_rank <= 15 OR sample_rank <= 15
        ORDER BY domain, location, sample_type, search_volume_lower DESC NULLS LAST;
    """
    audit_columns, audit_rows = fetch(connection, audit_query)
    write_csv(OUT / "strict_classification_audit.csv", audit_columns, audit_rows)

    effects = []
    for domain in DOMAINS:
        kr = [float(x[0]) for x in connection.execute(
            """SELECT duration_hours FROM strict_validated
               WHERE validated_domain = ? AND location = 'KR'
                 AND NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48""",
            [domain],
        ).fetchall()]
        for peer in [country for country in COUNTRIES if country != 'KR']:
            values = [float(x[0]) for x in connection.execute(
                """SELECT duration_hours FROM strict_validated
                   WHERE validated_domain = ? AND location = ?
                     AND NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48""",
                [domain, peer],
            ).fetchall()]
            p_shorter, p_longer, p_tie, direction = effect(kr, values)
            effects.append((domain, peer, len(kr), len(values), p_shorter, p_longer, p_tie, direction))
    effect_columns = ['domain','peer','korea_n','peer_n','p_korea_shorter','p_korea_longer','p_tie','direction_effect']
    write_csv(OUT / "strict_korea_effects.csv", effect_columns, effects)

    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "main_method": "strict trend-name lexicon with audited exclusions",
        "sensitivity_method": "expanded trend plus related-query breakdown",
        "exclusions": ["김치프리미엄 from food", "사이버대학교/민방위 사이버교육 from technology", "정부24 from politics"],
        "minimum_country_domain_n": MIN_COUNTRY_DOMAIN_N,
        "minimum_month_domain_n": MIN_MONTH_DOMAIN_N,
    }
    (OUT / "strict_validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 영역별 파일럿 C 엄격 분류 검증", "",
        "## 주 분석 전환", "",
        "표본 감사에서 주변 연관 검색어가 본 검색어를 다른 영역으로 끌고 가는 오류가 확인돼 검색어 자체만 적중시키는 strict 방식을 주 분석으로 전환했다.", "",
        "## 한국 순위", "",
        "| 영역 | 한국 표본 | 중앙 수명 | 순위 | 비교 국가 | 판정 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in korea_rows:
        lines.append(f"| {row[0]} | {row[1]} | {row[2]}시간 | {row[3]}위 | {row[4]} | {row[5]} |")
    lines += [
        "", "## 판정", "",
        "- 6개국 모두 표본 기준을 충족한 영역에서만 국가 특수성을 판정한다.",
        "- 표본 기준 미달 영역은 방향을 설명할 수 있지만 이론 증거로 승격하지 않는다.",
        "- expanded 방식은 민감도 검증으로만 보존한다.",
    ]
    (OUT / "strict_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
