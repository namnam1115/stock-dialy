"""詳細ページの遅延ロード HTMX パネル群。

views.py から責務分割（原則: 小さく分離）。日記詳細のタブで HTMX 遅延ロードされる
パネル：被リンク（backlinks_panel）と EDINET 開示連携（edinet_panel /
edinet_note_prefill / edinet_xbrl_analyze）＋補助関数。
urls.py は `from . import views_panels` で参照する。
"""
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.http import require_GET, require_POST

from .models import StockDiary
from .utils import find_backlinks

logger = logging.getLogger(__name__)



# ============================================================
# EDINET連携: 開示書類パネル（HTMXパーシャル）
# ============================================================

def _get_securities_code(stock_symbol):
    """銘柄コード（4桁）からEDINET用5桁証券コードに変換"""
    if stock_symbol and stock_symbol.isdigit() and len(stock_symbol) == 4:
        return stock_symbol + '0'
    return None


def _sentiment_tone_trend(doc, current_score):
    """同一銘柄の前回の重要開示（有報・半報）と比較した経営トーンの変化を返す。

    Returns:
        dict | None: {'delta': float, 'label': '改善'|'悪化'|'横ばい', 'prev_score': float}
    """
    from earnings_analysis.models.sentiment import SentimentAnalysisHistory
    from earnings_analysis.services.disclosure_sync import IMPORTANT_DOC_TYPE_CODES

    if current_score is None or not doc.securities_code or not doc.file_date:
        return None

    prev = (
        SentimentAnalysisHistory.objects
        .filter(
            document__securities_code=doc.securities_code,
            document__doc_type_code__in=IMPORTANT_DOC_TYPE_CODES,
            document__file_date__lt=doc.file_date,
        )
        .order_by('-document__file_date', '-analysis_date')
        .first()
    )
    if not prev or prev.overall_score is None:
        return None

    delta = float(current_score) - float(prev.overall_score)
    label = '改善' if delta > 0.05 else ('悪化' if delta < -0.05 else '横ばい')
    return {'delta': delta, 'label': label, 'prev_score': float(prev.overall_score)}


@login_required
@require_GET
def backlinks_panel(request, diary_id):
    """バックリンク（この銘柄に言及している他の記録）を HTMX で遅延ロードする。

    find_backlinks は本文・継続記録の全文走査で重いため、detail の初期表示では
    計算せず、関連タブを開いたときだけこのエンドポイントで描画する。
    """
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    backlinks = find_backlinks(diary, request.user)
    return render(request, 'stockdiary/partials/_backlinks.html', {
        'backlinks': backlinks,
    })


@login_required
@require_GET
def edinet_panel(request, diary_id):
    """
    EDINET関連開示書類パネル（HTMXで遅延ロード）
    日本株4桁コードにマッチする直近の開示書類と分析結果を返す
    """
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    documents = []
    error = None

    try:
        from earnings_analysis.models.company import Company
        from earnings_analysis.models.document import DocumentMetadata
        from earnings_analysis.models.financial import CompanyFinancialData
        from earnings_analysis.models.sentiment import SentimentAnalysisSession, SentimentAnalysisHistory

        sec_code = _get_securities_code(diary.stock_symbol)
        if not sec_code:
            return render(request, 'stockdiary/partials/edinet_panel.html', {
                'diary': diary,
                'documents': [],
                'not_supported': True,
            })

        # EDINETの Company マスタから edinet_code を解決する
        company = Company.objects.filter(securities_code=sec_code, is_active=True).first()
        if company:
            doc_filter = {'edinet_code': company.edinet_code, 'legal_status': '1'}
        else:
            # Company マスタに存在しない場合は securities_code で代替（EDINET制約は維持）
            doc_filter = {'securities_code': sec_code, 'legal_status': '1'}

        # 直近10件の開示書類を取得（legal_status='1'＝閲覧中のみ）
        docs = (
            DocumentMetadata.objects
            .filter(**doc_filter)
            .order_by('-submit_date_time')[:10]
        )

        for doc in docs:
            # 感情分析: セッション → 永続履歴にフォールバック
            sent_session = (
                SentimentAnalysisSession.objects
                .filter(document=doc, processing_status='COMPLETED')
                .order_by('-created_at')
                .first()
            )
            sent_history = None
            if not sent_session:
                try:
                    sent_history = (
                        SentimentAnalysisHistory.objects
                        .filter(document=doc)
                        .order_by('-analysis_date')
                        .first()
                    )
                except Exception as e:
                    logger.debug('感情分析履歴の取得に失敗: %s', e)
            sent = sent_session or sent_history

            # 経営トーンの前回比（同一銘柄の前回有報・半報との比較）
            tone_trend = None
            if sent and sent.overall_score is not None:
                tone_trend = _sentiment_tone_trend(doc, sent.overall_score)

            pdf_url = None
            if doc.pdf_flag:
                try:
                    pdf_url = reverse('copomo:document-download', args=[doc.doc_id]) + '?type=pdf'
                except Exception as e:
                    logger.debug('PDFダウンロードURLの解決に失敗: %s', e)

            # 感情分析データをJSON化（モーダル表示用）
            # 判定（AIスコア・AI投資ポイント）は載せず、語彙分析の根拠
            # （キーワード・キーセンテンス・統計）と前回比のみを出す
            sent_json = None
            if sent:
                try:
                    analysis_result = getattr(sent, 'analysis_result', None) or {}
                    kw = analysis_result.get('keyword_analysis', {})
                    stats_raw = analysis_result.get('statistics', {})

                    # キーワードは {'word': ..., 'count': ...} のdict配列なので文字列に変換
                    def _kw_word(k):
                        return k.get('word', '') if isinstance(k, dict) else str(k)

                    # センテンスは {'text': ..., 'score': ...} のdict配列なので文字列に変換
                    def _sent_text(s):
                        return s.get('text', '') if isinstance(s, dict) else str(s)

                    raw_sp = (analysis_result.get('sample_sentences') or {}).get('positive', [])
                    raw_sn = (analysis_result.get('sample_sentences') or {}).get('negative', [])

                    sent_json = json.dumps({
                        'overall_score': float(sent.overall_score) if sent.overall_score is not None else 0,
                        'sentiment_label': sent.sentiment_label or '',
                        'label_display': {
                            'positive': 'ポジティブ', 'negative': 'ネガティブ', 'neutral': '中立',
                        }.get(sent.sentiment_label, sent.sentiment_label or '—'),
                        'tone_trend': tone_trend,
                        'sample_sentences': {
                            'positive': [_sent_text(s) for s in raw_sp[:3]],
                            'negative': [_sent_text(s) for s in raw_sn[:3]],
                        },
                        'keyword_pos': [_kw_word(k) for k in kw.get('positive', [])[:10]],
                        'keyword_neg': [_kw_word(k) for k in kw.get('negative', [])[:10]],
                        'stats': {k: stats_raw[k] for k in (
                            'sentences_analyzed', 'positive_words_count', 'negative_words_count'
                        ) if k in stats_raw},
                    }, ensure_ascii=False)
                except Exception as e:
                    logger.debug('感情分析JSONの構築に失敗: %s', e)

            # 財務データ（XBRL 分析済みの場合）
            fin_data = (
                CompanyFinancialData.objects
                .filter(document=doc)
                .order_by('-updated_at')
                .first()
            )

            # 財務分析レポート JSON（fin_data がある場合に構築）
            report_json = None
            if fin_data:
                try:
                    from earnings_analysis.services.financial_analyzer import FinancialAnalyzer
                    from decimal import Decimal
                    # 判定（リスクレベル・強み/懸念・解釈文）は載せず、
                    # CFパターンの機械的分類（名称・定義）と数値のみを出す
                    cf_data = {}
                    if all(getattr(fin_data, f) is not None for f in ('operating_cf', 'investing_cf', 'financing_cf')):
                        cf_result = FinancialAnalyzer().analyze_cashflow_pattern({
                            'operating_cf': Decimal(str(fin_data.operating_cf)),
                            'investing_cf': Decimal(str(fin_data.investing_cf)),
                            'financing_cf': Decimal(str(fin_data.financing_cf)),
                        })
                        ptn = cf_result.get('pattern', {})
                        amt = cf_result.get('amounts', {})
                        cf_data = {
                            'name': ptn.get('name', ''),
                            'description': ptn.get('description', ''),
                            'operating_cf': amt.get('operating_cf', 0),
                            'investing_cf': amt.get('investing_cf', 0),
                            'financing_cf': amt.get('financing_cf', 0),
                        }

                    def _f(v):
                        return round(float(v), 2) if v is not None else None

                    # 比率フィールドが null の場合は元データから直接計算（_calculate_ratios の条件が厳しいため）
                    def _ratio_or_calc(stored, numerator, denominator):
                        if stored is not None:
                            return _f(stored)
                        try:
                            n = getattr(fin_data, numerator)
                            d = getattr(fin_data, denominator)
                            if n is not None and d is not None and Decimal(str(d)) != 0:
                                return round(float(Decimal(str(n)) / Decimal(str(d)) * 100), 2)
                        except Exception as e:
                            logger.debug('財務比率の計算に失敗: %s', e)
                        return None

                    report_json = json.dumps({
                        'company_name': doc.company_name,
                        'doc_type': doc.doc_type_display_name or doc.doc_type_code,
                        'file_date': str(doc.file_date) if doc.file_date else '',
                        # 財務安全性（DBの計算済み値 → なければ元データから直接計算）
                        'equity_ratio': _ratio_or_calc(fin_data.equity_ratio, 'net_assets', 'total_assets'),
                        # CF 数値
                        'operating_cf': _f(fin_data.operating_cf),
                        'investing_cf': _f(fin_data.investing_cf),
                        'financing_cf': _f(fin_data.financing_cf),
                        # CFパターン詳細
                        'cf': cf_data,
                    }, ensure_ascii=False)
                except Exception as e:
                    logger.debug('財務レポートJSONの構築に失敗: %s', e)

            # XBRL財務分析の可否（財務諸表を含む種別のみ。臨報・内部統制等は不可）
            from earnings_analysis.services.xbrl_analysis_service import XBRL_ANALYZABLE_DOC_TYPE_CODES
            can_xbrl_analyze = (
                doc.xbrl_flag and doc.doc_type_code in XBRL_ANALYZABLE_DOC_TYPE_CODES
            )

            documents.append({
                'doc': doc,
                'sent': sent,
                'tone_trend': tone_trend,
                'fin_data': fin_data,
                'can_xbrl_analyze': can_xbrl_analyze,
                'report_json': report_json,
                'pdf_url': pdf_url,
                'sent_json': sent_json,
            })

    except ImportError:
        error = 'earnings_analysis アプリが利用できません'
    except Exception as e:
        error = str(e)

    return render(request, 'stockdiary/partials/edinet_panel.html', {
        'diary': diary,
        'documents': documents,
        'error': error,
        'not_supported': False,
    })


@login_required
@require_GET
def edinet_note_prefill(request, diary_id):
    """
    EDINET開示書類をもとに「決算レビュー」ノートの下書きをJSON返却。
    確定財務サマリー（XBRL分析済みの場合）・経営トーンの前回比・
    投資仮説（reason）の引用を差し込み、仮説と確定決算の突き合わせを促す。
    新規Gemini呼び出しは行わず、保存済みの分析結果のみ使用する。
    """
    doc_id = request.GET.get('doc_id', '')
    if not doc_id:
        return JsonResponse({'error': 'doc_id required'}, status=400)

    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)

    try:
        from earnings_analysis.models.document import DocumentMetadata
        from earnings_analysis.models.financial import CompanyFinancialData
        from earnings_analysis.models.sentiment import SentimentAnalysisSession, SentimentAnalysisHistory
        from earnings_analysis.services.disclosure_sync import IMPORTANT_DOC_TYPE_CODES

        doc = get_object_or_404(DocumentMetadata, doc_id=doc_id, legal_status='1')

        content_parts = []
        content_parts.append(f'## 決算レビュー: {doc.company_name}')
        content_parts.append(f'書類: {doc.doc_type_display_name}（{doc.file_date} 提出）')
        content_parts.append('')

        # --- 確定財務サマリー（XBRL分析済みの場合・前回の有報/半報と比較） ---
        fin = (
            CompanyFinancialData.objects
            .filter(document=doc)
            .order_by('-updated_at')
            .first()
        )
        prev_fin = None
        if fin and doc.securities_code and doc.file_date:
            prev_fin = (
                CompanyFinancialData.objects
                .filter(
                    document__securities_code=doc.securities_code,
                    document__doc_type_code__in=IMPORTANT_DOC_TYPE_CODES,
                    document__file_date__lt=doc.file_date,
                )
                .order_by('-document__file_date', '-updated_at')
                .first()
            )

        def _oku(value):
            return f'{float(value) / 1e8:,.1f}億円' if value is not None else None

        def _pct(value):
            return f'{float(value):.1f}%' if value is not None else None

        def _metric_line(label, cur, prev):
            if cur is None:
                return None
            return f'- {label}: {cur}（前回 {prev}）' if prev is not None else f'- {label}: {cur}'

        if fin:
            fin_lines = [line for line in (
                _metric_line('売上高', _oku(fin.net_sales), _oku(prev_fin.net_sales) if prev_fin else None),
                _metric_line('営業利益', _oku(fin.operating_income), _oku(prev_fin.operating_income) if prev_fin else None),
                _metric_line('営業利益率', _pct(fin.operating_margin), _pct(prev_fin.operating_margin) if prev_fin else None),
                _metric_line('自己資本比率', _pct(fin.equity_ratio), _pct(prev_fin.equity_ratio) if prev_fin else None),
            ) if line]
            if all(getattr(fin, f) is not None for f in ('operating_cf', 'investing_cf', 'financing_cf')):
                def _sign(value):
                    return '+' if value > 0 else ('−' if value < 0 else '0')
                fin_lines.append(
                    f'- CF: 営業{_sign(fin.operating_cf)} / 投資{_sign(fin.investing_cf)} / 財務{_sign(fin.financing_cf)}'
                )
            if fin_lines:
                content_parts.append('### 確定財務サマリー')
                content_parts.extend(fin_lines)
                content_parts.append('')

        # --- 経営トーン（感情分析: セッション → 永続履歴。前回開示との差分つき） ---
        sent = (
            SentimentAnalysisSession.objects
            .filter(document=doc, processing_status='COMPLETED')
            .order_by('-created_at')
            .first()
        )
        if not sent:
            try:
                sent = (
                    SentimentAnalysisHistory.objects
                    .filter(document=doc)
                    .order_by('-analysis_date')
                    .first()
                )
            except Exception:
                sent = None

        if sent and sent.overall_score is not None:
            _label_map = {'positive': 'ポジティブ', 'negative': 'ネガティブ', 'neutral': '中立'}
            label_display = _label_map.get(sent.sentiment_label, sent.sentiment_label or '—')
            score = float(sent.overall_score)
            tone_line = f'- 経営トーン: **{label_display}**（スコア {score:.2f}'

            trend = _sentiment_tone_trend(doc, score)
            if trend:
                tone_line += f"、前回 {trend['prev_score']:.2f} から{trend['label']}"
            tone_line += '）'

            content_parts.append('### 経営トーン')
            content_parts.append(tone_line)
            content_parts.append('')

        # --- 投資仮説の点検（決算レビューの本体） ---
        content_parts.append('### 投資仮説の点検')
        reason_text = strip_tags(diary.reason or '').strip()
        if reason_text:
            excerpt = reason_text[:200] + ('…' if len(reason_text) > 200 else '')
            content_parts.append('> ' + excerpt.replace('\n', '\n> '))
            content_parts.append('')
        content_parts.append('この決算を受けて、仮説は **維持 / 修正 / 撤回** のどれか。理由も書く:')
        content_parts.append('- ')
        content_parts.append('')

        content_parts.append('---')
        content_parts.append(f'*書類参照: {doc.doc_type_display_name} / {doc.file_date} [#{doc.doc_id}]*')

        prefill_content = '\n'.join(content_parts)
        return JsonResponse({
            'content': prefill_content,
            'doc_id': doc.doc_id,
            'note_type': 'earnings',
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================
# EDINET連携: XBRL 財務分析トリガー（非AI・ルールベース）
# ============================================================

@login_required
@require_POST
def edinet_xbrl_analyze(request, diary_id):
    """
    EDINET XBRL から財務指標を抽出・算出して CompanyFinancialData に保存。
    AI（Gemini）は一切使用しない。
    """
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    doc_id = request.POST.get('doc_id', '')
    if not doc_id:
        return JsonResponse({'error': 'doc_id required'}, status=400)

    try:
        from earnings_analysis.models.document import DocumentMetadata
        from earnings_analysis.services.xbrl_analysis_service import (
            XBRLAnalysisService,
            XBRL_ANALYZABLE_DOC_TYPE_CODES,
        )

        doc = get_object_or_404(DocumentMetadata, doc_id=doc_id, legal_status='1')

        if not doc.xbrl_flag:
            return JsonResponse({'error': 'この書類には XBRL データがありません'}, status=400)
        if doc.doc_type_code not in XBRL_ANALYZABLE_DOC_TYPE_CODES:
            return JsonResponse(
                {'error': 'この書類種別は財務諸表を含まないため、XBRL財務分析に対応していません'},
                status=400,
            )

        result = XBRLAnalysisService().analyze_document(doc)

        if not result.get('ok'):
            return JsonResponse({'error': result.get('error', '分析に失敗しました')}, status=500)

        return JsonResponse({'ok': True, 'result': result})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


