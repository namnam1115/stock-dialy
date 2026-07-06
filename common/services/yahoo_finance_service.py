# common/services/yahoo_finance_service.py
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import defusedxml.ElementTree as _defused_ET
from datetime import datetime
import yfinance as yf
import pandas as pd
from decimal import Decimal
from typing import Dict, List, Optional
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class YahooFinanceService:
    """Yahoo Finance APIからデータを取得するサービスクラス

    公開APIは Django cache による短TTLキャッシュを経由する。
    yfinance はリクエスト経路で同期呼び出しされるため、外部APIのレイテンシが
    そのまま画面の体感速度になる。同一銘柄への連続アクセスをプロセス内で吸収し、
    さらに取得失敗時は期限切れの旧値（stale コピー）へフォールバックして
    yfinance の断続的な障害への耐性を持たせる。
    """

    # 種別ごとのフレッシュTTL（秒）。株価を含むものは短く、財務諸表ベースは長く。
    CACHE_TTL = {
        'price': 600,        # 株価系: 10分
        'financial': 86400,  # 財務諸表系: 24時間
        'news': 1800,        # ニュース: 30分
    }
    # stale コピーの保持期間。フレッシュ期限切れ後もこの間は障害時フォールバックに使う
    STALE_TTL = 7 * 86400

    @classmethod
    def cached_fetch(cls, kind: str, key: str, fetch_fn):
        """種別 kind・キー key で fetch_fn() の結果をキャッシュする共通ヘルパー。

        - フレッシュヒット → そのまま返す（fetch_fn は呼ばない）
        - ミス → fetch_fn() を実行。非空の結果はフレッシュ＋stale の二重に保存
        - fetch_fn() が空（{} / [] / None）or 例外 → stale コピーがあればそれを返す
          （空結果はキャッシュしない＝次回リトライされる）
        """
        fresh_key = f"yf:{kind}:{key}"
        stale_key = f"yf:stale:{kind}:{key}"

        hit = cache.get(fresh_key)
        if hit is not None:
            return hit

        try:
            result = fetch_fn()
        except Exception as e:
            logger.warning(f"cached_fetch: fetch失敗 ({fresh_key}): {e}")
            result = None

        if result:
            ttl = cls.CACHE_TTL.get(kind, 600)
            cache.set(fresh_key, result, ttl)
            cache.set(stale_key, result, cls.STALE_TTL)
            return result

        stale = cache.get(stale_key)
        if stale is not None:
            logger.info(f"cached_fetch: staleフォールバック ({fresh_key})")
            return stale
        return result

    # ticker.info フォールバック用マッピング（主要ソースで取得できなかった場合のみ使用）
    METRIC_MAPPING_FALLBACK = {
        'roe':              ('returnOnEquity',   lambda x: x * 100 if x else None),
        'roa':              ('returnOnAssets',    lambda x: x * 100 if x else None),
        'operating_margin': ('operatingMargins',  lambda x: x * 100 if x else None),
        'revenue_growth':   ('revenueGrowth',     lambda x: x * 100 if x else None),
        'profit_growth':    ('earningsGrowth',    lambda x: x * 100 if x else None),
        'per':              ('trailingPE',         None),
        'pbr':              ('priceToBook',        None),
        'dividend_rate':    ('dividendRate',       None),
        'dividend_yield':   ('dividendYield',      lambda x: x * 100 if x else None),  # 修正: ×100
        'payout_ratio':     ('payoutRatio',        lambda x: x * 100 if x else None),
        'equity_ratio':     ('_calc_equity_ratio', None),
        'debt_equity_ratio':('debtToEquity',       None),
        'current_ratio':    ('currentRatio',       None),  # 修正: ×100なし
        'market_cap':       ('marketCap',          lambda x: x / 100_000_000 if x else None),
        'revenue':          ('totalRevenue',       lambda x: x / 100_000_000 if x else None),
        'operating_profit': ('_calc_operating_profit', None),
    }

    @staticmethod
    def get_ticker_symbol(company_code: str) -> str:
        """企業コードからYahoo Financeのティッカーシンボルを生成"""
        return f"{company_code}.T"

    # ------------------------------------------------------------------
    # プライベートヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_decimal(value, round_digits: int = 2) -> Optional[Decimal]:
        """float/int を Decimal に安全変換。NaN/None は None を返す。"""
        try:
            if value is None or pd.isna(value):
                return None
            return Decimal(str(round(float(value), round_digits)))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _fetch_fast_info(cls, ticker) -> dict:
        """fast_info から price / market_cap / shares を取得"""
        try:
            fi = ticker.fast_info
            price      = getattr(fi, 'last_price',   None) or getattr(fi, 'lastPrice',   None)
            market_cap = getattr(fi, 'market_cap',   None) or getattr(fi, 'marketCap',   None)
            shares     = getattr(fi, 'shares',       None) or getattr(fi, 'sharesOutstanding', None)
            return {'price': price, 'market_cap': market_cap, 'shares': shares}
        except Exception as e:
            logger.warning(f"fast_info 取得失敗: {e}")
            return {}

    @classmethod
    def _fetch_from_statements(cls, ticker, price: Optional[float], shares: Optional[float]) -> dict:
        """
        income_stmt（年次）/ quarterly_balance_sheet から財務指標を計算。

        注意: quarterly_income_stmt は日本企業では累計値（Q2=上半期合計、
        Q3=9ヶ月合計等）で格納されることがあり、4四半期を合算すると実態の
        3〜4倍になる。そのため損益計算書は年次 income_stmt を使用する。
        """
        result = {}
        ttm_net = None
        equity  = None

        # ---------- 損益計算書（年次）----------
        try:
            income_stmt = ticker.income_stmt  # 年次: 通常4年分

            if income_stmt is not None and not income_stmt.empty:
                i_cols     = sorted(income_stmt.columns, reverse=True)
                latest_col = i_cols[0]
                prior_col  = i_cols[1] if len(i_cols) >= 2 else None

                def annual(label):
                    if label not in income_stmt.index:
                        return None
                    v = income_stmt.loc[label, latest_col]
                    return float(v) if not pd.isna(v) else None

                def prior_annual(label):
                    if prior_col is None or label not in income_stmt.index:
                        return None
                    v = income_stmt.loc[label, prior_col]
                    return float(v) if not pd.isna(v) else None

                annual_revenue   = annual('Total Revenue')
                annual_operating = annual('Operating Income')
                annual_net       = annual('Net Income')
                prior_revenue    = prior_annual('Total Revenue')
                prior_net        = prior_annual('Net Income')

                ttm_net = annual_net  # ROE/PER 計算に使用

                if annual_revenue and annual_revenue != 0:
                    result['revenue'] = annual_revenue / 1e8
                    if annual_operating is not None:
                        result['operating_margin'] = annual_operating / annual_revenue * 100
                        result['operating_profit'] = annual_operating / 1e8

                if annual_revenue and prior_revenue and prior_revenue != 0:
                    result['revenue_growth'] = (annual_revenue - prior_revenue) / abs(prior_revenue) * 100

                if annual_net and prior_net and prior_net != 0:
                    result['profit_growth'] = (annual_net - prior_net) / abs(prior_net) * 100

        except Exception as e:
            logger.warning(f"年次損益計算書の処理に失敗: {e}")

        # ---------- 貸借対照表（直近四半期）----------
        try:
            balance_sheet = ticker.quarterly_balance_sheet

            if balance_sheet is not None and not balance_sheet.empty:
                b_col = sorted(balance_sheet.columns, reverse=True)[0]

                def bs(label):
                    if label not in balance_sheet.index:
                        return None
                    v = balance_sheet.loc[label, b_col]
                    return float(v) if not pd.isna(v) else None

                equity     = bs('Stockholders Equity')
                assets     = bs('Total Assets')
                total_debt = bs('Total Debt')
                curr_a     = bs('Current Assets')
                curr_l     = bs('Current Liabilities')

                if equity and assets and assets != 0:
                    result['equity_ratio'] = equity / assets * 100
                    if ttm_net is not None:
                        result['roe'] = ttm_net / equity * 100
                        result['roa'] = ttm_net / assets * 100

                if total_debt is not None and equity and equity != 0:
                    result['debt_equity_ratio'] = total_debt / equity

                if curr_a and curr_l and curr_l != 0:
                    result['current_ratio'] = curr_a / curr_l

        except Exception as e:
            logger.warning(f"貸借対照表の処理に失敗: {e}")

        # ---------- PER / PBR（株価ベース）----------
        try:
            if price and shares and shares > 0:
                if ttm_net and ttm_net > 0:
                    result['per'] = price / (ttm_net / shares)
                if equity and equity > 0:
                    result['pbr'] = price / (equity / shares)
        except Exception as e:
            logger.warning(f"PER/PBR計算に失敗: {e}")

        return result

    @classmethod
    def _fetch_dividends(cls, ticker, price: Optional[float]) -> dict:
        """ticker.dividends (直近365日合計) から配当指標を計算"""
        result = {}
        try:
            divs = ticker.dividends
            if divs is None or len(divs) == 0:
                return result

            one_year_ago = pd.Timestamp.now(tz=divs.index.tz) - pd.DateOffset(years=1)
            annual_div   = float(divs[divs.index >= one_year_ago].sum())

            if annual_div > 0:
                result['dividend_rate'] = annual_div
                if price and price > 0:
                    result['dividend_yield'] = annual_div / price * 100
        except Exception as e:
            logger.warning(f"配当データの取得に失敗: {e}")
        return result

    @classmethod
    def _fallback_from_info(cls, ticker, missing_keys: list) -> dict:
        """ticker.info から指定された指標のみ取得（フォールバック）"""
        if not missing_keys:
            return {}
        result = {}
        try:
            info = ticker.info
            if not info:
                return result

            for key in missing_keys:
                if key not in cls.METRIC_MAPPING_FALLBACK:
                    continue
                api_field, transform = cls.METRIC_MAPPING_FALLBACK[key]

                if api_field == '_calc_equity_ratio':
                    v = cls._calculate_equity_ratio(info)
                elif api_field == '_calc_operating_profit':
                    v = cls._calculate_operating_profit(info)
                else:
                    v = info.get(api_field)
                    if v is not None and transform:
                        v = transform(v)

                if v is not None:
                    result[key] = v
        except Exception as e:
            logger.warning(f"ticker.info フォールバック失敗: {e}")
        return result

    # ------------------------------------------------------------------
    # 旧フォールバック用計算メソッド（互換維持）
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_equity_ratio(info: dict) -> Optional[float]:
        debt_to_equity = info.get('debtToEquity')
        if debt_to_equity is not None and debt_to_equity >= 0:
            return (1 / (1 + debt_to_equity / 100)) * 100
        return None

    @staticmethod
    def _calculate_operating_profit(info: dict) -> Optional[float]:
        revenue         = info.get('totalRevenue')
        operating_margin = info.get('operatingMargins')
        if revenue and operating_margin:
            return revenue * operating_margin / 100_000_000
        return None

    # ------------------------------------------------------------------
    # 公開API
    # ------------------------------------------------------------------

    @classmethod
    def fetch_company_data(cls, company_code: str, fiscal_year: Optional[str] = None) -> Dict[str, Decimal]:
        """
        企業の財務データを取得（財務諸表ベース＋ ticker.info フォールバック）

        結果は24時間キャッシュされる（財務諸表は日次より細かく変わらないため）。

        Args:
            company_code: 企業コード（例: 7203）
            fiscal_year: 会計年度（現在は未使用）

        Returns:
            指標名とその値の辞書（Decimal型）
        """
        return cls.cached_fetch(
            'financial', company_code,
            lambda: cls._fetch_company_data_uncached(company_code, fiscal_year),
        ) or {}

    @classmethod
    def _fetch_company_data_uncached(cls, company_code: str, fiscal_year: Optional[str] = None) -> Dict[str, Decimal]:
        ticker_symbol = cls.get_ticker_symbol(company_code)

        try:
            ticker = yf.Ticker(ticker_symbol)

            # 1. fast_info で price / market_cap / shares を取得
            fi = cls._fetch_fast_info(ticker)
            price      = fi.get('price')
            market_cap = fi.get('market_cap')
            shares     = fi.get('shares')

            # 2. 財務諸表から指標を計算
            result = cls._fetch_from_statements(ticker, price, shares)

            # 3. 配当データ
            result.update(cls._fetch_dividends(ticker, price))

            # 4. 時価総額（fast_info ベース）
            if market_cap:
                result['market_cap'] = market_cap / 1e8
            elif price and shares:
                result['market_cap'] = price * shares / 1e8

            # 5. 取得できなかった指標は ticker.info でフォールバック
            all_keys     = list(cls.METRIC_MAPPING_FALLBACK.keys())
            missing_keys = [k for k in all_keys if k not in result]
            fallback     = cls._fallback_from_info(ticker, missing_keys)
            for k, v in fallback.items():
                result[k] = v

            # 6. 全値を Decimal に変換
            final = {}
            for metric_name, value in result.items():
                d = cls._safe_decimal(value)
                if d is not None:
                    final[metric_name] = d

            logger.info(f"fetch_company_data: {company_code} → {len(final)} 指標取得")
            return final

        except Exception as e:
            logger.error(f"fetch_company_data 失敗 ({ticker_symbol}): {e}")
            return {}

    @classmethod
    def fetch_bulk_data(cls, company_codes: list, fiscal_year: Optional[str] = None) -> Dict[str, Dict[str, Decimal]]:
        """複数企業のデータを一括取得"""
        results = {}
        for code in company_codes:
            data = cls.fetch_company_data(code, fiscal_year)
            if data:
                results[code] = data
        return results

    @classmethod
    def get_available_metrics(cls) -> list:
        """取得可能な指標のリストを返す"""
        return list(cls.METRIC_MAPPING_FALLBACK.keys())

    @classmethod
    def validate_ticker(cls, company_code: str) -> bool:
        """ティッカーシンボルが有効かチェック（fast_info で軽量判定）"""
        ticker_symbol = cls.get_ticker_symbol(company_code)
        try:
            fi = yf.Ticker(ticker_symbol).fast_info
            price = getattr(fi, 'last_price', None) or getattr(fi, 'lastPrice', None)
            return price is not None and float(price) > 0
        except Exception:
            return False

    @classmethod
    def fetch_stock_news(cls, company_code: str, company_name: str = "", max_items: int = 5) -> List[dict]:
        """
        yfinance + Google News RSS から直近ニュースを取得する。
        結果は30分キャッシュされる。
        Returns: [{"title": str, "source": str, "published": str}, ...]
        外部通信失敗時は取得できた分だけ返す（例外を外に漏らさない）。
        """
        return cls.cached_fetch(
            'news', f"{company_code}:{max_items}",
            lambda: cls._fetch_stock_news_uncached(company_code, company_name, max_items),
        ) or []

    @staticmethod
    def _fetch_stock_news_uncached(company_code: str, company_name: str = "", max_items: int = 5) -> List[dict]:
        news_items: List[dict] = []

        # --- yfinance ニュース ---
        try:
            ticker = yf.Ticker(f"{company_code}.T")
            yf_news = ticker.news or []
            for item in yf_news[:max_items]:
                title = item.get('title', '') if isinstance(item, dict) else ''
                if not title:
                    continue
                ts = item.get('providerPublishTime') if isinstance(item, dict) else None
                try:
                    published = datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else ''
                except (OSError, ValueError, OverflowError):
                    published = ''
                news_items.append({
                    'title': title,
                    'source': item.get('publisher', 'Yahoo Finance') if isinstance(item, dict) else 'Yahoo Finance',
                    'published': published,
                })
        except Exception as e:
            logger.warning(f"yfinance news取得失敗 ({company_code}): {e}")

        # --- Google News RSS（日本語） ---
        try:
            query_str = f"{company_name} {company_code}" if company_name else company_code
            query = urllib.parse.quote(query_str)
            url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URLスキームは https:// にハードコード
                content = resp.read()
            root = _defused_ET.fromstring(content)
            for item in root.findall('.//item')[:max_items]:
                title_el = item.find('title')
                source_el = item.find('source')
                pub_el = item.find('pubDate')
                if title_el is None or not title_el.text:
                    continue
                pub_str = ''
                if pub_el is not None and pub_el.text:
                    try:
                        from email.utils import parsedate
                        t = parsedate(pub_el.text)
                        if t:
                            pub_str = datetime(*t[:3]).strftime('%Y-%m-%d')
                    except Exception as e:
                        logger.debug('RSS pubDateのパースに失敗: %s', e)
                news_items.append({
                    'title': title_el.text,
                    'source': source_el.text if source_el is not None and source_el.text else 'Google News',
                    'published': pub_str,
                })
        except Exception as e:
            logger.warning(f"Google News RSS取得失敗 ({company_code}): {e}")

        # 重複排除（タイトル先頭30文字で判定）・件数制限
        seen: set = set()
        unique: List[dict] = []
        for item in news_items:
            key = item['title'][:30]
            if key not in seen:
                seen.add(key)
                unique.append(item)
            if len(unique) >= max_items:
                break

        return unique
