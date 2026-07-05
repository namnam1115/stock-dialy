"""トレーディング・ダッシュボードとパフォーマンス可視化のビュー。

views.py から責務分割（原則: 小さく分離）。TradingDashboardView（各種集計）/
DiaryGraphView（関連グラフ）と、タグ別成績の集計ヘルパー build_tag_performance。
TAG_AXIS_COLORS（タグ軸の表示色）は本ブロック専用のためここに置く。
urls.py は `from . import views_dashboard` で参照する。
"""
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from company_master.models import CompanyMaster
from tags.models import Tag
from .models import StockDiary, Transaction

logger = logging.getLogger(__name__)

# タグの分類軸（Tag.AXIS_CHOICES）ごとの表示色。タグ別成績の軸バッジで使用。
TAG_AXIS_COLORS = {
    'theme': '#7c3aed',
    'business_model': '#0891b2',
    'risk': '#dc2626',
    'capital_policy': '#16a34a',
    'macro': '#d97706',
    'event': '#db2777',
    'custom': '#64748b',
}



def build_tag_performance(diaries, limit=15):
    """タグ別の通算成績（思考の分類 × 結果）を集計して返す。

    「@地政学リスクで買った銘柄は勝てているか」を可視化する自己分析。
    realized_profit はライフタイム値（通算）である点に注意（呼び出し側でラベリング）。
    TradingDashboardView と AnnualReviewView で共有する。
    """
    tag_stats = {}
    for diary in diaries:
        cash_stats = diary.calculate_cash_only_stats()
        total_invested = cash_stats['total_buy_amount']
        total_sell = cash_stats['total_sell_amount']
        current_value = Decimal('0')
        if cash_stats['current_quantity'] > 0 and cash_stats['average_purchase_price']:
            current_value = cash_stats['current_quantity'] * cash_stats['average_purchase_price']
        realized = float(cash_stats['realized_profit'] or 0)
        is_sold = bool(total_sell and total_sell > 0)

        for tag in diary.tags.all():
            st = tag_stats.setdefault(tag.id, {
                'tag_id': tag.id,
                'name': tag.name,
                'axis': tag.axis,
                'axis_label': tag.get_axis_display(),
                'diary_count': 0,
                'realized_profit': 0.0,
                'total_invested': Decimal('0'),
                'total_sell_amount': Decimal('0'),
                'current_value': Decimal('0'),
                'sold_count': 0,
                'win_count': 0,
            })
            st['diary_count'] += 1
            st['realized_profit'] += realized
            st['total_invested'] += total_invested
            st['total_sell_amount'] += total_sell
            st['current_value'] += current_value
            if is_sold:
                st['sold_count'] += 1
                if realized > 0:
                    st['win_count'] += 1

    tag_analysis = []
    for st in tag_stats.values():
        roi = Decimal('0')
        if st['total_invested'] > 0:
            roi = ((st['total_sell_amount'] + st['current_value'] - st['total_invested'])
                   / st['total_invested'] * 100)
        tag_win_rate = (round(st['win_count'] / st['sold_count'] * 100, 1)
                        if st['sold_count'] > 0 else None)
        tag_analysis.append({
            'tag_id': st['tag_id'],
            'name': st['name'],
            'axis': st['axis'],
            'axis_label': st['axis_label'],
            'axis_color': TAG_AXIS_COLORS.get(st['axis'], '#64748b'),
            'diary_count': st['diary_count'],
            'realized_profit': round(st['realized_profit'], 0),
            'total_invested': float(st['total_invested']),
            'roi': float(round(roi, 1)),
            'win_rate': tag_win_rate,
            'sold_count': st['sold_count'],
            'win_count': st['win_count'],
        })
    tag_analysis.sort(key=lambda x: (x['diary_count'], x['realized_profit']), reverse=True)
    return tag_analysis[:limit]


class TradingDashboardView(LoginRequiredMixin, TemplateView):
    """取引分析ダッシュボード（現物取引のみ・ROI改善版）"""
    template_name = 'stockdiary/trading_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # ========== 期間フィルター ==========
        period = self.request.GET.get('period', '6m')
        today = timezone.now().date()

        period_mapping = {
            '1m': 30,
            '3m': 90,
            '6m': 180,
            '1y': 365,
            'all': None
        }

        days = period_mapping.get(period)

        # ✅ 現物取引のみを取得
        # 為替変換は行わないため、円建て（JPY）の日記のみを集計対象とする。
        # （USD など他通貨の日記は通貨混在を避けるため本ダッシュボードの合計には含めない）
        if days:
            start_date = today - timedelta(days=days)
            period_transactions = Transaction.objects.filter(
                diary__user=user,
                diary__currency='JPY',
                transaction_date__gte=start_date,
                is_margin=False  # ✅ 信用取引を除外
            )
        else:
            period_transactions = Transaction.objects.filter(
                diary__user=user,
                diary__currency='JPY',
                is_margin=False  # ✅ 信用取引を除外
            )

        # 期間内に取引があった日記を取得
        diary_ids_in_period = period_transactions.values_list('diary_id', flat=True).distinct()
        diaries_in_period = StockDiary.objects.filter(
            id__in=diary_ids_in_period
        ).select_related('user').prefetch_related('tags')

        # 全日記（円建てのみ）
        all_diaries = StockDiary.objects.filter(user=user, currency='JPY')

        # ========== CompanyMasterから業種情報を取得 ==========
        from company_master.models import CompanyMaster

        company_codes = list(
            diaries_in_period.values_list('stock_symbol', flat=True).distinct()
        )

        company_industries = {}
        if company_codes:
            companies = CompanyMaster.objects.filter(
                code__in=company_codes
            ).values('code', 'industry_name_33')

            for company in companies:
                code = company['code'].split('.')[0]
                industry = company['industry_name_33']
                if industry:
                    industry = industry.strip()
                industry = industry if industry else '未分類'
                company_industries[code] = industry

        # ========== メトリクス集計（現物のみ） ==========
        total_transactions = 0
        total_cash_invested = Decimal('0')  # 総投資額（現物のみ）
        total_cash_sell_amount = Decimal('0')  # 総売却額（現物のみ）
        total_current_value = Decimal('0')  # 現在の評価額
        
        for diary in diaries_in_period:
            # ✅ 現物取引のみの統計を取得
            cash_stats = diary.calculate_cash_only_stats()
            
            # 現物取引の数をカウント
            cash_transaction_count = period_transactions.filter(diary=diary).count()
            total_transactions += cash_transaction_count
            
            # 総投資額（購入総額）
            total_cash_invested += cash_stats['total_buy_amount']
            
            # 総売却額
            total_cash_sell_amount += cash_stats['total_sell_amount']
            
            # 現在の評価額（保有数 × 平均取得単価）
            if cash_stats['current_quantity'] > 0 and cash_stats['average_purchase_price']:
                current_value = cash_stats['current_quantity'] * cash_stats['average_purchase_price']
                total_current_value += current_value

        # ✅ ROI = (売却総額 + 評価額 - 総投資額) / 総投資額 × 100
        if total_cash_invested > 0:
            total_roi = ((total_cash_sell_amount + total_current_value - total_cash_invested) 
                        / total_cash_invested * 100)
        else:
            total_roi = Decimal('0')

        # 実現損益
        total_realized_profit = total_cash_sell_amount - (total_cash_invested - total_current_value)

        # 保有中銘柄数（現物のみ）
        holding_count = 0
        for diary in all_diaries:
            cash_stats = diary.calculate_cash_only_stats()
            if cash_stats['current_quantity'] > 0:
                holding_count += 1

        # 平均利益率（売却ベース）& 勝率・プロフィットファクター
        profitable_rates = []
        sold_profits = []  # 売却した全銘柄の実現損益（勝率計算用）
        for diary in diaries_in_period:
            cash_stats = diary.calculate_cash_only_stats()
            if cash_stats['total_sell_amount'] and cash_stats['total_sell_amount'] > 0:
                buy_cost = cash_stats['total_buy_amount'] - cash_stats['total_cost']
                if buy_cost > 0:
                    profit_rate = ((cash_stats['total_sell_amount'] - buy_cost)
                                  / buy_cost) * 100
                    profitable_rates.append(profit_rate)
                sold_profits.append(float(cash_stats['realized_profit'] or 0))

        avg_profit_rate = (sum(profitable_rates) / len(profitable_rates)
                          if profitable_rates else 0)

        # 勝率
        sold_count = len(sold_profits)
        winning_count = sum(1 for p in sold_profits if p > 0)
        win_rate = round(winning_count / sold_count * 100, 1) if sold_count > 0 else None

        # プロフィットファクター = 総利益 / 総損失
        pf_gain = sum(p for p in sold_profits if p > 0)
        pf_loss = abs(sum(p for p in sold_profits if p < 0))
        profit_factor = round(pf_gain / pf_loss, 2) if pf_loss > 0 else None

        # ========== 取引回数ランキング（銘柄別・現物のみ） ==========
        stock_ranking = {}
        for diary in diaries_in_period:
            stock_code = diary.stock_symbol
            if stock_code not in stock_ranking:
                stock_ranking[stock_code] = {
                    'stock_code': stock_code,
                    'stock_name': diary.stock_name,
                    'transaction_count': 0,
                    'diaries': []
                }
            
            # ✅ 現物取引のみカウント
            cash_transaction_count = period_transactions.filter(diary=diary).count()
            stock_ranking[stock_code]['transaction_count'] += cash_transaction_count
            
            # ✅ 日記ごとの詳細情報（現物のみ）
            cash_stats = diary.calculate_cash_only_stats()
            
            last_transaction = period_transactions.filter(diary=diary).order_by('-transaction_date').first()
            last_trade = last_transaction.transaction_date if last_transaction else None
            
            if last_trade:
                delta = today - last_trade
                if delta.days == 0:
                    last_trade_display = '今日'
                elif delta.days == 1:
                    last_trade_display = '1日前'
                elif delta.days < 7:
                    last_trade_display = f'{delta.days}日前'
                elif delta.days < 30:
                    weeks = delta.days // 7
                    last_trade_display = f'{weeks}週間前'
                else:
                    months = delta.days // 30
                    last_trade_display = f'{months}ヶ月前'
            else:
                last_trade_display = '不明'
            
            # ✅ ROI計算：(売却総額 + 評価額 - 総投資額) / 総投資額 × 100
            total_invested = cash_stats['total_buy_amount']
            total_sell = cash_stats['total_sell_amount']
            current_value = Decimal('0')
            if cash_stats['current_quantity'] > 0 and cash_stats['average_purchase_price']:
                current_value = cash_stats['current_quantity'] * cash_stats['average_purchase_price']
            
            roi = Decimal('0')
            if total_invested > 0:
                roi = ((total_sell + current_value - total_invested) / total_invested * 100)
            
            stock_ranking[stock_code]['diaries'].append({
                'id': diary.id,
                'transaction_count': cash_transaction_count,
                'realized_profit': float(cash_stats['realized_profit'] or 0),
                'current_quantity': float(cash_stats['current_quantity'] or 0),
                'total_invested': float(total_invested),
                'total_sell_amount': float(total_sell),
                'current_value': float(current_value),
                'roi': float(roi),
                'last_trade_display': last_trade_display,
                'created_at': diary.created_at.strftime('%Y年%m月%d日'),
            })

        # ソート（stock_ranking は ROIランキングチャート・銘柄モーダルのデータ源として保持）

        # ========== 業種別分析（現物のみ） ==========
        sector_stats = {}
        sector_companies = {}

        for diary in diaries_in_period:
            stock_code = diary.stock_symbol.split('.')[0] if diary.stock_symbol else None
            sector = company_industries.get(stock_code, '未分類')
            sector = sector.strip() if sector else '未分類'

            if sector not in sector_stats:
                sector_stats[sector] = {
                    'sector': sector,
                    'transaction_count': 0,
                    'total_invested': Decimal('0'),
                    'total_sell_amount': Decimal('0'),
                    'total_current_value': Decimal('0'),
                    'diary_ids': set(),
                }
                sector_companies[sector] = []

            # ✅ 現物取引のみカウント
            cash_transaction_count = period_transactions.filter(diary=diary).count()
            sector_stats[sector]['transaction_count'] += cash_transaction_count
            sector_stats[sector]['diary_ids'].add(diary.id)
            
            # ✅ 現物取引の統計を集計
            cash_stats = diary.calculate_cash_only_stats()
            
            total_invested = cash_stats['total_buy_amount']
            total_sell = cash_stats['total_sell_amount']
            current_value = Decimal('0')
            if cash_stats['current_quantity'] > 0 and cash_stats['average_purchase_price']:
                current_value = cash_stats['current_quantity'] * cash_stats['average_purchase_price']
            
            sector_stats[sector]['total_invested'] += total_invested
            sector_stats[sector]['total_sell_amount'] += total_sell
            sector_stats[sector]['total_current_value'] += current_value

            # ✅ ROI計算
            roi = Decimal('0')
            if total_invested > 0:
                roi = ((total_sell + current_value - total_invested) / total_invested * 100)
            
            # ✅ 日記別に企業情報を保存
            sector_companies[sector].append({
                'id': diary.id,
                'name': diary.stock_name,
                'code': diary.stock_symbol,
                'transaction_count': cash_transaction_count,
                'realized_profit': float(cash_stats['realized_profit'] or 0),
                'total_invested': float(total_invested),
                'total_sell_amount': float(total_sell),
                'current_value': float(current_value),
                'current_quantity': float(cash_stats['current_quantity'] or 0),
                'roi': float(roi),
                'created_at': diary.created_at.strftime('%Y年%m月%d日'),
            })

        # ROIを計算
        sector_analysis = []
        for sector, data in sector_stats.items():
            diary_count = len(data['diary_ids'])
            total_invested = data['total_invested']
            total_sell = data['total_sell_amount']
            total_current_value = data['total_current_value']

            # ✅ ROI = (売却総額 + 評価額 - 総投資額) / 総投資額 × 100
            roi = Decimal('0')
            if total_invested > 0:
                roi = ((total_sell + total_current_value - total_invested) / total_invested * 100)

            # 実現損益: 各銘柄のFIFO計算済み実現損益を合計する
            realized_profit = sum(c['realized_profit'] for c in sector_companies.get(sector, []))

            sector_analysis.append({
                'sector': sector.strip(),
                'transaction_count': data['transaction_count'],
                'realized_profit': float(realized_profit),
                'total_invested': float(total_invested),
                'total_sell_amount': float(total_sell),
                'current_value': float(total_current_value),
                'roi': float(round(roi, 1)),
                'diary_count': diary_count,
            })

        # ソート & 幅パーセント計算
        sector_analysis.sort(key=lambda x: x['transaction_count'], reverse=True)
        sector_analysis = sector_analysis[:10]

        max_transaction_count = sector_analysis[0]['transaction_count'] if sector_analysis else 1
        if max_transaction_count == 0:
            max_transaction_count = 1
        total_all_transactions = sum(s['transaction_count'] for s in sector_analysis)
        
        for sector in sector_analysis:
            sector['width_percent'] = (sector['transaction_count'] / max_transaction_count) * 100
            sector['transaction_ratio'] = round((sector['transaction_count'] / total_all_transactions) * 100, 1) if total_all_transactions > 0 else 0

        # ========== 業種別企業明細データ（日記別） ==========
        sector_details = {}
        for sector, companies in sector_companies.items():
            company_list = []
            for c in companies:
                company_list.append({
                    'id': c['id'],
                    'name': c['name'],
                    'code': c['code'],
                    'transaction_count': c['transaction_count'],
                    'realized_profit': round(c['realized_profit'], 0),
                    'total_invested': round(c['total_invested'], 0),
                    'total_sell_amount': round(c['total_sell_amount'], 0),
                    'current_value': round(c['current_value'], 0),
                    'roi': c['roi'],
                    'current_quantity': c['current_quantity'],
                    'created_at': c['created_at'],
                })

            company_list.sort(key=lambda x: x['roi'], reverse=True)
            sector_details[sector.strip()] = company_list

        # ========== 利益/損失業種（ROIベース） ==========
        seen_sectors = set()
        unique_sector_analysis = []
        for s in sector_analysis:
            sector_key = s['sector'].strip()
            if sector_key not in seen_sectors:
                seen_sectors.add(sector_key)
                unique_sector_analysis.append(s)

        profitable_sectors = [s for s in unique_sector_analysis if s['roi'] > 0]
        profitable_sectors.sort(key=lambda x: x['roi'], reverse=True)
        profitable_sectors = profitable_sectors[:3]

        loss_sectors = [s for s in unique_sector_analysis if s['roi'] < 0]
        loss_sectors.sort(key=lambda x: x['roi'])
        loss_sectors = loss_sectors[:3]

        # ========== ROIランキングデータ（業種別） ==========
        sector_roi_list = []
        for sector in unique_sector_analysis:
            sector_roi_list.append({
                'label': sector['sector'],
                'roi': sector['roi'],
                'transaction_count': sector['transaction_count'],
                'diary_count': sector['diary_count']
            })
        
        sector_roi_list.sort(key=lambda x: x['roi'], reverse=True)
        
        # ========== ROIランキングデータ（銘柄別） ==========
        stock_roi_list = []
        for stock_code, stock_data in stock_ranking.items():
            # 日記全体のROIを計算
            total_invested = sum(d['total_invested'] for d in stock_data['diaries'])
            total_sell = sum(d['total_sell_amount'] for d in stock_data['diaries'])
            total_current_value = sum(d['current_value'] for d in stock_data['diaries'])
            
            roi = 0
            if total_invested > 0:
                roi = ((total_sell + total_current_value - total_invested) / total_invested * 100)
            
            stock_roi_list.append({
                'label': f"{stock_data['stock_name']} ({stock_code})",
                'roi': round(roi, 1),
                'transaction_count': stock_data['transaction_count'],
                'diary_count': len(stock_data['diaries'])
            })
        
        stock_roi_list.sort(key=lambda x: x['roi'], reverse=True)

        # ========== タグ別成績（思考の分類 × 結果） ==========
        # 「@地政学リスクで買った銘柄は勝てているか」を可視化する自己分析。
        # タグ＝自分の投資判断の分類なので、業種別と違いこのアプリでしか集計できない
        tag_stats = {}
        for diary in diaries_in_period:
            cash_stats = diary.calculate_cash_only_stats()
            total_invested = cash_stats['total_buy_amount']
            total_sell = cash_stats['total_sell_amount']
            current_value = Decimal('0')
            if cash_stats['current_quantity'] > 0 and cash_stats['average_purchase_price']:
                current_value = cash_stats['current_quantity'] * cash_stats['average_purchase_price']
            realized = float(cash_stats['realized_profit'] or 0)
            is_sold = bool(total_sell and total_sell > 0)

            for tag in diary.tags.all():
                st = tag_stats.setdefault(tag.id, {
                    'tag_id': tag.id,
                    'name': tag.name,
                    'axis': tag.axis,
                    'axis_label': tag.get_axis_display(),
                    'diary_count': 0,
                    'realized_profit': 0.0,
                    'total_invested': Decimal('0'),
                    'total_sell_amount': Decimal('0'),
                    'current_value': Decimal('0'),
                    'sold_count': 0,
                    'win_count': 0,
                })
                st['diary_count'] += 1
                st['realized_profit'] += realized
                st['total_invested'] += total_invested
                st['total_sell_amount'] += total_sell
                st['current_value'] += current_value
                if is_sold:
                    st['sold_count'] += 1
                    if realized > 0:
                        st['win_count'] += 1

        tag_analysis = []
        for st in tag_stats.values():
            roi = Decimal('0')
            if st['total_invested'] > 0:
                roi = ((st['total_sell_amount'] + st['current_value'] - st['total_invested'])
                       / st['total_invested'] * 100)
            tag_win_rate = (round(st['win_count'] / st['sold_count'] * 100, 1)
                            if st['sold_count'] > 0 else None)
            tag_analysis.append({
                'tag_id': st['tag_id'],
                'name': st['name'],
                'axis': st['axis'],
                'axis_label': st['axis_label'],
                'axis_color': TAG_AXIS_COLORS.get(st['axis'], '#64748b'),
                'diary_count': st['diary_count'],
                'realized_profit': round(st['realized_profit'], 0),
                'total_invested': float(st['total_invested']),
                'roi': float(round(roi, 1)),
                'win_rate': tag_win_rate,
                'sold_count': st['sold_count'],
                'win_count': st['win_count'],
            })
        # よく使う思考の分類から表示（同数なら実現損益の大きい順）
        tag_analysis.sort(key=lambda x: (x['diary_count'], x['realized_profit']), reverse=True)
        tag_analysis = tag_analysis[:15]

        # ========== コンテキスト ==========
        context.update({
            'total_transactions': total_transactions,
            'holding_count': holding_count,
            'total_realized_profit': float(total_realized_profit),
            'total_invested': float(total_cash_invested),
            'total_roi': round(float(total_roi), 1),
            'avg_profit_rate': round(avg_profit_rate, 1),
            'win_rate': win_rate,
            'winning_count': winning_count,
            'sold_count': sold_count,
            'profit_factor': profit_factor,
            'tag_analysis': tag_analysis,
            'sector_analysis': sector_analysis,
            'profitable_sectors': profitable_sectors,
            'loss_sectors': loss_sectors,
            'current_period': period,
            'has_data': total_transactions > 0,
            'sector_details': json.dumps(sector_details, ensure_ascii=False),
            'stock_ranking': json.dumps({s['stock_code']: s for s in stock_ranking.values()}, ensure_ascii=False),
            'sector_roi_data': json.dumps(sector_roi_list, ensure_ascii=False),
            'stock_roi_data': json.dumps(stock_roi_list, ensure_ascii=False),
        })

        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]

        return context


class DiaryGraphView(LoginRequiredMixin, TemplateView):
    """日記関連グラフ表示ページ"""
    template_name = 'stockdiary/diary_graph.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['diary_count'] = StockDiary.objects.filter(user=user).count()
        # ユーザーの日記に実際に使われているタグのみ取得
        context['tags'] = (
            Tag.objects.filter(user=user, stockdiary__user=user)
            .distinct()
            .order_by('name')
        )
        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る',
                'aria_label': '一覧に戻る'
            }
        ]
        return context


class ExploreGraphView(LoginRequiredMixin, TemplateView):
    """ドリルダウン探索グラフ（TG-DD）。

    空から要素（タグ）を1つ選び、近傍API（`api_graph_neighbors`）で1段ずつ
    深掘りする専用ページ。既存 diary_graph とは独立。
    設計: docs/graph_drilldown_redesign.md
    """
    template_name = 'stockdiary/explore_graph.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        # 入口の検索用に、タグ（要素）と銘柄の両方を埋め込む。
        # クライアント側で部分一致検索し、選んだ要素/銘柄を起点に展開する。
        tags = (
            Tag.objects.filter(user=user, stockdiary__user=user)
            .exclude(axis__in=('event', 'custom'))
            .distinct()
            .order_by('name')
            .values('id', 'name', 'axis')
        )
        items = [
            {'id': f"tag:{t['id']}", 'label': t['name'], 'type': 'tag', 'axis': t['axis']}
            for t in tags
        ]
        # 銘柄（symbol 単位で distinct）。銘柄コード・銘柄名どちらでも検索できるよう symbol も持たせる。
        seen = set()
        for symbol, name in (
            StockDiary.objects.filter(user=user)
            .exclude(stock_symbol__isnull=True).exclude(stock_symbol='')
            .order_by('stock_symbol')
            .values_list('stock_symbol', 'stock_name')
        ):
            if symbol in seen:
                continue
            seen.add(symbol)
            items.append({'id': f'stock:{symbol}', 'label': name or symbol,
                          'type': 'stock', 'symbol': symbol})
        context['explore_items'] = items
        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る',
                'aria_label': '一覧に戻る'
            }
        ]
        return context


