"""証券会社の取引CSV（楽天・SBI）の取込ビューとパーサ。

views.py から責務分割（原則: 小さく分離）。プレビュー解析と本取込を含む。
TradeUploadView / process_trade_upload と各社別の process_*_csv / parse_*_csv_preview。
urls.py は `from . import views_trade_import` で参照する。
"""
import csv
import io
import logging
import traceback

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction as db_transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView
from decimal import Decimal, InvalidOperation
from datetime import datetime
from collections import defaultdict
import chardet

from .models import StockDiary, Transaction
from .forms import TradeUploadForm

logger = logging.getLogger(__name__)


def _build_company_sector_map(stock_codes):
    """銘柄コード一覧から業種マップを1クエリで作る。

    インポート行ごとに CompanyMaster を引くとコストが大きいため、
    ファイル内の distinct なコードだけをまとめて1回で引く。
    """
    from company_master.models import CompanyMaster

    codes = {code for code in stock_codes if code}
    if not codes:
        return {}
    return {
        c.code: c.industry_name_33 or c.industry_name_17 or ''
        for c in CompanyMaster.objects.filter(code__in=codes)
    }


class TradeUploadView(LoginRequiredMixin, FormView):
    """取引履歴アップロードビュー"""
    template_name = 'stockdiary/trade_upload.html'
    form_class = TradeUploadForm
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]
        return context
    
    def form_valid(self, form):
        broker = form.cleaned_data['broker']
        csv_file = form.cleaned_data['csv_file']
        
        # セッションにブローカー情報を保存
        self.request.session['upload_broker'] = broker
        
        # 🔧 ファイル名を保存
        self.request.session['upload_filename'] = csv_file.name
        
        # CSVファイルを読み込んで処理
        try:
            # バイト列を読み込み
            csv_bytes = csv_file.read()
            
            # エンコーディングを検出
            detected = chardet.detect(csv_bytes)
            encoding = detected['encoding']
            
            # 検出されたエンコーディングで文字列に変換
            try:
                csv_content = csv_bytes.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                # 検出に失敗した場合は一般的なエンコーディングを試す
                for enc in ['shift-jis', 'cp932', 'utf-8-sig', 'utf-8', 'euc-jp']:
                    try:
                        csv_content = csv_bytes.decode(enc)
                        encoding = enc
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ValueError('CSVファイルのエンコーディングを判別できませんでした')
            
            # セッションにCSVコンテンツとエンコーディング情報を保存
            self.request.session['csv_content'] = csv_content
            self.request.session['csv_encoding'] = encoding
            
            messages.info(
                self.request, 
                f'CSVファイルを読み込みました（エンコーディング: {encoding}）'
            )
            
            # プレビュー画面に遷移
            return redirect('stockdiary:process_trade_upload')
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(
                self.request, 
                f'CSVファイルの読み込みに失敗しました: {str(e)}'
            )
            return self.form_invalid(form)


@login_required
def process_trade_upload(request):
    """取引履歴処理ビュー"""
    if request.method != 'POST':
        # GET時はプレビュー表示
        broker = request.session.get('upload_broker')
        csv_content = request.session.get('csv_content')
        filename = request.session.get('upload_filename', '不明')
        
        if not broker or not csv_content:
            messages.error(request, 'アップロードデータが見つかりません')
            return redirect('stockdiary:trade_upload')
        
        # CSVをパースしてプレビュー
        try:
            # ✅ ブローカーに応じて処理を分岐
            if broker == 'rakuten':
                preview_data = parse_rakuten_csv_preview(csv_content)
            elif broker == 'sbi':
                preview_data = parse_sbi_csv_preview(csv_content)
            else:
                raise ValueError(f'未対応の証券会社です: {broker}')
            
            context = {
                'broker': broker,
                'filename': filename,
                'preview_data': preview_data,
                'total_count': len(preview_data),
            }
            
            return render(request, 'stockdiary/trade_upload_preview.html', context)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f'CSVの解析に失敗しました: {str(e)}')
            return redirect('stockdiary:trade_upload')
    
    else:
        # POST時は実際にデータ登録
        broker = request.session.get('upload_broker')
        csv_content = request.session.get('csv_content')
        filename = request.session.get('upload_filename', '不明')
        
        if not broker or not csv_content:
            messages.error(request, 'アップロードデータが見つかりません')
            return redirect('stockdiary:trade_upload')
        
        try:
            # ✅ ブローカーに応じて処理を分岐
            if broker == 'rakuten':
                result = process_rakuten_csv(request.user, csv_content, filename)
            elif broker == 'sbi':
                result = process_sbi_csv(request.user, csv_content, filename)
            else:
                raise ValueError(f'未対応の証券会社です: {broker}')
            
            # セッションデータをクリア
            del request.session['upload_broker']
            del request.session['csv_content']
            if 'upload_filename' in request.session:
                del request.session['upload_filename']
            
            messages.success(
                request,
                f'取引履歴の登録が完了しました。'
                f'成功: {result["success_count"]}件、'
                f'上書き: {result["overwrite_count"]}件、'
                f'スキップ: {result["skip_count"]}件、'
                f'エラー: {result["error_count"]}件'
            )
            
            # エラーがあれば詳細を表示
            if result['errors']:
                for error in result['errors'][:5]:  # 最初の5件まで
                    messages.warning(request, error)
            
            return redirect('stockdiary:home')
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f'取引履歴の登録中にエラーが発生しました: {str(e)}')
            return redirect('stockdiary:trade_upload')
        

def process_rakuten_csv(user, csv_content, filename):
    """
    楽天証券CSVを処理してStockDiaryとTransactionを作成
    
    処理ルール:
    - 1ファイル内の同一キー: 数量を合算
    - 既存データと同一キーがある場合: 常に上書き（重複取り込み防止）
    """
    csv_file = io.StringIO(csv_content)
    reader = csv.DictReader(csv_file)
    
    success_count = 0
    skip_count = 0
    error_count = 0
    overwrite_count = 0
    errors = []
    # 今回の取込で実際に取引を作成・更新した Diary の pk。末尾でこれらだけを
    # 1回ずつ再集計する（ループ内の個別 save() の自動再集計は _defer_recalc で抑制）。
    touched_diary_pks = set()

    # まず全データを読み込んで日付順にソート
    all_rows = []
    for original_row_num, row in enumerate(reader, start=2):
        trade_date_str = row.get('受渡日', '').strip()
        if trade_date_str:
            all_rows.append({
                'data': row,
                'original_row': original_row_num
            })
    
    # 受渡日でソート（古い順）
    def parse_date(date_str):
        for date_format in ['%Y/%m/%d', '%Y-%m-%d', '%Y年%m月%d日']:
            try:
                return datetime.strptime(date_str, date_format)
            except ValueError:
                continue
        return datetime.max
    
    all_rows.sort(key=lambda r: parse_date(r['data'].get('受渡日', '')))

    # 業種マップは行ごとではなく、ファイル内の distinct コードで1回だけ引く
    company_sector_map = _build_company_sector_map(
        r['data'].get('銘柄コード', '').strip() for r in all_rows
    )

    for idx, row_data in enumerate(all_rows, start=1):
        row = row_data['data']
        original_row_num = row_data['original_row']

        try:
            # 受渡日を取得
            trade_date_str = row.get('受渡日', '').strip()
            if not trade_date_str:
                skip_count += 1
                continue

            # 日付をパース
            try:
                trade_date = None
                for date_format in ['%Y/%m/%d', '%Y-%m-%d', '%Y年%m月%d日']:
                    try:
                        trade_date = datetime.strptime(trade_date_str, date_format).date()
                        break
                    except ValueError:
                        continue

                if trade_date is None:
                    raise ValueError(f'日付形式が不正です: {trade_date_str}')
            except ValueError as e:
                errors.append(f'行{original_row_num}: {str(e)}')
                error_count += 1
                continue

            # 銘柄情報
            stock_code = row.get('銘柄コード', '').strip()
            stock_name = row.get('銘柄名', '').strip()

            if not stock_code or not stock_name:
                errors.append(f'行{original_row_num}: 銘柄コードまたは銘柄名が空です')
                skip_count += 1
                continue
            
            # 売買区分を取得
            trade_type_raw = row.get('売買区分', '').strip()
            trade_category = row.get('取引区分', '').strip()
            
            # ✅ 信用取引かどうかを判定
            is_margin_trade = '信用' in trade_category
            
            # 売買区分を変換
            if '買' in trade_type_raw or '積立' in trade_type_raw:
                transaction_type = 'buy'
            elif '売' in trade_type_raw:
                transaction_type = 'sell'
            else:
                errors.append(f'行{original_row_num}: 不明な売買区分: "{trade_type_raw}" ({stock_name})')
                error_count += 1
                continue
            
            # 数量と単価を取得
            quantity_str = row.get('数量［株］', '')
            price_str = row.get('単価［円］', '')
            
            # カンマを除去
            quantity_str = quantity_str.replace(',', '').strip()
            price_str = price_str.replace(',', '').strip()
            
            if not quantity_str or not price_str:
                errors.append(f'行{original_row_num}: 数量または単価が空です ({stock_name})')
                skip_count += 1
                continue
            
            # 数値に変換
            try:
                quantity = Decimal(quantity_str)
                price = Decimal(price_str)
            except (ValueError, InvalidOperation) as e:
                errors.append(f'行{original_row_num}: 数値の解析エラー: {stock_name} - 数量:{quantity_str}, 単価:{price_str}')
                error_count += 1
                continue
            
            if quantity <= 0 or price <= 0:
                errors.append(f'行{original_row_num}: 数量または単価が0以下です ({stock_name})')
                skip_count += 1
                continue
            
            # StockDiaryを取得または作成
            with db_transaction.atomic():
                # 既存のStockDiaryを取得（複数ある場合は最初のものを使用）
                diary = StockDiary.objects.filter(
                    user=user,
                    stock_symbol=stock_code
                ).order_by('created_at').first()
                
                if not diary:
                    # 存在しない場合は新規作成（業種はコードから自動設定。銘柄コードが
                    # CompanyMaster に無ければ空のまま＝詳細画面から任意で設定できる）
                    diary = StockDiary.objects.create(
                        user=user,
                        stock_symbol=stock_code,
                        stock_name=stock_name,
                        sector=company_sector_map.get(stock_code, ''),
                        reason=f'楽天証券からインポート（{trade_date}）',
                    )
                elif not diary.sector and company_sector_map.get(stock_code):
                    diary.sector = company_sector_map[stock_code]
                    diary.save(update_fields=['sector'])

                # この行の save() による自動再集計を抑制する（末尾で1回だけ実行）。
                diary._defer_recalc = True

                # メモ内容を作成
                memo_content = f'楽天証券からインポート({trade_category} {trade_type_raw}) [ファイル: {filename} 行: {original_row_num}]'
                
                # 同一キー（日付・銘柄・価格・取引種別）の取引を検索
                price_tolerance = Decimal('0.01')
                
                existing_transaction = Transaction.objects.filter(
                    diary=diary,
                    transaction_type=transaction_type,
                    transaction_date=trade_date,
                    price__gte=price - price_tolerance,
                    price__lte=price + price_tolerance
                ).first()
                
                if existing_transaction:
                    # ✅ 既存の同一キーがある場合は常に上書き（重複取り込み防止）
                    # defer フラグ付きの diary インスタンスを使わせ、save() の自動再集計を抑制。
                    existing_transaction.diary = diary
                    existing_transaction.quantity = quantity
                    existing_transaction.price = price
                    existing_transaction.memo = memo_content
                    existing_transaction.is_margin = is_margin_trade  # ✅ 信用取引フラグを更新
                    existing_transaction.save()
                    overwrite_count += 1

                else:
                    # ✅ 新規取引として作成
                    transaction_obj = Transaction(
                        diary=diary,
                        transaction_type=transaction_type,
                        transaction_date=trade_date,
                        price=price,
                        quantity=quantity,
                        memo=memo_content,
                        is_margin=is_margin_trade  # ✅ 信用取引フラグを設定
                    )

                    transaction_obj.save()
                    success_count += 1

                touched_diary_pks.add(diary.pk)

        except Exception as e:
            import traceback
            traceback.print_exc()
            stock_name_for_error = locals().get('stock_name', '不明')
            errors.append(f'行{original_row_num} ({stock_name_for_error}): {str(e)}')
            error_count += 1
            continue

    # 最後に、今回触れた Diary だけを1回ずつ再集計する。
    # pk__in で DB から取り直すため、ロールバックされた新規 Diary は自然に除外される。
    for diary in StockDiary.objects.filter(pk__in=touched_diary_pks):
        diary.update_aggregates()

    return {
        'success_count': success_count,
        'skip_count': skip_count,
        'error_count': error_count,
        'overwrite_count': overwrite_count,
        'errors': errors
    }

# ✅ プレビュー表示：1ファイル内の同一キー取引をグループ化
def parse_rakuten_csv_preview(csv_content):
    """楽天CSVをパースしてプレビュー用データを返す（1ファイル内の同一キーは合算表示）"""
    csv_file = io.StringIO(csv_content)
    reader = csv.DictReader(csv_file)
    
    # 同一キーごとにデータを集約
    grouped_data = defaultdict(lambda: {
        'quantity': 0,
        'amount': 0,
        'count': 0,
        'first_row': None
    })
    
    for row_num, row in enumerate(reader, 1):
        try:
            trade_date = row.get('受渡日', '').strip()
            stock_code = row.get('銘柄コード', '').strip()
            stock_name = row.get('銘柄名', '').strip()
            
            trade_category = row.get('取引区分', '').strip()
            trade_type = row.get('区分', '').strip()
            
            quantity_str = row.get('数量［株］', '') or row.get('数量[株]', '') or row.get('数量', '')
            price_str = row.get('単価［円］', '') or row.get('単価[円]', '') or row.get('単価', '')
            
            quantity_str = quantity_str.replace(',', '').strip()
            price_str = price_str.replace(',', '').strip()
            
            if not quantity_str or not price_str:
                continue
                
            try:
                quantity = float(quantity_str)
                price = float(price_str)
            except ValueError:
                continue
            
            # ✅ キーを生成（日付・銘柄コード・価格・取引種別）
            key = (trade_date, stock_code, f'{price:.2f}', trade_type)
            
            # ✅ 同一キーのデータを集約
            if grouped_data[key]['first_row'] is None:
                grouped_data[key]['first_row'] = {
                    'date': trade_date,
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'trade_category': trade_category,
                    'trade_type': trade_type,
                    'price': price
                }
            
            grouped_data[key]['quantity'] += quantity
            grouped_data[key]['amount'] += quantity * price
            grouped_data[key]['count'] += 1
            
        except Exception as e:
            logger.warning("Row %s parsing error: %s", row_num, e, exc_info=True)
            continue
    
    # ✅ 集約されたデータをプレビュー用に整形
    preview_data = []
    for key, data in grouped_data.items():
        row_data = data['first_row']
        total_quantity = data['quantity']
        total_amount = data['amount']
        merge_count = data['count']
        
        display_trade_type = f"{row_data['trade_category']} {row_data['trade_type']}" if row_data['trade_category'] else row_data['trade_type']
        
        # ✅ 合算される場合は注釈を追加
        quantity_display = f'{total_quantity:,.0f}'
        if merge_count > 1:
            quantity_display += f' ※{merge_count}件を合算'
        
        preview_data.append({
            'date': row_data['date'],
            'stock_code': row_data['stock_code'],
            'stock_name': row_data['stock_name'],
            'trade_type': display_trade_type,
            'trade_category': row_data['trade_category'],
            'buy_or_sell': row_data['trade_type'],
            'quantity': quantity_display,
            'price': f'{row_data["price"]:,.2f}',
            'amount': f'{total_amount:,.0f}',
            'is_merged': merge_count > 1  # ✅ 合算フラグ
        })
    
    # 日付順にソート
    preview_data.sort(key=lambda x: x['date'])
    
    return preview_data

def parse_sbi_csv_preview(csv_content):
    """SBI証券CSVをパースしてプレビュー用データを返す"""
    lines = csv_content.strip().split('\n')
    
    # 9行目（インデックス8）からがデータ
    if len(lines) < 7:
        raise ValueError('CSVファイルの形式が不正です（データ行が見つかりません）')
    
    # ヘッダー行を取得（9行目）
    header_line = lines[7]
    
    # データ行を取得（10行目以降）
    data_lines = lines[8:]
    
    csv_file = io.StringIO('\n'.join([header_line] + data_lines))
    reader = csv.DictReader(csv_file)
    
    # 同一キーごとにデータを集約
    grouped_data = defaultdict(lambda: {
        'quantity': 0,
        'amount': 0,
        'count': 0,
        'first_row': None
    })
    
    for row_num, row in enumerate(reader, 1):
        try:
            # 受渡日を取得（約定日ではなく受渡日を使用）
            trade_date = row.get('受渡日', '').strip()
            if not trade_date:
                continue
            
            stock_code = row.get('銘柄コード', '').strip()
            stock_name = row.get('銘柄', '').strip()
            
            # 銘柄コードがない場合はスキップ（投資信託など）
            if not stock_code:
                continue
            
            # 取引種別を取得
            trade_type_raw = row.get('取引', '').strip()
            
            # 売買区分を判定
            if '買' in trade_type_raw:
                trade_type = '買'
            elif '売' in trade_type_raw:
                trade_type = '売'
            else:
                continue
            
            # 数量と単価を取得
            quantity_str = row.get('約定数量', '').strip()
            price_str = row.get('約定単価', '').strip()
            
            # カンマを除去
            quantity_str = quantity_str.replace(',', '').strip()
            price_str = price_str.replace(',', '').strip()
            
            if not quantity_str or not price_str or quantity_str == '--' or price_str == '--':
                continue
            
            try:
                quantity = float(quantity_str)
                price = float(price_str)
            except ValueError:
                continue
            
            # キーを生成（日付・銘柄コード・価格・取引種別）
            key = (trade_date, stock_code, f'{price:.2f}', trade_type)
            
            # 同一キーのデータを集約
            if grouped_data[key]['first_row'] is None:
                grouped_data[key]['first_row'] = {
                    'date': trade_date,
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'trade_type_raw': trade_type_raw,
                    'trade_type': trade_type,
                    'price': price,
                    'market': row.get('市場', '').strip()
                }
            
            grouped_data[key]['quantity'] += quantity
            grouped_data[key]['amount'] += quantity * price
            grouped_data[key]['count'] += 1
            
        except Exception as e:
            logger.warning("Row %s parsing error: %s", row_num, e, exc_info=True)
            continue
    
    # 集約されたデータをプレビュー用に整形
    preview_data = []
    for key, data in grouped_data.items():
        row_data = data['first_row']
        total_quantity = data['quantity']
        total_amount = data['amount']
        merge_count = data['count']
        
        quantity_display = f'{total_quantity:,.0f}'
        if merge_count > 1:
            quantity_display += f' ※{merge_count}件を合算'
        
        preview_data.append({
            'date': row_data['date'],
            'stock_code': row_data['stock_code'],
            'stock_name': row_data['stock_name'],
            'trade_type': row_data['trade_type_raw'],
            'buy_or_sell': row_data['trade_type'],
            'quantity': quantity_display,
            'price': f'{row_data["price"]:,.2f}',
            'amount': f'{total_amount:,.0f}',
            'is_merged': merge_count > 1
        })
    
    # 日付順にソート
    preview_data.sort(key=lambda x: x['date'])
    
    return preview_data


def process_sbi_csv(user, csv_content, filename):
    """
    SBI証券CSVを処理してStockDiaryとTransactionを作成
    """
    lines = csv_content.strip().split('\n')
    
    if len(lines) < 9:
        raise ValueError('CSVファイルの形式が不正です')
    
    # ヘッダー行とデータ行を取得
    header_line = lines[7]
    data_lines = lines[8:]
    
    csv_file = io.StringIO('\n'.join([header_line] + data_lines))
    reader = csv.DictReader(csv_file)
    
    success_count = 0
    skip_count = 0
    error_count = 0
    overwrite_count = 0
    errors = []
    # 今回の取込で実際に取引を作成・更新した Diary の pk。末尾でこれらだけを
    # 1回ずつ再集計する（ループ内の個別 save() の自動再集計は _defer_recalc で抑制）。
    touched_diary_pks = set()

    # 全データを読み込んで日付順にソート
    all_rows = []
    for original_row_num, row in enumerate(reader, start=10):  # 実際のデータは10行目から
        trade_date_str = row.get('受渡日', '').strip()
        if trade_date_str:
            all_rows.append({
                'data': row,
                'original_row': original_row_num
            })
    
    # 受渡日でソート（古い順）
    def parse_date(date_str):
        for date_format in ['%Y/%m/%d', '%Y-%m-%d', '%Y年%m月%d日']:
            try:
                return datetime.strptime(date_str, date_format)
            except ValueError:
                continue
        return datetime.max
    
    all_rows.sort(key=lambda r: parse_date(r['data'].get('受渡日', '')))

    # 業種マップは行ごとではなく、ファイル内の distinct コードで1回だけ引く
    company_sector_map = _build_company_sector_map(
        r['data'].get('銘柄コード', '').strip() for r in all_rows
    )

    for idx, row_data in enumerate(all_rows, start=1):
        row = row_data['data']
        original_row_num = row_data['original_row']

        try:
            # 受渡日を取得
            trade_date_str = row.get('受渡日', '').strip()
            if not trade_date_str:
                skip_count += 1
                continue

            # 日付をパース
            try:
                trade_date = None
                for date_format in ['%Y/%m/%d', '%Y-%m-%d', '%Y年%m月%d日']:
                    try:
                        trade_date = datetime.strptime(trade_date_str, date_format).date()
                        break
                    except ValueError:
                        continue

                if trade_date is None:
                    raise ValueError(f'日付形式が不正です: {trade_date_str}')
            except ValueError as e:
                errors.append(f'行{original_row_num}: {str(e)}')
                error_count += 1
                continue

            # 銘柄情報
            stock_code = row.get('銘柄コード', '').strip()
            stock_name = row.get('銘柄', '').strip()

            # 銘柄コードがない場合はスキップ（投資信託など）
            if not stock_code or not stock_name:
                skip_count += 1
                continue
            
            # 取引種別を取得
            trade_type_raw = row.get('取引', '').strip()
            market = row.get('市場', '').strip()
            
            # ✅ 信用取引かどうかを判定
            is_margin_trade = '信用' in trade_type_raw
            
            # 売買区分を変換
            if '買' in trade_type_raw:
                transaction_type = 'buy'
            elif '売' in trade_type_raw:
                transaction_type = 'sell'
            else:
                skip_count += 1
                continue
            
            # 数量と単価を取得
            quantity_str = row.get('約定数量', '').strip()
            price_str = row.get('約定単価', '').strip()
            
            # カンマを除去
            quantity_str = quantity_str.replace(',', '').strip()
            price_str = price_str.replace(',', '').strip()
            
            if not quantity_str or not price_str or quantity_str == '--' or price_str == '--':
                skip_count += 1
                continue
            
            # 数値に変換
            try:
                quantity = Decimal(quantity_str)
                price = Decimal(price_str)
            except (ValueError, InvalidOperation) as e:
                errors.append(f'行{original_row_num}: 数値の解析エラー: {stock_name} - 数量:{quantity_str}, 単価:{price_str}')
                error_count += 1
                continue
            
            if quantity <= 0 or price <= 0:
                errors.append(f'行{original_row_num}: 数量または単価が0以下です ({stock_name})')
                skip_count += 1
                continue
            
            # StockDiaryを取得または作成
            with db_transaction.atomic():
                diary = StockDiary.objects.filter(
                    user=user,
                    stock_symbol=stock_code
                ).order_by('created_at').first()
                
                if not diary:
                    # 業種はコードから自動設定（CompanyMaster に無ければ空のまま＝
                    # 詳細画面から任意で設定できる）
                    diary = StockDiary.objects.create(
                        user=user,
                        stock_symbol=stock_code,
                        stock_name=stock_name,
                        sector=company_sector_map.get(stock_code, ''),
                        reason=f'SBI証券からインポート（{trade_date}）',
                    )
                elif not diary.sector and company_sector_map.get(stock_code):
                    diary.sector = company_sector_map[stock_code]
                    diary.save(update_fields=['sector'])

                # この行の save() による自動再集計を抑制する（末尾で1回だけ実行）。
                diary._defer_recalc = True

                # メモ内容を作成
                memo_content = f'SBI証券からインポート({trade_type_raw}'
                if market:
                    memo_content += f' {market}'
                memo_content += f') [ファイル: {filename} 行: {original_row_num}]'
                
                # 同一キーの取引を検索
                price_tolerance = Decimal('0.01')
                
                existing_transaction = Transaction.objects.filter(
                    diary=diary,
                    transaction_type=transaction_type,
                    transaction_date=trade_date,
                    price__gte=price - price_tolerance,
                    price__lte=price + price_tolerance
                ).first()
                
                if existing_transaction:
                    # 既存の同一キーがある場合は上書き
                    # defer フラグ付きの diary インスタンスを使わせ、save() の自動再集計を抑制。
                    existing_transaction.diary = diary
                    existing_transaction.quantity = quantity
                    existing_transaction.price = price
                    existing_transaction.memo = memo_content
                    existing_transaction.is_margin = is_margin_trade  # ✅ 信用取引フラグを更新
                    existing_transaction.save()
                    overwrite_count += 1
                else:
                    # 新規取引として作成
                    transaction_obj = Transaction(
                        diary=diary,
                        transaction_type=transaction_type,
                        transaction_date=trade_date,
                        price=price,
                        quantity=quantity,
                        memo=memo_content,
                        is_margin=is_margin_trade  # ✅ 信用取引フラグを設定
                    )
                    transaction_obj.save()
                    success_count += 1

                touched_diary_pks.add(diary.pk)

        except Exception as e:
            import traceback
            traceback.print_exc()
            stock_name_for_error = locals().get('stock_name', '不明')
            errors.append(f'行{original_row_num} ({stock_name_for_error}): {str(e)}')
            error_count += 1
            continue

    # 最後に、今回触れた Diary だけを1回ずつ再集計する。
    # pk__in で DB から取り直すため、ロールバックされた新規 Diary は自然に除外される。
    for diary in StockDiary.objects.filter(pk__in=touched_diary_pks):
        diary.update_aggregates()

    return {
        'success_count': success_count,
        'skip_count': skip_count,
        'error_count': error_count,
        'overwrite_count': overwrite_count,
        'errors': errors
    }


