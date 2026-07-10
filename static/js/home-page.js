/**
 * home-page.js
 * home.html にインラインで書かれていたページ固有スクリプトの外部化。
 * URL は home.html 内の window.HOME_PAGE_CONFIG から受け取る。
 * 記述順（日付見出しグルーピング → HTMX設定ほか → オートコンプリート/PC状態復元）は
 * テンプレート内での元の順序を維持している。
 */

(function () {
  function groupDiaryJournal() {
    var grid = document.getElementById('diary-container');
    if (!grid) return;
    // 既存の見出しを一旦除去（冪等に再計算）
    grid.querySelectorAll('.rp-day-label').forEach(function (el) { el.remove(); });
    var lastMonth = null;
    // article 要素のみを対象（広告・トリガー要素は無視）
    Array.prototype.forEach.call(grid.querySelectorAll('article.diary-article'), function (card) {
      var month = card.getAttribute('data-diary-month');
      var label = card.getAttribute('data-diary-month-label') || month;
      if (!month || month === lastMonth) return;
      lastMonth = month;
      var head = document.createElement('div');
      head.className = 'rp-day-label';
      head.innerHTML = '<span>' + label + '</span>';
      card.parentNode.insertBefore(head, card);
    });
  }
  document.addEventListener('DOMContentLoaded', groupDiaryJournal);
  // HTMX による検索・絞り込み・無限スクロール後にも再構築
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && (e.target.id === 'diary-container' ||
        (e.target.closest && e.target.closest('#diary-container')))) {
      groupDiaryJournal();

      // 検索・絞り込みの後は日記一覧まで自動スクロール（毎回手動で下へ
      // スクロールする手間を解消）。無限スクロール（?page=N の追記）では
      // 発火させない＝読み込み位置に留まる。
      var path = (e.detail && e.detail.pathInfo && e.detail.pathInfo.requestPath) ||
                 (e.detail && e.detail.xhr && e.detail.xhr.responseURL) || '';
      if (!/[?&]page=/.test(path)) {
        var grid = document.getElementById('diary-container');
        if (grid) {
          grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    }
  });

  // サイドバー検索（ネイティブGET送信＝フルリロード）後も、検索・絞り込みが
  // 効いている場合は日記一覧まで自動スクロールする（最上部に戻る手間を解消）。
  document.addEventListener('DOMContentLoaded', function () {
    var p = new URLSearchParams(window.location.search);
    var status = p.get('status');
    var hasFilter = p.get('query') || p.get('tag') || p.get('sector') ||
                    p.get('transaction_date_range') || p.get('disclosure') ||
                    p.get('date_range') || p.get('sort') ||
                    (status && status !== 'active');
    if (hasFilter) {
      var grid = document.getElementById('diary-container');
      if (grid) {
        // レイアウト確定後にスクロール（画像読み込み等での位置ズレを避ける）
        requestAnimationFrame(function () {
          grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
    }
  });
})();


// ── HTMX設定 ──
htmx.config.defaultSwapStyle = "innerHTML";
htmx.config.includeIndicatorStyles = true;
htmx.config.withCredentials = true;

// ── ステータス選択 ──
function setStatusTab(status) {
  document.getElementById('statusFilter').value = status;
  document.querySelectorAll('.status-tab').forEach(function(tab) {
    tab.classList.toggle('active', tab.dataset.status === status);
  });
  // ドロップダウンの表示ラベル・件数を選択中ステータスに同期
  var current = document.querySelector('.status-tab[data-status="' + status + '"]');
  var label = document.getElementById('statusDropdownLabel');
  var count = document.getElementById('statusDropdownCount');
  if (current && label && count) {
    var name = current.querySelector('.status-tab-name');
    var cnt = current.querySelector('.status-tab-count');
    if (name) label.textContent = name.textContent;
    if (cnt) count.textContent = cnt.textContent;
  }
}

document.querySelectorAll('.status-tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    setStatusTab(this.dataset.status);
    htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
  });
});

// ── 直近ハッシュタグチップ ──
document.querySelectorAll('.recent-hashtag-chip').forEach(function(chip) {
  chip.addEventListener('click', function() {
    var hashtag = this.dataset.hashtag || '';
    var hashtagInput = document.getElementById('hashtagFilter');
    if (!hashtagInput) return;
    // 同じチップ再タップでトグル解除
    if (this.classList.contains('is-active')) {
      hashtagInput.value = '';
      this.classList.remove('is-active');
    } else {
      hashtagInput.value = hashtag;
      document.querySelectorAll('.recent-hashtag-chip').forEach(function(c) {
        c.classList.remove('is-active');
      });
      this.classList.add('is-active');
    }
    htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
    setTimeout(updateFilterBadges, 100);
  });
});

// ── ハッシュタグチップの折りたたみトグル（モバイル） ──
var hashtagChipsToggle = document.getElementById('hashtagChipsToggle');
if (hashtagChipsToggle) {
  hashtagChipsToggle.addEventListener('click', function() {
    var chips = document.querySelector('.recent-hashtag-chips');
    if (!chips) return;
    var collapsed = chips.classList.toggle('is-collapsed');
    this.setAttribute('aria-expanded', String(!collapsed));
    this.classList.toggle('active', !collapsed);
  });
}

// ── フィルターパネル ──
var filterPanel = document.getElementById('filterPanel');
var filterOverlay = document.getElementById('filterOverlay');
var filterToggleBtn = document.getElementById('filterToggleBtn');

function openFilterPanel() {
  var sidebar = document.getElementById('homeSidebar');
  if (sidebar && getComputedStyle(sidebar).display !== 'none') return;
  filterPanel.classList.add('open');
  filterOverlay.classList.add('open');
  filterToggleBtn.setAttribute('aria-expanded', 'true');
}

function closeFilterPanel() {
  filterPanel.classList.remove('open');
  filterOverlay.classList.remove('open');
  filterToggleBtn.setAttribute('aria-expanded', 'false');
}

filterToggleBtn.addEventListener('click', openFilterPanel);
filterOverlay.addEventListener('click', closeFilterPanel);
document.getElementById('filterPanelClose').addEventListener('click', closeFilterPanel);

// モーダルフィルター ↔ サイドバーの値を同期するユーティリティ
function syncSidebarFromModal() {
  document.querySelectorAll('#homeSidebar .sidebar-filter').forEach(function(sidebarEl) {
    var targetEl = document.getElementById(sidebarEl.getAttribute('data-target'));
    if (targetEl) sidebarEl.value = targetEl.value;
  });
}

document.getElementById('filterApplyBtn').addEventListener('click', function() {
  closeFilterPanel();
  syncSidebarFromModal();
  htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
});

document.getElementById('filterResetBtn').addEventListener('click', function() {
  ['tagFilter', 'sectorFilter', 'transactionDateRangeFilter', 'dateRangeFilter', 'disclosureFilter', 'earningsFilter'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
  });
  closeFilterPanel();
  syncSidebarFromModal();
  htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
});

// サイドバーフィルター: 変更時に元のフィルターパネルの select を同期してから検索
document.querySelectorAll('#homeSidebar .sidebar-filter').forEach(function(sidebarEl) {
  sidebarEl.addEventListener('change', function() {
    var targetId = sidebarEl.getAttribute('data-target');
    var targetEl = document.getElementById(targetId);
    if (targetEl) targetEl.value = sidebarEl.value;
    htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
    setTimeout(updateFilterBadges, 100);
  });
});

// サイドバーリセット
var sidebarResetBtn = document.getElementById('sidebarResetBtn');
if (sidebarResetBtn) {
  sidebarResetBtn.addEventListener('click', function() {
    document.querySelectorAll('#homeSidebar .sidebar-filter').forEach(function(sidebarEl) {
      sidebarEl.value = '';
      var targetId = sidebarEl.getAttribute('data-target');
      var targetEl = document.getElementById(targetId);
      if (targetEl) targetEl.value = '';
    });
    htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
    setTimeout(updateFilterBadges, 100);
  });
}

// ── 並び順ドロップダウン ──
var sortPanel = document.getElementById('sortPanel');
var sortToggleBtn = document.getElementById('sortToggleBtn');

sortToggleBtn.addEventListener('click', function(e) {
  e.stopPropagation();
  var isOpen = sortPanel.classList.toggle('open');
  sortToggleBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
});

document.addEventListener('click', function(e) {
  if (!sortToggleBtn.contains(e.target) && !sortPanel.contains(e.target)) {
    sortPanel.classList.remove('open');
    sortToggleBtn.setAttribute('aria-expanded', 'false');
  }
});

document.querySelectorAll('.sort-option').forEach(function(option) {
  option.addEventListener('click', function() {
    var value = this.dataset.value;
    var label = this.dataset.label;
    document.getElementById('sortFilter').value = value;
    document.getElementById('sortLabel').textContent = label || '並び順';
    document.querySelectorAll('.sort-option').forEach(function(o) {
      o.classList.remove('active');
    });
    this.classList.add('active');
    sortPanel.classList.remove('open');
    sortToggleBtn.setAttribute('aria-expanded', 'false');
    sortToggleBtn.classList.toggle('active-sort', !!value);
    htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
    setTimeout(updateFilterBadges, 100);
  });
});

// ── フィルターチップ削除 ──
function removeFilter(filterName) {
  var fieldMap = {
    'tag': 'tagFilter',
    'sector': 'sectorFilter',
    'transaction_date_range': 'transactionDateRangeFilter',
    'date_range': 'dateRangeFilter',
    'disclosure': 'disclosureFilter',
    'earnings': 'earningsFilter',
    'hashtag': 'hashtagFilter',
    'sort': 'sortFilter'
  };
  var fieldId = fieldMap[filterName];
  if (fieldId) {
    var el = document.getElementById(fieldId);
    if (el) el.value = '';
  }
  if (filterName === 'sort') {
    document.getElementById('sortLabel').textContent = '並び順';
    sortToggleBtn.classList.remove('active-sort');
    document.querySelectorAll('.sort-option').forEach(function(o) { o.classList.remove('active'); });
  }

  var urlParams = new URLSearchParams(window.location.search);
  urlParams.delete(filterName);
  urlParams.delete('page');

  var newUrl = '/stockdiary/diary-list/?' + urlParams.toString();
  htmx.ajax('GET', newUrl, {
    target: '#diary-container',
    swap: 'innerHTML',
    pushUrl: true
  }).then(function() { updateFilterBadges(); });
}

// ── フィルターバッジ更新 ──
function updateFilterBadges() {
  var container = document.getElementById('activeFilters');
  if (!container) return;
  container.innerHTML = '';
  var count = 0;

  var rangeText = { '1w': '1週間', '1m': '1ヶ月', '3m': '3ヶ月', '6m': '6ヶ月', '1y': '1年' };

  var tagFilter = document.getElementById('tagFilter');
  if (tagFilter && tagFilter.value) {
    var opt = tagFilter.options[tagFilter.selectedIndex];
    addFilterChip('tag', tagFilter.value, 'タグ: ' + (opt ? opt.text : tagFilter.value));
    count++;
  }

  var sectorFilter = document.getElementById('sectorFilter');
  if (sectorFilter && sectorFilter.value) {
    addFilterChip('sector', sectorFilter.value, '業種: ' + sectorFilter.value);
    count++;
  }

  var tdrFilter = document.getElementById('transactionDateRangeFilter');
  if (tdrFilter && tdrFilter.value) {
    addFilterChip('transaction_date_range', tdrFilter.value,
      '売買があった時期: ' + (rangeText[tdrFilter.value] || tdrFilter.value));
    count++;
  }

  var drFilter = document.getElementById('dateRangeFilter');
  if (drFilter && drFilter.value) {
    addFilterChip('date_range', drFilter.value,
      '初回購入の時期: ' + (rangeText[drFilter.value] || drFilter.value));
    count++;
  }

  var disclosureFilter = document.getElementById('disclosureFilter');
  if (disclosureFilter && disclosureFilter.value) {
    var disclosureText = { 'new': '1週間以内', 'recent': '1ヶ月以内' };
    addFilterChip('disclosure', disclosureFilter.value,
      '開示更新: ' + (disclosureText[disclosureFilter.value] || disclosureFilter.value));
    count++;
  }

  var earningsFilter = document.getElementById('earningsFilter');
  if (earningsFilter && earningsFilter.value) {
    addFilterChip('earnings', earningsFilter.value,
      '決算予定: ' + earningsFilter.value + '日以内');
    count++;
  }

  var hashtagFilter = document.getElementById('hashtagFilter');
  if (hashtagFilter && hashtagFilter.value) {
    addFilterChip('hashtag', hashtagFilter.value, '@' + hashtagFilter.value);
    count++;
  }

  container.style.display = count > 0 ? 'flex' : 'none';

  var badge = document.getElementById('filterCountBadge');
  if (badge) {
    badge.textContent = count;
    badge.style.display = count > 0 ? 'inline-flex' : 'none';
    filterToggleBtn.classList.toggle('has-filters', count > 0);
  }

  // 並び順ボタンのラベルを同期
  var sortFilter = document.getElementById('sortFilter');
  var sortLabel = document.getElementById('sortLabel');
  if (sortFilter && sortLabel) {
    var sortText = {
      'earnings_asc': '決算が近い',
      'date_desc': '取引日（新）', 'date_asc': '取引日（古）',
      'name': '銘柄名', 'symbol': 'コード',
      'profit_desc': '損益↑', 'profit_asc': '損益↓',
      'transaction_count_desc': '取引回数↑', 'transaction_count_asc': '取引回数↓',
      'total_cost_desc': '総原価↑', 'total_cost_asc': '総原価↓'
    };
    sortLabel.textContent = sortFilter.value ? sortText[sortFilter.value] || sortFilter.value : '並び順';
    sortToggleBtn.classList.toggle('active-sort', !!sortFilter.value);
    document.querySelectorAll('.sort-option').forEach(function(o) {
      o.classList.toggle('active', o.dataset.value === sortFilter.value);
    });
  }
}

function addFilterChip(filterName, filterValue, displayText) {
  var container = document.getElementById('activeFilters');
  if (!container) return;
  var chip = document.createElement('div');
  chip.className = 'filter-chip';
  chip.setAttribute('data-filter', filterName);
  chip.setAttribute('data-value', filterValue);
  chip.innerHTML = '<span>' + displayText + '</span>' +
    '<button type="button" class="filter-chip-remove" onclick="removeFilter(\'' + filterName + '\')">' +
    '<i class="bi bi-x"></i></button>';
  container.appendChild(chip);
}

// ── 検索リセット ──
function resetSearch() {
  document.getElementById('mainSearchInput').value = '';
  document.getElementById('hashtagFilter').value = '';
  document.getElementById('sortFilter').value = '';
  ['tagFilter', 'sectorFilter', 'transactionDateRangeFilter', 'dateRangeFilter', 'disclosureFilter', 'earningsFilter'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
  });
  setStatusTab('active');
  htmx.ajax('GET', window.HOME_PAGE_CONFIG.urls.diaryList + '?status=active', {
    target: '#diary-container',
    swap: 'innerHTML',
    pushUrl: true
  }).then(function() { updateFilterBadges(); });
}

// ── 初期ロード ──
document.addEventListener('DOMContentLoaded', function() {
  // 初回コンテンツ読み込み
  // ※ 初期表示はサーバー側でレンダリング済み（diary_list.html パーシャルをインクルード）
  // フィルター変更時のHTMXリクエストで動的更新を行う

  // フィルターバッジの初期表示
  setTimeout(updateFilterBadges, 50);

  // オフライン監視
  window.addEventListener('online', function() { document.body.classList.remove('is-offline'); });
  window.addEventListener('offline', function() { document.body.classList.add('is-offline'); });
  if (!navigator.onLine) document.body.classList.add('is-offline');

  // CSRFトークンをHTMXリクエストに追加
  document.body.addEventListener('htmx:configRequest', function(evt) {
    var csrfToken = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (csrfToken) {
      if (!evt.detail.headers) evt.detail.headers = {};
      evt.detail.headers['X-CSRFToken'] = csrfToken.value;
    }
  });

  // HTMX完了後にバッジ更新
  document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'diary-container') {
      setTimeout(updateFilterBadges, 50);
    }
  });

  // フォーム送信後にバッジ更新
  document.getElementById('optimizedSearchForm').addEventListener('submit', function() {
    setTimeout(updateFilterBadges, 100);
  });

  // フィルターパネルの要素の値を optimizedSearchForm に同期
  ['tagFilter', 'sectorFilter', 'transactionDateRangeFilter', 'disclosureFilter', 'dateRangeFilter', 'earningsFilter'].forEach(function(id) {
    var elem = document.getElementById(id);
    if (elem) {
      elem.addEventListener('change', function() {
        // フォーム内の対応する要素の値を更新（存在する場合）
        var formElem = document.querySelector('#optimizedSearchForm [name="' + elem.name + '"]');
        if (formElem) {
          formElem.value = elem.value;
        }
        // フォーム送信
        htmx.trigger(document.getElementById('optimizedSearchForm'), 'submit');
      });
    }
  });

  // クイックノート：タイトル topic-chips UI
  // (詳細ページと同じ方式)

  // PC用サイドバーフォームの自動送信（change イベント時）
  var sidebarForm = document.getElementById('sidebarSearchForm');
  if (sidebarForm) {
    // フォーム送信前に空のパラメータを削除
    sidebarForm.addEventListener('submit', function(e) {
      // フォーム内の全ての input/select を取得
      var inputs = sidebarForm.querySelectorAll('input, select');
      inputs.forEach(function(input) {
        // 空の値の要素を削除
        if (input.value === '' && input.type !== 'hidden') {
          input.disabled = true;  // disabled すると form 送信時に含まれない
        }
      });
    });

    // サイドバーの select/input 要素の change イベントをリッスン
    ['sidebarTagFilter', 'sidebarSectorFilter', 'sidebarTransactionDateRangeFilter', 'sidebarDisclosureFilter', 'sidebarDateRangeFilter', 'sidebarSortFilter'].forEach(function(id) {
      var elem = document.getElementById(id);
      if (elem) {
        elem.addEventListener('change', function() {
          sidebarForm.submit();
        });
      }
    });

    // 検索入力のエンター押下時に送信
    var searchInput = document.getElementById('sidebarSearchInput');
    if (searchInput) {
      searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          sidebarForm.submit();
        }
      });
    }
  }
});

// オフライン時のエラー処理
htmx.on('htmx:beforeRequest', function(evt) {
  if (!navigator.onLine) {
    evt.preventDefault();
    document.body.classList.add('is-offline');
    var target = evt.detail.target;
    if (target) {
      target.innerHTML = '<div class="alert alert-warning m-2"><i class="bi bi-wifi-off me-2"></i>現在オフライン状態です。ネットワーク接続を確認してください。</div>';
    }
  }
});

// クイックノート：タイトル topic-chip クリックハンドラ
window.setQnTopic = function(el, value) {
  console.log('[setQnTopic] Setting topic to:', value);
  var topicEl = document.getElementById('qnTopic');
  if (topicEl) {
    topicEl.value = value;
    console.log('[setQnTopic] Input value set to:', topicEl.value);
  } else {
    console.error('[setQnTopic] qnTopic element not found');
  }
  document.querySelectorAll('.topic-chip').forEach(function(c) { c.classList.remove('active'); });
  if (el) el.classList.add('active');
};

// クイックノート：書き出しのヒント挿入（心理的ハードルを下げる）
window.qnPrompt = function(starter) {
  var ta = document.getElementById('qnContent');
  if (!ta) return;
  if (ta.value.trim() === '') {
    ta.value = starter;
  } else if (!ta.value.endsWith('\n')) {
    ta.value += '\n' + starter;
  } else {
    ta.value += starter;
  }
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  if (typeof updateQnCharCount === 'function') updateQnCharCount();
};

// クイックノート：記録送信
window.qnSubmitNote = function() {
  var noteUrl = document.getElementById('qnNoteUrl').value;
  var content = document.getElementById('qnContent').value.trim();
  var topic = document.getElementById('qnTopic').value.trim();

  console.log('[qnSubmitNote] noteUrl:', noteUrl);
  console.log('[qnSubmitNote] content:', content);
  console.log('[qnSubmitNote] topic:', topic);

  if (!content) {
    alert('記録内容を入力してください');
    return;
  }

  // FormData を使用して POST リクエストを送信
  var formData = new FormData();
  formData.append('content', content);
  formData.append('topic', topic);
  var csrfToken = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  formData.append('csrfmiddlewaretoken', csrfToken);

  console.log('[qnSubmitNote] Sending POST to:', noteUrl);
  console.log('[qnSubmitNote] FormData entries:');
  console.log('  - content:', content);
  console.log('  - topic:', topic);
  console.log('  - csrftoken:', csrfToken ? 'present' : 'MISSING');

  fetch(noteUrl, {
    method: 'POST',
    body: formData
  })
  .then(response => {
    console.log('[qnSubmitNote] Response status:', response.status);
    return response.json();
  })
  .then(data => {
    console.log('[qnSubmitNote] Response data:', data);
    if (data.success) {
      alert(data.message);
      closeBottomSheet('quickNoteFromHomeSheet');
      // フォームをリセット
      document.getElementById('qnTopic').value = '';
      document.getElementById('qnContent').value = '';
      // ページをリロード（日記一覧を更新）
      location.reload();
    } else {
      alert('エラー: ' + data.message);
    }
  })
  .catch(error => {
    console.error('[qnSubmitNote] Error:', error);
    alert('エラーが発生しました');
  });
};

// オフライン時のエラー処理


// ハッシュタグオートコンプリート機能
document.addEventListener('DOMContentLoaded', function() {
  const mainSearchInput = document.getElementById('mainSearchInput');
  const searchSuggestions = document.getElementById('searchSuggestions');
  let hashtagCache = [];
  let selectedIndex = -1;

  // ハッシュタグキャッシュを更新
  async function updateHashtagCache() {
    try {
      const response = await fetch(window.HOME_PAGE_CONFIG.urls.hashtags + '?limit=100');
      const data = await response.json();
      if (data.success) {
        hashtagCache = data.hashtags;
      }
    } catch (error) {
      console.error('Failed to fetch hashtags:', error);
    }
  }

  // 初回ロード時にキャッシュを取得
  updateHashtagCache();

  // 入力イベント
  mainSearchInput.addEventListener('input', function(e) {
    const value = e.target.value;
    const cursorPosition = e.target.selectionStart;

    // カーソル位置から@を探す
    const textBeforeCursor = value.substring(0, cursorPosition);
    const lastAtIndex = textBeforeCursor.lastIndexOf('@');

    if (lastAtIndex !== -1) {
      // @以降の文字列を取得
      const hashtagQuery = textBeforeCursor.substring(lastAtIndex + 1);

      // スペースが含まれていたらオートコンプリートを閉じる
      if (hashtagQuery.includes(' ')) {
        closeSuggestions();
        return;
      }

      // ハッシュタグの候補を表示
      showHashtagSuggestions(hashtagQuery, lastAtIndex);
    } else {
      closeSuggestions();
    }
  });

  // ハッシュタグ候補を表示
  function showHashtagSuggestions(query, hashIndex) {
    const filtered = hashtagCache.filter(tag =>
      tag.tag.toLowerCase().includes(query.toLowerCase())
    ).slice(0, 10);

    if (filtered.length === 0) {
      closeSuggestions();
      return;
    }

    searchSuggestions.innerHTML = filtered.map((tag, index) => `
      <div class="suggestion-item hashtag-suggestion" data-index="${index}" data-tag="${tag.tag}">
        <i class="bi bi-at suggestion-icon"></i>
        <span class="suggestion-text">${tag.tag}</span>
        <span class="suggestion-type">${tag.count}件</span>
      </div>
    `).join('');

    searchSuggestions.style.display = 'block';
    selectedIndex = -1;

    // クリックイベント
    document.querySelectorAll('.hashtag-suggestion').forEach(item => {
      item.addEventListener('click', function() {
        const tagName = this.getAttribute('data-tag');
        insertHashtag(tagName, hashIndex);
      });
    });
  }

  // ハッシュタグを挿入
  function insertHashtag(tagName, atIndex) {
    const value = mainSearchInput.value;
    const beforeAt = value.substring(0, atIndex);
    const afterCursor = value.substring(mainSearchInput.selectionStart);

    // @タグ名 を挿入
    mainSearchInput.value = beforeAt + '@' + tagName + ' ' + afterCursor;

    // カーソル位置を調整
    const newPosition = beforeAt.length + tagName.length + 2;
    mainSearchInput.setSelectionRange(newPosition, newPosition);

    closeSuggestions();
    mainSearchInput.focus();
  }

  // 候補を閉じる
  function closeSuggestions() {
    searchSuggestions.style.display = 'none';
    searchSuggestions.innerHTML = '';
    selectedIndex = -1;
  }

  // キーボード操作
  mainSearchInput.addEventListener('keydown', function(e) {
    const suggestions = document.querySelectorAll('.hashtag-suggestion');

    if (suggestions.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % suggestions.length;
      updateSelection(suggestions);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = selectedIndex <= 0 ? suggestions.length - 1 : selectedIndex - 1;
      updateSelection(suggestions);
    } else if (e.key === 'Enter') {
      if (selectedIndex >= 0 && selectedIndex < suggestions.length) {
        e.preventDefault();
        const tagName = suggestions[selectedIndex].getAttribute('data-tag');
        const textBeforeCursor = mainSearchInput.value.substring(0, mainSearchInput.selectionStart);
        const lastAtIndex = textBeforeCursor.lastIndexOf('@');
        insertHashtag(tagName, lastAtIndex);
      }
    } else if (e.key === 'Escape') {
      closeSuggestions();
    }
  });

  function updateSelection(suggestions) {
    suggestions.forEach((item, index) => {
      if (index === selectedIndex) {
        item.style.backgroundColor = 'var(--primary-100)';
        item.scrollIntoView({ block: 'nearest' });
      } else {
        item.style.backgroundColor = '';
      }
    });
  }

  // 外部クリックで閉じる
  document.addEventListener('click', function(e) {
    if (!mainSearchInput.contains(e.target) && !searchSuggestions.contains(e.target)) {
      closeSuggestions();
    }
  });
});

// タグクリックフィルタリング機能
document.addEventListener('DOMContentLoaded', function() {
  function setupTagClickHandlers() {
    const tagElements = document.querySelectorAll('.diary-tag');
    
    tagElements.forEach(tag => {
      tag.removeEventListener('click', handleTagClick);
      tag.addEventListener('click', handleTagClick);
      tag.style.cursor = 'pointer';
      tag.style.transition = 'all 0.2s ease';
      
      tag.addEventListener('mouseenter', function() {
        this.style.transform = 'scale(1.05)';
        this.style.opacity = '0.8';
      });
      
      tag.addEventListener('mouseleave', function() {
        this.style.transform = 'scale(1)';
        this.style.opacity = '1';
      });
    });
  }
  
  function handleTagClick(event) {
    event.preventDefault();
    event.stopPropagation();
    
    const tagElement = event.currentTarget;
    const tagId = tagElement.getAttribute('data-tag-id');
    const tagName = tagElement.textContent.trim();
    
    if (!tagId) {
      console.warn('タグIDが見つかりません');
      return;
    }
    
    applyTagFilter(tagId, tagName);
  }

  function applyTagFilter(tagId, tagName) {
    const tagFilterSelect = document.getElementById('tagFilter');
    if (tagFilterSelect) {
      tagFilterSelect.value = tagId;
    }

    const searchForm = document.getElementById('optimizedSearchForm');
    if (searchForm) {
      htmx.trigger(searchForm, 'submit');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      showFilterNotification(`タグ「${tagName}」でフィルタリング中`, 'bi-tag-fill');
    }
  }

  function setupHashtagClickHandlers() {
    const hashtagElements = document.querySelectorAll('.diary-hashtag');

    hashtagElements.forEach(hashtag => {
      hashtag.removeEventListener('click', handleHashtagClick);
      hashtag.addEventListener('click', handleHashtagClick);
      hashtag.style.cursor = 'pointer';
      hashtag.style.transition = 'all 0.2s ease';

      hashtag.addEventListener('mouseenter', function() {
        this.style.transform = 'scale(1.05)';
        this.style.opacity = '0.8';
      });

      hashtag.addEventListener('mouseleave', function() {
        this.style.transform = 'scale(1)';
        this.style.opacity = '1';
      });
    });
  }

  function handleHashtagClick(event) {
    event.preventDefault();
    event.stopPropagation();

    const hashtagElement = event.currentTarget;
    const hashtag = hashtagElement.getAttribute('data-hashtag');

    if (!hashtag) {
      console.warn('ハッシュタグが見つかりません');
      return;
    }

    applyHashtagFilter(hashtag);
  }

  function applyHashtagFilter(hashtag) {
    const hashtagInput = document.getElementById('hashtagFilter');
    if (hashtagInput) {
      hashtagInput.value = hashtag;
    }

    const searchForm = document.getElementById('optimizedSearchForm');
    if (searchForm) {
      htmx.trigger(searchForm, 'submit');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      showFilterNotification(`@${hashtag} でフィルタリング中`, 'bi-at');
    }
  }

  function showFilterNotification(label, iconClass) {
    const existingNotification = document.querySelector('.filter-notification');
    if (existingNotification) {
      existingNotification.remove();
    }

    const notification = document.createElement('div');
    notification.className = 'filter-notification';
    notification.innerHTML = `
      <i class="bi ${iconClass || 'bi-tag-fill'} me-2"></i>
      ${label}
    `;

    document.body.appendChild(notification);
    
    setTimeout(() => {
      notification.classList.add('show');
    }, 10);
    
    setTimeout(() => {
      notification.classList.remove('show');
      setTimeout(() => {
        notification.remove();
      }, 300);
    }, 3000);
  }
  
  document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target.id === 'diary-container') {
      setTimeout(setupTagClickHandlers, 100);
      setTimeout(setupHashtagClickHandlers, 100);
    }
  });

  setupTagClickHandlers();
  setupHashtagClickHandlers();
});

// カード高さ制限: はみ出し検出 → グラデーションオーバーレイ表示
// ※ 一覧では理由本文を3行クリップ表示。全文は「詳細を見る」へ誘導
function applyCardTruncation() {
  if (window.innerWidth >= 768) return;
  document.querySelectorAll('.card-body-inner').forEach(inner => {
    const article = inner.closest('.diary-article');
    if (!article) return;
    if (inner.scrollHeight > inner.clientHeight + 2) {
      article.classList.add('card-truncated');
    }
  });
}

document.addEventListener('DOMContentLoaded', applyCardTruncation);
document.body.addEventListener('htmx:afterSwap', function(event) {
  if (event.detail.target.id === 'diary-container') {
    setTimeout(applyCardTruncation, 50);
  }
});

// クイックノート pill ボタンの active 切替
document.addEventListener('click', function(e) {
  if (e.target.matches('.qn-type-btn')) {
    document.querySelectorAll('.qn-type-btn').forEach(function(b) { b.classList.remove('active'); });
    e.target.classList.add('active');
  }
});

// ========== PC左サイドバー 開閉制御 ==========
(function() {
  const sidebar = document.getElementById('homeSidebar');
  const toggleBtn = document.getElementById('sidebarToggleBtn');
  const closeBtn = document.getElementById('sidebarCloseBtn');
  const form = document.getElementById('optimizedSearchForm');
  const STORAGE_KEY = 'sidebarCollapsed';

  if (!sidebar || !form) return;

  const isPCView = window.innerWidth >= 992;

  // 初期状態を restore
  // 既定は展開。折りたたみ既定だと本文が760px固定のため、初回訪問時に
  // 左右の余白が広大な空白になり（1440pxで291px×2、1920pxではさらに広い）、
  // 検索・フィルターも隠れてしまう。ユーザーが明示的に閉じた（'true'）場合のみ
  // 折りたたみ状態を尊重する。
  function restoreState() {
    const isCollapsed = localStorage.getItem(STORAGE_KEY) === 'true';
    if (isCollapsed) {
      collapseSidebar();
    } else {
      expandSidebar();
    }
  }

  // サイドバーを collapse する
  function collapseSidebar() {
    sidebar.classList.add('collapsed');
    // トグルボタン（フィルターを開く）は PC のみ表示。モバイルでは出さない
    if (toggleBtn) toggleBtn.style.display = isPCView ? 'flex' : 'none';
    localStorage.setItem(STORAGE_KEY, 'true');
  }

  // サイドバーを expand する
  function expandSidebar() {
    sidebar.classList.remove('collapsed');
    toggleBtn.style.display = 'none'; // トグルボタンを非表示
    localStorage.setItem(STORAGE_KEY, 'false');
  }

  // トグルボタンのクリックハンドラ（PC ビューのみ）
  if (isPCView && toggleBtn) {
    toggleBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      expandSidebar();
    });
  }

  // close ボタンのクリックハンドラ（PC ビューのみ）
  if (isPCView && closeBtn) {
    closeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      collapseSidebar();
    });
  }

  // よく使う検索プリセット関数
  window.applyQuickSearch = function(preset) {
    const sortInput = form.querySelector('[name="sort"]');
    const noNotesInput = form.querySelector('[name="no_notes"]');

    if (preset === 'recent') {
      sortInput.value = 'date_desc';
      noNotesInput.value = '';
    } else if (preset === 'profit') {
      sortInput.value = 'profit_desc';
      noNotesInput.value = '';
    } else if (preset === 'no_notes') {
      sortInput.value = '';
      noNotesInput.value = '1';
    }

    // フォーム送信
    htmx.trigger(form, 'submit');
  };

  // リセット関数
  document.getElementById('sidebarResetBtn').addEventListener('click', function(e) {
    e.preventDefault();

    // optimizedSearchForm 内のフィールドをリセット
    var queryEl = form.querySelector('[name="query"]');
    var sortEl = form.querySelector('[name="sort"]');
    var noNotesEl = form.querySelector('[name="no_notes"]');
    if (queryEl) queryEl.value = '';
    if (sortEl) sortEl.value = '';
    if (noNotesEl) noNotesEl.value = '';

    // filterPanel 内のフィールドをリセット（tag/sector 等は filterPanel に存在）
    ['tagFilter', 'sectorFilter', 'transactionDateRangeFilter', 'disclosureFilter', 'dateRangeFilter', 'earningsFilter'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });

    // サイドバー検索入力もリセット
    var sidebarQuery = document.getElementById('sidebarSearchInput');
    if (sidebarQuery) sidebarQuery.value = '';

    // フォーム送信
    htmx.trigger(form, 'submit');
  });

  // サイドバー内の select 変更時に自動送信
  const selects = sidebar.querySelectorAll('select');
  selects.forEach(function(select) {
    select.addEventListener('change', function() {
      htmx.trigger(form, 'submit');
    });
  });

  // 検索入力フィールドのEnterキーで検索実行
  const searchInput = document.getElementById('sidebarSearchInput');
  if (searchInput) {
    searchInput.addEventListener('keypress', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        htmx.trigger(form, 'submit');
      }
    });
  }

  // 初期化（PC ビューのみ）
  if (isPCView) {
    restoreState();
  }
})();
