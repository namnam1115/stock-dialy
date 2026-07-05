/* ドリルダウン探索グラフ（TG-DD）
 * 空から要素（タグ）を1つ選び、近傍APIで1段ずつ展開する。再タップで畳む（子孫も畳む）。
 * 設計: docs/graph_drilldown_redesign.md
 */
(function () {
  'use strict';

  const AXIS_COLORS = {
    theme: '#7c3aed', business_model: '#0891b2', risk: '#dc2626',
    capital_policy: '#16a34a', macro: '#d97706', event: '#6b7280', custom: '#9333ea',
  };
  const STOCK_COLOR = '#10b981';

  const cfg = window.EXG_CONFIG || {};
  const EXPLORE_ITEMS = JSON.parse(document.getElementById('exg-items-data').textContent || '[]');

  const svgEl = document.getElementById('exgSvg');
  const emptyEl = document.getElementById('exgEmpty');
  const resetBtn = document.getElementById('exgReset');
  const searchIn = document.getElementById('exgSearch');
  const resultsEl = document.getElementById('exgResults');
  const panelEl = document.getElementById('exgPanel');
  const panelKind = document.getElementById('exgPanelKind');
  const panelName = document.getElementById('exgPanelName');
  const panelHint = document.getElementById('exgPanelHint');
  const panelLink = document.getElementById('exgPanelLink');

  // ---- 状態 -------------------------------------------------------------
  // meta[id] = ノードのメタ情報（label/type/axis/symbol/detail_url）
  // neighborCache[id] = そのノードの近傍ID配列（取得済みのみ）
  // expanded = 展開中ノードID集合。可視ノードは seed ∪ expanded ∪ (expandedの近傍)
  // revealedBy[id] = そのノードを最初に出した展開元ノード（=展開ツリーの親）。
  //   畳むときの「子孫」判定に使う。グラフは無向で近傍リストに親も含まれるため、
  //   近傍を辿って畳むと親まで畳んでしまう（→展開ツリーを revealedBy で辿る）。
  const meta = new Map();
  const neighborCache = new Map();
  const expanded = new Set();
  const revealedBy = new Map();
  let seedId = null;

  let simulation = null;
  const svg = d3.select(svgEl);
  const rootG = svg.append('g');
  const linkG = rootG.append('g');
  const nodeG = rootG.append('g');
  const zoom = d3.zoom().scaleExtent([0.3, 3]).on('zoom', (e) => rootG.attr('transform', e.transform));
  svg.call(zoom);

  // ---- 可視グラフの再計算（expanded から導出） ---------------------------
  function computeVisible() {
    const visible = new Set();
    if (seedId && meta.has(seedId)) visible.add(seedId);
    expanded.forEach((id) => {
      visible.add(id);
      (neighborCache.get(id) || []).forEach((nid) => visible.add(nid));
    });
    // リンク：展開ノード e と その近傍 n（両端可視）を結ぶ
    const linkKeys = new Set();
    const links = [];
    expanded.forEach((e) => {
      (neighborCache.get(e) || []).forEach((n) => {
        if (!visible.has(e) || !visible.has(n)) return;
        const key = e < n ? e + '|' + n : n + '|' + e;
        if (linkKeys.has(key)) return;
        linkKeys.add(key);
        links.push({ source: e, target: n });
      });
    });
    const nodes = [];
    visible.forEach((id) => { if (meta.has(id)) nodes.push(Object.assign({ id }, meta.get(id))); });
    return { nodes, links };
  }

  // ---- 近傍取得（オンデマンド） -----------------------------------------
  async function fetchNeighbors(nodeId) {
    if (neighborCache.has(nodeId)) return true;
    const url = cfg.neighborsUrl + '?node=' + encodeURIComponent(nodeId);
    let data;
    try {
      const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (!res.ok) return false;
      data = await res.json();
    } catch (e) { return false; }
    // ノード自身のメタを更新（stock なら detail_url 等が付く）
    if (data.node) meta.set(data.node.id, nodeMeta(data.node));
    const ids = [];
    (data.neighbors || []).forEach((nb) => {
      if (!meta.has(nb.id)) meta.set(nb.id, nodeMeta(nb));
      ids.push(nb.id);
    });
    neighborCache.set(nodeId, ids);
    return true;
  }

  function nodeMeta(n) {
    return {
      type: n.type, label: n.label, axis: n.axis || 'theme',
      symbol: n.symbol || null, detail_url: n.detail_url || null,
    };
  }

  // ---- 展開 / 畳み ------------------------------------------------------
  async function expand(nodeId) {
    const ok = await fetchNeighbors(nodeId);
    if (!ok) return;
    expanded.add(nodeId);
    // このノードが初めて出す近傍に「展開元」を記録する（展開ツリーの親）。
    (neighborCache.get(nodeId) || []).forEach((nid) => {
      if (nid !== seedId && !revealedBy.has(nid)) revealedBy.set(nid, nodeId);
    });
    render();
  }

  // nodeId の展開ツリー子孫（revealedBy を辿って nodeId に行き着く展開中ノード）
  function expandedDescendants(nodeId) {
    const result = new Set();
    expanded.forEach((e) => {
      let cur = e;
      const guard = new Set();
      while (cur != null && !guard.has(cur)) {
        guard.add(cur);
        const parent = revealedBy.get(cur);
        if (parent === nodeId) { result.add(e); break; }
        cur = parent;
      }
    });
    return result;
  }

  function collapse(nodeId) {
    // このノードと、その展開ツリー子孫だけを畳む（近傍にいる親は畳まない）。
    expanded.delete(nodeId);
    expandedDescendants(nodeId).forEach((d) => expanded.delete(d));
    // 畳んだ枝から revealedBy を張り直せるよう、消えたノードの記録は残してよい
    // （再展開時は first-writer 済みでも同じ親を指すため問題なし）。
    render();
  }

  function toggleNode(nodeId) {
    if (expanded.has(nodeId)) collapse(nodeId);
    else expand(nodeId);
  }

  // ---- 起点セット / リセット --------------------------------------------
  // item: {id, type:'tag'|'stock', label, axis?, symbol?}。タグ・銘柄どちらからも開始できる。
  async function seed(item) {
    if (item.type === 'stock') {
      meta.set(item.id, { type: 'stock', label: item.label, axis: 'theme', symbol: item.symbol || null, detail_url: null });
    } else {
      meta.set(item.id, { type: 'tag', label: item.label, axis: item.axis || 'theme', symbol: null, detail_url: null });
    }
    seedId = item.id;
    await expand(item.id);  // stock なら API 応答の node から detail_url が入る
    emptyEl.style.display = 'none';
    resetBtn.style.display = 'inline-flex';
  }

  function reset() {
    meta.clear(); neighborCache.clear(); expanded.clear(); revealedBy.clear(); seedId = null;
    render();
    emptyEl.style.display = 'flex';
    resetBtn.style.display = 'none';
    hidePanel();
  }

  // ---- パネル -----------------------------------------------------------
  function showPanel(d) {
    panelKind.textContent = d.type === 'stock' ? '銘柄' : '要素（タグ）';
    panelName.textContent = (d.type === 'tag' ? '@' : '') + d.label;
    const isExp = expanded.has(d.id);
    panelHint.textContent = isExp ? 'タップで畳む' : 'タップで関連を展開';
    if (d.type === 'stock' && d.detail_url) {
      panelLink.href = d.detail_url;
      panelLink.style.display = 'inline-flex';
    } else {
      panelLink.style.display = 'none';
    }
    panelEl.classList.add('open');
  }
  function hidePanel() { panelEl.classList.remove('open'); }

  // ---- 描画（増分・force） ----------------------------------------------
  function nodeColor(d) { return d.type === 'stock' ? STOCK_COLOR : (AXIS_COLORS[d.axis] || AXIS_COLORS.theme); }
  function nodeRadius(d) { return d.id === seedId ? 13 : (d.type === 'stock' ? 9 : 10); }

  function render() {
    const { nodes, links } = computeVisible();
    const w = svgEl.clientWidth || 800;
    const h = svgEl.clientHeight || 560;

    // 既存座標は保つ（増分描画）。既存は静止させ、新規ノードは展開元の近くに置いて
    // 原点から飛んでこないようにする（リンク張力による激しい揺れを防ぐ）。
    const cx = w / 2, cy = h / 2;
    const prev = new Map((simulation ? simulation.nodes() : []).map((n) => [n.id, n]));
    nodes.forEach((n) => {
      const p = prev.get(n.id);
      if (p) {
        n.x = p.x; n.y = p.y; n.vx = 0; n.vy = 0;
      } else {
        const src = prev.get(revealedBy.get(n.id));
        const bx = src ? src.x : cx;
        const by = src ? src.y : cy;
        const ang = Math.random() * Math.PI * 2, rad = 45 + Math.random() * 35;
        n.x = bx + Math.cos(ang) * rad;
        n.y = by + Math.sin(ang) * rad;
      }
    });

    const link = linkG.selectAll('line.exg-link').data(links, (d) => (d.source.id || d.source) + '|' + (d.target.id || d.target));
    link.exit().remove();
    link.enter().append('line').attr('class', 'exg-link');

    const node = nodeG.selectAll('g.exg-node').data(nodes, (d) => d.id);
    node.exit().remove();
    const enter = node.enter().append('g').attr('class', 'exg-node')
      .on('click', (ev, d) => { ev.stopPropagation(); showPanel(d); toggleNode(d.id); })
      .call(d3.drag()
        .on('start', (ev, d) => { if (!ev.active) simulation.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end', (ev, d) => { if (!ev.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));
    enter.each(function (d) {
      const g = d3.select(this);
      if (d.type === 'stock') {
        g.append('rect').attr('rx', 3).attr('stroke', '#fff').attr('stroke-width', 1.5);
      } else {
        g.append('circle').attr('stroke', '#fff').attr('stroke-width', 1.5);
      }
      g.append('text').attr('y', 4).attr('x', 0).attr('text-anchor', 'middle');
    });

    const merged = enter.merge(node);
    merged.select('circle').attr('r', (d) => nodeRadius(d)).attr('fill', (d) => nodeColor(d));
    merged.select('rect')
      .attr('width', (d) => nodeRadius(d) * 2).attr('height', (d) => nodeRadius(d) * 2)
      .attr('x', (d) => -nodeRadius(d)).attr('y', (d) => -nodeRadius(d)).attr('fill', (d) => nodeColor(d));
    merged.select('text').attr('y', (d) => nodeRadius(d) + 12)
      .text((d) => (d.type === 'tag' ? '@' : '') + d.label);

    if (!simulation) {
      simulation = d3.forceSimulation()
        // velocityDecay（摩擦）を高めに取り、重心を毎tick動かす forceCenter ではなく
        // 弱い forceX/forceY で中央に寄せる → 全体が左右に振れるのを防ぐ。
        .velocityDecay(0.55)
        .force('charge', d3.forceManyBody().strength(-240))
        .force('collide', d3.forceCollide().radius(30))
        .force('x', d3.forceX(cx).strength(0.06))
        .force('y', d3.forceY(cy).strength(0.06))
        .on('tick', () => {
          linkG.selectAll('line.exg-link')
            .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
            .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
          nodeG.selectAll('g.exg-node').attr('transform', (d) => `translate(${d.x},${d.y})`);
        });
    }
    simulation.force('x').x(cx);
    simulation.force('y').y(cy);
    simulation.nodes(nodes);
    simulation.force('link', d3.forceLink(links).id((d) => d.id).distance(90).strength(0.4));
    // 弱めのリヒート（既存ノードは静止済み・新規は展開元近くにあるので大きく暴れない）
    simulation.alpha(0.35).restart();
  }

  svg.on('click', hidePanel);

  // ---- 検索入口 ---------------------------------------------------------
  let activeIdx = -1;
  function renderResults(q) {
    const query = q.trim().toLowerCase();
    if (!query) { resultsEl.classList.remove('open'); resultsEl.innerHTML = ''; return; }
    // タグは名前、銘柄は名前＋コード（symbol）で部分一致。要素（タグ）を先に、銘柄を後に並べる。
    const matches = EXPLORE_ITEMS.filter((it) =>
      it.label.toLowerCase().includes(query) ||
      (it.type === 'stock' && (it.symbol || '').toLowerCase().includes(query))
    ).sort((a, b) => (a.type === b.type ? 0 : (a.type === 'tag' ? -1 : 1))).slice(0, 30);
    activeIdx = -1;
    if (!matches.length) {
      resultsEl.innerHTML = '<div class="exg-res-empty">一致する要素・銘柄がありません</div>';
      resultsEl.classList.add('open');
      return;
    }
    resultsEl.innerHTML = matches.map((it) => {
      if (it.type === 'stock') {
        return `<div class="exg-res" data-id="${it.id}" data-type="stock" data-label="${escapeHtml(it.label)}" data-symbol="${escapeHtml(it.symbol || '')}">
           <span class="exg-res-dot" style="background:${STOCK_COLOR};border-radius:3px;"></span>
           <span>${escapeHtml(it.label)}</span>
           <span style="margin-left:auto;font-size:12px;color:var(--t3);">${escapeHtml(it.symbol || '')}</span>
         </div>`;
      }
      return `<div class="exg-res" data-id="${it.id}" data-type="tag" data-label="${escapeHtml(it.label)}" data-axis="${it.axis}">
         <span class="exg-res-dot" style="background:${AXIS_COLORS[it.axis] || AXIS_COLORS.theme};"></span>
         <span>@${escapeHtml(it.label)}</span>
       </div>`;
    }).join('');
    resultsEl.classList.add('open');
    resultsEl.querySelectorAll('.exg-res').forEach((el) => {
      el.addEventListener('click', () => pick(el));
    });
  }
  function pick(el) {
    resultsEl.classList.remove('open');
    searchIn.value = '';
    seed({
      id: el.dataset.id, type: el.dataset.type, label: el.dataset.label,
      axis: el.dataset.axis, symbol: el.dataset.symbol,
    });
  }
  function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  searchIn.addEventListener('input', (e) => renderResults(e.target.value));
  searchIn.addEventListener('keydown', (e) => {
    const items = Array.from(resultsEl.querySelectorAll('.exg-res'));
    if (!items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); }
    else if (e.key === 'Enter') { e.preventDefault(); if (activeIdx >= 0) pick(items[activeIdx]); else if (items.length === 1) pick(items[0]); return; }
    else return;
    items.forEach((it, i) => it.classList.toggle('active', i === activeIdx));
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.exg-search')) resultsEl.classList.remove('open');
  });
  resetBtn.addEventListener('click', reset);

  window.addEventListener('resize', () => { if (simulation) render(); });

  // URL の ?start=<node id> があれば、その要素/銘柄を起点に自動で開く
  // （タグ詳細等からの「この要素で探索 →」導線用）。
  const startId = new URLSearchParams(location.search).get('start');
  if (startId) {
    const item = EXPLORE_ITEMS.find((it) => it.id === startId);
    if (item) seed(item);
  }
})();
