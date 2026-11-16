/* Fin-tip_lab -- shared top nav and disclaimer, injected into every page in
   docs/ so the menu lives in one place. Add a page by adding one entry to
   PAGES. Loaded with `defer`, so the DOM is ready when this runs. */
(function(){
  "use strict";

  var PAGES = [
    { href: "index.html",    label: "퀀트 시그널" },
    { href: "screener.html", label: "신호 스크리닝" },
    { href: "sw2.html",      label: "모의매매 성과" }
  ];

  var DISCLAIMER =
    "Fin-tip_lab의 모든 신호와 수치는 공개 데이터를 자동으로 계산한 참고 자료이며 투자 권유가 아닙니다. " +
    "데이터의 정확성과 완전성을 보장하지 않고, 과거 성과가 미래 수익을 보장하지 않습니다. " +
    "투자 판단과 그 결과의 책임은 투자자 본인에게 있습니다.";

  function currentPage(){
    var file = location.pathname.split("/").pop();
    return file === "" ? "index.html" : file;
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
        '<a class="ft-brand" href="index.html" aria-label="Fin-tip_lab 홈">' +
          '<img src="assets/logo.svg" alt="" width="30" height="30">' +
          '<span class="ft-word">Fin-tip<span class="ft-lab">_lab</span></span>' +
        "</a>" +
        '<div class="ft-links">' + links + "</div>" +
      "</div>";
    return nav;
  }

  function buildDisclaimer(){
    var p = document.createElement("p");
    p.className = "ft-disclaimer";
    p.textContent = DISCLAIMER;
    return p;
  }

  document.body.insertBefore(buildNav(), document.body.firstChild);
  document.body.appendChild(buildDisclaimer());
})();
