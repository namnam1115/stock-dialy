/**
 * diary-form.js
 * diary_form.html にインラインで書かれていたページ固有スクリプトの外部化。
 * ページ固有の設定・URL は diary_form.html 内の window.DIARY_FORM_CONFIG から受け取る。
 * 新規作成/編集の分岐はテンプレート分岐から isCreate フラグの実行時分岐に置き換えた。
 */


// ============================================
// 初回購入情報の表示/非表示（グローバルスコープ）
// ============================================
// この関数はHTML onchange属性から呼び出されるため、グローバルスコープに定義する必要がある
window.handleInitialPurchaseToggle = function(checkbox) {
  console.log('[初回購入情報] handleInitialPurchaseToggle called, checked:', checkbox.checked);
  const initialPurchaseSection = document.getElementById('initialPurchaseSection');

  if (!initialPurchaseSection) {
    console.error('[初回購入情報] Section not found!');
    return;
  }

  // updateRequiredFields関数を直接呼び出す
  const dateField = document.getElementById('id_initial_purchase_date');
  const priceField = document.getElementById('id_initial_purchase_price');
  const quantityField = document.getElementById('id_initial_purchase_quantity');

  if (checkbox.checked) {
    initialPurchaseSection.style.display = 'block';
    initialPurchaseSection.classList.add('active');

    // フィールドにrequired属性を追加
    [dateField, priceField, quantityField].forEach(field => {
      if (field) {
        field.setAttribute('required', 'required');
      }
    });
  } else {
    initialPurchaseSection.style.display = 'none';
    initialPurchaseSection.classList.remove('active');

    // フィールドからrequired属性を削除してクリア
    [dateField, priceField, quantityField].forEach(field => {
      if (field) {
        field.removeAttribute('required');
        field.value = '';
      }
    });

    const preview = document.getElementById('initial-purchase-preview');
    if (preview) {
      preview.style.display = 'none';
    }
  }
};

document.addEventListener('DOMContentLoaded', function() {

// ============================================
// オートコンプリート機能
// ============================================
const stockSymbolInput = document.getElementById('id_stock_symbol');
let autocompleteTimeout;
let autocompleteResults = [];
let selectedIndex = -1;

if (stockSymbolInput) {
  // オートコンプリート用の結果表示エリアを作成
  const autocompleteContainer = document.createElement('div');
  autocompleteContainer.id = 'autocomplete-results';
  autocompleteContainer.className = 'autocomplete-results';
  autocompleteContainer.style.cssText = `
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    max-height: 300px;
    overflow-y: auto;
    border-radius: var(--radius-md, 0.375rem);
    z-index: 1000;
    display: none;
  `;
  
  // 入力グループの親要素に追加
  const inputGroup = stockSymbolInput.closest('.input-group');
  if (inputGroup) {
    inputGroup.style.position = 'relative';
    inputGroup.appendChild(autocompleteContainer);
  }
  
  // 入力時のオートコンプリート
  stockSymbolInput.addEventListener('input', function(e) {
    const query = e.target.value.trim();
    
    clearTimeout(autocompleteTimeout);
    
    if (query.length < 2) {
      autocompleteContainer.style.display = 'none';
      return;
    }
    
    // 300ms後に検索実行
    autocompleteTimeout = setTimeout(async () => {
      try {
        const response = await fetch(`/stockdiary/api/stock/search/?query=${encodeURIComponent(query)}&limit=10`);
        const data = await response.json();
        
        if (data.success && data.companies.length > 0) {
          autocompleteResults = data.companies;
          displayAutocompleteResults(data.companies);
        } else {
          autocompleteContainer.style.display = 'none';
        }
      } catch (error) {
        console.error('Autocomplete error:', error);
        autocompleteContainer.style.display = 'none';
      }
    }, 300);
  });
  
  // 結果を表示
  function displayAutocompleteResults(companies) {
    let html = '<div class="list-group list-group-flush">';
    
    companies.forEach((company, index) => {
      html += `
        <button type="button" 
                class="list-group-item list-group-item-action autocomplete-item" 
                data-index="${index}"
                style="border: none; padding: 0.75rem 1rem; cursor: pointer; text-align: left;">
          <div class="d-flex justify-content-between align-items-start">
            <div>
              <strong class="text-primary">${escapeHtml(company.code)}</strong>
              <span class="ms-2">${escapeHtml(company.name)}</span>
            </div>
            <div style="text-align: right;">
              <small class="text-muted d-block">${escapeHtml(company.market)}</small>
              <small class="text-muted d-block">${escapeHtml(company.industry)}</small>
            </div>
          </div>
        </button>
      `;
    });
    
    html += '</div>';
    autocompleteContainer.innerHTML = html;
    autocompleteContainer.style.display = 'block';
    
    // クリックイベント設定
    const items = autocompleteContainer.querySelectorAll('.autocomplete-item');
    items.forEach(item => {
      item.addEventListener('click', function() {
        const index = parseInt(this.dataset.index);
        selectCompany(autocompleteResults[index]);
      });
      
      item.addEventListener('mouseenter', function() {
        this.style.backgroundColor = '';
        this.classList.add('autocomplete-hover');
      });
      item.addEventListener('mouseleave', function() {
        this.style.backgroundColor = '';
        this.classList.remove('autocomplete-hover');
      });
    });
  }
  
  // 企業選択
  function selectCompany(company) {
    stockSymbolInput.value = company.code;
    
    const stockNameInput = document.getElementById('id_stock_name');
    const sectorInput = document.getElementById('id_sector');
    
    if (stockNameInput && !stockNameInput.value) {
      stockNameInput.value = company.name;
    }
    
    if (sectorInput && !sectorInput.value) {
      sectorInput.value = company.industry;
    }
    
    autocompleteContainer.style.display = 'none';
    selectedIndex = -1;
    
    // 株価情報を自動取得
    const fetchStockInfoBtn = document.getElementById('fetchStockInfo');
    if (fetchStockInfoBtn) {
      fetchStockInfoBtn.click();
    }
  }
  
  // キーボード操作
  stockSymbolInput.addEventListener('keydown', function(e) {
    const items = autocompleteContainer.querySelectorAll('.autocomplete-item');
    
    if (items.length === 0) return;
    
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
      updateSelection(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, -1);
      updateSelection(items);
    } else if (e.key === 'Enter' && selectedIndex >= 0) {
      e.preventDefault();
      const index = parseInt(items[selectedIndex].dataset.index);
      selectCompany(autocompleteResults[index]);
    } else if (e.key === 'Escape') {
      autocompleteContainer.style.display = 'none';
      selectedIndex = -1;
    }
  });
  
  function updateSelection(items) {
    items.forEach((item, index) => {
      item.classList.remove('autocomplete-selected');
      if (index === selectedIndex) {
        item.classList.add('autocomplete-selected');
        item.scrollIntoView({ block: 'nearest' });
      }
    });
  }
  
  // 外側クリックで閉じる
  document.addEventListener('click', function(e) {
    if (!stockSymbolInput.contains(e.target) && !autocompleteContainer.contains(e.target)) {
      autocompleteContainer.style.display = 'none';
      selectedIndex = -1;
    }
  });
}

const fetchStockInfoBtn = document.getElementById('fetchStockInfo');
const stockNameInput = document.getElementById('id_stock_name');
const sectorInput = document.getElementById('id_sector');
const stockInfoCard = document.getElementById('stockInfoCard');
const symbolSpinnerContainer = document.getElementById('symbolSpinnerContainer');

if (fetchStockInfoBtn) {
  fetchStockInfoBtn.addEventListener('click', async function() {
    const stockCode = stockSymbolInput.value.trim();
    
    if (!stockCode) {
      alert('銘柄コードを入力してください');
      stockSymbolInput.focus();
      return;
    }
    
    fetchStockInfoBtn.disabled = true;
    fetchStockInfoBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    symbolSpinnerContainer.classList.remove('d-none');
    stockInfoCard.classList.add('d-none');
    
    try {
      const response = await fetch(`/stockdiary/api/stock/info/${stockCode}/`);
      
      if (!response.ok) {
        throw new Error('株式情報の取得に失敗しました');
      }
      
      const data = await response.json();
      
      if (data.success) {
        if (data.company_name) {
          stockNameInput.value = data.company_name;
        }
        
        if (data.industry && data.industry !== '不明') {
          sectorInput.value = data.industry;
        }
        
        displayStockInfo(data, stockCode);
        checkDuplicateDiary();
      } else {
        throw new Error(data.error || '銘柄情報の取得に失敗しました');
      }
      
    } catch (error) {
      console.error('Stock info fetch error:', error);
      showNotification('error', error.message);
      stockInfoCard.classList.add('d-none');
    } finally {
      fetchStockInfoBtn.disabled = false;
      fetchStockInfoBtn.innerHTML = '<i class="bi bi-search"></i> 検索';
      symbolSpinnerContainer.classList.add('d-none');
    }
  });
}

// ==========================================
// 重複日記チェック（新規作成時のみ。既存日記への追記を促す）
// ==========================================
async function checkDuplicateDiary() {
  const warning = document.getElementById('duplicateWarning');
  if (!warning) return;  // 編集モードでは存在しない

  const allowField = document.getElementById('id_allow_duplicate');
  if (allowField && allowField.value === 'on') return;  // ユーザーが重複作成を明示済み

  const symbol = (stockSymbolInput ? stockSymbolInput.value : '').trim();
  const name = (stockNameInput ? stockNameInput.value : '').trim();
  if (!symbol && !name) {
    warning.classList.add('d-none');
    return;
  }

  try {
    const params = new URLSearchParams({ symbol: symbol, name: name });
    const response = await fetch(`/stockdiary/api/diary/check-duplicate/?${params}`);
    if (!response.ok) return;
    const data = await response.json();

    if (data.exists && data.diaries.length > 0) {
      const diary = data.diaries[0];
      let body =
        `<a href="${diary.detail_url}" class="fw-bold">${diary.stock_name}（${diary.stock_symbol || 'コードなし'}）</a> ` +
        `<span class="badge bg-secondary">${diary.status}</span> ` +
        `<span class="text-muted">最終更新 ${diary.updated_at}</span>`;
      if (diary.retrospective_count > 0) {
        body += ` <span class="badge bg-dark">過去の振り返り ${diary.retrospective_count}件</span>`;
      }
      document.getElementById('duplicateWarningBody').innerHTML = body;
      document.getElementById('duplicateOpenBtn').href = diary.detail_url;
      warning.classList.remove('d-none');
    } else {
      warning.classList.add('d-none');
    }
  } catch (e) {
    console.error('Duplicate check error:', e);
  }
}

// 「新しい日記として作成」: 重複を許可して警告を閉じる
function allowDuplicateDiary() {
  const allowField = document.getElementById('id_allow_duplicate');
  if (allowField) allowField.value = 'on';
  const warning = document.getElementById('duplicateWarning');
  if (warning) warning.classList.add('d-none');
}

// 銘柄コード・銘柄名の確定時にも重複チェック（検索ボタンを押さない手入力ケース）
if (document.getElementById('duplicateWarning')) {
  if (stockSymbolInput) stockSymbolInput.addEventListener('blur', checkDuplicateDiary);
  if (stockNameInput) stockNameInput.addEventListener('blur', checkDuplicateDiary);
}

// 株式情報表示
function displayStockInfo(data, stockCode) {
  document.getElementById('stockInfoTitle').textContent = data.company_name || '銘柄情報';
  document.getElementById('stockInfoCode').textContent = stockCode;
  document.getElementById('stockInfoCode').className = 'badge badge-primary';

  // 通貨（為替変換なし・元通貨で表示）。APIの currency から判定。
  const currency = data.currency || 'JPY';
  const currencyUnit = currency === 'USD' ? 'ドル' : '円';
  // 通貨単位ラベルを更新
  document.querySelectorAll('.js-currency-unit').forEach(function (el) {
    el.textContent = currencyUnit;
  });
  // 通貨セレクトを自動設定
  const currencySelect = document.getElementById('id_currency');
  if (currencySelect) {
    currencySelect.value = currency;
  }

  if (data.price !== null && data.price !== undefined) {
    document.getElementById('stockInfoPrice').textContent =
      `${parseFloat(data.price).toLocaleString('ja-JP', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      })}${currencyUnit}`;
  } else {
    document.getElementById('stockInfoPrice').textContent = '取得できませんでした';
  }
  
  if (data.change_percent !== null && data.change_percent !== undefined) {
    const changeElement = document.getElementById('stockInfoChange');
    const changePercent = parseFloat(data.change_percent);
    const changeClass = changePercent > 0 ? 'profit-positive' : 
                       (changePercent < 0 ? 'profit-negative' : 'text-muted');
    const changeSign = changePercent > 0 ? '+' : '';
    
    changeElement.textContent = `${changeSign}${changePercent.toFixed(2)}%`;
    changeElement.className = `info-value ${changeClass}`;
  } else {
    document.getElementById('stockInfoChange').textContent = '--';
  }
  
  document.getElementById('stockInfoMarket').textContent = data.market || '不明';
  document.getElementById('stockInfoIndustry').textContent = data.industry || '不明';
  
  stockInfoCard.classList.remove('d-none');
}

// 通知表示 (グローバルに公開: doFetchCurrentPrice から参照)
window.showNotification = function showNotification(type, message) {
  const existingNotification = document.querySelector('.custom-notification');
  if (existingNotification) {
    existingNotification.remove();
  }
  
  const notification = document.createElement('div');
  const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
  const iconClass = type === 'success' ? 'bi-check-circle' : 'bi-exclamation-triangle';
  
  notification.className = `alert ${alertClass} alert-dismissible fade show custom-notification`;
  notification.style.cssText = 'position: fixed; top: 80px; right: 20px; z-index: 9999; min-width: 300px;';
  notification.innerHTML = `
    <i class="bi ${iconClass} me-2"></i>
    ${message}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  `;
  
  document.body.appendChild(notification);
  
  setTimeout(() => {
    notification.remove();
  }, 3000);
}

// ============================================
// EasyMDE Markdown Editor 初期化
// ============================================
window.initReasonEasyMDE = function initReasonEasyMDE() {
  if (window.easyMDE) return;
  const reasonTextareaEl = document.getElementById('id_reason');
  if (!reasonTextareaEl) return;
  const reasonCharCount = document.getElementById('reasonCharCount');
  const MAX_REASON_LENGTH = 5000;

  // カスタムマークダウンレンダラーを作成（ハッシュタグ対応）
  const customRenderer = {
    preprocess: function(markdown) {
      // プレビュー前処理: ハッシュタグを一時的に保護
      // 行頭の # + スペース 以外の # をエスケープ
      const lines = markdown.split('\n');
      const processedLines = lines.map(line => {
        // 行頭の # + スペース（見出し）はそのまま
        if (line.match(/^\s*#{1,6}\s/)) {
          return line;
        }

        // それ以外の # をハッシュタグとして処理
        // #タグ名 を <span class="hashtag">#タグ名</span> に変換
        return line.replace(
          /#([\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z0-9_]+)/g,
          '<span class="hashtag">#$1</span>'
        );
      });

      return processedLines.join('\n');
    }
  };

  window.easyMDE = new EasyMDE({
    element: reasonTextareaEl,
    spellChecker: false,
    autosave: {
      enabled: false,
    },
    placeholder: 'なぜこの銘柄に注目したのか、どのような分析をしたのか、将来の見通しなどを自由に記録してください...\n\n**太字**、*斜体*、- リスト、## 見出し などのMarkdown記法が使えます\n\n@ でタグ（例: @成長株）、[[ で他の日記を参照できます',
    maxHeight: '400px',
    toolbar: easymdeBiToolbar([
      'bold', 'italic', 'heading', '|',
      'unordered-list', 'ordered-list', '|',
      'link', 'quote', '|',
      'preview', 'fullscreen', 'side-by-side'
    ]),
    status: false,
    renderingConfig: {
      singleLineBreaks: true,
      codeSyntaxHighlighting: false,
    },
    uploadImage: false,
    // 重要: CodeMirrorの初期モードを無効化
    parsingConfig: {
      allowAtxHeaderWithoutSpace: false, // # の後にスペースが必須
    },
    // カスタムプレビューレンダラー
    previewRender: function(plainText) {
      // ハッシュタグを保護してからマークダウンをレンダリング
      const processed = customRenderer.preprocess(plainText);
      return this.parent.markdown(processed);
    },
  });

  // EasyMDE用の文字数カウンター
  function updateEasyMDECharCount() {
    const count = window.easyMDE.value().length;
    reasonCharCount.textContent = `${count} / ${MAX_REASON_LENGTH}文字`;

    reasonCharCount.classList.remove('warning', 'danger');
    if (count > MAX_REASON_LENGTH * 0.9) {
      reasonCharCount.classList.add('danger');
    } else if (count > MAX_REASON_LENGTH * 0.7) {
      reasonCharCount.classList.add('warning');
    }
  }

  window.easyMDE.codemirror.on('change', updateEasyMDECharCount);
  updateEasyMDECharCount();

  // ウィザードの Step1 下書きバナーで「続きから書く」された内容を、
  // 背景(Step2)の EasyMDE が初期化されたこの時点で反映する。
  if (window.__pendingDraftReason) {
    window.easyMDE.value(window.__pendingDraftReason);
    window.__pendingDraftReason = null;
    updateEasyMDECharCount();
  }

  // ========== Phase 2: 集中モード ＋ 下書き自動保存（localStorage・正直な復元） ==========
  (function setupWritingExperience() {
    const cm = window.easyMDE.codemirror;
    const container = document.querySelector('.EasyMDEContainer');
    if (!cm || !container) return;
    const form = reasonTextareaEl.closest('form');
    const DRAFT_KEY = 'kabulog_draft:' + location.pathname;

    // --- 集中モード: 執筆中は周辺の chrome を淡くして没入させる ---
    cm.on('focus', function () { document.body.classList.add('writing-focus'); });
    cm.on('blur', function () { document.body.classList.remove('writing-focus'); });

    // --- 自動保存インジケーター（実体のある保存のみ表示） ---
    const indicator = document.createElement('div');
    indicator.className = 'rp-autosave';
    indicator.style.display = 'none';
    indicator.innerHTML = '<span class="rp-autosave-dot"></span><span class="rp-autosave-text"></span>';
    container.parentNode.insertBefore(indicator, container.nextSibling);
    const indText = indicator.querySelector('.rp-autosave-text');
    function showSaved() {
      const now = new Date();
      indText.textContent = '下書きを保存しました · ' +
        String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
      indicator.style.display = 'inline-flex';
    }

    // --- デバウンス保存 ---
    let saveTimer = null;
    cm.on('change', function () {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        const v = window.easyMDE.value();
        try {
          if (v && v.trim()) {
            localStorage.setItem(DRAFT_KEY, JSON.stringify({ reason: v, ts: Date.now() }));
            showSaved();
          } else {
            localStorage.removeItem(DRAFT_KEY);
          }
        } catch (e) { /* private mode 等は無視 */ }
      }, 800);
    });

    // --- 復元バナー: 既存の下書きがあり、現在の本文と異なる場合のみ提示 ---
    // 新規作成（ウィザード）では背景が Step2 で初期非表示のため、復元導線は Step1 側の
    // 先頭バナー（下記 setupWizardDraftNotice）に一本化する。ここ（エディタ直前）には
    // 出さない（二重表示の回避）。編集モードは本文が見えているのでここで提示する。
    try {
      const raw = !window.DIARY_FORM_CONFIG.isCreate && localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw);
        const current = window.easyMDE.value();
        if (draft && draft.reason && draft.reason.trim() && draft.reason !== current) {
          const banner = document.createElement('div');
          banner.className = 'rp-draft-banner';
          const when = new Date(draft.ts || Date.now());
          banner.innerHTML =
            '<span><i class="bi bi-clock-history me-1"></i>前回の下書きがあります（' +
            when.toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) +
            '）</span><span class="rp-draft-actions">' +
            '<button type="button" class="btn-quiet btn-quiet--primary rp-draft-restore">復元する</button>' +
            '<button type="button" class="btn-quiet rp-draft-dismiss">破棄</button></span>';
          container.parentNode.insertBefore(banner, container);
          banner.querySelector('.rp-draft-restore').addEventListener('click', function () {
            window.easyMDE.value(draft.reason);
            banner.remove();
          });
          banner.querySelector('.rp-draft-dismiss').addEventListener('click', function () {
            try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
            banner.remove();
          });
        }
      }
    } catch (e) { /* ignore */ }

    // --- 送信時は下書きを破棄（クライアント検証で弾かれる長さ超過時は残す） ---
    if (form) {
      form.addEventListener('submit', function () {
        if (window.easyMDE.value().length <= MAX_REASON_LENGTH) {
          try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
        }
      });
    }
  })();

  // ライブプレビュー: 入力中に「想起カードでの見え方」をサーバーの extract_lead で算出
  (function setupLeadPreview() {
    const wrap = document.getElementById('lead-preview');
    const out = document.getElementById('lead-preview-text');
    if (!wrap || !out) return;
    const csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
    let timer = null;
    let lastSent = null;

    function render(lead) {
      if (lead) {
        out.textContent = lead;
        wrap.classList.remove('d-none');
      } else {
        wrap.classList.add('d-none');
      }
    }

    function refresh() {
      const text = window.easyMDE.value();
      if (text === lastSent) return;
      lastSent = text;
      if (!text.trim()) { render(''); return; }
      const body = new URLSearchParams({ reason: text });
      fetch(window.DIARY_FORM_CONFIG.urls.leadPreview, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrfEl ? csrfEl.value : '',
        },
        body: body.toString(),
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) render(data.lead); })
        .catch(() => {});
    }

    window.easyMDE.codemirror.on('change', function () {
      clearTimeout(timer);
      timer = setTimeout(refresh, 400);
    });
    refresh();
  })();

  // @ハッシュタグ／[[日記メンション オートコンプリート（投資仮説フィールド）
  try {
    new HashtagMentionAutocomplete(
      window.easyMDE.codemirror,
      window.DIARY_FORM_CONFIG.urls.hashtags
    );
    new DiaryMentionAutocomplete(
      window.easyMDE.codemirror,
      window.DIARY_FORM_CONFIG.urls.searchMyDiaries
    );
  } catch (e) {
    console.error('[HashtagAC] 初期化に失敗:', e);
  }

  // ハッシュタグのハイライト表示（CodeMirrorカスタムモード）
  // EasyMDE は CodeMirror をグローバルに公開しないためインスタンスから取得
  // ※ ハイライトは装飾のみ。失敗してもオートコンプリートには影響させない
  const CodeMirror = window.easyMDE.codemirror.constructor;
  CodeMirror.defineMode("markdown-with-hashtags", function(config) {
    const mdMode = CodeMirror.getMode(config, "markdown");

    return {
      startState: function() {
        const state = mdMode.startState();
        state.isAtLineStart = true;
        return state;
      },

      copyState: function(state) {
        const newState = CodeMirror.copyState(mdMode, state);
        newState.isAtLineStart = state.isAtLineStart;
        return newState;
      },

      token: function(stream, state) {
        // 行頭かどうかを追跡
        if (stream.sol()) {
          state.isAtLineStart = true;
        }

        // 行頭の空白をスキップ
        if (state.isAtLineStart && stream.eatSpace()) {
          return null;
        }

        // 行頭の # + スペース（マークダウン見出し）
        if (state.isAtLineStart && stream.match(/^#{1,6}\s/)) {
          state.isAtLineStart = false;
          return "header";
        }

        // それ以外の場合は行頭フラグをオフ
        if (state.isAtLineStart && !stream.eol()) {
          state.isAtLineStart = false;
        }

        // ハッシュタグをチェック（行頭以外、または#の後にスペースなし）
        if (stream.match(/#[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z0-9_]+/)) {
          return "hashtag";
        }

        // その他はマークダウンモードに委譲
        return mdMode.token(stream, state);
      }
    };
  });

  // ハッシュタグと見出し用のスタイルを追加
  const style = document.createElement('style');
  style.textContent = `
    /* ハッシュタグのスタイル */
    .cm-hashtag {
      color: #0969da !important;
      font-weight: 600;
      background-color: rgba(9, 105, 218, 0.1);
      padding: 2px 4px;
      border-radius: 3px;
    }

    .dark-mode .cm-hashtag,
    [data-theme="dark"] .cm-hashtag {
      color: #71c4ef !important;
      background-color: rgba(113, 196, 239, 0.15);
    }

    /* マークダウン見出しのスタイル（念のため） */
    .cm-header {
      color: #1f2328 !important;
      font-weight: bold !important;
    }

    .dark-mode .cm-header,
    [data-theme="dark"] .cm-header {
      color: #e6edf3 !important;
    }
  `;
  document.head.appendChild(style);

  // モードを適用
  window.easyMDE.codemirror.setOption('mode', 'markdown-with-hashtags');

  // CodeMirrorのマークダウン設定を強制上書き
  // allowAtxHeaderWithoutSpace を無効化して、# の後にスペースがない場合は見出しとして扱わない
  if (window.easyMDE.codemirror.getMode().name === 'markdown-with-hashtags') {
    const originalGetTokenAt = window.easyMDE.codemirror.getTokenAt.bind(window.easyMDE.codemirror);
    window.easyMDE.codemirror.getTokenAt = function(pos, precise) {
      const token = originalGetTokenAt(pos, precise);
      // header トークンを無効化（ハッシュタグとして扱う）
      if (token && token.type && token.type.includes('header')) {
        // 行頭の # + スペース 以外は header を削除
        const line = this.getLine(pos.line);
        if (!line.match(/^\s*#{1,6}\s/)) {
          token.type = token.type.replace(/header/g, '');
        }
      }
      return token;
    };
  }

  // 送信前の文字数チェック
  const diaryForm = document.getElementById('diaryForm');
  if (diaryForm) {
    diaryForm.addEventListener('submit', function(e) {
      if (window.easyMDE.value().length > MAX_REASON_LENGTH) {
        e.preventDefault();
        alert(`背景は${MAX_REASON_LENGTH}文字以内で入力してください。現在: ${window.easyMDE.value().length}文字`);
        return false;
      }

      // PWAは画面を開いたままカメラ撮影・画像圧縮を挟むなど送信までが長くなりやすく、
      // フォームに埋め込まれたCSRFトークンが古くなって弾かれることがある（quickNoteForm
      // と同じ対策）。送信直前に最新のcsrftoken Cookieへhidden inputを差し替える。
      const csrfInput = diaryForm.querySelector('input[name="csrfmiddlewaretoken"]');
      const freshToken = typeof getCookie === 'function' ? getCookie('csrftoken') : null;
      if (csrfInput && freshToken) {
        csrfInput.value = freshToken;
      }
    });
  }
};
// 編集モード: 要素が表示済みのため即時初期化
if (!window.DIARY_FORM_CONFIG.isCreate) {
  window.initReasonEasyMDE();
}

// 新規作成（ウィザード）: 背景(Step2)は初期非表示で EasyMDE も未初期化のため、
// 下書きの存在を Step1 で知らせる。load 時に localStorage を直接見て、フォーム先頭
// （Step1 で最初に目に入る位置）に復元バナーを出す。復元内容は Step2 初期化時に反映。
if (window.DIARY_FORM_CONFIG.isCreate) {
  // このスクリプトは DOM 構築後（body 末尾）に実行されるため DOMContentLoaded は
  // 既に発火済みのことがある。編集モードの初期化（上）と同じく同期実行する。
  (function setupWizardDraftNotice() {
    const form = document.getElementById('diaryForm');
    if (!form) return;
    const DRAFT_KEY = 'kabulog_draft:' + location.pathname;
    let draft = null;
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw) draft = JSON.parse(raw);
    } catch (e) { return; }
    if (!draft || !draft.reason || !draft.reason.trim()) return;

    const when = new Date(draft.ts || Date.now());
    const banner = document.createElement('div');
    banner.className = 'rp-draft-banner';
    banner.innerHTML =
      '<span><i class="bi bi-clock-history me-1"></i>' +
      when.toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) +
      ' の未送信の下書きがあります</span>' +
      '<span class="rp-draft-actions">' +
      '<button type="button" class="btn-quiet btn-quiet--primary rp-draft-restore">続きから書く</button>' +
      '<button type="button" class="btn-quiet rp-draft-dismiss">破棄</button></span>';
    form.insertBefore(banner, form.firstChild);

    banner.querySelector('.rp-draft-restore').addEventListener('click', function () {
      if (window.easyMDE) {
        window.easyMDE.value(draft.reason);
      } else {
        // Step2 で EasyMDE が初期化されたときに反映される
        window.__pendingDraftReason = draft.reason;
      }
      // 背景は非表示のため復元しても画面変化がない。復元できたことを確認表示する。
      banner.innerHTML =
        '<span><i class="bi bi-check-circle me-1"></i>背景の下書きを復元しました。銘柄名を入力して「次へ」で続けられます。</span>';
      setTimeout(function () { banner.remove(); }, 5000);
    });
    banner.querySelector('.rp-draft-dismiss').addEventListener('click', function () {
      try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
      banner.remove();
    });
  })();
}

// ============================================
// 初回購入情報のDOMContentLoaded時の初期化
// ============================================
(function initInitialPurchaseToggle() {
  const addInitialPurchaseCheckbox = document.getElementById('id_add_initial_purchase');
  const initialPurchaseSection = document.getElementById('initialPurchaseSection');

  console.log('[初回購入情報] Initializing...');
  console.log('[初回購入情報] Checkbox:', addInitialPurchaseCheckbox);
  console.log('[初回購入情報] Section:', initialPurchaseSection);

  if (!addInitialPurchaseCheckbox || !initialPurchaseSection) {
    console.warn('[初回購入情報] Elements not found - skipping (normal for edit mode)');
    return;
  }

  // 初期状態を反映（ページ読み込み時にチェックされている場合）
  if (addInitialPurchaseCheckbox.checked) {
    console.log('[初回購入情報] Initial checkbox state: checked');
    // handleInitialPurchaseToggleを呼び出して初期表示
    window.handleInitialPurchaseToggle(addInitialPurchaseCheckbox);
  } else {
    console.log('[初回購入情報] Initial checkbox state: unchecked');
  }

  console.log('[初回購入情報] Initialization complete');
})()

function updateRequiredFields(required) {
  const dateField = document.getElementById('id_initial_purchase_date');
  const priceField = document.getElementById('id_initial_purchase_price');
  const quantityField = document.getElementById('id_initial_purchase_quantity');
  
  [dateField, priceField, quantityField].forEach(field => {
    if (field) {
      if (required) {
        field.setAttribute('required', 'required');
      } else {
        field.removeAttribute('required');
        field.value = '';
      }
    }
  });
  
  if (!required) {
    const preview = document.getElementById('initial-purchase-preview');
    if (preview) {
      preview.style.display = 'none';
    }
  }
}

// ============================================
// 合計金額の計算 (グローバルに公開: doFetchCurrentPrice から参照)
// ============================================
window.updateInitialPurchasePreview = function updateInitialPurchasePreview() {
  const priceField = document.getElementById('id_initial_purchase_price');
  const quantityField = document.getElementById('id_initial_purchase_quantity');
  const checkbox = document.getElementById('id_add_initial_purchase');
  const preview = document.getElementById('initial-purchase-preview');
  const amountElement = document.getElementById('initial-purchase-amount');
  
  if (!priceField || !quantityField || !checkbox || !preview || !amountElement) return;
  
  const price = parseFloat(priceField.value) || 0;
  const quantity = parseFloat(quantityField.value) || 0;
  
  if (price > 0 && quantity > 0 && checkbox.checked) {
    const amount = price * quantity;
    amountElement.textContent = amount.toLocaleString('ja-JP', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    });
    preview.style.display = 'block';
  } else {
    preview.style.display = 'none';
  }
}

const priceField = document.getElementById('id_initial_purchase_price');
const quantityField = document.getElementById('id_initial_purchase_quantity');

if (priceField) {
  priceField.addEventListener('input', updateInitialPurchasePreview);
}

if (quantityField) {
  quantityField.addEventListener('input', updateInitialPurchasePreview);
}

// ============================================
// 画像プレビュー
// ============================================
const imageInput = document.getElementById('id_image');
const imagePreviewContainer = document.getElementById('image-preview-container');
const imagePreview = document.getElementById('image-preview');
const removeImageBtn = document.getElementById('remove-image');

if (imageInput && imagePreview && imagePreviewContainer && window.ImageCompressionHandler) {
  // 継続記録（note_image）と同じ圧縮設定に揃える。
  // サーバー側（nginx）は圧縮後サイズを前提に client_max_body_size を絞っているため、
  // 生ファイルをそのまま送るとアップロードが413で弾かれる。
  window.setupImageCompression({
    inputId: 'id_image',
    previewId: 'image-preview',
    containerId: 'image-preview-container',
    removeBtnId: 'remove-image',
    options: {
      maxWidth: 1200,
      maxHeight: 900,
      quality: 0.9,
      compressionThreshold: 2 * 1024 * 1024, // 2MB以上で圧縮
      maxFileSize: 3 * 1024 * 1024,          // 最大3MB
      onError: (message) => alert(message)
    }
  });
} else if (imageInput) {
  // 圧縮ライブラリ未読み込み時のフォールバック（旧挙動）
  imageInput.addEventListener('change', function(e) {
    const file = e.target.files[0];

    if (file) {
      if (file.size > 3 * 1024 * 1024) {
        alert('画像ファイルのサイズは3MB以下にしてください');
        imageInput.value = '';
        return;
      }

      const validFormats = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'];
      if (!validFormats.includes(file.type)) {
        alert('JPEG、PNG、GIF、WebP形式の画像ファイルのみアップロード可能です');
        imageInput.value = '';
        return;
      }

      const reader = new FileReader();
      reader.onload = function(e) {
        imagePreview.src = e.target.result;
        imagePreviewContainer.style.display = 'block';
      };
      reader.readAsDataURL(file);
    }
  });

  if (removeImageBtn) {
    removeImageBtn.addEventListener('click', function() {
      imageInput.value = '';
      imagePreviewContainer.style.display = 'none';
      imagePreview.src = '';
    });
  }
}

// ============================================
// 分析テンプレート展開
// ============================================
const analysisTemplateSelect = document.getElementById('id_analysis_template');
const expandAnalysisBtn = document.getElementById('expandAnalysisBtn');
const analysisItemsContainer = document.getElementById('analysisItemsContainer');
const analysisItemsContent = document.getElementById('analysisItemsContent');

if (expandAnalysisBtn) {
  expandAnalysisBtn.addEventListener('click', function() {
    const templateId = analysisTemplateSelect.value;
    
    if (!templateId) {
      alert('分析テンプレートを選択してください');
      analysisTemplateSelect.focus();
      return;
    }
    
    analysisItemsContainer.classList.remove('d-none');
    loadAnalysisItems(templateId);
  });
}

if (analysisTemplateSelect) {
  analysisTemplateSelect.addEventListener('change', function() {
    if (this.value && !analysisItemsContainer.classList.contains('d-none')) {
      loadAnalysisItems(this.value);
    }
  });
}

async function loadAnalysisItems(templateId) {
  analysisItemsContent.innerHTML = `
    <div class="text-center py-3">
      <div class="loading-spinner"></div>
      <div class="loading-text mt-2">分析項目を読み込んでいます...</div>
    </div>
  `;
  
  try {
    const diaryId = window.DIARY_FORM_CONFIG.diaryId;
    let url = `/analysis_template/api/items/?template_id=${templateId}`;
    
    if (diaryId) {
      url += `&diary_id=${diaryId}`;
    }
    
    const response = await fetch(url);
    
    if (!response.ok) {
      throw new Error('分析項目の取得に失敗しました');
    }
    
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '分析項目の取得に失敗しました');
    }
    
    renderAnalysisItems(data.items, data.values || {});
    
  } catch (error) {
    console.error('Analysis items fetch error:', error);
    analysisItemsContent.innerHTML = `
      <div class="alert alert-warning">
        <i class="bi bi-exclamation-triangle me-2"></i>
        分析項目の読み込みに失敗しました: ${error.message}
        <button type="button" class="btn btn-sm btn-outline-primary ms-2" onclick="loadAnalysisItems(${templateId})">
          <i class="bi bi-arrow-clockwise"></i> 再試行
        </button>
      </div>
    `;
  }
}


function renderAnalysisItems(items, values = {}) {
  if (!items || items.length === 0) {
    analysisItemsContent.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon"><i class="bi bi-info-circle"></i></div>
        <h4>分析項目がありません</h4>
        <p>このテンプレートには分析項目が設定されていません</p>
      </div>
    `;
    return;
  }
  
  let html = '';
  
  items.forEach((item, index) => {
    const existingValue = values[item.id];
    
    let icon = 'bi-circle';
    if (item.item_type === 'boolean') icon = 'bi-check-circle';
    else if (item.item_type === 'boolean_with_value') icon = 'bi-check-square';
    else if (item.item_type === 'number') icon = 'bi-123';
    else if (item.item_type === 'select') icon = 'bi-list-ul';
    else if (item.item_type === 'text') icon = 'bi-text-left';
    
    html += `<div class="analysis-item">`;
    
    if (item.item_type === 'boolean') {
      const checked = existingValue === true ? 'checked' : '';
      
      html += `
        <div class="analysis-item-header">
          <div class="analysis-item-label-wrapper">
            <i class="bi ${icon}"></i>
            <div>
              <label class="analysis-item-label" for="analysis_item_${item.id}">
                ${escapeHtml(item.name)}
              </label>
              ${item.description ? `<div class="analysis-item-desc">${escapeHtml(item.description)}</div>` : ''}
            </div>
          </div>
          <div class="analysis-item-checkbox">
            <div class="form-check">
              <input type="checkbox" 
                     class="form-check-input" 
                     name="analysis_item_${item.id}" 
                     id="analysis_item_${item.id}"
                     ${checked}>
            </div>
          </div>
        </div>
      `;
      
    } else if (item.item_type === 'boolean_with_value') {
      const boolChecked = existingValue?.boolean_value === true ? 'checked' : '';
      const numberValue = existingValue?.number_value ?? '';
      const valueLabel = item.value_label || '値';
      
      html += `
        <div class="analysis-item-header">
          <div class="analysis-item-label-wrapper">
            <i class="bi ${icon}"></i>
            <div>
              <label class="analysis-item-label">
                ${escapeHtml(item.name)}
              </label>
              ${item.description ? `<div class="analysis-item-desc">${escapeHtml(item.description)}</div>` : ''}
            </div>
          </div>
        </div>
        
        <div class="compound-input-wrapper">
          <div class="compound-checkbox-row">
            <input type="checkbox" 
                   class="form-check-input" 
                   name="analysis_item_${item.id}_boolean" 
                   id="analysis_item_${item.id}_boolean"
                   ${boolChecked}>
            <label class="form-check-label" for="analysis_item_${item.id}_boolean">
              該当する
            </label>
          </div>
          <div>
            <label class="form-label">${escapeHtml(valueLabel)}</label>
            <input type="number" 
                   class="form-control" 
                   name="analysis_item_${item.id}_value" 
                   id="analysis_item_${item.id}_value"
                   step="0.01"
                   placeholder="数値を入力"
                   value="${numberValue}">
          </div>
        </div>
      `;
      
    } else if (item.item_type === 'number') {
      const value = existingValue ?? '';
      
      html += `
        <div class="analysis-item-header">
          <div class="analysis-item-label-wrapper">
            <i class="bi ${icon}"></i>
            <div>
              <label class="analysis-item-label" for="analysis_item_${item.id}">
                ${escapeHtml(item.name)}
              </label>
              ${item.description ? `<div class="analysis-item-desc">${escapeHtml(item.description)}</div>` : ''}
            </div>
          </div>
        </div>
        <div class="analysis-item-input-section">
          <input type="number" 
                 class="form-control" 
                 name="analysis_item_${item.id}" 
                 id="analysis_item_${item.id}"
                 step="0.01"
                 placeholder="数値を入力"
                 value="${value}">
        </div>
      `;
      
    } else if (item.item_type === 'select') {
      html += `
        <div class="analysis-item-header">
          <div class="analysis-item-label-wrapper">
            <i class="bi ${icon}"></i>
            <div>
              <label class="analysis-item-label" for="analysis_item_${item.id}">
                ${escapeHtml(item.name)}
              </label>
              ${item.description ? `<div class="analysis-item-desc">${escapeHtml(item.description)}</div>` : ''}
            </div>
          </div>
        </div>
        <div class="analysis-item-input-section">
          <select class="form-select" 
                  name="analysis_item_${item.id}" 
                  id="analysis_item_${item.id}">
            <option value="">選択してください</option>
      `;
      
      let choicesList = [];
      if (item.choices) {
        if (Array.isArray(item.choices)) {
          choicesList = item.choices;
        } else if (typeof item.choices === 'string') {
          choicesList = item.choices.split(',').map(c => c.trim()).filter(c => c);
        }
      }
      
      choicesList.forEach(choice => {
        const selected = existingValue === choice ? 'selected' : '';
        html += `<option value="${escapeHtml(choice)}" ${selected}>${escapeHtml(choice)}</option>`;
      });
      
      html += `</select></div>`;
      
    } else {
      const value = existingValue ?? '';
      html += `
        <div class="analysis-item-header">
          <div class="analysis-item-label-wrapper">
            <i class="bi ${icon}"></i>
            <div>
              <label class="analysis-item-label" for="analysis_item_${item.id}">
                ${escapeHtml(item.name)}
              </label>
              ${item.description ? `<div class="analysis-item-desc">${escapeHtml(item.description)}</div>` : ''}
            </div>
          </div>
        </div>
        <div class="analysis-item-input-section">
          <input type="text" 
                 class="form-control" 
                 name="analysis_item_${item.id}" 
                 id="analysis_item_${item.id}"
                 placeholder="テキストを入力"
                 value="${escapeHtml(value)}">
        </div>
      `;
    }
    
    html += `</div>`;
  });
  
  analysisItemsContent.innerHTML = html;
}


// ============================================
// フォーム送信時のバリデーション
// ============================================
const form = document.getElementById('diaryForm');
if (form) {
  form.addEventListener('submit', function(e) {
    const checkbox = document.getElementById('id_add_initial_purchase');
    
    if (checkbox && checkbox.checked) {
      const errors = validateInitialPurchase();
      
      if (errors.length > 0) {
        e.preventDefault();
        alert('入力エラー:\n' + errors.join('\n'));
        return false;
      }
    }
    
    const submitBtn = document.getElementById('submitBtn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>保存中...';
    }
  });
}

function validateInitialPurchase() {
  const errors = [];
  const dateField = document.getElementById('id_initial_purchase_date');
  const priceField = document.getElementById('id_initial_purchase_price');
  const quantityField = document.getElementById('id_initial_purchase_quantity');
  
  if (!dateField.value) {
    errors.push('購入日を入力してください');
  }
  
  const price = parseFloat(priceField.value);
  if (!price || price <= 0) {
    errors.push('購入単価は正の数を入力してください');
  }
  
  const quantity = parseFloat(quantityField.value);
  if (!quantity || quantity <= 0) {
    errors.push('購入数量は正の数を入力してください');
  }
  
  return errors;
}

// ============================================
// 初期化処理
// ============================================
const initialPurchaseCheckboxEl = document.getElementById('id_add_initial_purchase');
if (initialPurchaseCheckboxEl && initialPurchaseCheckboxEl.checked) {
  updateInitialPurchasePreview();
}

// ツールチップの初期化
const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
tooltipTriggerList.map(function (tooltipTriggerEl) {
  return new bootstrap.Tooltip(tooltipTriggerEl);
});

// ============================================
// フローティング保存ボタンの表示制御
// ============================================
const floatingBtn = document.getElementById('floatingSaveBtn');
const formFooter = document.querySelector('.form-footer');

if (floatingBtn && formFooter) {
  let ticking = false;

  function updateFloatingBtnVisibility() {
    const footerRect = formFooter.getBoundingClientRect();
    const windowHeight = window.innerHeight;

    // フッターが画面内に完全に見えている場合はフローティングボタンを非表示
    // それ以外の場合は表示
    if (footerRect.top < windowHeight - 80 && footerRect.bottom > 0) {
      floatingBtn.classList.add('hidden');
    } else {
      floatingBtn.classList.remove('hidden');
    }

    ticking = false;
  }

  function requestTick() {
    if (!ticking) {
      window.requestAnimationFrame(updateFloatingBtnVisibility);
      ticking = true;
    }
  }

  // スクロールイベント（パフォーマンス最適化）
  window.addEventListener('scroll', requestTick, { passive: true });
  window.addEventListener('resize', requestTick, { passive: true });

  // 初回チェック（少し遅延させて正確な位置を取得）
  setTimeout(updateFloatingBtnVisibility, 100);

  // EasyMDEのフルスクリーン時は非表示
  if (typeof easyMDE !== 'undefined') {
    window.easyMDE.codemirror.on('optionChange', function(cm, option) {
      if (option === 'fullScreen') {
        if (cm.getOption('fullScreen')) {
          floatingBtn.classList.add('hidden');
        } else {
          floatingBtn.classList.remove('hidden');
          updateFloatingBtnVisibility();
        }
      }
    });
  }
}
});

// ============================================
// 日記入力テンプレート呼び出し
// ============================================
(function() {
  function setReasonValue(text) {
    if (window.easyMDE) {
      window.easyMDE.value(text);
    }
    var ta = document.getElementById('id_reason');
    if (ta) {
      ta.value = text;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function getReasonValue() {
    if (window.easyMDE) return window.easyMDE.value();
    var ta = document.getElementById('id_reason');
    return ta ? ta.value : '';
  }

  document.addEventListener('DOMContentLoaded', function() {
    var select = document.getElementById('diaryTemplateSelect');
    var applyBtn = document.getElementById('applyTemplateBtn');
    if (!select || !applyBtn) return;

    // 新規作成かどうか（編集時は既存本文を尊重し、記憶テンプレの自動適用はしない）
    var isCreate = window.DIARY_FORM_CONFIG.isCreate;
    var LS_KEY = 'stockdiary:lastTemplateId';

    function applyTemplate(id, opts) {
      opts = opts || {};
      applyBtn.disabled = true;
      return fetch('/diary-templates/api/' + encodeURIComponent(id) + '/', { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function(data) {
          if (data && data.success && data.template) {
            setReasonValue(data.template.body || '');
            try { localStorage.setItem(LS_KEY, String(id)); } catch (e) {}
            if (!opts.silent && window.showNotification) showNotification('success', 'テンプレートを適用しました');
          }
        })
        .catch(function() {
          if (!opts.silent && window.showNotification) showNotification('error', 'テンプレートの読み込みに失敗しました');
        })
        .finally(function() { applyBtn.disabled = false; });
    }

    fetch(window.DIARY_FORM_CONFIG.urls.templateList, { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data || !data.success) return;
        (data.templates || []).forEach(function(t) {
          var opt = document.createElement('option');
          opt.value = t.id;
          opt.textContent = t.title;
          select.appendChild(opt);
        });

        // 既定は空。前回使ったテンプレを記憶していれば、新規かつ未入力時のみ自動適用する。
        if (!isCreate || getReasonValue().trim()) return;
        var lastId = null;
        try { lastId = localStorage.getItem(LS_KEY); } catch (e) {}
        if (lastId && select.querySelector('option[value="' + lastId + '"]')) {
          select.value = lastId;
          applyTemplate(lastId, { silent: true });
        }
      })
      .catch(function() { /* 通信失敗は静かに無視 */ });

    applyBtn.addEventListener('click', function() {
      var id = select.value;
      if (!id) {
        if (window.showNotification) showNotification('error', 'テンプレートを選択してください');
        return;
      }
      if (getReasonValue().trim() &&
          !confirm('現在の入力内容を破棄してテンプレートで置き換えますか？')) {
        return;
      }
      applyTemplate(id);
    });
  });
})();

// ============================================
// 現在株価取得 (DOMContentLoaded の外側に定義 → onclick から確実に参照可能)
// ============================================
window.doFetchCurrentPrice = function(btn) {
  const stockCode = (document.getElementById('id_stock_symbol') || {}).value.trim();
  const priceInput = document.getElementById('id_initial_purchase_price');

  if (!stockCode) {
    console.warn('[fetchPrice] 銘柄コードが空です');
    if (window.showNotification) showNotification('error', '先に銘柄コードを入力してください');
    return;
  }

  console.log('[fetchPrice] 取得開始:', stockCode);
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i><span class="d-none d-sm-inline ms-1">取得中...</span>';

  fetch('/stockdiary/api/stock/info/' + stockCode + '/')
    .then(function(response) { return response.json(); })
    .then(function(data) {
      console.log('[fetchPrice] レスポンス:', data);
      if (data.price != null && priceInput) {
        priceInput.value = parseFloat(data.price).toFixed(2);
        if (window.updateInitialPurchasePreview) updateInitialPurchasePreview();
        priceInput.dispatchEvent(new Event('input'));
        if (window.showNotification) showNotification('success', '現在株価を取得しました');
      } else {
        if (window.showNotification) showNotification('error', data.error || '株価情報が見つかりませんでした');
      }
    })
    .catch(function(error) {
      console.error('[fetchPrice] エラー:', error);
      if (window.showNotification) showNotification('error', '株価の取得に失敗しました');
    })
    .finally(function() {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i><span class="d-none d-sm-inline">現在株価</span>';
    });
};

// HTMLエスケープ関数
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// ============================================
// ウィザードフォーム ナビゲーション (新規作成時のみ)
// ============================================
// ウィザードは新規作成時のみ（編集時は isCreate=false でスキップ）
if (window.DIARY_FORM_CONFIG.isCreate) {
(function() {
  const WIZARD_TOTAL_STEPS = 2;
  let currentWizardStep = 1;

  // ステップを表示する
  function showWizardStep(step) {
    document.querySelectorAll('.wizard-step').forEach(function(el) {
      el.classList.remove('wizard-active');
    });
    document.querySelectorAll('.wizard-step[data-step="' + step + '"]').forEach(function(el) {
      el.classList.add('wizard-active');
    });
    updateWizardProgress(step);
    currentWizardStep = step;

    // Step 2 (投資理由) が表示されたとき、EasyMDE を初期化またはリフレッシュ
    if (step === 2) {
      if (!window.easyMDE) {
        // 要素が visible になってから初期化（寸法計算が正確になるよう rAF で遅延）
        requestAnimationFrame(function() {
          if (window.initReasonEasyMDE) window.initReasonEasyMDE();
        });
      } else {
        window.easyMDE.codemirror.refresh();
      }
    }

    // フォームの先頭にスクロール
    var progressBar = document.getElementById('wizardProgressBar');
    if (progressBar) {
      progressBar.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  // プログレスバーを更新する
  function updateWizardProgress(step) {
    document.querySelectorAll('[data-step-indicator]').forEach(function(el) {
      var num = parseInt(el.dataset.stepIndicator, 10);
      el.classList.remove('wizard-si-active', 'wizard-si-done');
      if (num === step) {
        el.classList.add('wizard-si-active');
        el.querySelector('.wizard-step-dot').textContent = num;
      } else if (num < step) {
        el.classList.add('wizard-si-done');
        el.querySelector('.wizard-step-dot').innerHTML = '<i class="bi bi-check-lg" style="font-size:0.75rem"></i>';
      } else {
        el.querySelector('.wizard-step-dot').textContent = num;
      }
    });
  }

  // Step 1 バリデーション (銘柄名必須)
  function validateStep1() {
    var stockName = document.getElementById('id_stock_name');
    if (!stockName || !stockName.value.trim()) {
      stockName.classList.add('is-invalid');
      var existing = stockName.parentNode.querySelector('.wizard-invalid-feedback');
      if (!existing) {
        var fb = document.createElement('div');
        fb.className = 'invalid-feedback d-block wizard-invalid-feedback';
        fb.textContent = '銘柄名/タイトルを入力してください';
        stockName.parentNode.appendChild(fb);
      }
      stockName.focus();
      return false;
    }
    stockName.classList.remove('is-invalid');
    var fb = stockName.parentNode.querySelector('.wizard-invalid-feedback');
    if (fb) fb.remove();
    return true;
  }

  // 購入フィールドに値があれば hidden チェックボックスを自動セット
  window.wizardAutoSetPurchase = function() {
    var priceField    = document.getElementById('id_initial_purchase_price');
    var quantityField = document.getElementById('id_initial_purchase_quantity');
    var dateField     = document.getElementById('id_initial_purchase_date');
    var checkbox      = document.getElementById('id_add_initial_purchase');
    if (!checkbox) return;

    var hasData = (priceField && priceField.value.trim()) ||
                  (quantityField && quantityField.value.trim());
    checkbox.checked = !!hasData;

    // 日付が未入力なら今日を自動セット
    if (hasData && dateField && !dateField.value) {
      var today = new Date();
      var yyyy  = today.getFullYear();
      var mm    = String(today.getMonth() + 1).padStart(2, '0');
      var dd    = String(today.getDate()).padStart(2, '0');
      dateField.value = yyyy + '-' + mm + '-' + dd;
    }
  };

  // 購入フィールドをクリア
  function clearPurchaseFields() {
    ['id_initial_purchase_price', 'id_initial_purchase_quantity', 'id_initial_purchase_date'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var cb = document.getElementById('id_add_initial_purchase');
    if (cb) cb.checked = false;
    var preview = document.getElementById('initial-purchase-preview');
    if (preview) preview.style.display = 'none';
  }

  // ---- 公開関数 ----
  window.wizardNext = function() {
    if (currentWizardStep === 1 && !validateStep1()) return;
    if (currentWizardStep === 2) window.wizardAutoSetPurchase();
    if (currentWizardStep < WIZARD_TOTAL_STEPS) {
      showWizardStep(currentWizardStep + 1);
    }
  };

  window.wizardBack = function() {
    if (currentWizardStep > 1) {
      showWizardStep(currentWizardStep - 1);
    }
  };

  window.wizardSkip = function() {
    if (currentWizardStep === 2) clearPurchaseFields();
    if (currentWizardStep < WIZARD_TOTAL_STEPS) {
      showWizardStep(currentWizardStep + 1);
    }
  };

  // 購入金額リアルタイム計算 (Step 2 用)
  function updatePurchasePreview() {
    var price    = parseFloat((document.getElementById('id_initial_purchase_price') || {}).value) || 0;
    var quantity = parseFloat((document.getElementById('id_initial_purchase_quantity') || {}).value) || 0;
    var preview  = document.getElementById('initial-purchase-preview');
    var amount   = document.getElementById('initial-purchase-amount');
    if (!preview || !amount) return;
    if (price > 0 && quantity > 0) {
      amount.textContent = (price * quantity).toLocaleString('ja-JP', { maximumFractionDigits: 0 });
      preview.style.display = 'block';
    } else {
      preview.style.display = 'none';
    }
  }

  // ±100株 ボタン
  window.changeQuantity = function(delta) {
    var el = document.getElementById('id_initial_purchase_quantity');
    if (!el) return;
    var current = parseInt(el.value, 10) || 0;
    var next = current + delta;
    if (next < 1) next = 1;
    el.value = next;
    el.dispatchEvent(new Event('input'));
  };

  // showWizardStep のフック: Step 2 表示時に購入日を今日にセット
  var _originalShowStep = showWizardStep;
  showWizardStep = function(step) {
    _originalShowStep(step);
    if (step === 2) {
      var dateField = document.getElementById('id_initial_purchase_date');
      if (dateField && !dateField.value) {
        var today = new Date();
        var yyyy  = today.getFullYear();
        var mm    = String(today.getMonth() + 1).padStart(2, '0');
        var dd    = String(today.getDate()).padStart(2, '0');
        dateField.value = yyyy + '-' + mm + '-' + dd;
      }
    }
  };

  // バリデーションエラーがあるステップを検出して自動ジャンプ
  function detectErrorSteps() {
    var steps = [1, 2];
    var errorSteps = [];
    steps.forEach(function(s) {
      var container = document.querySelector('[data-step="' + s + '"]');
      // span.text-danger は必須フィールドの「*」なので除外し、div のみをエラーと判定
      if (container && container.querySelector('div.text-danger')) {
        errorSteps.push(s);
      }
    });
    return errorSteps;
  }

  function markErrorsOnProgress(errorSteps) {
    errorSteps.forEach(function(s) {
      var indicator = document.querySelector('[data-step-indicator="' + s + '"]');
      if (indicator) {
        indicator.classList.add('wizard-si-error');
        indicator.querySelector('.wizard-step-dot').innerHTML =
          '<i class="bi bi-exclamation-lg" style="font-size:0.75rem"></i>';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function() {
    // バリデーションエラーがあれば該当ステップに自動ジャンプ
    var errorSteps = detectErrorSteps();
    if (errorSteps.length > 0) {
      markErrorsOnProgress(errorSteps);
      showWizardStep(errorSteps[0]);
      // エラーがある残りのステップをプログレスバーでも示す
      var banner = document.createElement('div');
      banner.className = 'alert alert-danger py-2 px-3 mb-3';
      banner.style.fontSize = '0.875rem';
      banner.innerHTML = '<i class="bi bi-exclamation-triangle-fill me-2"></i>' +
        '入力に問題があります。赤いステップを確認してください。';
      var progressBar = document.getElementById('wizardProgressBar');
      if (progressBar) {
        progressBar.insertAdjacentElement('afterend', banner);
      }
    } else {
      // 通常: Step 1 をアクティブに
      showWizardStep(1);
    }

    // 購入フィールドの変化で金額プレビューを更新
    ['id_initial_purchase_price', 'id_initial_purchase_quantity'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', updatePurchasePreview);
    });

    // フォーム送信前にも購入チェックボックスを自動セット
    var form = document.getElementById('diaryForm');
    if (form) {
      form.addEventListener('submit', function() {
        window.wizardAutoSetPurchase();
      }, true);
    }
  });
})();

// ============================================
// ハッシュタグチップパネル
// ============================================
(function() {
  const AXIS_META = {
    theme:          { label: '●テーマ',       color: '#7c3aed' },
    macro:          { label: '●マクロ',       color: '#d97706' },
    capital_policy: { label: '●資本政策',     color: '#16a34a' },
    business_model: { label: '●ビジネス',     color: '#0891b2' },
    risk:           { label: '●リスク',       color: '#dc2626' },
    event:          { label: '●イベント',     color: '#6b7280' },
    custom:         { label: '●ラベル',       color: '#9333ea' },
  };
  const ANALYSIS_PRIMARY   = ['theme', 'macro', 'capital_policy'];
  const ANALYSIS_SECONDARY = ['business_model', 'risk', 'event'];

  function insertHashtag(tag) {
    if (window.easyMDE) {
      const cm = window.easyMDE.codemirror;
      const cursor = cm.getCursor();
      cm.replaceRange('@' + tag + ' ', cursor, cursor);
      cm.focus();
    }
  }

  function buildChipGroup(axis, tags) {
    const meta = AXIS_META[axis] || { label: '●' + axis, color: '#6b7280' };
    const group = document.createElement('div');
    group.className = 'hashtag-chip-group';
    group.dataset.axis = axis;

    const axisLabel = document.createElement('span');
    axisLabel.className = 'hashtag-chip-axis-label';
    axisLabel.style.color = meta.color;
    axisLabel.textContent = meta.label;
    group.appendChild(axisLabel);

    tags.forEach(function(t) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = axis === 'custom' ? 'hashtag-chip label-chip' : 'hashtag-chip';
      btn.style.color = meta.color;
      btn.textContent = '@' + t.tag;
      btn.dataset.tag = t.tag;
      btn.addEventListener('click', function() { insertHashtag(t.tag); });
      group.appendChild(btn);
    });

    return group;
  }

  function renderSuggestedThemes(hashtags) {
    // テーマ軸のキュレーション語を「おすすめテーマ」行に常設表示する。
    // タップで本文に @テーマ を挿入し、保存時に自動でテーマ軸が付与される（昇華）。
    var panel = document.getElementById('suggested-themes-panel');
    var chips = document.getElementById('suggested-themes-chips');
    if (!panel || !chips) return;
    // @ オートコンプリートでも呼べるため、チップは「よく使う数個」だけに絞る
    // （マイラベルと同方針。多すぎると主役の本文を圧迫するため）。
    var MAX_SUGGESTED = 6;
    var themeTags = hashtags
      .filter(function(h) { return h.axis === 'theme'; })
      .sort(function(a, b) { return (b.count || 0) - (a.count || 0); });
    if (themeTags.length === 0) return;
    themeTags.slice(0, MAX_SUGGESTED).forEach(function(t) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hashtag-chip';
      btn.style.color = '#7c3aed';
      btn.textContent = '@' + t.tag;
      btn.dataset.tag = t.tag;
      btn.addEventListener('click', function() { insertHashtag(t.tag); });
      chips.appendChild(btn);
    });
    panel.style.display = '';
  }

  function renderChipPanel(hashtags) {
    const panel = document.getElementById('hashtag-chip-panel');
    const container = document.getElementById('hashtag-chip-groups');
    const labelsContent = document.getElementById('hashtag-my-labels-content');
    const labelsEmpty = document.getElementById('hashtag-my-labels-empty');
    const analysisToggle = document.getElementById('hashtag-analysis-toggle');
    if (!panel || !container) return;

    // custom 軸（マイラベル）と分析タグに分離。
    // マイラベルは @ オートコンプリートでも呼べるため、チップは「よく使う数個」だけに絞る
    // （全ラベルを並べると多すぎて主役の本文を圧迫するため。上限超過分は @ で入力）。
    const MAX_MY_LABELS = 6;
    const customTags = hashtags
      .filter(function(h) { return h.axis === 'custom' && h.count > 0; })
      .sort(function(a, b) { return (b.count || 0) - (a.count || 0); })
      .slice(0, MAX_MY_LABELS);
    const analysisTags = hashtags.filter(function(h) { return h.axis !== 'custom'; });

    // マイラベルセクション
    if (customTags.length > 0) {
      const group = buildChipGroup('custom', customTags);
      if (labelsEmpty) labelsEmpty.style.display = 'none';
      if (labelsContent) labelsContent.appendChild(group);
    } else {
      if (labelsEmpty) labelsEmpty.style.display = '';
    }

    // 投資分析タグセクション（軸ごとにグループ化）
    const byAxis = {};
    analysisTags.forEach(function(h) {
      const ax = h.axis || 'theme';
      if (!byAxis[ax]) byAxis[ax] = [];
      byAxis[ax].push(h);
    });

    ANALYSIS_PRIMARY.forEach(function(ax) {
      if (byAxis[ax] && byAxis[ax].length > 0) {
        container.appendChild(buildChipGroup(ax, byAxis[ax]));
      }
    });
    ANALYSIS_SECONDARY.forEach(function(ax) {
      if (byAxis[ax] && byAxis[ax].length > 0) {
        container.appendChild(buildChipGroup(ax, byAxis[ax]));
      }
    });
    Object.keys(byAxis).forEach(function(ax) {
      if (!ANALYSIS_PRIMARY.includes(ax) && !ANALYSIS_SECONDARY.includes(ax) && byAxis[ax].length > 0) {
        container.appendChild(buildChipGroup(ax, byAxis[ax]));
      }
    });

    // 投資分析タグのトグル
    if (analysisToggle) {
      analysisToggle.addEventListener('click', function() {
        const isOpen = analysisToggle.classList.toggle('open');
        container.style.display = isOpen ? '' : 'none';
      });
    }

    panel.style.display = '';
  }

  document.addEventListener('DOMContentLoaded', function() {
    fetch(window.DIARY_FORM_CONFIG.urls.hashtags + "?limit=200", { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (data && data.success && data.hashtags && data.hashtags.length > 0) {
          renderSuggestedThemes(data.hashtags);
          renderChipPanel(data.hashtags);
        } else if (data && data.success) {
          // ハッシュタグなしでも空状態パネルを表示
          const panel = document.getElementById('hashtag-chip-panel');
          const labelsEmpty = document.getElementById('hashtag-my-labels-empty');
          if (panel) panel.style.display = '';
          if (labelsEmpty) labelsEmpty.style.display = '';
        }
      })
      .catch(function() { /* silently skip */ });
  });
})();

// ========== 新規日記作成の使い方ツアー ==========
document.addEventListener('DOMContentLoaded', function () {
  if (!window.FeatureTour) return;

  var steps = [
    {
      element: '#wizardProgressBar',
      popover: {
        title: '2つのステップで簡単記録',
        description: '「銘柄情報」→「背景」の順に入力していきます。取引は作成後に詳細ページから追加できます。途中の項目は後からでも編集できるので、気軽に進めてみましょう。',
        side: 'bottom',
      },
    },
    {
      element: '#wizardSymbolSearch',
      popover: {
        title: '銘柄コードで自動入力',
        description: '証券コードを入力して「検索」を押すと、会社名や現在の株価・業種などが自動で入力されます。記録だけしたい場合はメモとして自由に使うこともできます。',
        side: 'bottom',
      },
    },
    {
      element: '#wizardStockNameField',
      popover: {
        title: '銘柄名 / タイトル（必須）',
        description: '銘柄名、またはメモとして使う場合はタイトルを入力します。検索を使った場合はここに自動で入力されます。',
        side: 'bottom',
      },
    },
    {
      element: '#wizardStep1Nav',
      popover: {
        title: '入力できたら次へ',
        description: '「次へ」をタップすると次のステップに進みます。購入記録などは任意項目なので、後からまとめて入力することもできます。',
        side: 'top',
      },
    },
  ].filter(function (step) {
    return document.querySelector(step.element);
  });

  FeatureTour.start('diary-create-v1', steps);
});
}
