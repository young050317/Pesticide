/* ============================================================
   농약 정보 - 프론트엔드
   ============================================================ */
const RECENT_KEY = "pesticide_recent_searches_v2";
const GAUGE_MAX = 200;                       // 게이지 최대값 (api.py의 criteria.게이지최대와 동일)
const PAGE_SIZE = 20;                        // 검색 결과 한 번에 보여줄 개수
const PAGES = ["homePage", "detailPage", "ingredientPage", "recommendPage"];
const LEVEL_TEXT = { low: "유해성 낮음", mid: "유해성 중간", high: "유해성 높음", none: "자료 없음" };

let currentPage = "homePage";
let detailBack = "homePage";
let ingredientBack = "detailPage";
let detailContext = { crop: "", pest: "" };
let detailReturnScroll = 0;
let detailReturnTab = "info";
let searchState = { values: {}, sort: "recommend", purpose: "", offset: 0, total: 0, busy: false };
let searchRequestSerial = 0;

/* ---------- 공통 유틸 ---------- */
function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
const esc = escapeHtml;
const $ = id => document.getElementById(id);

function showPage(id) {
    PAGES.forEach(p => { const el = $(p); if (el) el.hidden = p !== id; });
    currentPage = id;
    window.scrollTo({ top: 0 });
}
function openMenu() {}
function closeMenu() {}
function goHome() { showPage("homePage"); }
function showRecommend() { showPage("recommendPage"); }
function showCompare() { showPage("comparePage"); if (!$("compareInputs").children.length) { addCompareInput(); addCompareInput(); } }

async function getJson(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const res = await fetch(url, { cache: "no-store", signal: controller.signal });
        if (!res.ok) {
            const details = (await res.text()).slice(0, 250);
            throw new Error(`HTTP ${res.status} · ${details}`);
        }
        return await res.json();
    } catch (e) {
        if (e.name === "AbortError") throw new Error("15초 동안 서버 응답이 없습니다. 서버 터미널의 GET 요청과 오류 메시지를 확인하세요.");
        throw e;
    } finally {
        clearTimeout(timeout);
    }
}

/* ---------- 독성 표시 조각 ---------- */
function pill(level, text) { return `<span class="pill ${level}">${esc(text ?? LEVEL_TEXT[level])}</span>`; }

/** 독성 게이지: 파랑(낮음) · 노랑(중간) · 높음(빨강). range=[최소,최대]이면 점선 구간으로 표시 */
function gauge(value, range, mini) {
    const pct = v => Math.max(0, Math.min(100, (v / GAUGE_MAX) * 100));
    let mark = "";
    if (range) {
        mark = `<div class="gauge-range" style="left:${pct(range[0])}%;width:${Math.max(2, pct(range[1]) - pct(range[0]))}%"></div>`;
    } else if (value != null) {
        mark = `<div class="gauge-marker" style="left:${pct(value)}%"></div>`;
    }
    return `<div class="gauge ${mini ? "mini" : ""}">
        <div class="gauge-bar"><i class="g-low"></i><i class="g-mid"></i><i class="g-high"></i></div>${mark}
        <div class="gauge-ticks"><span style="left:0%">0</span><span style="left:25%">50</span><span style="left:50%">100</span><span style="left:100%">200+</span></div>
    </div>`;
}

function ghsMini(codes) {
    if (!codes || !codes.length) return "";
    return `<span class="ghs-mini">${codes.map(c => `<img src="/static/ghs/${esc(c)}.png" alt="${esc(c)}" title="${esc(c)}">`).join("")}</span>`;
}
function ghsBig(list) {
    if (!list || !list.length) return '<p class="no-ghs">표시된 위험 그림문자가 없습니다.</p>';
    return `<div class="ghs-row">${list.map(g => `<div class="ghs-item"><img src="/static/ghs/${esc(g.code)}.png" alt="${esc(g.code)} ${esc(g.name)}"><b>${esc(g.name)}</b><span>${esc(g.description)}</span></div>`).join("")}</div>`;
}
function ghsInline(list) {
    if (!list || !list.length) return "";
    return `<div class="ghs-inline">${list.map(g => `<figure><img src="/static/ghs/${esc(g.code)}.png" alt="${esc(g.code)}">${esc(g.name)}</figure>`).join("")}</div>`;
}

function legendHtml(c) {
    return `<div class="legend">
        <span><i style="background:var(--low-bar)"></i>낮음 · ${esc(c?.낮음 ?? "")}</span>
        <span><i style="background:var(--mid-bar)"></i>중간 · ${esc(c?.중간 ?? "")}</span>
        <span><i style="background:var(--high-bar)"></i>높음 · ${esc(c?.높음 ?? "")}</span></div>`;
}

/* ---------- 최근 검색 ---------- */
function loadRecent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } }
function saveRecent(values) {
    const label = [values.product, values.crop, values.pest].filter(Boolean).join(" + ");
    if (!label) return;
    let items = loadRecent().filter(x => x.label !== label);
    items.unshift({ ...values, label });
    localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, 10)));
    renderRecent();
}
function removeRecent(i) { const items = loadRecent(); items.splice(i, 1); localStorage.setItem(RECENT_KEY, JSON.stringify(items)); renderRecent(); }
function clearRecentSearches() { localStorage.removeItem(RECENT_KEY); renderRecent(); }
function applyRecent(i) { const it = loadRecent()[i]; if (it) setSearch(it.product || "", it.crop || "", it.pest || ""); }
function renderRecent() {
    const items = loadRecent();
    $("recentWrap").hidden = !items.length;       // 기록이 없으면 영역 자체를 숨긴다
    $("recentSearches").innerHTML = items.map((it, i) =>
        `<button class="recent-chip" onclick="applyRecent(${i})"><span>${esc(it.label)}</span><span class="x" onclick="event.stopPropagation(); removeRecent(${i})">×</span></button>`).join("");
}
function setSearch(product, crop, pest) {
    $("productInput").value = product; $("cropInput").value = crop; $("pestInput").value = pest;
    searchPesticide();
}

/* ---------- 검색 ---------- */
function searchPesticide() {
    const values = { product: $("productInput").value.trim(), crop: $("cropInput").value.trim(), pest: $("pestInput").value.trim() };
    if (!values.product && !values.crop && !values.pest) { alert("검색 조건을 하나 이상 입력해주세요."); return; }
    saveRecent(values);
    searchState = { values, sort: ["recommend","tox","name"].includes($("sortSelect").value) ? $("sortSelect").value : "recommend", purpose: "", offset: 0, total: 0, busy: false };
    fetchSearch(true);
}

function changeSort() {
    searchState.sort = $("sortSelect").value || "recommend";
    searchState.offset = 0;
    fetchSearch(true);
}
function setPurpose(p) { searchState.purpose = p; searchState.offset = 0; fetchSearch(true); }
function loadMore() { fetchSearch(false); }

async function fetchSearch(reset) {
    // 새 정렬 선택은 진행 중인 요청을 무시하고 최신 정렬로 다시 요청한다.
    if (searchState.busy && !reset) return;
    const serial = ++searchRequestSerial;
    searchState.busy = true;
    const { values, sort, purpose, offset } = searchState;
    const params = new URLSearchParams({ sort, offset: reset ? 0 : offset, limit: PAGE_SIZE });
    ["product", "crop", "pest"].forEach(k => { if (values[k]) params.set(k, values[k]); });
    if (purpose) params.set("purpose", purpose);

    const box = $("searchResults");
    $("resultSection").hidden = false;           // 검색 버튼을 누른 뒤에만 결과 창이 나타난다
    if (reset) {
        box.innerHTML = '<div class="loading">검색 중입니다...</div>';
        $("moreButton").hidden = true;
    } else {
        $("moreButton").disabled = true; $("moreButton").textContent = "불러오는 중...";
    }
    try {
        const data = await getJson(`/api/search?${params}`);
        if (serial !== searchRequestSerial) return; // 이전 응답이 새 정렬 결과를 덮지 않도록
        searchState.total = data.total;
        if (reset) {
            if (!data.results.length) {
                box.innerHTML = '<div class="empty"><h3>검색 결과가 없습니다</h3><p>조건을 줄이거나 다른 단어로 검색해보세요.</p></div>';
            } else {
                box.innerHTML = data.results.map(resultCard).join("");
            }
            renderPurposeChips(data.facets);
            if (!purpose) $("resultSection").scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
            box.insertAdjacentHTML("beforeend", data.results.map(resultCard).join(""));
        }
        searchState.offset = data.offset + data.results.length;
        $("resultSummary").textContent = `총 ${data.total.toLocaleString()}개 중 ${searchState.offset.toLocaleString()}개 표시`;
        $("moreButton").hidden = !data.has_more;
        $("moreButton").disabled = false;
        $("moreButton").textContent = `더 보기 (${Math.min(PAGE_SIZE, data.total - searchState.offset)}개)`;
        $("rankNote").textContent = {
            tox: "품목 인축독성 → 중대 H코드 → 제품 참고점수(원제 함량 반영) 순으로 비교합니다.",
            recommend: "품목 인축독성 → 중대 H코드 → 원제 유해성 구간 → 희석농도 → 제품 참고점수(원제 함량 반영) → 안전사용시기 순서입니다. 생물농약은 별도로 배치합니다.",
            name: "상표명 가나다순으로 보여줍니다."
        }[sort];
    } catch (e) {
        if (serial === searchRequestSerial) box.innerHTML = `<div class="empty error"><h3>검색 중 오류가 발생했습니다</h3><p>${esc(e.message)}</p></div>`;
    } finally {
        if (serial === searchRequestSerial) searchState.busy = false;
    }
}

function renderPurposeChips(facets) {
    const entries = Object.entries(facets || {});
    const all = entries.reduce((s, [, n]) => s + n, 0);
    if (entries.length < 2) { $("purposeFilter").innerHTML = ""; return; }
    const on = searchState.purpose;
    $("purposeFilter").innerHTML =
        `<button class="chip ${on === "" ? "on" : ""}" onclick="setPurpose('')">전체<b>${all.toLocaleString()}</b></button>` +
        entries.map(([k, n]) => `<button class="chip ${on === k ? "on" : ""}" onclick="setPurpose('${esc(k)}')">${esc(k)}<b>${n.toLocaleString()}</b></button>`).join("");
}

function resultCard(item) {
    const t = item.독성 || {};
    const lv = t.등급 || "none";
    // 제품 참고점수가 자료 없음/미확인인 경우 중대 유해성도 판단 불가로 표시한다.
    // 기존의 "중대 유해성 확인 제품보다 후순위"라는 내부 정렬 문구를
    // 화면 상태 판정에 사용하면 "미확인" 제품이 "있음"으로 잘못 표시될 수 있다.
    let severeStatus = "미확인";
    if (t.상태 === "nodata" || t.상태 === "partial" || t.상태 === "incomplete") {
        severeStatus = "미확인";
    } else if (Array.isArray(t.중대한유해성) && t.중대한유해성.length > 0) {
        severeStatus = "있음";
    } else {
        severeStatus = "없음";
    }
    return `<button class="result-card lv-${lv}" onclick="openDetail('${esc(item.등록번호)}')">
        <div class="rc-top">
            <div class="rc-title"><div class="rc-brand">${esc(item.상표명 || "상표명 없음")}</div><div class="rc-item">${esc(item.품목명 || "")}${item.용도 ? " · " + esc(item.용도) : ""}</div></div>
            <div class="rc-bottom" style="margin:0">${pill(lv, `${LEVEL_TEXT[lv]}${t.점수 != null ? " · " + (t.점수확정 ? "" : "≤") + fmtScore(t.점수) + "점" : ""}`)}${ghsMini(t.그림문자)}</div>
        </div>
        <div class="rc-meta">
            <span><b>작물</b>${esc(item.작물명 || "-")}</span>
            <span><b>병해충</b>${esc(item.적용병해충 || "-")}</span>
            <span><b>인축독성</b>${esc(item.인축독성 || "-")}</span>
            <span><b>중대 유해성</b>${esc(severeStatus)}</span>
        </div>
        <div class="result-arrow">›</div>
    </button>`;
}

/* ============================================================
   농약 상세 (상단 탭: 기본 정보 · 적용 정보 · 유해성 정보 · 성분 · 대안 제품)
   ============================================================ */
const TABS = [["info", "기본 정보"], ["usage", "적용 정보"], ["tox", "유해성 정보"], ["ing", "성분"], ["alt", "대안 제품"]];

async function openDetail(reg, tab = "info") {
    const sourcePage = currentPage;
    const sourceScroll = window.scrollY;
    const sourceTab = document.querySelector('.tabs button.on')?.dataset.tab || "info";
    const sourceContext = { ...detailContext };
    try {
        const data = await getJson(`/api/pesticides/${encodeURIComponent(reg)}`);
        if (sourcePage !== "detailPage") {
            detailBack = sourcePage;
            detailReturnScroll = sourceScroll;
        } else {
            detailBack = "detailPage";
            detailReturnScroll = sourceScroll;
            detailReturnTab = sourceTab;
        }
        if (sourcePage === "homePage") {
            detailContext = { crop: searchState.values.crop || "", pest: searchState.values.pest || "" };
        } else if (sourcePage === "recommendPage") {
            detailContext = { crop: $("recCrop").value.trim(), pest: $("recPest").value.trim() };
        } else if (sourcePage === "comparePage") {
            detailContext = { crop: $("cmpCrop").value.trim(), pest: $("cmpPest").value.trim() };
        } else if (sourcePage === "detailPage") {
            detailContext = sourceContext;
        } else {
            detailContext = { crop: "", pest: "" };
        }
        $("detailContent").innerHTML = createDetail(data);
        showPage("detailPage");
        window._detailReg = reg;
        selectTab(tab);
    } catch (e) { alert(e.message); }
}
function backFromDetail() {
    const destination = detailBack || "homePage";
    showPage(destination === "detailPage" ? "homePage" : destination);
    requestAnimationFrame(() => window.scrollTo({ top: detailReturnScroll, behavior: "instant" }));
}

function selectTab(name) {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = p.dataset.panel !== name; });
    if (name === "alt") loadAlternatives();
    const tabs = document.querySelector(".tabs");
    if (tabs) window.scrollTo({ top: Math.max(0, tabs.offsetTop - 70) });
}

function infoRows(item, fields) {
    return `<dl class="info-grid" style="margin:0">${fields.map(([k, label]) =>
        `<div class="info-row"><dt>${label}</dt><dd>${esc(item[k] || "-")}</dd></div>`).join("")}</dl>`;
}

function createDetail(data) {
    const p = data.product, t = data.tox, c = data.criteria;
    const lv = t.등급;
    const tabs = TABS.map(([k, label]) =>
        `<button data-tab="${k}" class="${k === "tox" ? "tox-tab" : ""}" onclick="selectTab('${k}')">${label}${k === "tox" ? `<span class="dot ${lv}"></span>` : ""}</button>`).join("");

    return `
    <section class="detail-hero">
        <span class="cat">${esc(p.용도 || "농약")}</span>
        <h2>${esc(p.상표명 || p.품목명)}</h2>
        <p>${esc(p.품목명 || "")} · ${esc(p.회사명 || "")}</p>
        <div class="hero-tox">${pill(lv, `${LEVEL_TEXT[lv]}${t.참고점수 != null ? " · " + (t.참고점수_확정 ? "" : "≤") + fmtScore(t.참고점수) + "점" : ""}`)}${ghsMini((t.그림문자 || []).map(g => g.code))}
            <button class="chip" onclick="selectTab('tox')">유해성 정보 자세히 ›</button></div>
    </section>

    <nav class="tabs">${tabs}</nav>

    <div class="tab-panel" data-panel="info"><section class="card">
        <h3 class="section-title">기본 정보</h3>
        ${infoRows(p, [["등록번호", "등록번호"], ["제조수입", "구분"], ["상표명", "상표명"], ["품목명", "품목명"], ["주성분함량", "주성분함량"], ["회사명", "회사명"], ["등록일", "등록일"], ["제형", "제형"], ["용도", "용도"], ["작용기작", "작용기작"]])}
    </section></div>

    <div class="tab-panel" data-panel="usage"><section class="card">
        <h3 class="section-title">작물·병해충별 사용 정보</h3>
        ${usageTable(data.usages || [])}
    </section></div>

    <div class="tab-panel" data-panel="tox">${toxPanel(data)}</div>

    <div class="tab-panel" data-panel="ing"><section class="card">
        <h3 class="section-title">성분 (원제)</h3>
        ${(data.ingredients || []).map(i => `<button class="ing-chip" onclick="openIngredient('${esc(i.원제ID)}')">${esc(i.한글명 || i.영문명)} ${pill(i.등급, null)}</button>`).join("") || "<p class='muted'>연결된 원제 자료가 없습니다.</p>"}
        <p class="note-text">성분을 누르면 원제별 H-code·연결 제품을 볼 수 있습니다.</p>
    </section></div>

    <div class="tab-panel" data-panel="alt"><section class="card">
        <h3 class="section-title">같은 작물·병해충에 쓸 수 있는 다른 제품</h3>
        <p class="muted small" id="altIntro" style="margin-bottom:12px">유해성이 낮은 제품부터 보여줍니다.</p>
        <div id="altResults"><div class="loading">불러오는 중입니다...</div></div>
    </section></div>`;
}

function usageTable(usages) {
    if (!usages.length) return '<div class="empty">등록된 적용 정보가 없습니다.</div>';
    return `<div class="table-wrap"><table class="usage"><thead><tr><th>작물</th><th>병해충</th><th>희석배수</th><th>사용적기</th><th>사용방법</th><th>안전사용시기</th><th>횟수</th></tr></thead><tbody>
        ${usages.map(x => `<tr><td>${esc(x.작물명 || "-")}</td><td>${esc(x.적용병해충 || "-")}</td><td>${esc(dilutionText(x.희석배수))}</td><td>${esc(x.사용적기 || "-")}</td><td>${esc(x.사용방법 || "-")}</td><td>${esc(x.안전사용시기 || "-")}</td><td>${esc(x.안전사용횟수 || "-")}</td></tr>`).join("")}
    </tbody></table></div>`;
}

async function loadAlternatives() {
    const box = $("altResults");
    if (!box) return;
    const cacheKey = window._detailReg + "?" + new URLSearchParams(Object.fromEntries(Object.entries(detailContext).filter(([,v]) => v))).toString();
    if (box.dataset.loaded === cacheKey) return;
    try {
        const params = new URLSearchParams();
        if (detailContext.crop) params.set("crop", detailContext.crop);
        if (detailContext.pest) params.set("pest", detailContext.pest);
        const key = window._detailReg + "?" + params.toString();
        const data = await getJson(`/api/alternatives/${encodeURIComponent(window._detailReg)}?${params}`);
        box.dataset.loaded = key;
        if (data.crop || data.pest) $("altIntro").textContent = `${data.crop || ""} ${data.pest ? "· " + data.pest : ""} 조건에서 품목 인축독성 및 원제 유해성 기준의 참고 순위로 보여줍니다.`;
        box.innerHTML = data.results.length ? data.results.map(resultCard).join("") : `<div class="empty">${esc(data.message || "해당 조건에서 비교할 다른 제품을 찾지 못했습니다.")}</div>`;
    } catch { box.innerHTML = '<div class="empty error">대안 제품을 불러오지 못했습니다.</div>'; }
}

/* ---------- 유해성 정보 탭 ---------- */
function signalWord(ings) {
    const words = ings.map(i => i.신호어 || "");
    if (words.some(w => w === "위험" || w === "경고 또는 위험")) return "위험";
    if (words.some(w => w === "경고")) return "경고";
    return words.some(w => w === "없음") ? "없음" : "미기재";
}

function fmtScore(v) {
    if (v == null || Number.isNaN(Number(v))) return "-";
    return Number(v).toFixed(2);
}

function dilutionText(value) {
    return String(value ?? "").trim() === "" ? "원액 사용" : String(value);
}

function contributionText(i) {
    if (i.성분기여점수 != null) return `${fmtScore(i.성분기여점수)}점`;
    if (i.성분기여점수범위) return `${fmtScore(i.성분기여점수범위[0])}~${fmtScore(i.성분기여점수범위[1])}점`;
    return "미확인";
}

function toxPanel(data) {
    const t = data.tox, c = data.criteria, ings = data.ingredients || [];
    const lv = t.등급;
    const confirmed = t.참고점수_확정;
    const range = t.참고점수범위 || null;

    let stateNote = "";
    if (t.상태 === "range") stateNote = `<div class="alert-box yellow"><b>일부 점수는 범위로 표시됩니다</b>H300·H310·H330·H314는 세부 구분(Category)에 따라 점수가 달라져 가능한 최소~최대 범위를 표시합니다.</div>`;
    else if (t.상태 === "partial" || t.상태 === "incomplete") stateNote = `<div class="alert-box yellow"><b>제품 참고점수 계산에 필요한 자료가 일부 없습니다</b>자료가 없는 부분은 0점이나 안전으로 처리하지 않고 미확인으로 표시합니다.</div>`;
    else if (t.상태 === "nodata") stateNote = `<div class="alert-box gray"><b>원제 유해성 자료가 없습니다</b>연결된 원제의 H-code 자료가 없어 제품 참고점수를 계산하지 못했습니다.</div>`;

    const severe = t.중대한유해성?.length
        ? `<div class="alert-box red"><b>중대한 유해성 확인 · ${esc(t.중대한유해성.join(", "))}</b>점수와 별개로 취급·보호구 안내를 꼭 확인하세요.</div>` : "";

    const livestock = t.인축독성 || {};
    const productScore = t.제품독성점수 != null ? fmtScore(t.제품독성점수) :
        (t.제품독성점수범위 ? `${fmtScore(t.제품독성점수범위[0])}~${fmtScore(t.제품독성점수범위[1])}` : "미확인");
    const productScoreHtml = productScore === "미확인"
        ? `<div class="score">미확인</div>`
        : `<div class="score">${productScore}<small>점 (제품 참고점수)</small></div>`;

    const unknownScore = productScore === "미확인";
    const scoreVisual = unknownScore ? "" : `${gauge(t.제품독성점수, range, false)}${legendHtml(c)}`;
    const scoreExplain = unknownScore
        ? `<div><span>품목 인축독성(공식)</span><b>${esc(livestock.표기 || "미기재")}</b></div>`
        : `<div><span>품목 인축독성(공식)</span><b>${esc(livestock.표기 || "미기재")}</b></div><div><span>제품 참고점수</span><b>${productScore}</b></div>`;

    return `
    <section class="card">
        <h3 class="section-title">제품 유해성 정보</h3>
        <div class="tox-summary">
            <div class="tox-big">
                ${pill(lv, LEVEL_TEXT[lv])}
                ${productScoreHtml}
            </div>
            ${scoreVisual}
        </div>
        <div class="score-explain">
            ${scoreExplain}
        </div>
        <p class="note-text">제품 참고점수는 각 원제의 H-code 참고점수에 제품 내 원제 함량(%)을 반영해 계산한 자체 DSS 지표입니다. 공식 제품 독성점수나 실제 위해성 평가가 아닙니다.</p>
        ${stateNote}${severe}
    </section>

    <section class="card">
        <h3 class="section-title">공식 유해성 분류 · 신호어</h3>
        <div class="official-grid">
            <div class="official"><span>인축독성 (공식 등록정보)</span>${pill(livestock.등급 || "none", livestock.표기)}</div>
            <div class="official"><span>어독성 (공식 등록정보)</span><b>${esc(t.어독성 || "미기재")}</b></div>
            <div class="official"><span>신호어 (원제 기준)</span><b>${esc(signalWord(ings))}</b></div>
        </div>
    </section>

    <section class="card">
        <h3 class="section-title">원제 점수 · 농약 성분 기여점수</h3>
        ${ings.map(ingredientCard).join("") || "<p class='muted'>연결된 원제 자료가 없습니다.</p>"}
        <p class="note-text">
            <b>원제 점수</b> = 해당 원제의 H-code 점수 합계 ·
            <b>성분 기여점수</b> = 원제 점수 × 제품 내 원제 함량(%)/100.
            복합제는 각 원제의 기여점수를 계산해 제품 참고점수에 반영합니다.
            중대 유해성(H340·H350·H360·H370·H372)은 점수와 별도로 표시합니다.
        </p>
        <p class="note-text">※ 본 점수는 덴마크 PLI 구조를 참고해 국내 공개 원제 H-code에 대응시킨 자체 참고지표이며 공식 PLI·공식 독성점수·실제 위해성 평가가 아닙니다. 실제 사용 전 제품 라벨과 공식 등록사항을 확인하세요.</p>
    </section>`;
}


function pointText(row) {
    if (row.점수 != null) return `${fmtScore(row.점수)}점`;
    if (row.점수범위) return `${fmtScore(row.점수범위[0])}~${fmtScore(row.점수범위[1])}점`;
    return row.상태 === "점수 미정" ? "점수 미정" : "—";
}

function ingredientCard(i) {
    const lv = i.등급;
    let score = "자료 없음";
    if (i.상태 === "confirmed") score = `${fmtScore(i.점수)}<small>점</small>`;
    else if (i.상태 === "range") score = `${fmtScore(i.점수범위[0])}~${fmtScore(i.점수범위[1])}<small>점</small>`;

    const rows = i.H코드상세 || [];
    const human = rows.filter(r => r.영역 === "인체건강");
    const other = rows.filter(r => r.영역 !== "인체건강");
    const rowHtml = (r, excluded) => `<tr class="${excluded ? "excluded" : ""}">
        <td><span class="hcode ${r.등급}">${esc(r.H코드)}</span></td>
        <td>${esc(r.문구 || "")}${r.상태 === "범위" ? '<br><small class="muted">세부 구분 확인 필요</small>' : ""}</td>
        <td>${excluded ? "점수 제외" : pointText(r)}</td></tr>`;

    return `<div class="ing-card lv-${lv}">
        <div class="ing-head">
            <div><h4>${esc(i.한글명 || i.영문명 || "원제")}</h4><p>${esc(i.영문명 || "")}${i.CAS ? " · CAS " + esc(i.CAS) : ""}</p></div>
            <div style="text-align:right">${pill(lv, null)}<div class="ing-score">${score}</div></div>
        </div>
        <div class="ingredient-score-grid">
            <div><span>제품 내 원제 함량</span><b>${i.함량 != null ? fmtScore(i.함량) + "%" : "미확인"}</b></div>
            <div><span>원제 참고점수</span><b>${score.replace("<small>"," ").replace("</small>","")}</b></div>
            <div><span>농약 성분 기여점수</span><b>${contributionText(i)}</b></div>
        </div>
        ${i.상태 === "nodata" ? "" : gauge(i.점수 ?? null, i.점수범위, true)}
        ${i.비고 ? `<p class="muted small" style="margin-bottom:8px">${esc(i.비고)}</p>` : ""}
        ${i.중대한유해성?.length ? `<div class="alert-box red" style="margin:8px 0"><b>중대한 유해성 · ${esc(i.중대한유해성.join(", "))}</b></div>` : ""}
        ${ghsInline(i.그림문자)}
        ${human.length ? `<div class="area-label">인체건강 H-code · 신호어 ${esc(i.신호어 || "미기재")}</div><table class="h-table">${human.map(r => rowHtml(r, false)).join("")}</table>` : ""}
        ${other.length ? `<div class="area-label">환경·물리적 위험 H-code</div><table class="h-table">${other.map(r => rowHtml(r, true)).join("")}</table>` : ""}
    </div>`;
}


/* ============================================================
   농약 추천 (작물 + 병해충 → 1개)
   ============================================================ */
async function runRecommend() {
    const crop = $("recCrop").value.trim(), pest = $("recPest").value.trim();
    if (!crop && !pest) { alert("작물 또는 병해충을 입력해주세요."); return; }
    const box = $("recommendResult");
    box.innerHTML = '<div class="loading">알맞은 농약을 찾는 중입니다...</div>';
    try {
        const d = await getJson(`/api/recommend?crop=${encodeURIComponent(crop)}&pest=${encodeURIComponent(pest)}`);
        box.innerHTML = d.found ? recommendCard(d) : `<div class="card empty"><h3>추천할 농약을 찾지 못했어요</h3><p>${esc(d.message || "")}</p></div>`;
        box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { box.innerHTML = `<div class="card empty error">${esc(e.message)}</div>`; }
}

function recommendCard(d) {
    const p = d.product, t = d.tox, lv = t.등급;
    const usage = [["희석배수", dilutionText(p.희석배수)], ["사용량", p.사용량], ["사용적기", p.사용적기], ["사용방법", p.사용방법], ["안전사용시기", p.안전사용시기], ["안전사용횟수", p.안전사용횟수]];
    return `<section class="rec-card">
        <span class="rec-kicker">🌱 ${esc([d.crop, d.pest].filter(Boolean).join(" · "))} · ${esc(d.mode || "참고 순위")}</span>
        <div class="rec-name">${esc(p.상표명)}</div>
        <div class="rec-sub">${esc(p.품목명 || "")} · ${esc(p.용도 || "")} · ${esc(p.회사명 || "")}</div>
        <div class="hero-tox" style="margin-top:14px">${pill(lv, `${LEVEL_TEXT[lv]} · ${fmtScore(t.참고점수)}점`)}${pill(t.인축독성.등급, "인축독성 " + t.인축독성.표기)}</div>
        ${gauge(t.참고점수, null, false)}
        ${ghsBig(t.그림문자)}
        <div class="sub-title">${esc(d.mode || "참고 순위")} · 선정 근거</div>
        <ul class="reason-list">${d.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
        <div class="sub-title">제품 단위 판단 정보</div>
        <div class="rec-grid"><div><span>품목 인축독성</span><b>${esc(d.decision?.인축독성?.표기 || "미확인")}</b></div><div><span>중대 원제 H코드</span><b>${esc(d.decision?.중대코드?.join(", ") || "없음/미확인")}</b></div><div><span>원제 구간</span><b>${esc(d.decision?.원제구간 || "미확인")}</b></div><div><span>희석 후 명목농도</span><b>${d.decision?.희석농도?.원액 ? "원액 사용" : (d.decision?.희석농도?.값 == null ? "미확인/비교불가" : esc(Number(d.decision.희석농도.값).toFixed(2) + "%"))}</b></div>
         <div><span>중대 유해성 순위 기준</span><b>${esc(d.decision?.중대유해성순위 || "미확인")}</b></div><div><span>안전사용시기</span><b>${esc(d.decision?.안전사용시기 || "미확인")}</b></div></div>
        ${d.ranked?.length > 1 ? `<div class="sub-title">제품별 참고 순위</div><ol class="reason-list">${d.ranked.map(x=>`<li>${esc(x.상표명)} · ${esc(x.판단.인축독성.표기)}${x.판단.자료미확인 ? " · 미확인" : ""}</li>`).join("")}</ol>` : ""}
         ${d.decision?.중대유해성순위이유 ? `<p class="note-text">${esc(d.decision.중대유해성순위이유)}</p>` : ""}
        ${d.biological?.length ? `<p class="note-text">생물농약·페로몬제·보조제 ${d.biological.length}개는 화학농약과 별도 분류했습니다.</p>` : ""}
        <div class="sub-title">${esc(p.작물명 || "")} · ${esc(p.적용병해충 || "")} 사용 정보</div>
        <div class="rec-grid">${usage.map(([k, v]) => `<div><span>${k}</span><b>${esc(v || "-")}</b></div>`).join("")}</div>
        <button class="btn-primary wide" onclick="openDetail('${esc(p.등록번호)}', 'tox')">유해성 정보 자세히 보기</button>
        <details class="details-box"><summary>선정 과정 보기</summary><ul>
            <li>입력 작물·병해충과 일치하는 모든 사용등록 제품 ${d.candidate_count}개를 후보로 삼았습니다.</li>
            <li>화학농약 ${d.eligible_count}개를 단계별로 비교했습니다. 미확인 자료는 안전으로 간주하지 않습니다.</li>
            <li>품목 인축독성 → 중대 H코드 → 원제 구간 → 희석 명목농도 → 원제 점수 순으로 비교했습니다.</li>
        </ul></details>
        <p class="note-text">공식 사용권장이 아닌 참고용 결과입니다. 사용 전 제품 라벨과 등록사항을 확인하세요.</p>
    </section>`;
}

/* ============================================================
   농약 비교 (후보 여러 개 → 1개 선정)
   ============================================================ */
function addCompareInput(value = "") {
    const wrap = $("compareInputs");
    if (wrap.children.length >= 5) return;
    const row = document.createElement("div");
    row.className = "cmp-row";
    row.innerHTML = `<span class="num"></span><input placeholder="농약명 또는 성분명" autocomplete="off"><button class="del" aria-label="삭제">×</button>`;
    row.querySelector("input").value = value;
    row.querySelector("input").addEventListener("keydown", e => { if (e.key === "Enter") runCompare(); });
    row.querySelector(".del").onclick = () => { if (wrap.children.length > 2) { row.remove(); renumberCompare(); } };
    wrap.appendChild(row);
    renumberCompare();
}
function renumberCompare() {
    const rows = [...$("compareInputs").children];
    rows.forEach((r, i) => { r.querySelector(".num").textContent = i + 1; r.querySelector(".del").hidden = rows.length <= 2; });
    $("addCompareBtn").hidden = rows.length >= 5;
}

async function runCompare() {
    const names = [...$("compareInputs").querySelectorAll("input")].map(i => i.value.trim()).filter(Boolean);
    if (names.length < 2) { alert("비교할 농약을 2개 이상 입력해주세요."); return; }
    const crop = $("cmpCrop").value.trim(), pest = $("cmpPest").value.trim();
    const box = $("compareResult");
    box.innerHTML = '<div class="loading">유해성을 비교하는 중입니다...</div>';
    try {
        const d = await getJson(`/api/compare?products=${encodeURIComponent(names.join(","))}&crop=${encodeURIComponent(crop)}&pest=${encodeURIComponent(pest)}`);
        if (!d.found) { box.innerHTML = `<div class="card empty"><h3>${esc(d.message)}</h3></div>`; return; }
        box.innerHTML = compareResult(d);
        box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { box.innerHTML = `<div class="card empty error">${esc(e.message)}</div>`; }
}

function compareResult(d) {
    const winner = d.results.find(e => e.찾음 && e.등록번호 === d.winner);
    const cond = [d.crop, d.pest].filter(Boolean).join(" · ");
    const banner = winner
        ? `<section class="winner-banner">
            <span class="rec-kicker">✅ ${cond ? esc(cond) + " 기준 " : ""}우선 표시 제품(참고 순위)</span>
            <div class="rec-name">${esc(winner.상표명)}</div>
            <div class="rec-sub">${esc(winner.품목명)} · 입력: ${esc(winner.입력)}</div>
            <ul class="reason-list">${d.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
        </section>`
        : `<section class="card empty"><h3>하나를 정하지 못했어요</h3><p>비교 가능한 화학농약이 2개 이상 없거나 등록 조건이 확인되지 않았습니다. 아래 제품 정보를 확인해주세요.</p></section>`;
    const msgs = d.messages.length ? `<div class="notice-list">${d.messages.map(m => `<div class="alert-box yellow">${esc(m)}</div>`).join("")}</div>` : "";
    return banner + msgs + `<div class="cmp-grid">${d.results.map(e => compareCard(e, d)).join("")}</div>
        <p class="note-text">순위: 품목 인축독성 → 중대한 원제 H코드 → 원제 구간 → 희석 후 명목농도(비교 가능한 경우) → 원제 점수. 원제·품목의 유해성 정보이며 위해성 판정이 아닙니다. 라벨의 보호구 안내를 따르세요.</p>`;
}

function compareCard(e, d) {
    if (!e.찾음) return `<div class="cmp-card"><h3>${esc(e.입력)}</h3><p class="sub">농약 DB에서 찾지 못했습니다.</p></div>`;
    const t = e.tox, lv = t.등급, win = e.등록번호 === d.winner;
    const reg = e.사용등록 === true ? pill("ok", "사용등록 확인") : e.사용등록 === false ? pill("warn", "사용등록 없음") : pill("none", "작물·병해충 미입력");
    return `<div class="cmp-card lv-${lv} ${win ? "win" : ""}">
        ${win ? '<div style="margin-bottom:8px">' + pill("ok", "🏆 선정") + "</div>" : ""}
        <h3>${esc(e.상표명)}</h3>
        <p class="sub">${esc(e.품목명)} · 입력 "${esc(e.입력)}"${e.동일명제품수 > 1 ? ` · 같은 이름 제품 ${e.동일명제품수}개 중 대표` : ""}</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${pill(lv, `${LEVEL_TEXT[lv]}${t.참고점수 != null ? " · " + (t.참고점수_확정 ? "" : "≤") + fmtScore(t.참고점수) + "점" : ""}`)}${reg}</div>
        ${gauge(t.참고점수, null, true)}
        <div class="cmp-row2"><span>공식 인축독성</span>${pill(t.인축독성.등급, t.인축독성.표기)}</div>
        <div class="cmp-row2"><span>중대한 유해성</span><b>${t.중대한유해성.length ? esc(t.중대한유해성.join(", ")) : "없음"}</b></div>
        <div class="cmp-row2"><span>판단 구간</span><b>${esc(e.판단?.원제구간 || "미확인")}</b></div>
        <div class="cmp-row2"><span>희석 명목농도</span><b>${e.판단?.희석농도?.원액 ? "원액 사용" : (e.판단?.희석농도?.값 == null ? "미확인/비교불가" : esc(Number(e.판단.희석농도.값).toFixed(2) + "%"))}</b></div>
        ${e.판단?.자료미확인 ? '<div class="alert-box yellow">원제/제품 자료 미확인</div>' : ""}
        ${e.판단?.생물농약 ? '<div class="alert-box yellow">생물농약 – 별도 기준</div>' : ""}
        <div class="cmp-row2"><span>원제</span><b>${t.원제수}개</b></div>
        <div class="cmp-row2"></div>
        <button class="btn-soft" onclick="openDetail('${esc(e.등록번호)}', 'tox')">유해성 정보 보기</button>
    </div>`;
}

/* ============================================================
   성분 페이지
   ============================================================ */
function openIngredient(id) {
    ingredientBack = currentPage;
    showPage("ingredientPage");
    searchIngredients();
    showIngredientDetail(id);
}
function backFromIngredient() { showPage(ingredientBack || "homePage"); }

async function searchIngredients() {
    const q = $("ingredientSearchInput").value.trim();
    const d = await getJson(`/api/ingredients/search?q=${encodeURIComponent(q)}`);
    const s = d.summary, total = Math.max(1, d.total);
    $("ingredientBar").innerHTML = `
        <div class="stack-bar"><i class="s-low" style="width:${s.low / total * 100}%"></i><i class="s-mid" style="width:${s.mid / total * 100}%"></i><i class="s-high" style="width:${s.high / total * 100}%"></i></div>
        <div class="stack-legend">
            <span>${pill("low", "낮음")}<b>${s.low}</b></span><span>${pill("mid", "중간")}<b>${s.mid}</b></span>
            <span>${pill("high", "높음")}<b>${s.high}</b></span><span>${pill("none", "자료 없음")}<b>${s.none}</b></span>
            <span class="muted">전체 ${d.total.toLocaleString()}개</span></div>`;
    $("ingredientList").innerHTML = d.results.map(i => {
        const sc = i.상태 === "confirmed" ? i.점수 : i.상태 === "range" ? `${i.점수범위[0]}+` : "?";
        return `<button class="ing-item" onclick="showIngredientDetail('${esc(i.원제ID)}')">
            <span class="ing-badge ${i.등급}">${sc}</span>
            <span class="grow"><div class="nm">${esc(i.한글명 || "-")}</div><div class="en">${esc(i.영문명 || "")}${i.CAS ? " · CAS " + esc(i.CAS) : ""}</div></span>
            ${ghsMini(i.그림문자.map(g => g.code))}<span class="cnt">${i.제품수}개 제품</span></button>`;
    }).join("");
}

async function showIngredientDetail(id) {
    const d = await getJson(`/api/ingredients/${encodeURIComponent(id)}`);
    $("ingredientDetail").innerHTML = `<section class="card" id="ingDetailCard">
        <h3 class="section-title">원제 상세</h3>${ingredientCard(d.active)}
        <h3 class="section-title" style="margin-top:22px">이 성분이 포함된 제품 (${d.products.length})</h3>
        ${d.products.slice(0, 30).map(p => `<button class="result-card lv-${p.독성.등급}" onclick="openDetail('${esc(p.등록번호)}')">
            <div class="rc-brand">${esc(p.상표명)}</div><div class="rc-item">${esc(p.품목명)}</div>
            <div class="rc-meta"><span>${esc(p.용도)}</span><span>인축 ${esc(p.인축독성)}</span></div><div class="result-arrow">›</div></button>`).join("")}
    </section>`;
    $("ingDetailCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- 초기화 ---------- */
document.addEventListener("DOMContentLoaded", () => {
    renderRecent();
    ["productInput", "cropInput", "pestInput"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") searchPesticide(); }));
    $("ingredientSearchInput").addEventListener("keydown", e => { if (e.key === "Enter") searchIngredients(); });
    ["recCrop", "recPest"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") runRecommend(); }));
    ["cmpCrop", "cmpPest"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") runCompare(); }));
});