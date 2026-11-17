/* Fin-tip_lab -- shared top nav, ticker search and disclaimer, injected into
   every page in docs/ so the menu lives in one place. Add a page by adding one
   entry to PAGES. Loaded with `defer`, so the DOM is ready when this runs. */
(function(){
  "use strict";

  var PAGES = [
    { href: "index.html",    label: "메인" },
    { href: "ticker.html",   label: "현물" },
    { href: "quant.html",    label: "퀀트 시그널" },
    { href: "screener.html", label: "신호 스크리닝" },
    { href: "sw2.html",      label: "모의매매 성과" }
  ];

  var WATCHLIST_URL = "https://raw.githubusercontent.com/Hyunwoo-Ml/trading-strategy-lab/main/data/market/watchlist.json";

  // Korean names people are likely to type. Tickers and English names come from the watchlist.
  var KO = {
    AAPL: "애플", MSFT: "마이크로소프트", NVDA: "엔비디아", TSLA: "테슬라", GOOGL: "구글 알파벳",
    AMZN: "아마존", META: "메타", AVGO: "브로드컴", TSM: "TSMC 대만반도체", MU: "마이크론",
    AMD: "AMD", QCOM: "퀄컴", MRVL: "마벨", INTC: "인텔", AMAT: "어플라이드 머티리얼즈",
    ARM: "ARM 암홀딩스", ASML: "ASML", LRCX: "램리서치", ORCL: "오라클", DELL: "델",
    PLTR: "팔란티어", NFLX: "넷플릭스", CRM: "세일즈포스", IBM: "IBM", CRWD: "크라우드스트라이크",
    PANW: "팔로알토", NOW: "서비스나우", APP: "앱러빈", BAC: "뱅크오브아메리카", MS: "모건스탠리",
    UNH: "유나이티드헬스", LLY: "일라이릴리", COIN: "코인베이스", HOOD: "로빈후드", SOFI: "소파이",
    RKLB: "로켓랩", IONQ: "아이온큐", VRT: "버티브", GEV: "GE 버노바", OKLO: "오클로", VST: "비스트라",
    CEG: "컨스텔레이션", JPM: "JP모건", SPY: "S&P500", QQQ: "나스닥100", DIA: "다우", IWM: "러셀2000",
    SMH: "반도체 ETF", TLT: "미국채 장기", GLD: "금"
  };

  var DISCLAIMER =
    "Fin-tip_lab의 모든 신호와 수치는 공개 데이터를 자동으로 계산한 참고 자료이며 투자 권유가 아닙니다. " +
    "데이터의 정확성과 완전성을 보장하지 않고, 과거 성과가 미래 수익을 보장하지 않습니다. " +
    "투자 판단과 그 결과의 책임은 투자자 본인에게 있습니다.";

  function currentPage(){
    var file = location.pathname.split("/").pop();
    return file === "" ? "index.html" : file;
  }

  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function buildNav(){
    var here = currentPage();
    var nav = document.createElement("nav");
    nav.className = "ft-nav";
    nav.setAttribute("aria-label", "사이트 메뉴");

    var links = PAGES.map(function(p){
      var current = p.href === here ? ' aria-current="page"' : "";
      return '<a class="ft-link" href="' + p.href + '"' + current + ">" + p.label + "</a>";
    }).join("");

    nav.innerHTML =
      '<div class="ft-nav-inner">' +
        '<a class="ft-brand" href="index.html" aria-label="Fin-tip_lab 메인">' +
          '<img src="assets/logo.svg" alt="" width="30" height="30">' +
          '<span class="ft-word">Fin-tip<span class="ft-lab">_lab</span></span>' +
        "</a>" +
        '<div class="ft-search" role="search">' +
          '<input type="search" class="ft-search-input" placeholder="티커·종목 검색" autocomplete="off" ' +
            'aria-label="티커 또는 종목명 검색" aria-autocomplete="list" aria-controls="ft-search-list" aria-expanded="false">' +
          '<ul class="ft-search-list" id="ft-search-list" role="listbox" hidden></ul>' +
        "</div>" +
        '<div class="ft-links">' + links + "</div>" +
      "</div>";
    return nav;
  }

  // ---- ticker search ----
  var entries = null, loading = null;

  function loadEntries(){
    if (entries) return Promise.resolve(entries);
    if (loading) return loading;
    loading = fetch(WATCHLIST_URL, { cache: "no-cache" })
      .then(function(r){ if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function(w){
        entries = (w.rows || []).map(function(r){
          return { t: r.ticker, name: r.name || "", ko: KO[r.ticker] || "", group: r.group || "" };
        });
        return entries;
      })
      .catch(function(){ loading = null; entries = null; return []; });
    return loading;
  }

  function norm(s){ return String(s || "").toLowerCase().replace(/\s+/g, ""); }

  function match(list, q){
    q = norm(q);
    if (!q) return [];
    var scored = [];
    list.forEach(function(e){
      var t = e.t.toLowerCase(), n = norm(e.name), k = norm(e.ko), s = -1;
      if (t === q) s = 0;
      else if (t.indexOf(q) === 0) s = 1;
      else if (k && k.indexOf(q) === 0) s = 2;
      else if (n.indexOf(q) === 0) s = 3;
      else if ((k && k.indexOf(q) >= 0) || n.indexOf(q) >= 0) s = 4;
      if (s >= 0) scored.push([s, e]);
    });
    scored.sort(function(a, b){ return a[0] - b[0] || a[1].t.localeCompare(b[1].t); });
    return scored.slice(0, 8).map(function(x){ return x[1]; });
  }

  // On the 현물 page this only changes the hash, which that page listens for.
  function go(ticker){ location.href = "ticker.html#" + encodeURIComponent(ticker); }

  function wireSearch(nav){
    var input = nav.querySelector(".ft-search-input");
    var list = nav.querySelector(".ft-search-list");
    var results = [], active = -1;

    function close(){ list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; }
    function render(){
      if (!input.value.trim()){ close(); return; }
      if (!results.length){
        list.innerHTML = '<li class="ft-search-empty">' + (entries ? "커버하는 종목 중에 없어요" : "종목 목록을 불러오는 중…") + "</li>";
      } else {
        list.innerHTML = results.map(function(e, i){
          return '<li role="option" id="ft-opt-' + i + '" data-t="' + esc(e.t) + '"' + (i === active ? ' aria-selected="true"' : "") + ">" +
            '<b>' + esc(e.t) + '</b><span>' + esc(e.ko || e.name) + '</span><em>' + esc(e.group) + "</em></li>";
        }).join("");
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      if (active >= 0) input.setAttribute("aria-activedescendant", "ft-opt-" + active); else input.removeAttribute("aria-activedescendant");
    }
    function update(){
      var q = input.value;
      loadEntries().then(function(list){
        if (input.value !== q) return;
        results = match(list, q); active = results.length ? 0 : -1; render();
      });
      render();
    }

    input.addEventListener("focus", loadEntries);
    input.addEventListener("input", update);
    input.addEventListener("keydown", function(ev){
      if (ev.key === "ArrowDown" && results.length){ active = (active + 1) % results.length; render(); ev.preventDefault(); }
      else if (ev.key === "ArrowUp" && results.length){ active = (active - 1 + results.length) % results.length; render(); ev.preventDefault(); }
      else if (ev.key === "Enter"){
        var pick = results[active >= 0 ? active : 0];
        if (pick){ ev.preventDefault(); go(pick.t); }
      } else if (ev.key === "Escape"){ close(); input.blur(); }
    });
    list.addEventListener("mousedown", function(ev){
      var li = ev.target.closest("li[data-t]");
      if (li){ ev.preventDefault(); go(li.getAttribute("data-t")); }
    });
    input.addEventListener("blur", function(){ setTimeout(close, 120); });
  }

  function buildDisclaimer(){
    var p = document.createElement("p");
    p.className = "ft-disclaimer";
    p.textContent = DISCLAIMER;
    return p;
  }

  var nav = buildNav();
  document.body.insertBefore(nav, document.body.firstChild);
  wireSearch(nav);
  document.body.appendChild(buildDisclaimer());
})();
