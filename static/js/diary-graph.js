/**
 * diary-graph.js  v3
 * 日記関連グラフ可視化モジュール
 * D3.js v7 を使用した force-directed graph
 *
 * v3 追加機能:
 *   - タグ軸フィルター（テーマ/BM/リスク/資本政策/マクロ/イベント）
 *   - カラーモード: axis（タグ軸で色分け）
 *   - タグハブノードを軸色で描画
 *   - ツールチップ/サイドパネルに軸ラベル表示
 */
(function () {
  'use strict';

  // ==============================
  // 定数
  // ==============================
  const NODE_RADIUS_MIN   = 8;
  const NODE_RADIUS_MAX   = 26;
  // T-B1: タグ（ハブ）を控えめに。以前は最大56で銘柄(最大26)を視覚的に圧倒し、
  // 「タグが出すぎて銘柄が見えない」原因だった。銘柄を主役にするため縮小する。
  const HUB_RADIUS_MIN    = 10;
  const HUB_RADIUS_MAX    = 34;

  const FORCE_LINK_DISTANCE_DEFAULT  = 120;
  const FORCE_LINK_DISTANCE_HUB      = 160;
  const FORCE_CHARGE       = -320;
  const FORCE_COLLISION_MULT = 1.6;

  // 鮮度（継続記録ベース）の閾値（日数）
  const FRESH_DAYS = 14;   // この日数以内に継続記録があれば「最近更新」リング表示
  const STALE_DAYS = 90;   // 保有中でこの日数を超えて記録がなければ退色表示

  // エッジ色
  const EDGE_COLOR = {
    manual:        '#94a3b8',
    tag:           '#a78bfa',
    sector:        '#fb923c',
    hashtag:       '#a78bfa',  // タグと統合（同色）
    mention:       '#f59e0b',
    tag_hierarchy: '#7c3aed',  // 親子タグ（細分タグ）のハブ-ハブ接続
  };

  // タグエッジの方向色（DiaryTagDirection）。追い風(up)=緑 / 向かい風(down)=赤
  const DIR_COLOR = {
    up:   '#16a34a',
    down: '#dc2626',
  };

  // ハブノード色（タグはデフォルト。軸色分けモードでは AXIS_COLORS を使う）
  const HUB_COLOR = {
    tag:     '#7c3aed',
    sector:  '#d97706',
    hashtag: '#7c3aed',  // タグと統合（同色）
  };

  // タグ軸ごとの色（tag_axis_config.py と対応）
  const AXIS_COLORS = {
    theme:          '#7c3aed',
    business_model: '#0891b2',
    risk:           '#dc2626',
    capital_policy: '#16a34a',
    macro:          '#d97706',
    event:          '#6b7280',
  };

  // タグ軸の日本語ラベル
  const AXIS_LABELS = {
    theme:          'テーマ',
    business_model: 'ビジネスモデル',
    risk:           'リスク',
    capital_policy: '資本政策',
    macro:          'マクロ感応',
    event:          'イベント',
  };

  // セクター色パレット
  const SECTOR_PALETTE = [
    '#6366f1','#8b5cf6','#ec4899','#f43f5e','#f97316',
    '#eab308','#22c55e','#14b8a6','#06b6d4','#3b82f6',
    '#84cc16','#a855f7',
  ];

  // ==============================
  // DiaryGraph クラス
  // ==============================
  class DiaryGraph {
    constructor(config) {
      this.apiUrl    = config.apiUrl;
      this.relatedAddUrl    = config.relatedAddUrl || '';
      this.relatedRemoveUrl = config.relatedRemoveUrl || '';
      this.svgEl     = document.getElementById('diary-graph-svg');
      this.loadingEl = document.getElementById('graph-loading');
      this.emptyEl   = document.getElementById('graph-empty');
      this.tooltipEl = document.getElementById('graph-tooltip');
      this.hintEl    = document.getElementById('graph-hint');
      this.legendEl  = document.getElementById('graph-legend');
      this.statsEl   = document.getElementById('graph-stats');
      this.sidePanel = document.getElementById('graph-side-panel');
      this.sidePanelBody  = document.getElementById('side-panel-body');
      this.sidePanelTitle = document.getElementById('side-panel-title');

      this.allNodes  = [];
      this.allEdges  = [];
      this.simulation = null;
      this.svg        = null;
      this.gRoot      = null;
      this.zoomBehavior = null;

      this.currentStatuses  = new Set(['holding']);
      this.currentTag       = '';
      this.currentEdgeModes = new Set(config.defaultEdgeModes || ['tag']);
      this.currentColorMode = 'axis';
      // 軸フィルター（デフォルト: テーマのみ。ユーザーが追加可）
      this.currentAxes = new Set(['theme']);
      // クラスタ面（ハル）表示。ハブごとのメンバー銘柄を薄い面で囲む
      this.showHulls = false;
      // URL 同期で「デフォルトと同じならパラメータを省く」ための既定値
      this._defaults = {
        statuses: new Set(this.currentStatuses),
        modes:    new Set(this.currentEdgeModes),
        axes:     new Set(this.currentAxes),
        color:    this.currentColorMode,
      };
      this._nodeById = new Map();
      this.searchQuery      = '';
      this.sectorColorMap   = {};
      this.focusNodeId      = null;
      this.focusNeighborIds = new Set();
      this.focusDepth       = 2;    // フォーカスの探索深さ（ホップ数・デフォルト2）
      this.focusSeedIds     = [];   // フォーカスの起点ノードid（単一/複数共通）。深さ変更時の再探索に使う
      this.linkSourceId     = null; // リンク作成モードの起点 diary ノードid
      this._isolatedDiaries = [];   // どのエッジにも繋がっていない primary 日記ノード
      this._adj             = null; // 隣接マップ（_buildAdjacency で構築）
      this._searchMatchIds  = [];
      this._searchAutoFocused = null; // 検索の単一一致で自動フォーカスした際の nodeId（クリア時に解除）

      this._init();
    }

    // ==============================
    // 初期化
    // ==============================
    _init() {
      this._loadStateFromUrl();
      this._bindControls();
      this._bindModalControls();
      this._syncStatusCheckboxes();
      this._syncEdgeModeCheckboxes();
      this._syncTagSelect();
      this._syncModalState();
      this._fetchAndRender();
    }

    // ==============================
    // フィルター状態の URL 同期
    //   リロード・ブックマーク・共有でグラフの表示状態を再現できるようにする
    // ==============================
    _loadStateFromUrl() {
      const p = new URLSearchParams(window.location.search);
      const VALID = {
        status: ['holding', 'sold', 'memo'],
        modes:  ['manual', 'tag', 'hashtag', 'sector', 'mention'],
        axes:   ['theme', 'macro', 'business_model', 'capital_policy', 'risk', 'event'],
        color:  ['axis', 'status', 'sector', 'profit'],
      };
      const list = (key, valid) =>
        (p.get(key) || '').split(',').map(s => s.trim()).filter(v => valid.includes(v));

      const st = list('status', VALID.status);
      if (st.length) this.currentStatuses = new Set(st);
      const md = list('modes', VALID.modes);
      if (md.length) this.currentEdgeModes = new Set(md);
      const ax = list('axes', VALID.axes);
      if (ax.length) this.currentAxes = new Set(ax);
      const color = p.get('color');
      if (VALID.color.includes(color)) this.currentColorMode = color;
      const tag = p.get('tag');
      if (tag && /^\d+$/.test(tag)) this.currentTag = tag;
      if (p.get('hull') === '1') this.showHulls = true;
    }

    _syncUrl() {
      const join = s => [...s].sort().join(',');
      const p = new URLSearchParams();
      if (join(this.currentStatuses)  !== join(this._defaults.statuses)) p.set('status', join(this.currentStatuses));
      if (join(this.currentEdgeModes) !== join(this._defaults.modes))    p.set('modes', join(this.currentEdgeModes));
      if (join(this.currentAxes)      !== join(this._defaults.axes))     p.set('axes', join(this.currentAxes));
      if (this.currentColorMode       !== this._defaults.color)          p.set('color', this.currentColorMode);
      if (this.currentTag) p.set('tag', this.currentTag);
      if (this.showHulls) p.set('hull', '1');
      const qs = p.toString();
      window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
    }

    _syncStatusCheckboxes() {
      document.querySelectorAll('.status-filter-check').forEach(cb => {
        cb.checked = this.currentStatuses.has(cb.value);
      });
    }

    _syncTagSelect() {
      const sel = document.getElementById('tagFilter');
      if (sel && this.currentTag) sel.value = this.currentTag;
    }

    _syncEdgeModeCheckboxes() {
      document.querySelectorAll('.edge-mode-check').forEach(cb => {
        cb.checked = this.currentEdgeModes.has(cb.value);
      });
      const unifiedTagCb = document.getElementById('mode-tag-unified');
      if (unifiedTagCb) {
        unifiedTagCb.checked = this.currentEdgeModes.has('tag') || this.currentEdgeModes.has('hashtag');
      }
    }

    // ==============================
    // コントロールイベントバインド
    // ==============================
    _bindControls() {
      document.querySelectorAll('.status-filter-check').forEach(cb => {
        cb.addEventListener('change', () => {
          this.currentStatuses = new Set(
            [...document.querySelectorAll('.status-filter-check')]
              .filter(c => c.checked).map(c => c.value)
          );
          this._fetchAndRender();
        });
      });

      const tagSel = document.getElementById('tagFilter');
      if (tagSel) {
        tagSel.addEventListener('change', e => {
          this.currentTag = e.target.value;
          this._fetchAndRender();
        });
      }

      // 軸フィルターはモーダル (_bindModalControls) で制御

      // タグ統合ボタン（tag + hashtag を同時制御）
      const unifiedTagCb = document.getElementById('mode-tag-unified');
      if (unifiedTagCb) {
        unifiedTagCb.addEventListener('change', () => {
          if (unifiedTagCb.checked) {
            this.currentEdgeModes.add('tag');
            this.currentEdgeModes.add('hashtag');
          } else {
            const otherModes = ['manual', 'sector', 'mention'];
            const hasOther = otherModes.some(m => this.currentEdgeModes.has(m));
            if (!hasOther) { unifiedTagCb.checked = true; return; }
            this.currentEdgeModes.delete('tag');
            this.currentEdgeModes.delete('hashtag');
          }
          this._updateLegend();
          this._fetchAndRender();
        });
      }

      // その他のエッジモード（手動・業種・コード参照）
      document.querySelectorAll('.edge-mode-check').forEach(cb => {
        cb.addEventListener('change', () => {
          const checked = [...document.querySelectorAll('.edge-mode-check')].filter(c => c.checked);
          const tagUnifiedOn = unifiedTagCb && unifiedTagCb.checked;
          if (checked.length === 0 && !tagUnifiedOn) {
            cb.checked = true;
            return;
          }
          this.currentEdgeModes = new Set(checked.map(c => c.value));
          if (tagUnifiedOn) {
            this.currentEdgeModes.add('tag');
            this.currentEdgeModes.add('hashtag');
          }
          this._updateLegend();
          this._fetchAndRender();
        });
      });

      const searchInput = document.getElementById('graphSearch');
      if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener('input', e => {
          clearTimeout(debounceTimer);
          debounceTimer = setTimeout(() => {
            this.searchQuery = e.target.value.trim().toLowerCase();
            this._applySearch();
            this._maybeAutoFocusSearch();   // 単一一致なら自動でその銘柄へ飛ぶ
          }, 200);
        });
      }

      const searchClear = document.getElementById('graphSearchClear');
      if (searchClear) {
        searchClear.addEventListener('click', () => {
          const inp = document.getElementById('graphSearch');
          if (inp) inp.value = '';
          this.searchQuery = '';
          this._applySearch();
          this._maybeAutoFocusSearch();     // クリアで自動フォーカスを解除
        });
      }

      // 色モードはモーダル (_bindModalControls) で制御

      const resetBtn = document.getElementById('resetZoom');
      if (resetBtn) {
        resetBtn.addEventListener('click', () => this._resetZoom());
      }

      const closeBtn = document.getElementById('side-panel-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this._closeSidePanel());
      }

      // 凡例の開閉トグル。スマホでは既定で折りたたみ（ノードを覆わないように）
      const legendToggle = document.getElementById('legend-toggle');
      if (legendToggle && this.legendEl) {
        if (window.matchMedia('(max-width: 640px)').matches) {
          this.legendEl.classList.add('glf-collapsed');
          legendToggle.setAttribute('aria-expanded', 'false');
        }
        legendToggle.addEventListener('click', () => {
          const collapsed = this.legendEl.classList.toggle('glf-collapsed');
          legendToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        });
      }

      const fsBtn = document.getElementById('toggleFullscreen');
      if (fsBtn) {
        fsBtn.addEventListener('click', () => this._toggleFullscreen());
      }
      const exitFsBtn = document.getElementById('exitFullscreen');
      if (exitFsBtn) {
        exitFsBtn.addEventListener('click', () => this._toggleFullscreen(false));
      }
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          if (this._isFullscreen) this._toggleFullscreen(false);
          if (this.focusNodeId) this._exitFocusMode();
          if (this.linkSourceId) this._exitLinkMode();
        }
      });

      const focusExitBtn = document.getElementById('focus-banner-exit');
      if (focusExitBtn) {
        focusExitBtn.addEventListener('click', () => this._exitFocusMode());
      }

      const linkCancelBtn = document.getElementById('link-banner-cancel');
      if (linkCancelBtn) {
        linkCancelBtn.addEventListener('click', () => this._exitLinkMode());
      }

      // 未接続銘柄バッジ → 一覧パネル
      const isolatedBtn = document.getElementById('stats-isolated');
      if (isolatedBtn) {
        isolatedBtn.addEventListener('click', () => this._openIsolatedPanel());
      }

      // 深さセレクタ（フォーカス中の探索ホップ数をライブ変更）
      const depthSel = document.getElementById('focus-depth-select');
      if (depthSel) {
        depthSel.addEventListener('change', () => {
          this.focusDepth = parseInt(depthSel.value, 10) || 2;
          if (this.focusSeedIds.length > 0) {
            const n = this._applyFocus().size;
            const countEl = document.querySelector('#focus-mode-banner #focus-banner-count');
            if (countEl) countEl.textContent = `${n} ノード表示中`;
          }
        });
      }

      const searchFocusBtn = document.getElementById('search-focus-btn');
      if (searchFocusBtn) {
        searchFocusBtn.addEventListener('click', () => {
          if (this._searchMatchIds && this._searchMatchIds.length > 0) {
            const ids = [...this._searchMatchIds];
            const inp = document.getElementById('graphSearch');
            if (inp) inp.value = '';
            this.searchQuery = '';
            this._applySearch();
            this._enterMultiFocusMode(ids);
          }
        });
      }
      // ウィンドウリサイズ時（端末回転など）はセンター追従のみ。手動 zoom/pan は維持
      window.addEventListener('resize', () => {
        if (this._resizeTimer) clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => this._handleResize(false), 150);
      });
    }

    // ==============================
    // モーダル設定コントロール
    // ==============================
    _bindModalControls() {
      const AXIS_COLOR = {
        theme: '#7c3aed', macro: '#d97706', business_model: '#0891b2',
        capital_policy: '#16a34a', risk: '#dc2626', event: '#6b7280',
      };

      // 軸カード: クリックでトグル（data-axis を持つカードのみ。ハルカードは除外）
      document.querySelectorAll('.gs-axis-card[data-axis]').forEach(card => {
        card.addEventListener('click', () => {
          const axis = card.dataset.axis;
          if (this.currentAxes.has(axis)) {
            if (this.currentAxes.size <= 1) return; // 最低1つは残す
            this.currentAxes.delete(axis);
          } else {
            this.currentAxes.add(axis);
          }
          this._syncModalState();
          this._fetchAndRender();
        });
      });

      // 軸: すべて表示
      const allBtn = document.getElementById('gs-axis-all');
      if (allBtn) {
        allBtn.addEventListener('click', () => {
          this.currentAxes = new Set(['theme','macro','business_model','capital_policy','risk','event']);
          this._syncModalState();
          this._fetchAndRender();
        });
      }

      // 軸: テーマのみ
      const noneBtn = document.getElementById('gs-axis-none');
      if (noneBtn) {
        noneBtn.addEventListener('click', () => {
          this.currentAxes = new Set(['theme']);
          this._syncModalState();
          this._fetchAndRender();
        });
      }

      // 色モードカード: クリックで選択
      document.querySelectorAll('.gs-color-card').forEach(card => {
        card.addEventListener('click', () => {
          this.currentColorMode = card.dataset.mode;
          this._syncModalState();
          this._applyColorMode();
          this._updateLegend();
          this._syncUrl();
        });
      });

      // クラスタ面（ハル）トグル
      const hullCard = document.getElementById('gs-hull-card');
      if (hullCard) {
        hullCard.addEventListener('click', () => {
          this.showHulls = !this.showHulls;
          this._syncModalState();
          this._syncUrl();
          this._updateHulls();
        });
      }
    }

    // モーダル内UIをcurrentAxes/currentColorModeに合わせる
    _syncModalState() {
      const AXIS_COLOR = {
        theme: '#7c3aed', macro: '#d97706', business_model: '#0891b2',
        capital_policy: '#16a34a', risk: '#dc2626', event: '#6b7280',
      };

      document.querySelectorAll('.gs-axis-card[data-axis]').forEach(card => {
        const axis   = card.dataset.axis;
        const active = this.currentAxes.has(axis);
        const color  = AXIS_COLOR[axis] || '#7c3aed';
        card.classList.toggle('gs-active', active);
        // border色を設定
        card.style.borderColor = active ? color : '';

        const pill = card.querySelector('.gs-axis-pill');
        if (pill) {
          if (active) {
            pill.textContent = '表示中';
            pill.style.background = color + '22';
            pill.style.color = color;
            pill.style.borderColor = color + '55';
            pill.classList.remove('gs-axis-pill-off');
          } else {
            pill.textContent = '非表示';
            pill.style.background = '';
            pill.style.color = '';
            pill.style.borderColor = '';
            pill.classList.add('gs-axis-pill-off');
          }
        }

        const icon = card.querySelector('.gs-check-icon');
        if (icon) {
          icon.className = active
            ? 'bi bi-check-circle-fill gs-check-icon'
            : 'bi bi-circle gs-check-icon';
          icon.style.color   = color;
          icon.style.opacity = active ? '1' : '0.3';
        }
      });

      // 色モードカード
      document.querySelectorAll('.gs-color-card').forEach(card => {
        card.classList.toggle('gs-active', card.dataset.mode === this.currentColorMode);
      });

      // クラスタ面（ハル）カード
      const hullCard = document.getElementById('gs-hull-card');
      if (hullCard) {
        const on = this.showHulls;
        hullCard.classList.toggle('gs-active', on);
        hullCard.style.borderColor = on ? '#0891b2' : '';
        const pill = document.getElementById('gs-hull-pill');
        if (pill) {
          pill.textContent = on ? '表示中' : '非表示';
          pill.classList.toggle('gs-axis-pill-off', !on);
          pill.style.background = on ? '#0891b222' : '';
          pill.style.color = on ? '#0891b2' : '';
          pill.style.borderColor = on ? '#0891b255' : '';
        }
        const icon = document.getElementById('gs-hull-icon');
        if (icon) {
          icon.className = on
            ? 'bi bi-check-circle-fill gs-check-icon'
            : 'bi bi-circle gs-check-icon';
          icon.style.opacity = on ? '1' : '0.3';
        }
      }

      this._updateSettingsBadge();
    }

    _updateSettingsBadge() {
      const badge = document.getElementById('settings-summary-badge');
      if (!badge) return;
      const AXIS_LABEL = {
        theme: 'テーマ', macro: 'マクロ', business_model: 'BM',
        capital_policy: '資本政策', risk: 'リスク', event: 'イベント',
      };
      const n = this.currentAxes.size;
      const total = 6;
      let text, bg;
      if (n === total) {
        text = '全グループ';
        bg   = '#6b7280';
      } else if (n === 1) {
        const axis = [...this.currentAxes][0];
        text = AXIS_LABEL[axis] || axis;
        const colors = {theme:'#7c3aed',macro:'#d97706',business_model:'#0891b2',capital_policy:'#16a34a',risk:'#dc2626',event:'#6b7280'};
        bg = colors[axis] || '#7c3aed';
      } else {
        text = `${n}グループ`;
        bg   = '#3b82f6';
      }
      badge.textContent = text;
      badge.style.background = bg;
      badge.style.display = 'inline';
    }

    // ==============================
    // データ取得 → 描画
    // ==============================
    async _fetchAndRender() {
      this._showLoading();
      this._closeSidePanel();
      this.focusNodeId = null;
      this.focusNeighborIds = new Set();
      this.focusSeedIds = [];
      this._hideFocusBanner();
      this._exitLinkMode();
      this._syncUrl();

      if (this.currentStatuses.size === 0) {
        this._showEmpty();
        return;
      }
      const params = new URLSearchParams();
      params.set('status', [...this.currentStatuses].join(','));
      if (this.currentTag) {
        params.set('tag', this.currentTag);
      }
      params.set('edge_modes', [...this.currentEdgeModes].join(','));
      if (this.currentAxes.size > 0 && this.currentAxes.size < 6) {
        params.set('axes', [...this.currentAxes].join(','));
      }

      try {
        const resp = await fetch(`${this.apiUrl}?${params.toString()}`, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        this.allNodes = data.nodes || [];
        this.allEdges = data.edges || [];

        // T-B2: 1銘柄しか繋がっていないタグ（葉タグ）は銘柄同士を橋渡ししない＝
        // 関連を1つも作らないノイズ。視認性のため除外する（関連の連鎖は不変）。
        this._pruneLeafTags();

        // 孤立ノード（どのエッジにも接続していない）を除外。
        // 除外した primary 日記は「未接続銘柄」として控え、バッジから一覧できるようにする
        const connectedIds = new Set();
        this.allEdges.forEach(e => {
          connectedIds.add(String(e.source));
          connectedIds.add(String(e.target));
        });
        this._isolatedDiaries = this.allNodes.filter(n =>
          !connectedIds.has(String(n.id)) && n.node_type === 'diary' && n.is_primary
        );
        this.allNodes = this.allNodes.filter(n => connectedIds.has(String(n.id)));

        // id → ノードの索引（カラーモード適用・パネル遷移などで使い回す）
        this._nodeById = new Map(this.allNodes.map(n => [String(n.id), n]));

        // 隣接マップを1回だけ構築（フォーカスの BFS で使い回す）
        this._buildAdjacency();

        if (this.allEdges.length === 0 || this.allNodes.length === 0) {
          this._showEmpty();
          return;
        }

        this._buildSectorColorMap();
        this._renderGraph();
        this._updateStats(data.meta);
        this._updateLegend();
      } catch (err) {
        console.error('グラフデータ取得エラー:', err);
        this._showError(err.message);
      }
    }

    // ==============================
    // セクターカラーマップ構築
    // ==============================
    _buildSectorColorMap() {
      const sectors = [...new Set(
        this.allNodes
          .filter(n => n.node_type === 'diary')
          .map(n => n.sector || '未分類')
      )];
      this.sectorColorMap = {};
      sectors.forEach((s, i) => {
        this.sectorColorMap[s] = SECTOR_PALETTE[i % SECTOR_PALETTE.length];
      });
    }

    // 関連の希少度（weight: 0〜1, 少数銘柄しか結ばない関連ほど大）を
    // 太さ・濃さに反映する。汎用タグの接続は細く薄く、希少な関連は太く濃く。
    _edgeWidth(d)   {
      const w = typeof d.weight === 'number' ? Math.max(0, Math.min(d.weight, 1)) : 0.5;
      return +(1 + w * 1.6).toFixed(2);
    }
    _edgeOpacity(d) {
      const w = typeof d.weight === 'number' ? Math.max(0, Math.min(d.weight, 1)) : 0.5;
      return +(0.3 + w * 0.45).toFixed(2);
    }

    // ==============================
    // D3 グラフ描画
    // ==============================
    _renderGraph() {
      if (this.simulation) {
        this.simulation.stop();
        this.simulation = null;
      }
      this.svgEl.innerHTML = '';

      const svgEl = this.svgEl;
      const width  = svgEl.clientWidth || svgEl.parentElement.clientWidth || 800;
      const height = svgEl.clientHeight || 600;

      const diaryNodes = this.allNodes.filter(n => n.node_type === 'diary');
      const maxLinks = Math.max(...diaryNodes.map(n => n.link_count || 0), 1);
      const radiusScale = d3.scaleLinear()
        .domain([0, maxLinks])
        .range([NODE_RADIUS_MIN, NODE_RADIUS_MAX])
        .clamp(true);

      const hubNodes = this.allNodes.filter(n => n.node_type !== 'diary');
      const hubMaxLinks = Math.max(...hubNodes.map(n => n.link_count || 0), 1);
      const hubRadiusScale = d3.scaleLinear()
        .domain([0, hubMaxLinks])
        .range([HUB_RADIUS_MIN, HUB_RADIUS_MAX])
        .clamp(true);

      const svg = d3.select(svgEl);
      this.svg = svg;

      const g = svg.append('g').attr('class', 'graph-root');
      this.gRoot = g;

      // クラスタ面（ハル）レイヤー。最初に append してエッジ・ノードの背面に置く
      this.hullGroup = g.append('g').attr('class', 'graph-hulls');

      // ユーザーが手動でズーム・パン操作したかを記録するフラグ
      this._userHasInteracted = false;
      // 自動フィットを一度だけ実行するためのフラグ（多重フィットによる「ガクつき」防止）
      this._hasAutoFitted = false;

      this.zoomBehavior = d3.zoom()
        .scaleExtent([0.1, 6])
        .on('zoom', event => {
          g.attr('transform', event.transform);
          // sourceEvent がある = ユーザー操作（プログラム制御の transform 変更は除外）
          if (event.sourceEvent) this._userHasInteracted = true;
        });
      svg.call(this.zoomBehavior);

      // パン・スクロールと単純クリックを区別（mouse / touch 両対応）
      let _ptrStart = null;
      svg.on('pointerdown.clickguard', event => {
        _ptrStart = [event.clientX, event.clientY];
      });
      svg.on('pointercancel.clickguard', () => {
        // ブラウザがページスクロールを引き継いだ場合 → 次の click でパネルを閉じない
        if (_ptrStart) _ptrStart = [-9999, -9999];
      });
      svg.on('click', event => {
        // pointerdown から 4px 以上動いていたら移動操作 → パネルを閉じない
        const moved = _ptrStart && (
          Math.abs(event.clientX - _ptrStart[0]) > 4 ||
          Math.abs(event.clientY - _ptrStart[1]) > 4
        );
        _ptrStart = null;
        if (moved) return;
        this._hideTooltip();
        this._closeSidePanel();
        if (this.focusNodeId) this._exitFocusMode();
      });

      // ノードは間引かない（ハブも全表示）。d3 が x/y や source/target を
      // 書き換えるため、元データを汚さないようコピーを渡す。
      const nodes = this.allNodes.map(d => ({ ...d }));
      const edges = this.allEdges.map(d => ({ ...d }));

      const hasHubMode = this.currentEdgeModes.has('tag') || this.currentEdgeModes.has('sector') || this.currentEdgeModes.has('hashtag');
      const linkDist  = hasHubMode ? FORCE_LINK_DISTANCE_HUB : FORCE_LINK_DISTANCE_DEFAULT;

      this.simulation = d3.forceSimulation(nodes)
        .force('link',
          d3.forceLink(edges).id(d => d.id).distance(linkDist)
        )
        .force('charge', d3.forceManyBody().strength(FORCE_CHARGE))
        .force('center',  d3.forceCenter(width / 2, height / 2))
        // 中央への弱い引力。レイアウトが横長・縦長に広がりすぎるのを抑え、
        // 自動フィット時にノードが豆粒化しないよう全体をまとめる。
        // 縦は画面が短いので少し強めに引く。
        .force('x', d3.forceX(width / 2).strength(0.05))
        .force('y', d3.forceY(height / 2).strength(0.08))
        .force('collision',
          d3.forceCollide().radius(d => {
            if (d.node_type !== 'diary') {
              return hubRadiusScale(d.link_count || 0) * 1.4;
            }
            return radiusScale(d.link_count || 0) * FORCE_COLLISION_MULT;
          })
        );

      // クラスタ面（ハル）用: ハブごとのメンバー日記ノード参照を構築。
      // forceLink 初期化後は edges の source/target がノードオブジェクトになる
      const clusterByHub = new Map();
      edges.forEach(e => {
        const s = e.source, t = e.target;
        let hub = null, member = null;
        if (t && t.node_type && t.node_type !== 'diary') { hub = t; member = s; }
        else if (s && s.node_type && s.node_type !== 'diary') { hub = s; member = t; }
        if (!hub || !member || member.node_type !== 'diary') return;
        if (!clusterByHub.has(hub.id)) {
          clusterByHub.set(hub.id, { id: hub.id, hub, members: [] });
        }
        const c = clusterByHub.get(hub.id);
        if (!c.members.includes(member)) c.members.push(member);
      });
      this._clusters = [...clusterByHub.values()].filter(c => c.members.length >= 2);

      // エッジ。希少な関連を太く濃く（_edgeWidth/_edgeOpacity 参照）
      const linkSel = g.append('g').attr('class', 'links')
        .selectAll('path')
        .data(edges)
        .join('path')
          .attr('class', d => {
            // タグ/ハッシュタグエッジは方向（追い風up/向かい風down）を
            // クラスで付与し、CSS で確実に色・破線を上書きする（エッジ種別の
            // デフォルト dasharray に負けないよう CSS 側を !important に）。
            let cls = `graph-link edge-${d.edge_type || 'manual'}`;
            const dirable = d.edge_type === 'tag' || d.edge_type === 'hashtag';
            if (dirable && (d.direction === 'up' || d.direction === 'down')) {
              cls += ` dir-${d.direction}`;
            }
            return cls;
          })
          .attr('stroke', d => {
            // タグ/ハッシュタグエッジは方向（追い風/向かい風）で着色。中立・他モードは従来色
            const dirable = d.edge_type === 'tag' || d.edge_type === 'hashtag';
            if (dirable && DIR_COLOR[d.direction]) return DIR_COLOR[d.direction];
            return EDGE_COLOR[d.edge_type] || EDGE_COLOR.manual;
          })
          .attr('stroke-width',   d => this._edgeWidth(d))
          .attr('stroke-opacity', d => this._edgeOpacity(d));
      // トグル変更時に再描画せず強調度だけ更新するため保持
      this.linkSel = linkSel;

      // クリック用の透明ヒットライン（細い線でもタップしやすく）。
      // クリックでエッジの根拠（なぜ繋がっているか）をサイドパネルに表示する
      const linkHitSel = g.append('g').attr('class', 'links-hit')
        .selectAll('path')
        .data(edges)
        .join('path')
          .attr('class', 'graph-link-hit')
          .on('mouseenter', (event, d) => {
            linkSel.classed('highlighted', l => l === d);
          })
          .on('mouseleave', () => {
            linkSel.classed('highlighted', false);
          })
          .on('click', (event, d) => {
            event.stopPropagation();
            this._openEdgePanel(d);
          });
      this.linkHitSel = linkHitSel;

      // ノードグループ
      const self = this;
      const nodeSel = g.append('g').attr('class', 'nodes')
        .selectAll('g')
        .data(nodes)
        .join('g')
          .attr('class', d => {
            if (d.node_type === 'tag')     return 'graph-node node-hub node-tag';
            if (d.node_type === 'sector')  return 'graph-node node-hub node-sector';
            if (d.node_type === 'hashtag') return 'graph-node node-hub node-hashtag';
            return `graph-node status-${d.status}`;
          })
          .attr('data-id',   d => String(d.id))
          .attr('data-name', d => (d.stock_name || d.tag_name || d.sector_name || '').toLowerCase())
          .call(
            d3.drag()
              .on('start', (event, d) => {
                if (!event.active) self.simulation.alphaTarget(0.3).restart();
                d.fx = d.x; d.fy = d.y;
              })
              .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
              .on('end',  (event, d) => {
                if (!event.active) self.simulation.alphaTarget(0);
                // ドラッグ位置にピン留め。クラスで固定中を示す
                d3.select(event.sourceEvent.target.closest('.graph-node')).classed('pinned', true);
              })
          );

      // 形状描画
      nodeSel.each(function(d) {
        const el = d3.select(this);
        if (d.node_type === 'diary') {
          const r = radiusScale(d.link_count || 0);
          d._r = r;  // ハル描画時の余白計算に使う
          el.append('circle')
            .attr('r', r)
            .classed('secondary-node', !d.is_primary);
          // 鮮度: 最近継続記録を書いた銘柄はリング、放置中の保有銘柄は退色
          const fresh = _freshness(d);
          if (fresh === 'fresh') {
            el.append('circle')
              .attr('class', 'node-fresh-ring')
              .attr('r', r + 3.5);
          } else if (fresh === 'stale') {
            el.classed('stale', true);
          }
          // 売却済みで振り返りノートが未記入なら右上にマーカー
          if (_needsRetrospective(d)) {
            const off = r * 0.72 + 2;
            el.append('circle')
              .attr('class', 'retro-marker')
              .attr('cx', off).attr('cy', -off)
              .attr('r', 4.5);
          }
          // 銘柄コードをノード内にアイコン的に表示（十分な大きさのノードのみ）
          if (r >= 13 && d.stock_symbol) {
            el.append('text')
              .attr('class', 'node-code')
              .attr('text-anchor', 'middle')
              .attr('dy', '0.34em')
              .style('font-size', Math.min(r * 0.62, 11).toFixed(1) + 'px')
              .text(d.stock_symbol);
          }
        } else if (d.node_type === 'tag') {
          const r = hubRadiusScale(d.link_count || 0);
          d._r = r;
          const axisColor = AXIS_COLORS[d.axis] || HUB_COLOR.tag;
          el.append('polygon')
            .attr('points', _hexPoints(r))
            .attr('fill', axisColor)
            .attr('stroke', 'white')
            .attr('stroke-width', 2)
            .attr('data-axis-color', axisColor);
        } else if (d.node_type === 'sector') {
          const r  = hubRadiusScale(d.link_count || 0);
          d._r = r;
          const s  = r * 1.5;
          el.append('rect')
            .attr('x', -s / 2).attr('y', -s / 2)
            .attr('width', s).attr('height', s)
            .attr('rx', 5).attr('ry', 5)
            .attr('fill', HUB_COLOR.sector)
            .attr('stroke', 'white')
            .attr('stroke-width', 2);
        } else if (d.node_type === 'hashtag') {
          const r = hubRadiusScale(d.link_count || 0);
          d._r = r;
          el.append('polygon')
            .attr('points', _hexPoints(r))
            .attr('fill', AXIS_COLORS[d.axis] || HUB_COLOR.hashtag)
            .attr('stroke', 'white')
            .attr('stroke-width', 2);
        }
      });

      // ラベル
      nodeSel.append('text')
        .attr('dy', d => {
          if (d.node_type !== 'diary') return hubRadiusScale(d.link_count || 0) + 14;
          return radiusScale(d.link_count || 0) + 13;
        })
        .attr('text-anchor', 'middle')
        .attr('class', 'graph-label')
        .text(d => {
          if (d.node_type === 'hashtag') {
            const n = d.tag_name || '';
            return '@' + (n.length > 7 ? n.substring(0, 7) + '…' : n);
          }
          if (d.node_type === 'tag') {
            const n = d.tag_name || '';
            return n.length > 8 ? n.substring(0, 8) + '…' : n;
          }
          if (d.node_type === 'sector') {
            const n = d.sector_name || '';
            return n.length > 8 ? n.substring(0, 8) + '…' : n;
          }
          const name = d.stock_name || '';
          return name.length > 8 ? name.substring(0, 8) + '…' : name;
        });

      // ホバー時の隣接ハイライト用マップを構築
      const neighborMap = new Map();
      nodes.forEach(n => neighborMap.set(String(n.id), new Set()));
      edges.forEach(e => {
        const s = String(typeof e.source === 'object' ? e.source.id : e.source);
        const t = String(typeof e.target === 'object' ? e.target.id : e.target);
        if (neighborMap.has(s)) neighborMap.get(s).add(t);
        if (neighborMap.has(t)) neighborMap.get(t).add(s);
      });

      // イベント
      nodeSel
        .on('mouseenter', (event, d) => {
          this._showTooltip(event, d);
          const id = String(d.id);
          const neighbors = neighborMap.get(id) || new Set();
          // 隣接ノードをハイライト、それ以外を薄く
          nodeSel.classed('neighbor-dim', n => {
            const nid = String(n.id);
            return nid !== id && !neighbors.has(nid);
          });
          nodeSel.classed('neighbor-highlight', n => {
            const nid = String(n.id);
            return nid !== id && neighbors.has(nid);
          });
          // 接続エッジをハイライト
          linkSel.classed('highlighted', e => {
            const s = String(typeof e.source === 'object' ? e.source.id : e.source);
            const t = String(typeof e.target === 'object' ? e.target.id : e.target);
            return s === id || t === id;
          });
          linkSel.classed('dimmed', e => {
            const s = String(typeof e.source === 'object' ? e.source.id : e.source);
            const t = String(typeof e.target === 'object' ? e.target.id : e.target);
            return s !== id && t !== id;
          });
        })
        .on('mousemove', event => { this._moveTooltip(event); })
        .on('mouseleave', () => {
          this._hideTooltip();
          nodeSel.classed('neighbor-dim', false).classed('neighbor-highlight', false);
          linkSel.classed('highlighted', false).classed('dimmed', false);
        })
        .on('dblclick', (event, d) => {
          // ダブルクリックでピン留め解除
          event.stopPropagation();
          d.fx = null; d.fy = null;
          d3.select(event.currentTarget).classed('pinned', false);
          self.simulation.alphaTarget(0.1).restart();
          setTimeout(() => self.simulation.alphaTarget(0), 500);
        })
        .on('click', (event, d) => {
          event.stopPropagation();
          if (this.linkSourceId) {
            this._handleLinkTargetClick(d);
            return;
          }
          this._openSidePanel(d);
        });

      // tick
      // エッジは緩い弧（Q曲線）で描く。直線だと交差・平行が重なって見づらいため、
      // 中点を法線方向にわずかにずらして視覚的に分離する（B: ラインの重なり低減）。
      const linkPath = d => {
        const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
        const dx = tx - sx, dy = ty - sy;
        const dr = Math.sqrt(dx * dx + dy * dy) || 1;
        const off = dr * 0.12;                 // 弧の膨らみ（距離の12%）
        const nx = -dy / dr, ny = dx / dr;     // 法線
        const cx = (sx + tx) / 2 + nx * off, cy = (sy + ty) / 2 + ny * off;
        return `M${sx},${sy}Q${cx},${cy} ${tx},${ty}`;
      };
      this.simulation.on('tick', () => {
        linkSel.attr('d', linkPath);
        linkHitSel.attr('d', linkPath);
        nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
        if (this.showHulls) this._updateHulls();

        // レイアウトがほぼ安定したら（end を待たず）一度だけ滑らかにフィット。
        // end まで待つと数秒かかり、その間に瞬間的な縮小が起きて見づらいため。
        if (!this._hasAutoFitted && !this._userHasInteracted
            && this.simulation.alpha() < 0.06) {
          this._hasAutoFitted = true;
          this._fitToView(true);
        }
      });

      this._applyColorMode();
      this._applySearch();
      this._showGraph();
      this._applyHubGlow();
      this._updateHulls();

      // シミュレーション安定後の自動フィット（tick で未実行だった場合の保険）。
      // すでに tick 内でフィット済み、または手動操作済みなら何もしない。
      this.simulation.on('end', () => {
        if (!this._userHasInteracted && !this._hasAutoFitted) {
          this._hasAutoFitted = true;
          this._fitToView(true);
        }
      });
      // 4秒後にもフィット（tick も end も発火しないケースの最終保険）
      setTimeout(() => {
        if (this.svg && !this._userHasInteracted && !this._hasAutoFitted) {
          this._hasAutoFitted = true;
          this._fitToView(true);
        }
      }, 4000);
    }

    // ==============================
    // グラフ内検索
    // ==============================
    _applySearch() {
      const q = this.searchQuery;
      const matchIds = [];

      document.querySelectorAll('.graph-node').forEach(el => {
        if (!q) {
          el.classList.remove('highlighted', 'dimmed');
          return;
        }
        const name = (el.dataset.name || '').toLowerCase();
        if (name.includes(q)) {
          el.classList.add('highlighted');
          el.classList.remove('dimmed');
          matchIds.push(el.dataset.id);
        } else {
          el.classList.remove('highlighted');
          el.classList.add('dimmed');
        }
      });

      this._searchMatchIds = matchIds;
      this._updateSearchFocusButton();
    }

    // 検索が単一の銘柄に絞れたら、その銘柄＋近傍だけへ自動フォーカスして飛ぶ
    // （A: フォーカス優先。密集グラフから目的の銘柄を探す手間を消す）。
    // _applySearch は多方面から呼ばれ再帰しうるため、フォーカス発火は
    // 検索入力ハンドラからのみ本メソッド経由で行う。
    _maybeAutoFocusSearch() {
      const q = this.searchQuery;
      if (!q) {
        // 検索クリア：検索由来のフォーカスなら解除（手動フォーカスは触らない）
        if (this._searchAutoFocused) {
          this._searchAutoFocused = null;
          if (this.focusNodeId) this._exitFocusMode();
        }
        return;
      }
      const ids = this._searchMatchIds || [];
      if (ids.length === 1) {
        const id = String(ids[0]);
        if (this.focusNodeId === id) return;   // 既にその銘柄にフォーカス済み
        this._searchAutoFocused = id;
        this._enterFocusMode(id);
        setTimeout(() => this._fitToView(true), 80);  // 可視ノードへズーム/センタリング
      }
    }

    // T-B2: タグ（ハブ）ノードのうち、接続している「銘柄」が1つ以下のものを除去する。
    // 1銘柄だけのタグは銘柄同士を橋渡ししない＝関連情報をゼロしか持たないため、
    // 消してもグラフの関連構造（2ホップ以上の連鎖）は一切損なわれない。純粋なノイズ除去。
    _pruneLeafTags() {
      const isDiary = new Map(this.allNodes.map(n => [String(n.id), n.node_type === 'diary']));
      const diaryDeg = new Map();   // hubId -> 接続している diary 数
      this.allEdges.forEach(e => {
        const s = String(e.source), t = String(e.target);
        if (isDiary.get(t) === false && isDiary.get(s) === true) diaryDeg.set(t, (diaryDeg.get(t) || 0) + 1);
        if (isDiary.get(s) === false && isDiary.get(t) === true) diaryDeg.set(s, (diaryDeg.get(s) || 0) + 1);
      });
      const remove = new Set();
      this.allNodes.forEach(n => {
        if (n.node_type !== 'diary' && (diaryDeg.get(String(n.id)) || 0) <= 1) {
          remove.add(String(n.id));
        }
      });
      if (remove.size === 0) return;
      this.allNodes = this.allNodes.filter(n => !remove.has(String(n.id)));
      this.allEdges = this.allEdges.filter(e => !remove.has(String(e.source)) && !remove.has(String(e.target)));
    }

    // ==============================
    // カラーモード
    // ==============================
    _applyColorMode() {
      const mode = this.currentColorMode;
      const axisLegend = document.getElementById('legend-axis');

      document.querySelectorAll('.graph-node').forEach(el => {
        const node = this._nodeOf(el.dataset.id);
        if (!node) return;

        if (node.node_type !== 'diary') {
          // タグ・ハッシュタグ ハブノードは軸色で表示
          if (node.node_type === 'tag' || node.node_type === 'hashtag') {
            const poly = el.querySelector('polygon');
            if (poly) {
              const axisColor = AXIS_COLORS[node.axis] || HUB_COLOR.tag;
              const opacity = mode === 'axis' ? 1 : 0.85;
              poly.style.fill = axisColor;
              poly.style.opacity = opacity;
            }
          }
          return;
        }

        const circle = el.querySelector('circle');
        if (!circle) return;
        if (mode === 'status') {
          circle.style.fill = '';
        } else if (mode === 'sector') {
          circle.style.fill = this.sectorColorMap[node.sector] || '#9ca3af';
        } else if (mode === 'profit') {
          const p = node.realized_profit || 0;
          circle.style.fill = p > 0 ? '#10b981' : p < 0 ? '#ef4444' : '#9ca3af';
        } else if (mode === 'axis') {
          // diary ノードはステータス色（axis モードでもグレー統一）
          circle.style.fill = '';
        }
      });

      // 軸凡例の表示切り替え
      if (axisLegend) axisLegend.classList.toggle('legend-hidden', mode !== 'axis');
    }

    // ==============================
    // 凡例更新
    // ==============================
    _updateLegend() {
      const modes  = this.currentEdgeModes;
      const tagLeg  = document.getElementById('legend-tag-node');
      const secLeg  = document.getElementById('legend-sector-node');
      const hubWrap = document.getElementById('legend-hubs');
      const mentionLeg = document.getElementById('legend-mention-edge');
      const hierarchyLeg = document.getElementById('legend-hierarchy-edge');
      const axisLeg = document.getElementById('legend-axis');

      const toggle = (el, show) => el && el.classList.toggle('legend-hidden', !show);
      const hasTag = modes.has('tag') || modes.has('hashtag');
      toggle(tagLeg,  hasTag);
      toggle(secLeg,  modes.has('sector'));
      const hasHub = hasTag || modes.has('sector');
      toggle(hubWrap, hasHub);
      toggle(mentionLeg, modes.has('mention'));
      // 親子タグ接続の凡例は tag モード時のみ表示（ハブ-ハブ接続は tag モード限定）
      toggle(hierarchyLeg, modes.has('tag'));
      toggle(axisLeg, this.currentColorMode === 'axis');
      // 方向（追い風/向かい風）凡例は tag / hashtag いずれかのモード時に表示
      toggle(document.getElementById('legend-direction'), modes.has('tag') || modes.has('hashtag'));
    }

    // ==============================
    // ズームリセット（全ノードをビューに収める）
    // ==============================
    _resetZoom() {
      this._fitToView(true);
    }

    _fitToView(animate) {
      if (!this.svg || !this.zoomBehavior || !this.gRoot) return;
      const svgEl = this.svgEl;
      const width  = svgEl.clientWidth  || svgEl.parentElement.clientWidth  || 800;
      const height = svgEl.clientHeight || 600;
      try {
        const bbox = this.gRoot.node().getBBox();
        if (!bbox || bbox.width === 0 || bbox.height === 0) return;
        const padding = 48;
        // 全体が収まる縮尺
        const fitScale = Math.min(
          (width  - padding * 2) / bbox.width,
          (height - padding * 2) / bbox.height
        );
        // ノードが豆粒化しない下限と、少数ノード時に寄りすぎない上限でクランプ。
        // 全体が下限縮尺で収まらない場合は中央付近を表示し、パン/ピンチで全体を辿れる。
        const MIN_READABLE_SCALE = 0.5;
        const MAX_SCALE = 1.6;
        const scale = Math.max(MIN_READABLE_SCALE, Math.min(fitScale, MAX_SCALE));
        const tx = width  / 2 - scale * (bbox.x + bbox.width  / 2);
        const ty = height / 2 - scale * (bbox.y + bbox.height / 2);
        const t = d3.zoomIdentity.translate(tx, ty).scale(scale);
        const sel = animate ? this.svg.transition().duration(500) : this.svg;
        sel.call(this.zoomBehavior.transform, t);
      } catch (_) { /* getBBox が利用できない環境では無視 */ }
    }

    // ==============================
    // 全画面モード切替（スマホでノードが見えづらい問題対応）
    // ==============================
    _toggleFullscreen(force) {
      const stage = document.getElementById('graph-stage');
      if (!stage) return;
      const next = (typeof force === 'boolean')
        ? force
        : !stage.classList.contains('is-fullscreen');

      stage.classList.toggle('is-fullscreen', next);
      document.body.classList.toggle('graph-fullscreen-active', next);
      this._isFullscreen = next;

      const btn = document.getElementById('toggleFullscreen');
      if (btn) {
        const icon = btn.querySelector('i');
        if (icon) {
          icon.className = next ? 'bi bi-fullscreen-exit' : 'bi bi-arrows-fullscreen';
        }
        btn.setAttribute('title', next ? '全画面を終了' : '全画面表示');
        btn.setAttribute('aria-pressed', next ? 'true' : 'false');
      }

      // CSS のレイアウト変更が反映されてから再センター + フィット（明示操作なので強制フィット）
      this._handleResize(true);
    }

    // ==============================
    // 描画領域サイズ変更時の再センター/フィット
    //   forceFit=true: 全体が見える位置に強制リフィット（全画面トグル時）
    //   forceFit=false: シミュレーション中心のみ更新、ユーザーの zoom/pan は維持
    // ==============================
    _handleResize(forceFit) {
      if (!this.svg || !this.simulation) return;
      requestAnimationFrame(() => {
        const svgEl = this.svgEl;
        const w = svgEl.clientWidth  || svgEl.parentElement.clientWidth  || 800;
        const h = svgEl.clientHeight || svgEl.parentElement.clientHeight || 600;
        const centerForce = this.simulation.force('center');
        if (centerForce) {
          centerForce.x(w / 2).y(h / 2);
        }
        this.simulation.alpha(0.2).restart();
        if (forceFit) {
          this._userHasInteracted = false;
          this._fitToView(true);
        }
      });
    }

    // ==============================
    // ツールチップ（ホバー）
    // ==============================
    _showTooltip(event, d) {
      let html = '';
      if (d.node_type === 'tag') {
        const axisColor = AXIS_COLORS[d.axis] || '#7c3aed';
        const axisLabel = AXIS_LABELS[d.axis] || d.axis || 'テーマ';
        html = `
          <div class="tt-name"><i class="bi bi-tag-fill me-1" style="color:${axisColor};"></i>${_esc(d.tag_name)}</div>
          <div class="tt-meta">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${axisColor};margin-right:4px;"></span>${_esc(axisLabel)}軸
            &nbsp;|&nbsp; 接続銘柄: ${d.diary_count || 0}件
          </div>
          <div class="tt-hint">クリックで詳細パネル</div>`;
      } else if (d.node_type === 'sector') {
        html = `
          <div class="tt-name"><i class="bi bi-building me-1" style="color:#d97706;"></i>${_esc(d.sector_name)}</div>
          <div class="tt-meta">業種 &nbsp;|&nbsp; 接続銘柄: ${d.diary_count || 0}件</div>
          <div class="tt-hint">クリックで詳細パネル</div>`;
      } else if (d.node_type === 'hashtag') {
        const axisColor = AXIS_COLORS[d.axis] || '#7c3aed';
        const axisLabel = AXIS_LABELS[d.axis] || d.axis || 'テーマ';
        html = `
          <div class="tt-name"><i class="bi bi-hash me-1" style="color:${axisColor};"></i>${_esc(d.tag_name)}</div>
          <div class="tt-meta"><span style="color:${axisColor};">■</span> ${axisLabel} &nbsp;|&nbsp; 接続銘柄: ${d.diary_count || 0}件</div>
          <div class="tt-hint">クリックで詳細パネル</div>`;
      } else {
        const profit = d.realized_profit || 0;
        const sign   = profit > 0 ? '+' : '';
        const pStr   = profit === 0 ? '-' : sign + profit.toLocaleString('ja-JP') + '円';
        const pClass = profit > 0 ? 'positive' : profit < 0 ? 'negative' : '';
        const statusInfo = {
          holding: { c: '#10b981', t: '保有中' },
          sold:    { c: '#ef4444', t: '売却済み' },
          memo:    { c: '#9ca3af', t: 'メモ' },
        }[d.status] || { c: '#9ca3af', t: d.status };
        // 継続記録の鮮度（コンテンツ情報を持つ primary ノードのみ）
        let noteLine = '';
        if (typeof d.note_count === 'number') {
          const days = _daysSince(d.last_note_date);
          if (d.note_count > 0 && days !== null) {
            const when = days === 0 ? '今日' : `${days}日前`;
            noteLine = `<div class="tt-meta">継続記録: ${d.note_count}件（最終 ${when}）</div>`;
          } else {
            noteLine = '<div class="tt-meta">継続記録: なし</div>';
          }
          if (_freshness(d) === 'stale') {
            noteLine += `<div class="tt-meta" style="color:#d97706;">${STALE_DAYS}日以上 記録の更新なし</div>`;
          }
          if (_needsRetrospective(d)) {
            noteLine += '<div class="tt-meta" style="color:#d97706;"><i class="bi bi-exclamation-circle me-1"></i>振り返りが未記入です</div>';
          }
        }
        html = `
          <div class="tt-name"><span class="tt-badge" style="background:${statusInfo.c};">${_esc(statusInfo.t)}</span>${_esc(d.stock_name)}</div>
          <div class="tt-symbol">${_esc(d.stock_symbol || '-')} &nbsp;/&nbsp; ${_esc(d.sector)}</div>
          <div class="tt-profit ${pClass}">実現損益: ${pStr}</div>
          <div class="tt-meta">接続: ${d.link_count}本</div>
          ${noteLine}
          <div class="tt-hint">クリックで詳細パネル</div>`;
      }
      this.tooltipEl.innerHTML = html;
      this.tooltipEl.style.display = 'block';
      this._moveTooltip(event);
    }

    _moveTooltip(event) {
      const padding = 14;
      const ttw = this.tooltipEl.offsetWidth  || 200;
      const tth = this.tooltipEl.offsetHeight || 90;
      let x = event.clientX + padding;
      let y = event.clientY - 10;
      if (x + ttw > window.innerWidth  - 8) x = event.clientX - ttw - padding;
      if (y + tth > window.innerHeight - 8) y = window.innerHeight - tth - 8;
      this.tooltipEl.style.left = x + 'px';
      this.tooltipEl.style.top  = y + 'px';
    }

    _hideTooltip() {
      this.tooltipEl.style.display = 'none';
    }

    // ==============================
    // サイドパネル
    // ==============================
    _openSidePanel(d) {
      this._hideTooltip();
      let title = '';
      let html  = '';

      if (d.node_type === 'tag' || d.node_type === 'hashtag') {
        const isHash    = d.node_type === 'hashtag';
        const axisColor = AXIS_COLORS[d.axis] || '#7c3aed';
        const axisLabel = AXIS_LABELS[d.axis] || d.axis || 'テーマ';
        const icon      = isHash ? 'bi-hash' : 'bi-tag-fill';
        title = `<i class="bi ${icon} me-1" style="color:${axisColor};"></i>${isHash ? '@' : ''}${_esc(d.tag_name)}`;
        html  = `
          <div class="sp-meta-line">
            <span class="sp-chip sp-chip-static" style="--chip-color:${axisColor};">
              <i class="bi bi-circle-fill"></i>${_esc(axisLabel)}
            </span>
            <span>${isHash ? '@ハッシュタグ' : 'タグ'}</span>
            <span class="sp-meta-sep">・</span>
            <span>接続銘柄 ${d.diary_count || 0}件</span>
          </div>`;
      } else if (d.node_type === 'sector') {
        title = `<i class="bi bi-building me-1" style="color:#d97706;"></i>${_esc(d.sector_name)}`;
        html  = `
          <div class="sp-meta-line">
            <span class="sp-chip sp-chip-static" style="--chip-color:#d97706;">
              <i class="bi bi-building"></i>業種
            </span>
            <span>接続銘柄 ${d.diary_count || 0}件</span>
          </div>`;
      } else {
        const profit = d.realized_profit || 0;
        const sign   = profit > 0 ? '+' : '';
        const pClass = profit > 0 ? 'pos' : profit < 0 ? 'neg' : 'zero';
        const statusInfo = {
          holding: { c: '#10b981', t: '保有中' },
          sold:    { c: '#ef4444', t: '売却済み' },
          memo:    { c: '#9ca3af', t: 'メモ' },
        }[d.status] || { c: '#9ca3af', t: d.status };
        title = _esc(d.stock_name || '-');
        html  = `
          <div class="sp-hero">
            <span class="sp-badge" style="background:${statusInfo.c};">${_esc(statusInfo.t)}</span>
            ${profit === 0 ? '' : `<div class="sp-profit ${pClass}">${sign}${Math.abs(profit).toLocaleString('ja-JP')}<span>円</span></div>`}
          </div>
          <div class="sp-meta-line">
            <span class="sp-meta-strong">${_esc(d.stock_symbol || '-')}</span>
            <span class="sp-meta-sep">・</span>
            <span>${_esc(d.sector || '未分類')}</span>
            <span class="sp-meta-sep">・</span>
            <span>接続 ${d.link_count || 0}本</span>
          </div>`;

        // 隣接ノードを「タグ・グループ（チップ）」と「関連銘柄（行）」に分けて表示
        const neighbors = this._neighborsOf(d.id);
        const hubNbrs   = neighbors.filter(n => n.node_type !== 'diary');
        const diaryNbrs = neighbors.filter(n => n.node_type === 'diary');

        if (hubNbrs.length > 0) {
          html += `
          <div class="side-panel-section">
            <div class="side-panel-label">タグ・グループ (${hubNbrs.length})</div>
            <div class="sp-chips">${hubNbrs.map(c => this._hubChip(c)).join('')}</div>
          </div>`;
        }

        // 投資理由の抜粋（コンテンツ情報を持つ primary ノードのみ）
        if (d.reason_excerpt) {
          html += `
          <div class="side-panel-section">
            <div class="side-panel-label">投資理由</div>
            <div class="sp-excerpt">${_esc(d.reason_excerpt)}</div>
          </div>`;
        }

        // 継続記録のサマリー（最新の記録・鮮度）
        if (typeof d.note_count === 'number') {
          const fresh = _freshness(d);
          if (d.note_count > 0) {
            const days = _daysSince(d.last_note_date);
            const when = days === null ? '' : days === 0 ? '今日' : `${days}日前`;
            html += `
          <div class="side-panel-section">
            <div class="side-panel-label">継続記録（${d.note_count}件）</div>
            <div class="sp-note-meta">
              ${fresh === 'fresh' ? '<span class="sp-fresh-dot"></span>' : ''}
              <span>${_esc(d.last_note_date || '')}${when ? `（${when}）` : ''}</span>
              ${d.last_note_type ? `<span class="sp-note-badge">${_esc(d.last_note_type)}</span>` : ''}
            </div>
            ${d.last_note_excerpt ? `<div class="sp-excerpt">${_esc(d.last_note_excerpt)}</div>` : ''}
            ${fresh === 'stale' ? `<div class="sp-stale-note mt-1"><i class="bi bi-hourglass-split me-1"></i>${STALE_DAYS}日以上 記録の更新がありません</div>` : ''}
          </div>`;
          } else {
            html += `
          <div class="side-panel-section">
            <div class="side-panel-label">継続記録</div>
            <div class="text-muted" style="font-size:.78rem;">まだありません</div>
            ${fresh === 'stale' ? `<div class="sp-stale-note mt-1"><i class="bi bi-hourglass-split me-1"></i>${STALE_DAYS}日以上 記録の更新がありません</div>` : ''}
          </div>`;
          }
        }

        // 売却済みで振り返り未記入なら注意表示 + CTA
        if (_needsRetrospective(d)) {
          html += `
          <div class="side-panel-section">
            <div class="sp-stale-note"><i class="bi bi-exclamation-circle me-1"></i>売却済みですが振り返りが未記入です</div>
            <a href="${_esc(d.url || '#')}" class="btn btn-outline-warning btn-sm w-100 mt-1">
              <i class="bi bi-pencil me-1"></i>振り返りを書く
            </a>
          </div>`;
        }

        if (diaryNbrs.length > 0) {
          html += `
          <div class="side-panel-section">
            <div class="side-panel-label">関連銘柄 (${diaryNbrs.length})</div>
            <div class="sp-conn-list">${diaryNbrs.map(c => this._connRow(c)).join('')}</div>
          </div>`;
        }
      }

      // ハブノードの関連銘柄リスト。タグ/@タグで方向（追い風/向かい風）が
      // 設定されている場合は方向別にグルーピングして表示する
      if (d.node_type !== 'diary') {
        const dirGroups = (d.node_type === 'tag' || d.node_type === 'hashtag')
          ? this._hubNeighborsByDirection(d.id)
          : null;
        if (dirGroups && (dirGroups.up.length > 0 || dirGroups.down.length > 0)) {
          const total = dirGroups.up.length + dirGroups.down.length + dirGroups.neutral.length;
          const section = (key, icon, label, items) => items.length === 0 ? '' : `
            <div class="sp-dir-head dir-${key}"><i class="bi ${icon}"></i>${label}（${items.length}）</div>
            <div class="sp-conn-list">${items.map(c => this._connRow(c)).join('')}</div>`;
          html += `
          <div class="side-panel-section">
            <div class="side-panel-label">関連銘柄 (${total})</div>
            ${section('up', 'bi-arrow-up-right', '追い風', dirGroups.up)}
            ${section('down', 'bi-arrow-down-right', '向かい風', dirGroups.down)}
            ${section('neutral', 'bi-dash-lg', '中立・未設定', dirGroups.neutral)}
          </div>`;
        } else {
          const conns = this._neighborsOf(d.id);
          if (conns.length > 0) {
            html += `
          <div class="side-panel-section">
            <div class="side-panel-label">関連ノード (${conns.length})</div>
            <div class="sp-conn-list">${conns.map(c => this._connRow(c)).join('')}</div>
          </div>`;
          }
        }
      }

      // 日記詳細CTA（diaryノードのみ）
      if (d.node_type === 'diary' && d.url) {
        html += `
          <div class="side-panel-footer mt-3">
            <a href="${_esc(d.url)}" class="sp-cta">
              <i class="bi bi-journal-text"></i>日記の詳細を見る
            </a>
          </div>`;
      }

      html += `
        <div class="side-panel-section mt-2">
          <button class="btn btn-outline-primary btn-sm w-100" id="focus-mode-btn"
                  data-node-id="${_esc(String(d.id))}">
            <i class="bi bi-crosshair me-1"></i>この関連のみ表示
          </button>
        </div>`;

      // グラフ上での手動リンク作成（primary の日記ノードのみ）
      if (d.node_type === 'diary' && d.is_primary && this.relatedAddUrl) {
        html += `
        <div class="side-panel-section">
          <button class="btn btn-outline-secondary btn-sm w-100" id="link-mode-btn"
                  data-node-id="${_esc(String(d.id))}">
            <i class="bi bi-link-45deg me-1"></i>この銘柄からリンクを作成
          </button>
        </div>`;
      }

      this.sidePanelTitle.innerHTML = title;
      this.sidePanelBody.innerHTML  = html;
      this.sidePanel.classList.add('open');
      document.getElementById('graph-wrapper').classList.add('panel-open');
      this._bindPanelGoto();

      const focusBtn = document.getElementById('focus-mode-btn');
      if (focusBtn) {
        focusBtn.addEventListener('click', () => {
          const nid = focusBtn.dataset.nodeId;
          this._closeSidePanel();
          this._enterFocusMode(nid);
        });
      }

      const linkBtn = document.getElementById('link-mode-btn');
      if (linkBtn) {
        linkBtn.addEventListener('click', () => {
          const nid = linkBtn.dataset.nodeId;
          this._closeSidePanel();
          this._enterLinkMode(nid);
        });
      }
    }

    // ==============================
    // エッジ詳細パネル（接続の根拠表示）
    // ==============================
    _openEdgePanel(edge) {
      this._hideTooltip();
      const sid = String(typeof edge.source === 'object' ? edge.source.id : edge.source);
      const tid = String(typeof edge.target === 'object' ? edge.target.id : edge.target);
      const src = this._nodeOf(sid);
      const tgt = this._nodeOf(tid);
      if (!src || !tgt) return;

      const TYPE_INFO = {
        manual:        { icon: 'bi-link-45deg',   label: '手動リンク',   desc: '日記詳細ページの「関連日記」で設定した接続' },
        tag:           { icon: 'bi-tag-fill',     label: 'タグ接続',     desc: '銘柄に付けたタグによる接続' },
        hashtag:       { icon: 'bi-hash',         label: '@タグ接続',    desc: '投資理由・継続記録内の @タグによる接続' },
        sector:        { icon: 'bi-building',     label: '業種接続',     desc: '同じ業種に属する銘柄の接続' },
        mention:       { icon: 'bi-upc-scan',     label: 'コード参照',   desc: '本文中で他銘柄のコードに言及した接続' },
        tag_hierarchy: { icon: 'bi-diagram-2-fill', label: '親子タグ接続', desc: '親タグと子タグ（細分タグ）の階層関係による接続' },
      };
      const info = TYPE_INFO[edge.edge_type] || TYPE_INFO.manual;

      let html = `
        <div class="side-panel-section">
          <div class="side-panel-label">接続種別</div>
          <div>${info.label}</div>
          <div class="text-muted" style="font-size:.74rem;">${info.desc}</div>
        </div>
        <div class="side-panel-section">
          <div class="side-panel-label">つながっているノード</div>
          <div class="sp-conn-list">${this._connRow(src)}${this._connRow(tgt)}</div>
        </div>`;

      // タグ/@タグ: 方向属性（追い風・向かい風）
      if ((edge.edge_type === 'tag' || edge.edge_type === 'hashtag')
          && (edge.direction === 'up' || edge.direction === 'down')) {
        const dir = edge.direction === 'up'
          ? { c: '#16a34a', t: '追い風（プラス影響）' }
          : { c: '#dc2626', t: '向かい風（マイナス影響）' };
        html += `
        <div class="side-panel-section">
          <div class="side-panel-label">影響方向</div>
          <div><span class="sp-cdot d-inline-block" style="background:${dir.c};margin-right:6px;"></span>${dir.t}</div>
        </div>`;
      }

      // ハブ経由の接続: 共有銘柄数（少ないほど希少で強い関連）
      const hub = tgt.node_type !== 'diary' ? tgt : (src.node_type !== 'diary' ? src : null);
      if (hub && typeof hub.diary_count === 'number') {
        html += `
        <div class="side-panel-section">
          <div class="side-panel-label">共有銘柄数</div>
          <div>${hub.diary_count} 件 <span class="text-muted" style="font-size:.72rem;">（少ないほど希少な関連）</span></div>
        </div>`;
      }

      // コード参照: 言及箇所の本文抜粋（source が言及した側）
      if (edge.edge_type === 'mention' && edge.excerpt) {
        const srcName = src.stock_name || '';
        html += `
        <div class="side-panel-section">
          <div class="side-panel-label">言及箇所（「${_esc(srcName)}」の本文より）</div>
          <div class="sp-excerpt">${_esc(edge.excerpt)}</div>
        </div>`;
      }

      // 手動リンク: グラフ上から解除できるようにする
      const canUnlink = edge.edge_type === 'manual'
        && src.node_type === 'diary' && tgt.node_type === 'diary'
        && this.relatedRemoveUrl;
      if (canUnlink) {
        html += `
        <div class="side-panel-section mt-2">
          <button class="btn btn-outline-danger btn-sm w-100" id="unlink-btn"
                  data-src="${_esc(sid)}" data-tgt="${_esc(tid)}">
            <i class="bi bi-x-circle me-1"></i>このリンクを解除
          </button>
        </div>`;
      }

      this.sidePanelTitle.innerHTML = `<i class="bi ${info.icon} me-1"></i>${info.label}`;
      this.sidePanelBody.innerHTML  = html;
      this.sidePanel.classList.add('open');
      document.getElementById('graph-wrapper').classList.add('panel-open');
      this._bindPanelGoto();

      const unlinkBtn = document.getElementById('unlink-btn');
      if (unlinkBtn) {
        unlinkBtn.addEventListener('click', () => {
          const msg = `「${src.stock_name || ''}」と「${tgt.stock_name || ''}」の手動リンクを解除しますか？`;
          if (!window.confirm(msg)) return;
          this._removeManualLink(unlinkBtn.dataset.src, unlinkBtn.dataset.tgt);
        });
      }
    }

    // パネル内の「関連ノード」行クリック → そのノードのパネルへ切り替え
    _bindPanelGoto() {
      this.sidePanelBody.querySelectorAll('[data-goto]').forEach(row => {
        row.addEventListener('click', () => {
          const target = this._nodeOf(row.dataset.goto);
          if (target) this._openSidePanel(target);
        });
      });
    }

    // id からノードオブジェクトを引く（_nodeById 索引を使用）
    _nodeOf(id) {
      if (!this._nodeById || this._nodeById.size === 0) {
        this._nodeById = new Map(this.allNodes.map(n => [String(n.id), n]));
      }
      return this._nodeById.get(String(id));
    }

    // ハブの隣接日記ノードをエッジ方向（追い風/向かい風/中立）でグルーピング
    _hubNeighborsByDirection(hubId) {
      const id = String(hubId);
      const groups = { up: [], down: [], neutral: [] };
      const seen = new Set();
      this.allEdges.forEach(e => {
        const s = String(typeof e.source === 'object' ? e.source.id : e.source);
        const t = String(typeof e.target === 'object' ? e.target.id : e.target);
        let otherId = null;
        if (s === id) otherId = t;
        else if (t === id) otherId = s;
        if (!otherId || seen.has(otherId)) return;
        seen.add(otherId);
        const node = this._nodeOf(otherId);
        if (!node) return;
        const dir = (e.direction === 'up' || e.direction === 'down') ? e.direction : 'neutral';
        groups[dir].push(node);
      });
      return groups;
    }

    // ==============================
    // 未接続銘柄の一覧パネル
    // ==============================
    _openIsolatedPanel() {
      const items = this._isolatedDiaries;
      if (items.length === 0) return;
      const statusColor = { holding: '#10b981', sold: '#ef4444', memo: '#9ca3af' };
      const rows = items.map(d => `
        <a class="sp-conn" href="${_esc(d.url || '#')}" style="text-decoration:none;">
          <span class="sp-cdot" style="background:${statusColor[d.status] || '#9ca3af'};"></span>
          <span class="sp-cname">${_esc(d.stock_name || '')}</span>
          <span class="sp-ck">${_esc(d.stock_symbol || '')}</span>
        </a>`).join('');
      this.sidePanelTitle.innerHTML =
        `<i class="bi bi-exclamation-circle me-1" style="color:#d97706;"></i>未接続の銘柄（${items.length}件）`;
      this.sidePanelBody.innerHTML = `
        <div class="side-panel-section">
          <div class="text-muted" style="font-size:.78rem;line-height:1.6;">
            現在の表示条件で、どの接続にも繋がっていない銘柄です。
            タグ・@タグ・関連日記を追加すると地図に繋がります。
          </div>
        </div>
        <div class="side-panel-section">
          <div class="sp-conn-list">${rows}</div>
        </div>`;
      this.sidePanel.classList.add('open');
      document.getElementById('graph-wrapper').classList.add('panel-open');
    }

    // ==============================
    // リンク作成モード（グラフ上で手動リンクを作成・解除）
    // ==============================
    _csrfToken() {
      const input = document.querySelector('#graph-csrf [name=csrfmiddlewaretoken]');
      if (input && input.value) return input.value;
      const m = document.cookie.match(/csrftoken=([^;]+)/);
      return m ? m[1] : '';
    }

    _enterLinkMode(sourceId) {
      const src = this._nodeOf(sourceId);
      if (!src || src.node_type !== 'diary') return;
      this.linkSourceId = String(sourceId);

      const banner = document.getElementById('link-mode-banner');
      if (banner) {
        // エラー表示で書き換えられている場合があるため毎回組み立て直す
        const text = banner.querySelector('#link-banner-text');
        if (text) {
          text.innerHTML = 'リンク元: <span id="link-banner-source" class="fw-semibold"></span>'
            + ' — リンク先の銘柄ノードをクリックしてください';
          banner.querySelector('#link-banner-source').textContent = src.stock_name || '';
        }
        banner.style.display = 'flex';
      }
      const wrapper = document.getElementById('graph-wrapper');
      if (wrapper) wrapper.classList.add('link-mode');
      const srcEl = document.querySelector(`.graph-node[data-id="${this.linkSourceId}"]`);
      if (srcEl) srcEl.classList.add('link-source');
    }

    _exitLinkMode() {
      if (!this.linkSourceId) return;
      this.linkSourceId = null;
      const banner = document.getElementById('link-mode-banner');
      if (banner) banner.style.display = 'none';
      const wrapper = document.getElementById('graph-wrapper');
      if (wrapper) wrapper.classList.remove('link-mode');
      document.querySelectorAll('.graph-node.link-source')
        .forEach(el => el.classList.remove('link-source'));
    }

    _handleLinkTargetClick(d) {
      if (d.node_type !== 'diary') return;                 // ハブノードは対象外
      const targetId = String(d.id);
      if (targetId === this.linkSourceId) return;          // 自分自身は不可
      this._createManualLink(this.linkSourceId, targetId);
    }

    async _createManualLink(sourceId, targetId) {
      const url = this.relatedAddUrl.replace('/diary/0/', `/diary/${sourceId}/`);
      try {
        const resp = await fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this._csrfToken(),
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: JSON.stringify({ related_id: parseInt(targetId, 10) }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
        // 成功: 作成したリンクが見えるよう手動モードを有効にして再取得
        this._exitLinkMode();
        this.currentEdgeModes.add('manual');
        this._syncEdgeModeCheckboxes();
        this._fetchAndRender();
      } catch (err) {
        console.error('リンク作成エラー:', err);
        const text = document.getElementById('link-banner-text');
        if (text) text.innerHTML = `<span class="text-danger">リンクの作成に失敗しました（${_esc(err.message)}）</span>`;
        setTimeout(() => this._exitLinkMode(), 2500);
      }
    }

    async _removeManualLink(sourceId, targetId) {
      const url = this.relatedRemoveUrl
        .replace('/diary/0/', `/diary/${sourceId}/`)
        .replace('/related/0/', `/related/${targetId}/`);
      try {
        const resp = await fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'X-CSRFToken': this._csrfToken(),
            'X-Requested-With': 'XMLHttpRequest',
          },
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
        this._closeSidePanel();
        this._fetchAndRender();
      } catch (err) {
        console.error('リンク解除エラー:', err);
        window.alert('リンクの解除に失敗しました');
      }
    }

    // 隣接ノード（オブジェクト配列）を返す
    _neighborsOf(id) {
      if (!this._adj) this._buildAdjacency();
      const set = this._adj.get(String(id)) || new Set();
      return [...set]
        .map(nid => this._nodeOf(nid))
        .filter(Boolean);
    }

    // ハブノード（タグ/@タグ/業種）1件ぶんのチップHTMLを生成
    _hubChip(c) {
      const isSector = c.node_type === 'sector';
      const color  = isSector ? '#d97706' : (AXIS_COLORS[c.axis] || '#7c3aed');
      const name   = (isSector ? c.sector_name : c.tag_name) || '';
      const prefix = c.node_type === 'hashtag' ? '@' : '';
      const icon   = isSector ? 'bi-building' : 'bi-tag-fill';
      return `<button type="button" class="sp-chip" data-goto="${_esc(String(c.id))}"
        style="--chip-color:${color};" title="${_esc(name)}の詳細を見る">
        <i class="bi ${icon}"></i>${prefix}${_esc(name)}</button>`;
    }

    // 関連ノード1行ぶんのHTMLを生成
    _connRow(c) {      let color, sub, name, prefix = '';
      if (c.node_type === 'diary') {
        const statusColor = { holding: '#10b981', sold: '#ef4444', memo: '#9ca3af' };
        color = statusColor[c.status] || '#9ca3af';
        sub   = c.stock_symbol || '銘柄';
        name  = c.stock_name || '';
      } else if (c.node_type === 'sector') {
        color = '#d97706';
        sub   = '業種';
        name  = c.sector_name || '';
      } else {
        // tag / hashtag
        color  = AXIS_COLORS[c.axis] || '#7c3aed';
        sub    = c.node_type === 'hashtag' ? 'ハッシュタグ' : 'タグ';
        name   = c.tag_name || '';
        prefix = c.node_type === 'hashtag' ? '@' : '';
      }
      return `<button type="button" class="sp-conn" data-goto="${_esc(String(c.id))}">
        <span class="sp-cdot" style="background:${color};"></span>
        <span class="sp-cname">${prefix}${_esc(name)}</span>
        <span class="sp-ck">${_esc(sub)}</span>
      </button>`;
    }

    _closeSidePanel() {
      if (this.sidePanel)  this.sidePanel.classList.remove('open');
      const wrapper = document.getElementById('graph-wrapper');
      if (wrapper) wrapper.classList.remove('panel-open');
    }

    // ==============================
    // 統計バッジ更新
    // ==============================
    _updateStats(meta) {
      if (!meta || !this.statsEl) return;
      document.getElementById('stats-nodes').textContent = `${meta.total_nodes} ノード`;
      document.getElementById('stats-edges').textContent = `${meta.total_edges} 接続`;

      // タグモード時: 軸フィルターの状態をインライン表示
      const axisInfoEl = document.getElementById('stats-axis-info');
      if (axisInfoEl) {
        const tagCount = (this.allNodes || []).filter(n => n.node_type === 'tag').length;
        if (this.currentEdgeModes.has('tag') && tagCount > 0) {
          const AXIS_LABELS_SHORT = {
            theme: 'テーマ', business_model: 'BM', risk: 'リスク',
            capital_policy: '資本', macro: 'マクロ', event: 'イベント',
          };
          const activeAxes = [...this.currentAxes].map(a => AXIS_LABELS_SHORT[a] || a).join('・');
          axisInfoEl.textContent = `タグ ${tagCount}件（${activeAxes || '全軸'}）`;
          axisInfoEl.style.display = 'inline-block';
        } else {
          axisInfoEl.style.display = 'none';
        }
      }

      // 未接続銘柄バッジ
      const isolatedBtn = document.getElementById('stats-isolated');
      if (isolatedBtn) {
        const n = this._isolatedDiaries.length;
        if (n > 0) {
          isolatedBtn.innerHTML = `<i class="bi bi-exclamation-circle"></i>未接続 ${n}件`;
          isolatedBtn.style.display = 'inline-flex';
        } else {
          isolatedBtn.style.display = 'none';
        }
      }

      this.statsEl.style.display = 'block';
    }

    // ==============================
    // フォーカスモード
    // ==============================
    // 隣接マップ構築（無向グラフとして双方向に登録）
    _buildAdjacency() {
      const adj = new Map();
      const add = (a, b) => {
        if (!adj.has(a)) adj.set(a, new Set());
        adj.get(a).add(b);
      };
      this.allEdges.forEach(e => {
        const s = String(typeof e.source === 'object' ? e.source.id : e.source);
        const t = String(typeof e.target === 'object' ? e.target.id : e.target);
        add(s, t); add(t, s);
      });
      this._adj = adj;
    }

    // シードから BFS で maxDepth ホップ辿り Map<id, 距離> を返す（シードは距離0）
    _bfsDistances(seedIds, maxDepth) {
      if (!this._adj) this._buildAdjacency();
      const dist = new Map();
      let frontier = [];
      seedIds.forEach(id => { const s = String(id); dist.set(s, 0); frontier.push(s); });
      for (let d = 1; d <= maxDepth && frontier.length; d++) {
        const next = [];
        frontier.forEach(cur => {
          (this._adj.get(cur) || []).forEach(n => {
            if (!dist.has(n)) { dist.set(n, d); next.push(n); }
          });
        });
        frontier = next;
      }
      return dist;
    }

    // フォーカス表示の共通適用（focusSeedIds / focusDepth を読んで BFS → 表示反映）
    _applyFocus() {
      const seeds = this.focusSeedIds;
      if (!seeds || seeds.length === 0) return new Set();
      const dist = this._bfsDistances(seeds, this.focusDepth);
      this.focusNeighborIds = new Set([...dist.keys()].filter(id => dist.get(id) > 0));
      const visibleIds = new Set(dist.keys());

      document.querySelectorAll('.graph-node').forEach(el => {
        const id = el.dataset.id;
        el.classList.remove('focus-hidden', 'focus-visible',
          'focus-dist-0', 'focus-dist-1', 'focus-dist-2', 'focus-dist-3');
        if (dist.has(id)) {
          el.classList.add('focus-visible', 'focus-dist-' + Math.min(dist.get(id), 3));
        } else {
          el.classList.add('focus-hidden');
        }
      });

      const edgeHidden = e => {
        const s = String(typeof e.source === 'object' ? e.source.id : e.source);
        const t = String(typeof e.target === 'object' ? e.target.id : e.target);
        return !(visibleIds.has(s) && visibleIds.has(t));
      };
      if (this.linkSel)    this.linkSel.classed('focus-hidden', edgeHidden);
      if (this.linkHitSel) this.linkHitSel.classed('focus-hidden', edgeHidden);
      // フォーカス中は非表示ノードを含むクラスタ面が誤解を招くため隠す
      if (this.hullGroup)  this.hullGroup.classed('hull-hidden', true);

      this._updateFocusStats(visibleIds);
      return visibleIds;
    }

    // フォーカス時の統計バッジ更新
    _updateFocusStats(visibleIds) {
      const nodesEl = document.getElementById('stats-nodes');
      const edgesEl = document.getElementById('stats-edges');
      const focusedEdgeCount = this.allEdges.filter(e => {
        const s = String(typeof e.source === 'object' ? e.source.id : e.source);
        const t = String(typeof e.target === 'object' ? e.target.id : e.target);
        return visibleIds.has(s) && visibleIds.has(t);
      }).length;
      if (nodesEl) nodesEl.textContent = `${visibleIds.size} ノード (フォーカス)`;
      if (edgesEl) edgesEl.textContent = `${focusedEdgeCount} 接続`;
    }

    _enterFocusMode(nodeId) {
      this.focusNodeId  = String(nodeId);
      this.focusSeedIds = [String(nodeId)];
      this._showFocusBanner(this._applyFocus().size);
    }

    _exitFocusMode() {
      this.focusNodeId = null;
      this.focusNeighborIds = new Set();
      this.focusSeedIds = [];

      document.querySelectorAll('.graph-node').forEach(el => {
        el.classList.remove('focus-hidden', 'focus-visible',
          'focus-dist-0', 'focus-dist-1', 'focus-dist-2', 'focus-dist-3');
      });

      if (this.linkSel)    this.linkSel.classed('focus-hidden', false);
      if (this.linkHitSel) this.linkHitSel.classed('focus-hidden', false);
      if (this.hullGroup)  this.hullGroup.classed('hull-hidden', false);

      this._hideFocusBanner();

      const nodesEl = document.getElementById('stats-nodes');
      const edgesEl = document.getElementById('stats-edges');
      if (nodesEl) nodesEl.textContent = `${this.allNodes.length} ノード`;
      if (edgesEl) edgesEl.textContent = `${this.allEdges.length} 接続`;

      this._applySearch();
    }

    _showFocusBanner(nodeCount) {
      const banner = document.getElementById('focus-mode-banner');
      if (!banner) return;
      const node = this._nodeOf(this.focusNodeId);
      const label = node
        ? (node.stock_name || node.tag_name || node.sector_name || 'ノード')
        : 'ノード';
      const labelEl = banner.querySelector('#focus-banner-label');
      const countEl = banner.querySelector('#focus-banner-count');
      if (labelEl) labelEl.textContent = label;
      if (countEl) countEl.textContent = `${nodeCount} ノード表示中`;
      banner.style.display = 'flex';
    }

    _hideFocusBanner() {
      const banner = document.getElementById('focus-mode-banner');
      if (banner) banner.style.display = 'none';
    }

    _enterMultiFocusMode(nodeIds) {
      if (nodeIds.length === 0) return;
      if (nodeIds.length === 1) { this._enterFocusMode(nodeIds[0]); return; }

      this.focusNodeId  = '__multi__';
      this.focusSeedIds = nodeIds.map(String);
      const visibleIds = this._applyFocus();

      const banner = document.getElementById('focus-mode-banner');
      if (banner) {
        const labelEl = banner.querySelector('#focus-banner-label');
        const countEl = banner.querySelector('#focus-banner-count');
        if (labelEl) labelEl.textContent = `${nodeIds.length} 件の検索結果`;
        if (countEl) countEl.textContent = `${visibleIds.size} ノード表示中`;
        banner.style.display = 'flex';
      }

      const sfBtn = document.getElementById('search-focus-btn');
      if (sfBtn) sfBtn.style.display = 'none';
    }

    _updateSearchFocusButton() {
      const btn = document.getElementById('search-focus-btn');
      if (!btn) return;
      if (!this.searchQuery || this.focusNodeId || this._searchMatchIds.length === 0) {
        btn.style.display = 'none'; return;
      }
      const count = this._searchMatchIds.length;
      if (count === 1) {
        const node = this._nodeOf(this._searchMatchIds[0]);
        const label = node ? (node.stock_name || node.tag_name || node.sector_name || '') : '';
        btn.innerHTML = `<i class="bi bi-crosshair me-1"></i>${_esc(label)} の関連のみ表示`;
      } else {
        btn.innerHTML = `<i class="bi bi-crosshair me-1"></i>一致した ${count} 件を中心に表示`;
      }
      btn.style.display = 'inline-flex';
    }

    // ==============================
    // クラスタ面（ハル）描画
    //   ハブとそのメンバー銘柄を包む凸包を薄い色面で描く。
    //   tick ごとに呼ばれるため、showHulls が false なら即座に抜ける
    // ==============================
    _updateHulls() {
      if (!this.hullGroup) return;
      if (!this.showHulls) {
        this.hullGroup.selectAll('path').remove();
        return;
      }
      const data = [];
      (this._clusters || []).forEach(c => {
        const pts = [];
        [c.hub, ...c.members].forEach(n => {
          if (typeof n.x !== 'number' || typeof n.y !== 'number') return;
          // ノードの半径 + 余白ぶん広げた周囲8点でパディングし、面が窮屈にならないようにする
          const pad = (n._r || 10) + 14;
          for (let i = 0; i < 8; i++) {
            const a = (Math.PI / 4) * i;
            pts.push([n.x + Math.cos(a) * pad, n.y + Math.sin(a) * pad]);
          }
        });
        if (pts.length < 3) return;
        const hull = d3.polygonHull(pts);
        if (!hull) return;
        data.push({ id: c.id, hull, color: this._clusterColor(c.hub) });
      });

      const line = d3.line().curve(d3.curveCatmullRomClosed.alpha(0.8));
      this.hullGroup.selectAll('path')
        .data(data, d => d.id)
        .join('path')
          .attr('d', d => line(d.hull))
          .attr('fill', d => d.color)
          .attr('fill-opacity', 0.07)
          .attr('stroke', d => d.color)
          .attr('stroke-opacity', 0.25)
          .attr('stroke-width', 1.5);
    }

    _clusterColor(hub) {
      if (hub.node_type === 'sector') return HUB_COLOR.sector;
      return AXIS_COLORS[hub.axis] || HUB_COLOR.tag;
    }

    // ==============================
    // ハブノードのグロー強調
    // ==============================
    _applyHubGlow() {
      if (!this.svg) return;

      let defs = this.svg.select('defs');
      if (defs.empty()) defs = this.svg.insert('defs', ':first-child');
      defs.selectAll('filter.hub-glow-filter').remove();

      const TOP_N = 5;
      const glowColorMap = { tag: '#a78bfa', sector: '#fb923c', hashtag: '#a78bfa' };

      const hubNodes = this.allNodes
        .filter(n => ['tag', 'sector', 'hashtag'].includes(n.node_type))
        .sort((a, b) => (b.link_count || 0) - (a.link_count || 0))
        .slice(0, TOP_N);

      if (hubNodes.length === 0) return;

      hubNodes.forEach((node, rank) => {
        const filterId = `hub-glow-${String(node.id).replace(/[^a-z0-9]/gi, '_')}`;
        const glowColor = glowColorMap[node.node_type] || '#a78bfa';
        const stdDev  = 4 - (rank / Math.max(TOP_N - 1, 1)) * 2.5;
        const opacity = 0.85 - (rank / Math.max(TOP_N - 1, 1)) * 0.5;

        const filter = defs.append('filter')
          .attr('id', filterId)
          .attr('class', 'hub-glow-filter')
          .attr('x', '-50%').attr('y', '-50%')
          .attr('width', '200%').attr('height', '200%');
        filter.append('feFlood')
          .attr('flood-color', glowColor)
          .attr('flood-opacity', opacity)
          .attr('result', 'color');
        filter.append('feComposite')
          .attr('in', 'color').attr('in2', 'SourceGraphic')
          .attr('operator', 'in').attr('result', 'coloredSource');
        filter.append('feGaussianBlur')
          .attr('in', 'coloredSource')
          .attr('stdDeviation', stdDev)
          .attr('result', 'blur');
        const merge = filter.append('feMerge');
        merge.append('feMergeNode').attr('in', 'blur');
        merge.append('feMergeNode').attr('in', 'SourceGraphic');

        this.svg.select(`.graph-node[data-id="${String(node.id)}"]`)
          .attr('filter', `url(#${filterId})`);
      });
    }

    // ==============================
    // 表示状態切り替え
    // ==============================
    _showLoading() {
      this.loadingEl.style.display = 'flex';
      this.svgEl.style.display     = 'none';
      this.emptyEl.style.display   = 'none';
      if (this.statsEl)  this.statsEl.style.display  = 'none';
      if (this.hintEl)   this.hintEl.style.display   = 'none';
      if (this.legendEl) this.legendEl.style.display = 'none';
    }
    _showGraph() {
      this.loadingEl.style.display = 'none';
      this.svgEl.style.display     = 'block';
      this.emptyEl.style.display   = 'none';
      if (this.hintEl)   this.hintEl.style.display   = '';
      if (this.legendEl) this.legendEl.style.display = '';
    }
    _showEmpty() {
      this.loadingEl.style.display = 'none';
      this.svgEl.style.display     = 'none';
      this.emptyEl.style.display   = 'flex';
      if (this.statsEl)  this.statsEl.style.display  = 'none';
      if (this.hintEl)   this.hintEl.style.display   = 'none';
      if (this.legendEl) this.legendEl.style.display = 'none';
    }
    _showError(msg) {
      this.loadingEl.innerHTML = `
        <div class="text-center py-4">
          <i class="bi bi-exclamation-triangle text-danger" style="font-size:2.5rem;"></i>
          <p class="mt-2 text-danger">グラフデータの読み込みに失敗しました。</p>
          <small class="text-muted">${_esc(msg)}</small><br>
          <button class="btn btn-outline-secondary btn-sm mt-3" onclick="location.reload()">
            <i class="bi bi-arrow-clockwise me-1"></i>再試行
          </button>
        </div>`;
      this.loadingEl.style.display = 'flex';
    }
  }

  // ==============================
  // ユーティリティ
  // ==============================
  function _hexPoints(r) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI / 3) * i - Math.PI / 6;
      pts.push(`${(r * Math.cos(angle)).toFixed(2)},${(r * Math.sin(angle)).toFixed(2)}`);
    }
    return pts.join(' ');
  }

  function _triPoints(r) {
    return `0,${-r} ${(r * 0.866).toFixed(2)},${(r * 0.5).toFixed(2)} ${(-r * 0.866).toFixed(2)},${(r * 0.5).toFixed(2)}`;
  }

  function _esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ISO 日付文字列から経過日数を返す（不正値は null）
  function _daysSince(iso) {
    if (!iso) return null;
    const t = new Date(iso + 'T00:00:00');
    if (isNaN(t.getTime())) return null;
    return Math.floor((Date.now() - t.getTime()) / 86400000);
  }

  // 売却済みなのに振り返り（retrospective）ノートが無い日記か
  // （コンテンツ情報のない secondary ノードは判定対象外）
  function _needsRetrospective(d) {
    return d.node_type === 'diary'
      && d.status === 'sold'
      && typeof d.note_count === 'number'
      && !d.has_retrospective;
  }

  // 日記ノードの鮮度を判定する: 'fresh' | 'stale' | null
  // fresh は継続記録の有無のみで判定。stale（放置）は保有中の銘柄に限り、
  // 最終継続記録（なければ日記作成日）からの経過で判定する。
  function _freshness(d) {
    if (d.node_type !== 'diary') return null;
    const noteDays = _daysSince(d.last_note_date);
    if (noteDays !== null && noteDays <= FRESH_DAYS) return 'fresh';
    if (d.status === 'holding') {
      const baseDays = noteDays !== null ? noteDays : _daysSince(d.created_date);
      if (baseDays !== null && baseDays > STALE_DAYS) return 'stale';
    }
    return null;
  }

  // ==============================
  // エントリポイント
  // ==============================
  document.addEventListener('DOMContentLoaded', () => {
    if (typeof GRAPH_CONFIG === 'undefined') return;
    new DiaryGraph(GRAPH_CONFIG);
  });

})();
