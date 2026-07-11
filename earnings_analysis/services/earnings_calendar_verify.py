# earnings_analysis/services/earnings_calendar_verify.py
"""記録銘柄の決算予想日を個社API（決算短信の実績）で答え合わせする。

なぜ必要か:
  日次同期の母体である /v1/calendar（横断フィード）には提供元側のインデックス
  漏れがあり、実際には開示済みの銘柄が丸ごと出てこないことがある（実例:
  8267 イオンの2026-07-10開示が /v1/calendar に一切載っていなかった）。自前算出
  の予想（四半期末+会社ごとの実績オフセット）で埋めても、実際の発表日とは
  数日ズレることがある。

  個社エンドポイント /companies/{edinet_code}/earnings は決算短信そのものを
  見るため正確だが、1銘柄=1リクエスト（無料枠100件/日）を消費する。カブログは
  複数ユーザーが使うため対象銘柄（記録銘柄）は「特定ユーザーの保有」ではなく
  「全ユーザーの日記に記録された銘柄コードの和集合」であり、決算集中期には
  対象が数百件規模になり得る。全対象を毎日答え合わせすると無料枠を簡単に
  超えるため、発表が近い順に固定予算（既定60件）まで処理して機械的に打ち切る。
  これにより対象件数がどれだけ増えても無料枠は絶対に超えない。

  一度 edinet_code が判明した銘柄は Company マスタにキャッシュし、以後は
  検索(1リクエスト)を省略して実績取得(1リクエスト)のみで答え合わせできる。
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)

# 日次バッチの calendar+history 取得（約18リクエスト）を差し引いた安全な既定予算。
# edinet_code 未キャッシュの銘柄は「検索+実績取得」で2リクエスト消費するため、
# 無料枠100件/日に対して十分な余裕を残す。
DEFAULT_VERIFY_BUDGET = 60

# 個社実績の発表日が、自前の予想日からこの日数以内なら「同じ四半期の答え合わせ」
# とみなして上書きする。離れすぎている場合は別の四半期の実績である可能性が高く、
# 誤って無関係の日付で上書きしないよう何もしない（既存の予想を残す）。
_MATCH_WINDOW_DAYS = 30


def _recorded_candidate_codes():
    """全ユーザーの日記の銘柄コード（4桁）の和集合と、EarningsSchedule照合用の
    候補コード（4桁+末尾0付き5桁）を返す。"""
    from stockdiary.models import StockDiary
    from stockdiary.services.earnings_lookup import candidate_codes

    symbols = set(
        StockDiary.objects
        .exclude(is_excluded=True)
        .filter(stock_symbol__regex=r'^\d{4}$')
        .values_list('stock_symbol', flat=True)
        .distinct()
    )
    return candidate_codes(symbols)


def verify_estimates_via_company_api(budget: int = DEFAULT_VERIFY_BUDGET,
                                     today=None) -> int:
    """予想日(is_estimated=True)のうち発表が近い順に、予算件数まで個社APIで
    答え合わせする。実績と食い違っていれば確定日で上書きする。

    Args:
        budget: 消費して良い最大リクエスト数（既定 DEFAULT_VERIFY_BUDGET）
        today: 基準日（既定=今日）。テスト用に上書き可能。

    Returns:
        int: 実績で更新（確定に昇格）した件数
    """
    from django.db import IntegrityError
    from earnings_analysis.models import EarningsSchedule, Company
    from earnings_analysis.services.earnings_calendar_api import (
        EarningsCalendarAPIService,
    )

    service = EarningsCalendarAPIService()
    if not service.is_configured or budget <= 0:
        return 0

    today = today or date.today()
    codes = _recorded_candidate_codes()
    if not codes:
        return 0

    targets = list(
        EarningsSchedule.objects
        .filter(securities_code__in=codes, is_estimated=True,
                earnings_date__gte=today)
        .order_by('earnings_date', 'securities_code')
    )

    remaining = budget
    updated = 0
    for schedule in targets:
        if remaining <= 0:
            break

        company = (
            Company.objects
            .filter(securities_code__in=_sec_code_variants(schedule.securities_code))
            .first()
        )
        if company:
            edinet_code = company.edinet_code
        else:
            edinet_code = service.resolve_edinet_code(schedule.securities_code)
            remaining -= 1
            if edinet_code:
                Company.objects.update_or_create(
                    edinet_code=edinet_code,
                    defaults={
                        'securities_code': schedule.securities_code,
                        'company_name': schedule.company_name or '',
                    },
                )

        if not edinet_code or remaining <= 0:
            continue

        latest = service.fetch_latest_disclosure(edinet_code)
        remaining -= 1
        if not latest:
            continue

        actual_date = latest['earnings_date']
        if abs((actual_date - schedule.earnings_date).days) > _MATCH_WINDOW_DAYS:
            # 別の四半期の実績とみなし、無関係の日付で上書きしない
            continue
        if actual_date == schedule.earnings_date and not schedule.is_estimated:
            continue

        schedule.earnings_date = actual_date
        schedule.is_estimated = False
        try:
            schedule.save(update_fields=['earnings_date', 'is_estimated', 'updated_at'])
        except IntegrityError:
            # 同一(コード,日付)の確定レコードが既に別途存在する → この予想は不要
            EarningsSchedule.objects.filter(pk=schedule.pk).delete()
        updated += 1

    logger.info('決算予想の個社API答え合わせ完了: 更新=%s件（予算消費=%s/%s）',
               updated, budget - remaining, budget)
    return updated


def _sec_code_variants(sec_code: str):
    """4桁/末尾0付き5桁の両方でCompanyマスタを引けるようにする。"""
    code = (sec_code or '').strip()
    if not code:
        return []
    if len(code) == 5 and code.endswith('0'):
        return [code, code[:4]]
    return [code, code + '0']
