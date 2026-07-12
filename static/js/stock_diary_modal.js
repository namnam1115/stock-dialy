/**
 * 銘柄別日記リスト表示モーダル（共通ロジック）
 * trading_dashboard.html と tag_detail.html で共通利用
 */

/**
 * 銘柄別の日記リストをモーダルで表示
 * @param {string} stockCode - 証券コード
 * @param {string} stockName - 銘柄名
 */
function showStockDiariesModal(stockCode, stockName) {
    // モーダル要素を取得（なければ作成）
    let modal = document.getElementById('stockDiaryModal');
    
    if (!modal) {
        // モーダルが存在しない場合は動的に作成
        modal = createStockDiaryModal();
        document.body.appendChild(modal);
    }
    
    // モーダルタイトルとアイコンを設定
    const modalTitle = modal.querySelector('#stockModalTitle');
    const modalIcon = modal.querySelector('#stockModalIcon');
    const modalContent = modal.querySelector('#stockModalContent');
    
    if (modalTitle) modalTitle.textContent = stockName;
    if (modalIcon) modalIcon.textContent = '📊';
    if (modalContent) modalContent.innerHTML = '<div class="text-center py-5"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">読み込み中...</span></div></div>';
    
    // モーダルを表示
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
    
    // APIから日記データを取得
    fetch(`/stockdiary/api/stock-diaries/${stockCode}/`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                throw new Error(data.error || 'データの取得に失敗しました');
            }
            
            const diaries = data.diaries || [];
            
            if (diaries.length === 0) {
                modalContent.innerHTML = renderEmptyState(stockName);
                return;
            }
            
            // 日記リストを描画
            modalContent.innerHTML = renderStockDiaryList(diaries, stockCode, stockName);
        })
        .catch(error => {
            console.error('Error loading stock diaries:', error);
            modalContent.innerHTML = renderErrorState(error.message);
        });
}

/**
 * モーダルを閉じる
 */
function closeStockDiaryModal() {
    const modal = document.getElementById('stockDiaryModal');
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

/**
 * モーダルHTMLを動的に作成
 */
function createStockDiaryModal() {
    const modal = document.createElement('div');
    modal.id = 'stockDiaryModal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-container">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">
                        <span id="stockModalIcon">📊</span>
                        <span id="stockModalTitle"></span>
                    </h3>
                    <button class="modal-close" onclick="closeStockDiaryModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <div id="stockModalContent"></div>
                </div>
            </div>
        </div>
    `;
    
    // モーダル外クリックで閉じる
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeStockDiaryModal();
        }
    });
    
    return modal;
}

/**
 * 日記リストHTMLを生成
 */
function renderStockDiaryList(diaries, stockCode, stockName) {
    // 合計値を計算
    let totalTransactions = 0;
    let totalInvested = 0;
    let totalSell = 0;
    let totalCurrentValue = 0;
    let totalCurrentQuantity = 0;
    
    diaries.forEach(diary => {
        totalTransactions += diary.transaction_count || 0;
        totalInvested += diary.total_buy_amount || 0;
        totalSell += diary.total_sell_amount || 0;
        
        // 現在の評価額を計算
        if (diary.current_quantity > 0 && diary.average_purchase_price) {
            totalCurrentValue += diary.current_quantity * diary.average_purchase_price;
        }
        totalCurrentQuantity += diary.current_quantity || 0;
    });

    // ROI計算
    let totalRoi = 0;
    if (totalInvested > 0) {
        totalRoi = ((totalSell + totalCurrentValue - totalInvested) / totalInvested * 100);
    }

    // 実現損益: 「総売却額－簿価減少分」の逆算ではなく、各日記が保持する
    // FIFO/移動平均計算済みの realized_profit をそのまま合算する
    // （逆算式は総売却額と簿価計算の前提が崩れると大きくズレるため使わない）
    let realizedProfit = diaries.reduce((sum, d) => sum + (d.realized_profit || 0), 0);
    
    const roiClass = totalRoi >= 0 ? 'highlight-positive' : 'highlight-negative';
    const profitClass = realizedProfit >= 0 ? 'highlight-positive' : 'highlight-negative';

    let html = `
        <div class="summary-section">
            <!-- メインメトリクス -->
            <div class="main-metrics">
                <div class="metric-box ${roiClass}">
                    <div class="metric-box-label">投資効率 (ROI)</div>
                    <div class="metric-box-value ${totalRoi >= 0 ? 'positive' : 'negative'}">
                        ${totalRoi >= 0 ? '+' : ''}${totalRoi.toFixed(1)}<span class="metric-box-unit">%</span>
                    </div>
                </div>
                <div class="metric-box ${profitClass}">
                    <div class="metric-box-label">実現損益</div>
                    <div class="metric-box-value ${realizedProfit >= 0 ? 'positive' : 'negative'}">
                        ${realizedProfit >= 0 ? '+' : ''}¥${Math.abs(realizedProfit).toLocaleString()}
                    </div>
                </div>
            </div>

            <!-- サブメトリクス -->
            <div class="sub-metrics">
                <div class="sub-metric">
                    <div class="sub-metric-label">取引回数</div>
                    <div class="sub-metric-value">${totalTransactions}<span class="unit">回</span></div>
                </div>
                <div class="sub-metric">
                    <div class="sub-metric-label">保有数</div>
                    <div class="sub-metric-value">${totalCurrentQuantity.toFixed(0)}<span class="unit">株</span></div>
                </div>
                <div class="sub-metric">
                    <div class="sub-metric-label">投資額</div>
                    <div class="sub-metric-value">¥${totalInvested.toLocaleString()}</div>
                </div>
                <div class="sub-metric">
                    <div class="sub-metric-label">評価額</div>
                    <div class="sub-metric-value">¥${totalCurrentValue.toLocaleString()}</div>
                </div>
            </div>

            <!-- メタ情報 -->
            <div class="summary-meta">
                <span>証券コード: ${stockCode}</span>
                <span class="summary-badge">現物取引のみ</span>
            </div>
        </div>

        <div class="detail-list">
    `;

    diaries.forEach((diary, index) => {
        const statusBadge = diary.is_holding ? 'holding' : (diary.is_sold_out ? 'sold' : 'memo');
        const statusText = diary.is_holding ? '保有中' : (diary.is_sold_out ? '売却済' : 'メモ');
        
        // ROI計算
        let diaryRoi = 0;
        const diaryInvested = diary.total_buy_amount || 0;
        const diarySell = diary.total_sell_amount || 0;
        let diaryCurrentValue = 0;
        
        if (diary.current_quantity > 0 && diary.average_purchase_price) {
            diaryCurrentValue = diary.current_quantity * diary.average_purchase_price;
        }
        
        if (diaryInvested > 0) {
            diaryRoi = ((diarySell + diaryCurrentValue - diaryInvested) / diaryInvested * 100);
        }
        
        const roiClass = diaryRoi >= 0 ? 'positive' : 'negative';
        const roiSign = diaryRoi >= 0 ? '+' : '';
        const profitClass = (diary.realized_profit || 0) >= 0 ? 'positive' : 'negative';

        html += `
            <div class="detail-card" onclick="window.location.href='/stockdiary/${diary.id}/'">
                <div class="detail-card-header">
                    <div class="detail-card-title">
                        <div class="detail-card-name">
                            #${index + 1} 日記
                            <span class="detail-status-badge ${statusBadge}">${statusText}</span>
                        </div>
                        <div class="detail-card-meta">
                            <span class="detail-card-date">📅 ${diary.created_at}〜</span>
                        </div>
                    </div>
                    <div class="detail-roi-badge ${roiClass}">
                        ${roiSign}${diaryRoi.toFixed(1)}%
                    </div>
                </div>

                <div class="detail-card-stats">
                    <div class="detail-stat">
                        <span class="detail-stat-label">取引回数</span>
                        <span class="detail-stat-value">${diary.transaction_count || 0}回</span>
                    </div>
                    <div class="detail-stat">
                        <span class="detail-stat-label">保有数</span>
                        <span class="detail-stat-value">${(diary.current_quantity || 0).toFixed(0)}株</span>
                    </div>
                    <div class="detail-stat">
                        <span class="detail-stat-label">実現損益</span>
                        <span class="detail-stat-value ${profitClass}">
                            ${(diary.realized_profit || 0) >= 0 ? '+' : ''}¥${Math.abs(diary.realized_profit || 0).toLocaleString()}
                        </span>
                    </div>
                    <div class="detail-stat">
                        <span class="detail-stat-label">投資額</span>
                        <span class="detail-stat-value">¥${(diaryInvested || 0).toLocaleString()}</span>
                    </div>
                </div>
            </div>
        `;
    });

    html += '</div>';
    return html;
}

/**
 * 空状態のHTMLを生成
 */
function renderEmptyState(stockName) {
    return `
        <div class="empty-message">
            <div class="empty-message-icon">📭</div>
            <p class="empty-message-text">${stockName}の日記が見つかりません</p>
        </div>
    `;
}

/**
 * エラー状態のHTMLを生成
 */
function renderErrorState(errorMessage) {
    return `
        <div class="empty-message">
            <div class="empty-message-icon">⚠️</div>
            <p class="empty-message-text">データの読み込みに失敗しました</p>
            <p class="text-muted small">${errorMessage}</p>
            <button class="btn btn-sm btn-outline-primary mt-3" onclick="window.location.reload()">
                <i class="bi bi-arrow-clockwise me-1"></i>再試行
            </button>
        </div>
    `;
}

// ESCキーでモーダルを閉じる
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeStockDiaryModal();
    }
});