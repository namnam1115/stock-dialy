"""
損益集計ロジックを StockDiary モデルから分離したサービス。
"""
import logging
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)


class AggregateService:
    """StockDiary の集計フィールド再計算を担当するサービス。

    すべてのメソッドは diary インスタンスのフィールドを更新するが、
    DB への save() は行わない（呼び出し元が制御する）。
    """

    @staticmethod
    def recalculate(diary):
        """全取引と現物取引の集計を再計算して diary フィールドに書き込み、save() する。"""
        AggregateService._recalculate_all(diary)
        AggregateService._recalculate_cash_only(diary)
        diary.save()

    @staticmethod
    @contextmanager
    def deferred(diary):
        """一括操作のための「忘れても壊れない」再集計コンテキスト。

        ``Transaction.save()`` / ``delete()`` は単体操作なら自動で再集計するが、
        ``bulk_create`` / ``bulk_update`` / ``QuerySet.update`` / ``QuerySet.delete``
        は save() を経由しないため自動再集計が走らない。従来はその都度
        ``recalculate(diary)`` を手動で呼ぶ規約だったが、呼び忘れると集計値が
        静かにずれる（＝メモリ任せの不変条件）。

        このブロックを使うと、ブロックを抜けるときに **必ず1回だけ** 再集計が走る。
        explicit な recalculate 呼び出しは不要になる::

            with AggregateService.deferred(diary):
                Transaction.objects.bulk_create([...])
            # ここで diary の集計は確定済み

        ブロック内で個別 ``save()`` を多数呼ぶ場合も、それらの自動再集計を抑制し
        （``diary._defer_recalc`` フラグ）、出口で一度だけ走らせて O(N)→O(1) にする。
        """
        diary._defer_recalc = True
        exc_occurred = False
        try:
            yield diary
        except Exception:
            exc_occurred = True
            raise
        finally:
            diary._defer_recalc = False
            if not exc_occurred:
                AggregateService.recalculate(diary)

    @staticmethod
    def _recalculate_all(diary):
        """全取引（信用含む）の集計を diary フィールドに書き込む。save() は呼ばない。"""
        # 同日内は 'buy' < 'sell'（辞書順）を使い、買いを先に処理する。
        # 証券会社CSVの受渡日は同日内の実際の約定順を保証しないため（例: 信用の
        # 返済取引が新規建て取引より先に並ぶ実例が確認済み）、created_at
        # （＝取込のファイル行順）だけに頼ると FIFO/移動平均の状態機械が
        # 建玉の新規/決済を取り違える。現物取引や同一方向の日計り取引では
        # 買い→売りの順が常に正しいため、このタイブレークで解消する
        # （信用の異方向・同日複数ラウンドトリップは対象外。要 建玉単位の追跡）。
        transactions = diary.transactions.all().order_by(
            'transaction_date', 'transaction_type', 'created_at'
        )

        diary.current_quantity = Decimal('0')
        diary.total_cost = Decimal('0')
        diary.realized_profit = Decimal('0')
        diary.total_bought_quantity = Decimal('0')
        diary.total_sold_quantity = Decimal('0')
        diary.total_buy_amount = Decimal('0')
        diary.total_sell_amount = Decimal('0')
        diary.transaction_count = 0
        diary.first_purchase_date = None
        diary.last_transaction_date = None
        diary.average_purchase_price = None

        logger.debug("集計開始: %s (%s)", diary.stock_name, diary.stock_symbol)

        for idx, transaction in enumerate(transactions, 1):
            adjusted_quantity = transaction.quantity
            adjusted_price = transaction.price
            before_qty = diary.current_quantity

            if transaction.transaction_type == 'buy':
                buy_amount = adjusted_price * adjusted_quantity

                if diary.current_quantity < 0:
                    # 信用売りの返済買い
                    returned_quantity = min(adjusted_quantity, abs(diary.current_quantity))

                    if diary.total_cost < 0:
                        avg_sell_price = abs(diary.total_cost) / abs(diary.current_quantity)
                        returned_cost = avg_sell_price * returned_quantity
                        buy_cost = adjusted_price * returned_quantity
                        profit = returned_cost - buy_cost
                        diary.realized_profit += profit

                        logger.debug(
                            "%d. %s 返済買い %s株 @ %s円 (平均売却単価: %.2f円) 損益: %+,.2f円",
                            idx, transaction.transaction_date, returned_quantity,
                            adjusted_price, avg_sell_price, profit,
                        )

                    diary.current_quantity += returned_quantity
                    diary.total_cost += avg_sell_price * returned_quantity if diary.total_cost < 0 else 0

                    remaining_quantity = adjusted_quantity - returned_quantity
                    if remaining_quantity > 0:
                        remaining_amount = adjusted_price * remaining_quantity
                        diary.total_cost += remaining_amount
                        diary.current_quantity += remaining_quantity
                else:
                    diary.total_cost += buy_amount
                    diary.current_quantity += adjusted_quantity

                diary.total_bought_quantity += adjusted_quantity
                diary.total_buy_amount += buy_amount

                logger.debug(
                    "%d. %s 購入 %s株 @ %s円 → 保有: %s → %s",
                    idx, transaction.transaction_date, adjusted_quantity,
                    adjusted_price, before_qty, diary.current_quantity,
                )

                if diary.first_purchase_date is None:
                    diary.first_purchase_date = transaction.transaction_date

            elif transaction.transaction_type == 'sell':
                sell_amount = adjusted_price * adjusted_quantity

                if diary.current_quantity > 0:
                    avg_price = diary.total_cost / diary.current_quantity
                    sold_quantity = min(adjusted_quantity, diary.current_quantity)
                    sell_cost = avg_price * sold_quantity
                    actual_sell_amount = adjusted_price * sold_quantity
                    profit = actual_sell_amount - sell_cost
                    diary.realized_profit += profit

                    diary.total_cost -= sell_cost
                    diary.current_quantity -= sold_quantity

                    logger.debug(
                        "%d. %s 売却 %s株 @ %s円 (平均単価: %.2f円) "
                        "→ 保有: %s → %s 損益: %+,.2f円",
                        idx, transaction.transaction_date, sold_quantity,
                        adjusted_price, avg_price, before_qty, diary.current_quantity, profit,
                    )

                    remaining_quantity = adjusted_quantity - sold_quantity
                    if remaining_quantity > 0:
                        diary.current_quantity -= remaining_quantity
                        diary.total_cost -= adjusted_price * remaining_quantity

                        logger.debug(
                            "    ↳ 信用売り %s株 → 保有: %s",
                            remaining_quantity, diary.current_quantity,
                        )
                else:
                    diary.current_quantity -= adjusted_quantity
                    diary.total_cost -= sell_amount

                    logger.debug(
                        "%d. %s 信用売り %s株 @ %s円 → 保有: %s → %s",
                        idx, transaction.transaction_date, adjusted_quantity,
                        adjusted_price, before_qty, diary.current_quantity,
                    )

                diary.total_sold_quantity += adjusted_quantity
                diary.total_sell_amount += sell_amount

            diary.transaction_count += 1
            diary.last_transaction_date = transaction.transaction_date

        # 平均取得単価
        if diary.current_quantity > 0 and diary.total_cost > 0:
            diary.average_purchase_price = (
                diary.total_cost / diary.current_quantity
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif diary.current_quantity < 0 and diary.total_cost < 0:
            diary.average_purchase_price = (
                abs(diary.total_cost) / abs(diary.current_quantity)
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        diary.current_quantity = diary.current_quantity.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.total_cost = diary.total_cost.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.realized_profit = diary.realized_profit.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        logger.debug(
            "集計完了: 保有数=%s, 購入計=%s, 売却計=%s, 実現損益=%s",
            diary.current_quantity, diary.total_bought_quantity,
            diary.total_sold_quantity, diary.realized_profit,
        )

    @staticmethod
    def _recalculate_cash_only(diary):
        """現物取引（is_margin=False）のみの集計を diary フィールドに書き込む。save() は呼ばない。"""
        # 同日内は 'buy' < 'sell'（辞書順）で買いを先に処理する（詳細は _recalculate_all 参照）。
        # 現物は空売りができないため、このタイブレークだけで同日ラウンドトリップの
        # 取り違え（保有数超の売却扱い）を確実に解消できる。
        cash_transactions = diary.transactions.filter(
            is_margin=False
        ).order_by('transaction_date', 'transaction_type', 'created_at')

        cash_quantity = Decimal('0')
        cash_cost = Decimal('0')
        cash_realized_profit = Decimal('0')
        cash_bought_quantity = Decimal('0')
        cash_sold_quantity = Decimal('0')
        cash_buy_amount = Decimal('0')
        cash_sell_amount = Decimal('0')

        for transaction in cash_transactions:
            adjusted_quantity = transaction.quantity
            adjusted_price = transaction.price

            if transaction.transaction_type == 'buy':
                buy_amount = adjusted_price * adjusted_quantity
                cash_cost += buy_amount
                cash_quantity += adjusted_quantity
                cash_bought_quantity += adjusted_quantity
                cash_buy_amount += buy_amount
            elif transaction.transaction_type == 'sell':
                if cash_quantity > 0:
                    avg_price = cash_cost / cash_quantity
                    sell_quantity = min(adjusted_quantity, cash_quantity)
                    sell_cost = avg_price * sell_quantity
                    actual_sell_amount = adjusted_price * sell_quantity
                    profit = actual_sell_amount - sell_cost
                    cash_realized_profit += profit
                    cash_cost -= sell_cost
                    cash_quantity -= sell_quantity
                cash_sold_quantity += adjusted_quantity
                cash_sell_amount += adjusted_price * adjusted_quantity

        cash_avg_price = None
        if cash_quantity > 0:
            cash_avg_price = (cash_cost / cash_quantity).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

        diary.cash_only_current_quantity = cash_quantity.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.cash_only_average_purchase_price = cash_avg_price
        diary.cash_only_total_cost = cash_cost.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.cash_only_realized_profit = cash_realized_profit.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.cash_only_total_bought_quantity = cash_bought_quantity
        diary.cash_only_total_sold_quantity = cash_sold_quantity
        diary.cash_only_total_buy_amount = cash_buy_amount
        diary.cash_only_total_sell_amount = cash_sell_amount
