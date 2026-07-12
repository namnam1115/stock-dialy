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
        """全取引（信用含む）の集計を diary フィールドに書き込む。save() は呼ばない。

        current_quantity/total_cost/realized_profit（信用ショート用フィールドを
        除く既存フィールド）は現物＋信用ロングを1本の状態機械で追跡する（従来通り）。

        信用ショート（新規売り→返済買い）は margin_short_* に完全に別建てで
        追跡する。理由: 符号付き単一カウンタでは「ロングを保有しながら同じ
        銘柄でショートも建てる」を表現できない。例えば current_quantity=+100
        （ロング保有中）の状態で新規に信用売りをすると、それが「新規のショート
        建て」なのか「ロングの決済売り」なのかは transaction_type（buy/sell）
        と is_margin だけでは判別できず、誤ってロングの決済として処理してしまう。

        新規/返済の判定は Transaction.margin_action（証券CSV取込が「取引区分」
        列から設定）を優先する。手入力等で margin_action が未設定（null）の
        場合のみ、状態依存のヒューリスティック（保有中なら決済・保有していな
        ければ新規）にフォールバックする。フォールバックは同一銘柄のロング・
        ショートを同時に持たない単純なケースでのみ正しく動作する
        （手入力でロング・ショートを同時に持つケースは margin_action が
        無いと原理的に判別不能なため対象外）。

        同日内の処理順序: margin_action が既知なら「新規→返済」、未知なら
        「買い→売り」をタイブレークに使う（証券会社CSVの受渡日は同日内の
        実際の約定順を保証しないため）。
        """
        def _rank(t):
            if t.is_margin and t.margin_action:
                return 0 if t.margin_action == 'open' else 1
            return 0 if t.transaction_type == 'buy' else 1

        transactions = list(diary.transactions.all().order_by('transaction_date', 'created_at'))
        transactions.sort(key=lambda t: (t.transaction_date, _rank(t)))

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

        margin_short_quantity = Decimal('0')
        margin_short_proceeds = Decimal('0')
        margin_short_realized_profit = Decimal('0')

        logger.debug("集計開始: %s (%s)", diary.stock_name, diary.stock_symbol)

        for idx, transaction in enumerate(transactions, 1):
            adjusted_quantity = transaction.quantity
            adjusted_price = transaction.price
            before_qty = diary.current_quantity

            is_short_open = (
                transaction.is_margin and transaction.transaction_type == 'sell' and (
                    transaction.margin_action == 'open'
                    or (transaction.margin_action is None and diary.current_quantity <= 0)
                )
            )
            is_short_close = (
                transaction.is_margin and transaction.transaction_type == 'buy' and (
                    transaction.margin_action == 'close'
                    or (transaction.margin_action is None and margin_short_quantity > 0)
                )
            )

            if is_short_close:
                # 信用売り建玉の返済買い（ロング用カウンタとは別建てで処理）
                close_quantity = min(adjusted_quantity, margin_short_quantity) if margin_short_quantity > 0 else Decimal('0')
                if close_quantity > 0:
                    avg_short_price = margin_short_proceeds / margin_short_quantity
                    returned_proceeds = avg_short_price * close_quantity
                    buy_cost = adjusted_price * close_quantity
                    profit = returned_proceeds - buy_cost
                    margin_short_realized_profit += profit
                    margin_short_proceeds -= returned_proceeds
                    margin_short_quantity -= close_quantity

                    logger.debug(
                        "%d. %s 信用返済買い %s株 @ %s円 (平均建単価: %.2f円) 損益: %+,.2f円",
                        idx, transaction.transaction_date, close_quantity,
                        adjusted_price, avg_short_price, profit,
                    )

                remaining_quantity = adjusted_quantity - close_quantity
                if remaining_quantity > 0:
                    # 決済数量が建玉を上回った分は新規の買い（現物/信用ロング）として計上する
                    diary.total_cost += adjusted_price * remaining_quantity
                    diary.current_quantity += remaining_quantity
                    if diary.first_purchase_date is None:
                        diary.first_purchase_date = transaction.transaction_date

                diary.total_bought_quantity += adjusted_quantity
                diary.total_buy_amount += adjusted_price * adjusted_quantity

            elif is_short_open:
                # 信用の新規売り建て（ロング用カウンタとは別建てで処理）
                margin_short_quantity += adjusted_quantity
                margin_short_proceeds += adjusted_price * adjusted_quantity

                logger.debug(
                    "%d. %s 信用新規売り %s株 @ %s円 → 建玉: %s",
                    idx, transaction.transaction_date, adjusted_quantity,
                    adjusted_price, margin_short_quantity,
                )

                diary.total_sold_quantity += adjusted_quantity
                diary.total_sell_amount += adjusted_price * adjusted_quantity

            elif transaction.transaction_type == 'buy':
                buy_amount = adjusted_price * adjusted_quantity

                if diary.current_quantity < 0:
                    # 現物のオーバーセル等、既存の負のポジションに対する買い戻し
                    # （信用ショートは上の is_short_close で別建て処理されるため、
                    # ここに来るのは is_margin=False の想定外ケースのみ）
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
                        # is_margin=False の想定外オーバーセルのみここに来る
                        # （信用ショートは is_short_open で別建て処理される）
                        diary.current_quantity -= remaining_quantity
                        diary.total_cost -= adjusted_price * remaining_quantity

                        logger.debug(
                            "    ↳ オーバーセル %s株 → 保有: %s",
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

        margin_short_quantity = margin_short_quantity.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        diary.margin_short_quantity = margin_short_quantity
        diary.margin_short_total_proceeds = margin_short_proceeds.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.margin_short_realized_profit = margin_short_realized_profit.quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        diary.margin_short_average_price = (
            (margin_short_proceeds / margin_short_quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if margin_short_quantity > 0 else None
        )

        logger.debug(
            "集計完了: 保有数=%s, 購入計=%s, 売却計=%s, 実現損益=%s, 信用ショート建玉=%s",
            diary.current_quantity, diary.total_bought_quantity,
            diary.total_sold_quantity, diary.realized_profit, diary.margin_short_quantity,
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
