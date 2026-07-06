"""
Gemini APIを使った複数銘柄の投資比較分析サービス
"""
from google import genai
import logging
import json
import re
from django.conf import settings
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class GeminiStockAnalyzer:
    """複数銘柄の財務比較データを分析するGeminiサービス"""

    def __init__(self):
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        self.api_available = bool(api_key)
        self.client = None
        self.model = None
        self.initialization_error = None

        if not api_key:
            logger.warning("GEMINI_API_KEYが設定されていません")
            self.initialization_error = "API_KEY_MISSING"
            return

        try:
            self.client = genai.Client(api_key=api_key)
            self.model = "gemini-2.5-flash-lite"
            logger.info("GeminiStockAnalyzer: API初期化完了")
        except Exception as e:
            logger.error(f"Gemini初期化エラー: {e}")
            self.api_available = False
            self.initialization_error = str(e)

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------
    def analyze_stocks(
        self,
        stocks_data: List[Dict[str, Any]],
        news_map: Optional[Dict[str, List[Dict]]] = None,
    ) -> Dict[str, Any]:
        """
        複数銘柄を比較分析してJSON形式で返す。
        news_map: {stock_code: [{"title":str, "source":str, "published":str}, ...]}
        """
        if not stocks_data:
            return self._error_result("銘柄データがありません")

        if not self.model:
            logger.warning("Gemini API未使用 - フォールバック分析を実行")
            result = self._fallback_analysis(stocks_data)
            result['api_success'] = False
            result['fallback_used'] = True
            result['error_message'] = self.initialization_error
            return result

        prompt = self._build_prompt(stocks_data, news_map)
        try:
            logger.info(f"Gemini API呼び出し開始: {len(stocks_data)}銘柄")
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            if response.text:
                parsed = self._parse_response(response.text, stocks_data)
                if parsed.get('api_success'):
                    logger.info("Gemini API分析完了")
                    return parsed
        except Exception as e:
            logger.error(f"Gemini API呼び出しエラー: {e}")

        result = self._fallback_analysis(stocks_data)
        result['api_success'] = False
        result['fallback_used'] = True
        return result

    # ----------------------------------------------------------------
    # Prompt building
    # ----------------------------------------------------------------
    def _build_prompt(
        self,
        stocks_data: List[Dict],
        news_map: Optional[Dict[str, List[Dict]]] = None,
    ) -> str:
        stocks_desc = ""
        for d in stocks_data:
            name = d.get('stock_name') or d.get('code', '不明')
            code = d.get('code', '')

            roe = self._latest(d.get('roe', []))
            rev = d.get('revenue', [])
            oi = d.get('operating_income', [])
            eq_ratio = self._latest(d.get('equity_ratio', []))
            per = d.get('per')
            pbr = d.get('pbr')
            div_yield = d.get('dividend_yield')

            rev_latest = self._latest(rev)
            oi_latest = self._latest(oi)
            opm = round(oi_latest / rev_latest * 100, 1) if rev_latest and oi_latest else None

            rev_cagr = self._calc_cagr(rev)
            oi_cagr = self._calc_cagr(oi)

            margin_current = d.get('margin_current')
            margin_trend = d.get('margin_trend', [])

            def fmt(v, suffix=''):
                return f"{v}{suffix}" if v is not None else "データなし"

            if margin_current is not None:
                level = "買い優勢（高水準）" if margin_current >= 3 else \
                        "売り優勢（低水準）" if margin_current <= 0.5 else "中立水準"
                margin_str = f"{margin_current:.2f}倍 ({level})"
            else:
                margin_str = "データなし"

            news_section = ""
            if news_map:
                news_items = news_map.get(code, [])
                if news_items:
                    headlines = "\n".join(
                        f"    ・{n['title']}（{n['source']}、{n['published']}）"
                        for n in news_items[:5]
                    )
                    news_section = f"\n  【直近ニュース（参考）】\n{headlines}"

            stocks_desc += f"""
■ {name}（{code}）
  ROE: {fmt(roe, '%')} | 営業利益率: {fmt(opm, '%')} | 自己資本比率: {fmt(eq_ratio, '%')}
  PER: {fmt(per, '倍')} | PBR: {fmt(pbr, '倍')} | 配当利回り: {fmt(div_yield, '%')}
  売上CAGR(4年): {fmt(rev_cagr, '%')} | 営業利益CAGR: {fmt(oi_cagr, '%')}
  信用倍率（最新）: {margin_str}{news_section}
"""

        n = len(stocks_data)
        prompt = f"""あなたは日本株の投資プロアナリストです。以下の{n}銘柄の財務データを詳細に分析し、個人投資家向けの投資判断支援レポートを作成してください。

【分析銘柄】
{stocks_desc}
【3軸評価の定義（必ず遵守）】
各銘柄を以下の3軸でS/A/B/C/Dの5段階に評価してください。

① business_grade（企業価値）: ROE・営業利益率・売上/利益CAGR・自己資本比率など事業の質と成長力を評価。株価は考慮しない。
② valuation_grade（株価評価）: PER・PBR・配当利回りなど現在の株価がファンダメンタルズ対比で割安か割高かを評価。
  PER基準: 12倍以下→S, 12〜25倍→A, 25〜40倍→B, 40〜60倍→C, 60倍超→D
③ grade（総合評価）: ①と②を総合判断。PER50倍超はS禁止。PER60倍超は最大B。

【信用倍率の解釈（参考指標）】
信用倍率は「買い残高÷売り残高」の需給指標です。評価グレードへの直接反映は不要ですが、strengths/risks に言及してください。
【正しい解釈】高倍率（3倍以上）＝買い残過多→将来の売り圧力リスク（risks に記載）。低倍率（1倍以下）＝売り残優勢→将来の買い戻し需要・需給好転の可能性（strengths に記載）。

【直近ニュースの取り扱い】
各銘柄に「直近ニュース（参考）」が付いている場合、以下のルールで活用してください。
・財務データを評価の主軸とするが、重要なニュースはグレードにも反映してよい。
・業績修正・不祥事・事業転換など企業価値に直結するニュースは business_grade や grade の調整根拠に使用可。
・ポジティブなニュース（好決算・新製品・提携等）は strengths と business_grade 向上の根拠に使用可。
・ネガティブなニュース（不祥事・業績悪化・規制強化等）は risks と business_grade 引き下げの根拠に使用可。
・ただし PER/PBR に基づく valuation_grade と PER 制約（PER60倍超→最大B）は必ず守ること。
・ニュースがない、または株価・業績と無関係な内容はノイズとして無視すること。

以下のJSON形式（日本語）で回答してください。コードブロック（```）や余分な説明テキストは不要です。JSONのみを返してください。

{{
  "overall_summary": "全銘柄を俯瞰した分析コメント（100〜150文字）",
  "top_pick": "最も投資推奨度が高い銘柄コード（例: 7203）",
  "top_pick_reason": "推奨理由（50〜80文字）",
  "stocks": [
    {{
      "code": "銘柄コード",
      "business_grade": "S/A/B/C/D（企業価値評価）",
      "valuation_grade": "S/A/B/C/D（株価割安度評価）",
      "grade": "S/A/B/C/D（総合評価）",
      "investment_appeal": "投資魅力度の説明（50〜80文字）",
      "strengths": ["強み1（30文字程度）", "強み2"],
      "risks": ["リスク1（30文字程度）", "リスク2"],
      "recommendation": "積極買い/打診買い/様子見/回避"
    }}
  ],
  "comparison_insight": "銘柄間の相対的な優劣・特徴（80〜120文字）",
  "portfolio_note": "ポートフォリオ観点での組み入れ提案（50〜80文字）"
}}"""
        return prompt.strip()

    # ----------------------------------------------------------------
    # Response parsing
    # ----------------------------------------------------------------
    def _parse_response(self, text: str, stocks_data: List[Dict]) -> Dict:
        # コードブロックを除去
        text = re.sub(r'```(?:json)?', '', text).strip().rstrip('`')

        try:
            data = json.loads(text)
            data['api_success'] = True
            data['fallback_used'] = False
            # stocksの順序をstocks_dataに合わせて整列
            self._align_stocks_order(data, stocks_data)
            return data
        except json.JSONDecodeError:
            # JSONオブジェクトを抽出して再パース
            match = re.search(r'\{[\s\S]+\}', text)
            if match:
                try:
                    data = json.loads(match.group())
                    data['api_success'] = True
                    data['fallback_used'] = False
                    self._align_stocks_order(data, stocks_data)
                    return data
                except Exception as e:
                    logger.debug('Gemini応答のJSONパースに失敗(次の候補を試行): %s', e)

        logger.warning("Gemini応答のJSONパースに失敗 - フォールバックへ")
        return self._fallback_analysis(stocks_data)

    def _align_stocks_order(self, data: Dict, stocks_data: List[Dict]):
        """APIが返したstocksの順序をフロントエンドの銘柄順に整列"""
        if 'stocks' not in data:
            return
        code_map = {s.get('code', ''): s for s in data['stocks']}
        aligned = []
        for d in stocks_data:
            code = d.get('code', '')
            if code in code_map:
                aligned.append(code_map[code])
        if aligned:
            data['stocks'] = aligned

    # ----------------------------------------------------------------
    # Fallback analysis (API不使用時)
    # ----------------------------------------------------------------
    def _fallback_analysis(self, stocks_data: List[Dict]) -> Dict:
        stocks_analysis = []
        best_code = ''
        best_score = -1

        grade_map = {4: 'S', 3: 'A', 2: 'B', 1: 'C', 0: 'D'}

        def to_grade(score, thresholds):
            """スコアをS/A/B/C/Dに変換。thresholds=(S最低, A最低, B最低, C最低)"""
            s, a, b, c = thresholds
            if score >= s: return 'S'
            if score >= a: return 'A'
            if score >= b: return 'B'
            if score >= c: return 'C'
            return 'D'

        for d in stocks_data:
            code = d.get('code', '')
            roe = self._latest(d.get('roe', []))
            per = d.get('per')
            pbr = d.get('pbr')
            rev_cagr = self._calc_cagr(d.get('revenue', []))
            oi_cagr = self._calc_cagr(d.get('operating_income', []))
            eq_ratio = self._latest(d.get('equity_ratio', []))
            div_yield = d.get('dividend_yield')

            margin_current = d.get('margin_current')
            strengths = []
            risks = []

            # ── 信用倍率（需給指標、スコアには加算しない） ──────
            if margin_current is not None:
                if margin_current >= 3:
                    risks.append(f'信用倍率 {margin_current:.2f}倍: 買い残過多・将来の売り圧力に注意')
                elif margin_current <= 1.0:
                    strengths.append(f'信用倍率 {margin_current:.2f}倍: 売り残優勢・短期的な買い戻し余地あり')

            # ── ① 企業価値スコア (0〜11点) ──────────────────
            b_score = 0
            if roe is not None:
                if roe >= 15:
                    b_score += 3; strengths.append(f'高ROE {roe}%: 資本効率に優れる')
                elif roe >= 10:
                    b_score += 2; strengths.append(f'ROE {roe}%: 良好な収益性')
                elif roe < 5:
                    b_score -= 1; risks.append(f'ROE {roe}%: 収益性の改善が必要')

            if rev_cagr is not None:
                if rev_cagr >= 10:
                    b_score += 3; strengths.append(f'売上CAGR +{rev_cagr}%: 高成長')
                elif rev_cagr >= 5:
                    b_score += 2; strengths.append(f'売上CAGR +{rev_cagr}%: 安定成長')
                elif rev_cagr < 0:
                    b_score -= 1; risks.append(f'売上CAGR {rev_cagr}%: 売上減少傾向')

            if oi_cagr is not None and oi_cagr >= 10:
                b_score += 1; strengths.append(f'営業利益CAGR +{oi_cagr}%: 利益成長')

            if eq_ratio is not None:
                if eq_ratio >= 60:
                    b_score += 2; strengths.append(f'自己資本比率 {eq_ratio}%: 財務健全')
                elif eq_ratio >= 40:
                    b_score += 1
                elif eq_ratio < 20:
                    b_score -= 1; risks.append(f'自己資本比率 {eq_ratio}%: 財務に注意')

            business_grade = to_grade(b_score, (8, 6, 3, 1))

            # ── ② 株価評価スコア ────────────────────────────
            v_score = 0
            if per is not None:
                if per <= 12:
                    v_score += 3; strengths.append(f'PER {per}倍: 割安圏')
                elif per <= 25:
                    v_score += 1; strengths.append(f'PER {per}倍: 適正水準')
                elif per <= 40:
                    v_score -= 1; risks.append(f'PER {per}倍: やや割高')
                elif per <= 60:
                    v_score -= 3; risks.append(f'PER {per}倍: 割高圏。下方リスク大')
                else:
                    v_score -= 5; risks.append(f'PER {per}倍: 極めて割高。期待値の剥落リスク大')

            if pbr is not None:
                if pbr <= 1:
                    v_score += 1; strengths.append(f'PBR {pbr}倍: 解散価値以下')
                elif pbr > 5:
                    v_score -= 1; risks.append(f'PBR {pbr}倍: 高プレミアム')

            if div_yield is not None and div_yield >= 3:
                v_score += 1; strengths.append(f'配当利回り {div_yield}%: 株主還元充実')

            valuation_grade = to_grade(v_score, (3, 1, -1, -3))

            # ── ③ 総合スコア ─────────────────────────────────
            total_score = b_score + v_score
            grade = to_grade(total_score, (9, 7, 4, 2))
            # PER制約を上書き適用
            if per is not None:
                if per > 60 and grade in ('S', 'A'):
                    grade = 'B'
                elif per > 50 and grade == 'S':
                    grade = 'A'

            rec_map = {'S': '積極買い', 'A': '打診買い', 'B': '様子見', 'C': '様子見', 'D': '回避'}

            if total_score > best_score:
                best_score = total_score
                best_code = code

            stocks_analysis.append({
                'code': code,
                'business_grade': business_grade,
                'valuation_grade': valuation_grade,
                'grade': grade,
                'investment_appeal': (
                    f'企業価値{business_grade}・株価評価{valuation_grade}・総合{grade}。'
                    f'財務スコア{b_score}点、バリュエーションスコア{v_score}点。'
                ),
                'strengths': strengths[:3] if strengths else ['データ分析中'],
                'risks': risks[:2] if risks else ['特段のリスクなし'],
                'recommendation': rec_map.get(grade, '様子見'),
            })

        return {
            'overall_summary': (
                f'{len(stocks_data)}銘柄を企業価値・株価評価・総合の3軸で自動評価しました。'
                'より詳細な見解はAI分析ボタンをご活用ください。'
            ),
            'top_pick': best_code,
            'top_pick_reason': '総合財務スコアが最も高い銘柄です（自動計算）',
            'stocks': stocks_analysis,
            'comparison_insight': '3軸スコアリングによる相対評価です。業種特性を考慮した判断をお勧めします。',
            'portfolio_note': 'リスク分散のため、異なる業種への投資も検討してください。',
            'api_success': False,
            'fallback_used': True,
        }

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------
    @staticmethod
    def _latest(arr: List) -> Optional[float]:
        if not arr:
            return None
        for v in reversed(arr):
            if v is not None:
                return v
        return None

    @staticmethod
    def _calc_cagr(arr: List) -> Optional[float]:
        valid = [v for v in (arr or []) if v is not None]
        if len(valid) < 2 or valid[0] <= 0:
            return None
        return round((pow(valid[-1] / valid[0], 1 / (len(valid) - 1)) - 1) * 100, 1)

    @staticmethod
    def _error_result(msg: str) -> Dict:
        return {
            'overall_summary': msg,
            'top_pick': '',
            'top_pick_reason': '',
            'stocks': [],
            'comparison_insight': '',
            'portfolio_note': '',
            'api_success': False,
            'fallback_used': True,
            'error_message': msg,
        }
