from __future__ import annotations

import bisect
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from archive_pilot import (
    BASE_URL,
    CACHE,
    COUNTRIES,
    END_DATE,
    FILES,
    ROOT,
    START_DATE,
    download,
)

OUT = ROOT / "output_domains"
OUT.mkdir(parents=True, exist_ok=True)

MIN_COUNTRY_DOMAIN_N = 300
MIN_MONTH_DOMAIN_N = 100
DOMAINS = ["technology", "investment", "food", "entertainment", "politics", "sports"]

PATTERNS = {
    "technology": [
        r"(?<![a-z])ai(?![a-z])", r"artificial intelligence", r"machine learning",
        r"chatgpt", r"openai", r"gemini", r"claude", r"copilot", r"deepseek",
        r"nvidia", r"semiconductor", r"microchip", r"chipset", r"gpu", r"cpu",
        r"software", r"cybersecurity", r"cyber attack", r"hacking", r"hacker",
        r"smartphone", r"iphone", r"android", r"apple intelligence", r"windows",
        r"microsoft", r"cloud computing", r"data center", r"robot", r"robotics",
        r"인공지능", r"챗gpt", r"오픈ai", r"제미나이", r"클로드", r"딥시크",
        r"엔비디아", r"반도체", r"스마트폰", r"아이폰", r"안드로이드", r"소프트웨어",
        r"사이버", r"해킹", r"로봇", r"클라우드", r"데이터센터",
        r"人工知能", r"チャットgpt", r"オープンai", r"ジェミニ", r"クロード",
        r"ディープシーク", r"エヌビディア", r"半導体", r"スマホ", r"スマートフォン",
        r"アイフォン", r"アンドロイド", r"ソフトウェア", r"サイバー", r"ハッキング",
        r"ロボット", r"クラウド",
        r"人工智慧", r"人工智能", r"聊天機器人", r"聊天机器人", r"輝達", r"英伟达",
        r"半導體", r"半导体", r"智慧型手機", r"智能手机", r"軟體", r"软件",
        r"網路安全", r"网络安全", r"駭客", r"黑客", r"機器人", r"机器人", r"雲端", r"云端",
    ],
    "investment": [
        r"stock market", r"stock price", r"stocks", r"shares", r"wall street",
        r"nasdaq", r"dow jones", r"s&p 500", r"earnings", r"dividend", r"investing",
        r"investment", r"investor", r"bitcoin", r"btc", r"ethereum", r"crypto",
        r"cryptocurrency", r"etf", r"ipo", r"bond yield", r"interest rate",
        r"exchange rate", r"forex", r"inflation", r"mortgage rate", r"real estate price",
        r"gold price", r"oil price",
        r"주식", r"증시", r"주가", r"코스피", r"코스닥", r"나스닥", r"다우지수",
        r"투자", r"투자자", r"비트코인", r"이더리움", r"가상자산", r"암호화폐",
        r"코인 시세", r"금리", r"환율", r"채권", r"공모주", r"배당", r"부동산 가격",
        r"집값", r"금값", r"유가",
        r"株価", r"株式", r"日経平均", r"投資", r"投資家", r"ビットコイン",
        r"イーサリアム", r"仮想通貨", r"金利", r"為替", r"円安", r"円高",
        r"債券", r"配当", r"不動産価格", r"原油価格", r"金価格",
        r"股票", r"股市", r"股價", r"股价", r"投資", r"投资", r"投資人", r"投资者",
        r"比特幣", r"比特币", r"以太坊", r"加密貨幣", r"加密货币", r"虛擬貨幣",
        r"虚拟货币", r"利率", r"匯率", r"汇率", r"債券", r"债券", r"房價", r"房价",
        r"金價", r"金价", r"油價", r"油价",
    ],
    "food": [
        r"recipe", r"restaurant", r"food menu", r"food recall", r"food safety",
        r"cooking", r"cuisine", r"coffee", r"cafe", r"pizza", r"burger", r"chicken recipe",
        r"ramen", r"sushi", r"barbecue", r"bbq", r"beer", r"wine", r"cake", r"bread",
        r"chocolate", r"ice cream", r"fast food", r"michelin",
        r"음식", r"식품", r"맛집", r"레시피", r"요리", r"식당", r"메뉴", r"카페",
        r"커피", r"치킨", r"피자", r"햄버거", r"라면", r"떡볶이", r"김치", r"삼겹살",
        r"맥주", r"와인", r"케이크", r"빵", r"초콜릿", r"아이스크림", r"미슐랭",
        r"料理", r"レシピ", r"食品", r"飲食店", r"グルメ", r"メニュー", r"カフェ",
        r"コーヒー", r"ラーメン", r"寿司", r"焼肉", r"ピザ", r"ハンバーガー",
        r"ビール", r"ワイン", r"ケーキ", r"パン", r"チョコレート", r"アイスクリーム",
        r"食物", r"食品", r"美食", r"餐廳", r"餐厅", r"食譜", r"食谱", r"料理",
        r"菜單", r"菜单", r"咖啡", r"拉麵", r"拉面", r"壽司", r"寿司", r"燒肉", r"烧肉",
        r"披薩", r"披萨", r"漢堡", r"汉堡", r"啤酒", r"葡萄酒", r"蛋糕", r"麵包", r"面包",
        r"巧克力", r"冰淇淋", r"米其林",
    ],
    "entertainment": [
        r"movie", r"film", r"cinema", r"tv series", r"television series", r"drama series",
        r"actor", r"actress", r"singer", r"song", r"music video", r"album", r"concert",
        r"netflix", r"disney plus", r"box office", r"oscar", r"grammy", r"anime", r"manga",
        r"playstation", r"xbox", r"nintendo", r"video game", r"gaming", r"steam game",
        r"영화", r"드라마", r"배우", r"가수", r"노래", r"음악", r"앨범", r"콘서트",
        r"넷플릭스", r"디즈니플러스", r"박스오피스", r"애니메이션", r"웹툰", r"만화",
        r"플레이스테이션", r"엑스박스", r"닌텐도", r"비디오게임", r"게임 출시",
        r"映画", r"ドラマ", r"俳優", r"女優", r"歌手", r"音楽", r"アルバム",
        r"コンサート", r"ネットフリックス", r"ディズニープラス", r"アニメ", r"漫画",
        r"プレイステーション", r"エックスボックス", r"任天堂", r"ゲーム発売",
        r"電影", r"电影", r"戲劇", r"戏剧", r"電視劇", r"电视剧", r"演員", r"演员",
        r"歌手", r"歌曲", r"音樂", r"音乐", r"專輯", r"专辑", r"演唱會", r"演唱会",
        r"網飛", r"网飞", r"迪士尼", r"動漫", r"动漫", r"漫畫", r"漫画", r"任天堂",
        r"遊戲上市", r"游戏上市",
    ],
    "politics": [
        r"election", r"president", r"prime minister", r"parliament", r"congress",
        r"senate", r"governor", r"government", r"political party", r"vote", r"voting",
        r"cabinet minister", r"impeachment", r"constitution court", r"supreme court ruling",
        r"white house", r"downing street",
        r"선거", r"대통령", r"국회", r"총리", r"장관", r"정부", r"정당", r"투표",
        r"정치", r"탄핵", r"헌법재판소", r"헌재", r"대법원 판결", r"국민의힘", r"민주당",
        r"選挙", r"大統領", r"首相", r"国会", r"政府", r"政党", r"投票", r"政治",
        r"大臣", r"弾劾", r"最高裁判所", r"自民党", r"立憲民主党",
        r"選舉", r"选举", r"總統", r"总统", r"總理", r"总理", r"國會", r"国会",
        r"立法院", r"政府", r"政黨", r"政党", r"投票", r"政治", r"部長", r"部长",
        r"彈劾", r"弹劾", r"最高法院", r"國民黨", r"国民党", r"民進黨", r"民进党",
    ],
    "sports": [
        r"football", r"soccer", r"baseball", r"basketball", r"volleyball", r"tennis",
        r"golf tournament", r"cricket", r"rugby", r"formula 1", r"f1 race", r"ufc",
        r"boxing", r"nba", r"mlb", r"nfl", r"nhl", r"premier league", r"champions league",
        r"world cup", r"olympic", r"grand slam", r"super bowl", r"wimbledon",
        r"축구", r"야구", r"농구", r"배구", r"테니스", r"골프대회", r"복싱", r"격투기",
        r"프리미어리그", r"챔피언스리그", r"월드컵", r"올림픽", r"슈퍼볼", r"윔블던",
        r"サッカー", r"野球", r"バスケットボール", r"バレー", r"テニス", r"ゴルフ大会",
        r"ボクシング", r"格闘技", r"プレミアリーグ", r"チャンピオンズリーグ",
        r"ワールドカップ", r"オリンピック", r"スーパーボウル", r"ウィンブルドン",
        r"足球", r"棒球", r"籃球", r"篮球", r"排球", r"網球", r"网球", r"高爾夫",
        r"高尔夫", r"拳擊", r"拳击", r"格鬥", r"格斗", r"英超", r"歐冠", r"欧冠",
        r"世界盃", r"世界杯", r"奧運", r"奥运", r"超級盃", r"超级碗", r"溫布頓", r"温布尔登",
    ],
}


def alternation(items: list[str]) -> str:
    return "(?:" + "|".join(items) + ")"


def write_csv(path: Path, columns: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def fetch_table(connection: duckdb.DuckDBPyConnection, query: str):
    cursor = connection.execute(query)
    return [item[0] for item in cursor.description], cursor.fetchall()


def common_language_effect(kr_values: list[float], peer_values: list[float]) -> tuple[float, float, float, float]:
    peer = sorted(peer_values)
    n_peer = len(peer)
    shorter = longer = ties = 0
    for value in kr_values:
        lo = bisect.bisect_left(peer, value)
        hi = bisect.bisect_right(peer, value)
        longer += lo
        ties += hi - lo
        shorter += n_peer - hi
    total = len(kr_values) * n_peer
    if total == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    p_shorter = shorter / total
    p_longer = longer / total
    p_tie = ties / total
    return p_shorter, p_longer, p_tie, p_shorter - p_longer


def main() -> None:
    started = datetime.now(timezone.utc)
    parquet_paths = []
    downloads = []
    for name in FILES:
        url = BASE_URL.format(name=name)
        path = CACHE / name
        downloads.append(download(url, path))
        parquet_paths.append(str(path))

    connection = duckdb.connect(str(ROOT / "domain_pilot.duckdb"))
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
        CREATE OR REPLACE TABLE base AS
        SELECT
            trends,
            location,
            episode_id,
            try_cast(start_time AS TIMESTAMP) AS start_time,
            try_cast(end_time AS TIMESTAMP) AS end_time,
            try_cast(collection_date AS DATE) AS collection_date,
            search_volume_lower,
            n_queries,
            trend_breakdown,
            duration_is_estimate,
            duration_hours,
            lower(coalesce(trends, '') || ' ' || coalesce(trend_breakdown, '')) AS expanded_text,
            lower(coalesce(trends, '')) AS trend_text
        FROM archive_source
        WHERE location IN ({countries_sql})
          AND try_cast(collection_date AS DATE)
              BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}';
        """
    )

    hit_columns = []
    strict_columns = []
    for domain in DOMAINS:
        pattern = alternation(PATTERNS[domain]).replace("'", "''")
        hit_columns.append(f"regexp_matches(expanded_text, '{pattern}', 'i') AS {domain}_hit")
        strict_columns.append(f"regexp_matches(trend_text, '{pattern}', 'i') AS {domain}_strict_hit")

    connection.execute(
        f"""
        CREATE OR REPLACE TABLE classified_hits AS
        SELECT *, {", ".join(hit_columns)}, {", ".join(strict_columns)}
        FROM base;
        """
    )

    expanded_sum = " + ".join(f"cast({d}_hit AS INTEGER)" for d in DOMAINS)
    strict_sum = " + ".join(f"cast({d}_strict_hit AS INTEGER)" for d in DOMAINS)
    expanded_cases = "\n".join(f"WHEN {domain}_hit THEN '{domain}'" for domain in DOMAINS)
    strict_cases = "\n".join(f"WHEN {domain}_strict_hit THEN '{domain}'" for domain in DOMAINS)

    connection.execute(
        f"""
        CREATE OR REPLACE TABLE classified AS
        SELECT *,
            ({expanded_sum}) AS expanded_hit_count,
            ({strict_sum}) AS strict_hit_count,
            CASE
                WHEN ({expanded_sum}) = 0 THEN 'unclassified'
                WHEN ({expanded_sum}) > 1 THEN 'ambiguous'
                {expanded_cases}
                ELSE 'unclassified'
            END AS domain,
            CASE
                WHEN ({strict_sum}) = 0 THEN 'unclassified'
                WHEN ({strict_sum}) > 1 THEN 'ambiguous'
                {strict_cases}
                ELSE 'unclassified'
            END AS strict_domain
        FROM classified_hits;
        """
    )

    coverage_query = """
        SELECT location, count(*) AS episodes_total,
            count(*) FILTER (WHERE domain IN ('technology','investment','food','entertainment','politics','sports')) AS assigned,
            round(100.0 * assigned / episodes_total, 3) AS assigned_pct,
            count(*) FILTER (WHERE domain = 'ambiguous') AS ambiguous,
            round(100.0 * ambiguous / episodes_total, 3) AS ambiguous_pct,
            count(*) FILTER (WHERE domain = 'unclassified') AS unclassified,
            round(100.0 * unclassified / episodes_total, 3) AS unclassified_pct
        FROM classified GROUP BY location ORDER BY location;
    """
    coverage_columns, coverage_rows = fetch_table(connection, coverage_query)
    write_csv(OUT / "domain_coverage_country.csv", coverage_columns, coverage_rows)

    duration_query = """
        SELECT domain, location, count(*) AS episodes_total,
            count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS episodes_complete,
            round(100 * avg(CASE WHEN duration_is_estimate THEN 1.0 ELSE 0.0 END), 3) AS estimated_pct,
            round(median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS median_h,
            round(avg(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS mean_h,
            round(quantile_cont(duration_hours, 0.90) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48), 4) AS p90_h,
            round(median(search_volume_lower), 2) AS median_search_volume
        FROM classified
        WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
        GROUP BY domain, location ORDER BY domain, median_h;
    """
    duration_columns, duration_rows = fetch_table(connection, duration_query)
    write_csv(OUT / "domain_duration_country.csv", duration_columns, duration_rows)

    rank_query = f"""
        WITH stats AS (
            SELECT domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY domain, location
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY domain ORDER BY median_h ASC) AS short_rank,
                count(*) FILTER (WHERE n_complete >= {MIN_COUNTRY_DOMAIN_N}) OVER (PARTITION BY domain) AS eligible_countries
            FROM stats WHERE n_complete >= {MIN_COUNTRY_DOMAIN_N}
        )
        SELECT domain, location, n_complete, round(median_h, 4) AS median_h, short_rank,
            eligible_countries,
            CASE WHEN eligible_countries = 6 THEN 'comparable' ELSE 'incomplete_coverage' END AS comparison_status
        FROM ranked ORDER BY domain, short_rank, location;
    """
    rank_columns, rank_rows = fetch_table(connection, rank_query)
    write_csv(OUT / "domain_country_ranks.csv", rank_columns, rank_rows)

    korea_rank_query = f"""
        WITH stats AS (
            SELECT domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY domain, location
        ), eligible AS (
            SELECT * FROM stats WHERE n_complete >= {MIN_COUNTRY_DOMAIN_N}
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY domain ORDER BY median_h ASC) AS short_rank,
                count(*) OVER (PARTITION BY domain) AS eligible_countries
            FROM eligible
        )
        SELECT domain, n_complete AS korea_n, round(median_h, 4) AS korea_median_h,
            short_rank AS korea_short_rank, eligible_countries,
            CASE
                WHEN eligible_countries = 6 AND short_rank = 1 THEN 'Korea shortest'
                WHEN eligible_countries = 6 THEN 'Korea not shortest'
                ELSE 'Insufficient peer coverage'
            END AS result
        FROM ranked WHERE location = 'KR' ORDER BY domain;
    """
    korea_rank_columns, korea_rank_rows = fetch_table(connection, korea_rank_query)
    write_csv(OUT / "korea_domain_ranks.csv", korea_rank_columns, korea_rank_rows)

    daily_query = """
        WITH daily AS (
            SELECT domain, location, collection_date, count(*) AS episodes
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY domain, location, collection_date
        )
        SELECT domain, location, count(*) AS observed_days,
            round(avg(episodes), 4) AS daily_mean,
            round(stddev_samp(episodes), 4) AS daily_sd,
            round(stddev_samp(episodes) / nullif(avg(episodes), 0), 4) AS daily_cv
        FROM daily GROUP BY domain, location ORDER BY domain, daily_cv DESC;
    """
    daily_columns, daily_rows = fetch_table(connection, daily_query)
    write_csv(OUT / "domain_daily_activity.csv", daily_columns, daily_rows)

    sensitivity_query = """
        WITH expanded AS (
            SELECT 'expanded' AS method, domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY domain, location
        ), strict AS (
            SELECT 'strict' AS method, strict_domain AS domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM classified
            WHERE strict_domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY strict_domain, location
        )
        SELECT method, domain, location, n_complete, round(median_h, 4) AS median_h FROM expanded
        UNION ALL
        SELECT method, domain, location, n_complete, round(median_h, 4) AS median_h FROM strict
        ORDER BY domain, location, method;
    """
    sensitivity_columns, sensitivity_rows = fetch_table(connection, sensitivity_query)
    write_csv(OUT / "domain_classification_sensitivity.csv", sensitivity_columns, sensitivity_rows)

    month_query = f"""
        WITH monthly AS (
            SELECT date_trunc('month', collection_date) AS month, domain, location,
                count(*) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS n_complete,
                median(duration_hours) FILTER (WHERE NOT duration_is_estimate AND duration_hours BETWEEN 0 AND 48) AS median_h
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
            GROUP BY month, domain, location
        ), eligible AS (
            SELECT * FROM monthly WHERE n_complete >= {MIN_MONTH_DOMAIN_N}
        ), ranked AS (
            SELECT *, rank() OVER (PARTITION BY month, domain ORDER BY median_h ASC) AS short_rank,
                count(*) OVER (PARTITION BY month, domain) AS eligible_countries
            FROM eligible
        )
        SELECT domain,
            count(*) FILTER (WHERE location = 'KR' AND eligible_countries = 6) AS korea_comparable_months,
            count(*) FILTER (WHERE location = 'KR' AND eligible_countries = 6 AND short_rank = 1) AS korea_shortest_months,
            round(100.0 * korea_shortest_months / nullif(korea_comparable_months, 0), 3) AS korea_shortest_pct
        FROM ranked GROUP BY domain ORDER BY domain;
    """
    month_columns, month_rows = fetch_table(connection, month_query)
    write_csv(OUT / "domain_monthly_korea_shortest.csv", month_columns, month_rows)

    example_query = """
        WITH ranked AS (
            SELECT domain, location, trends, trend_breakdown, search_volume_lower, duration_hours,
                row_number() OVER (PARTITION BY domain, location ORDER BY search_volume_lower DESC NULLS LAST, episode_id) AS volume_rank,
                row_number() OVER (PARTITION BY domain, location ORDER BY hash(trends, episode_id)) AS sample_rank
            FROM classified
            WHERE domain IN ('technology','investment','food','entertainment','politics','sports')
        )
        SELECT domain, location,
            CASE WHEN volume_rank <= 10 THEN 'top_volume' ELSE 'deterministic_sample' END AS sample_type,
            trends, trend_breakdown, search_volume_lower, round(duration_hours, 4) AS duration_hours
        FROM ranked
        WHERE volume_rank <= 10 OR sample_rank <= 10
        ORDER BY domain, location, sample_type, search_volume_lower DESC NULLS LAST;
    """
    example_columns, example_rows = fetch_table(connection, example_query)
    write_csv(OUT / "classification_audit_examples.csv", example_columns, example_rows)

    effects = []
    for domain in DOMAINS:
        kr_values = [float(row[0]) for row in connection.execute(
            """SELECT duration_hours FROM classified
               WHERE domain = ? AND location = 'KR' AND NOT duration_is_estimate
                 AND duration_hours BETWEEN 0 AND 48""", [domain]).fetchall()]
        for peer in [c for c in COUNTRIES if c != "KR"]:
            peer_values = [float(row[0]) for row in connection.execute(
                """SELECT duration_hours FROM classified
                   WHERE domain = ? AND location = ? AND NOT duration_is_estimate
                     AND duration_hours BETWEEN 0 AND 48""", [domain, peer]).fetchall()]
            p_shorter, p_longer, p_tie, effect = common_language_effect(kr_values, peer_values)
            effects.append((domain, peer, len(kr_values), len(peer_values),
                            round(p_shorter, 6), round(p_longer, 6), round(p_tie, 6), round(effect, 6)))
    effect_columns = ["domain", "peer", "korea_n", "peer_n", "p_korea_shorter", "p_korea_longer", "p_tie", "direction_effect"]
    write_csv(OUT / "korea_domain_effects.csv", effect_columns, effects)

    totals = connection.execute(
        """SELECT count(*) AS rows_total,
            count(*) FILTER (WHERE domain IN ('technology','investment','food','entertainment','politics','sports')) AS assigned_rows,
            count(*) FILTER (WHERE domain = 'ambiguous') AS ambiguous_rows,
            count(*) FILTER (WHERE domain = 'unclassified') AS unclassified_rows
        FROM classified""").fetchone()

    summary = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "aurman/GoogleTrendArchive",
        "window": [START_DATE, END_DATE],
        "countries": COUNTRIES,
        "domains": DOMAINS,
        "method": "high_precision_multilingual_lexicon",
        "rows_total": totals[0],
        "assigned_rows": totals[1],
        "assigned_pct": round(100 * totals[1] / totals[0], 4),
        "ambiguous_rows": totals[2],
        "unclassified_rows": totals[3],
        "minimum_country_domain_n": MIN_COUNTRY_DOMAIN_N,
        "minimum_month_domain_n": MIN_MONTH_DOMAIN_N,
        "downloads": downloads,
    }
    (OUT / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_lines = [
        "# GoogleTrendArchive 영역별 파일럿 C", "", "## 목적", "",
        "기술·투자·음식·연예·정치·스포츠 영역별로 6개국의 Trending Now 에피소드 수명을 비교한다.", "",
        "## 분류 방식", "",
        "- 한국어·일본어·중국어·영어 고정 사전 기반 고정밀 분류",
        "- 검색어와 trend_breakdown을 함께 사용한 expanded 방식이 주 분석",
        "- 검색어 자체만 사용하는 strict 방식을 민감도 검증으로 병행",
        "- 둘 이상의 영역이 동시에 적중하면 ambiguous로 제외",
        "- 어느 영역에도 적중하지 않으면 unclassified로 유지",
        f"- 전체 {totals[0]:,}행 중 {totals[1]:,}행({summary['assigned_pct']:.2f}%) 분류", "",
        "## 한국의 영역별 순위", "",
        "| 영역 | 한국 표본 | 한국 중앙 수명 | 최단 순위 | 비교 가능 국가 | 판정 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in korea_rank_rows:
        report_lines.append(f"| {row[0]} | {row[1]} | {row[2]}시간 | {row[3]}위 | {row[4]} | {row[5]} |")
    report_lines += ["", "## 월별 한국 최단 빈도", "", "| 영역 | 비교 가능 월 | 한국 최단 월 | 비율 |", "|---|---:|---:|---:|"]
    for row in month_rows:
        report_lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]}% |")
    report_lines += [
        "", "## 해석 제한", "",
        "1. 사전에 잡힌 명시적 영역어만 분류하므로 고유명사 중심 검색어가 대량으로 미분류된다.",
        "2. trend_breakdown은 분류 범위를 넓히지만 주변 맥락이 원 검색어의 영역을 잘못 끌어갈 수 있다.",
        "3. 국가·언어별 사전 포괄률이 다르므로 국가 간 차이에 분류 편향이 섞일 수 있다.",
        "4. 에피소드 수명은 사전등록된 관심 반감기 H가 아니다.",
        "5. 표본 300개 미만인 국가·영역은 국가 순위 판정에서 제외한다.",
        "", "## 판정 원칙", "",
        "- 한국이 6개국 모두 비교 가능한 영역에서 1위이고, 월별 최단 빈도와 strict 방식에서도 방향이 유지될 때만 영역 제한형의 지지 정황으로 본다.",
        "- 한 조건이라도 무너지면 탐색 후보로만 남긴다.",
    ]
    (OUT / "domain_pilot_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
