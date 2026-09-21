(function () {
  "use strict";

  var N8N_BASE = "https://sondullab.app.n8n.cloud/webhook";
  var URLS = {
    updatePrices: N8N_BASE + "/auto-trading-update-prices",
    listPrices: N8N_BASE + "/auto-trading-list-prices",
    getPrice: N8N_BASE + "/auto-trading-get-price",
    checkRun: N8N_BASE + "/auto-trading-check-run",
    runBacktest: N8N_BASE + "/auto-trading-run-backtest",
    listResults: N8N_BASE + "/auto-trading-list-results",
    getResult: N8N_BASE + "/auto-trading-get-result",
    findBest: N8N_BASE + "/auto-trading-find-best",
    getFile: N8N_BASE + "/auto-trading-get-file",
    deleteResult: N8N_BASE + "/auto-trading-delete-result",
    runWalkforward: N8N_BASE + "/auto-trading-run-walkforward",
  };

  // ── 0. 메뉴 탭 ─────────────────────────────────────────
  // DATA 수집과 전략분석을 상위 탭으로 나누고, 전략분석 안에 전략
  // 비교·비교 결과·최적 조건 찾기를 하위 탭으로 둔다. 매매를 실제로
  // 바꾸는 동작은 없고 화면 표시만 다루므로 여기서 제일 먼저 연결한다.
  function wireTabs(navSelector, attr) {
    var buttons = document.querySelectorAll(navSelector + " [data-" + attr + "]");
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.getAttribute("data-" + attr);
        buttons.forEach(function (b) {
          var active = b === btn;
          b.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll("[data-" + (attr === "tab" ? "panel" : "subpanel") + "]").forEach(function (panel) {
          if (navSelector === "#main-tabs") {
            panel.hidden = panel.getAttribute("data-panel") !== target;
          } else {
            panel.hidden = panel.getAttribute("data-subpanel") !== target;
          }
        });
      });
    });
    if (buttons.length) buttons[0].click();
  }
  wireTabs("#main-tabs", "tab");
  wireTabs("#strategy-subtabs", "subtab");

  // 백엔드 src/auto_trading/backtest.py의 STRATEGY_SCHEMAS와 같은 내용을
  // 화면에서 쓰기 위해 그대로 옮겨 적었다. 전략 종류를 바꾸면 양쪽을
  // 같이 고쳐야 한다.
  var STRATEGY_SCHEMAS = {
    lump_sum: { label: "일회 매수", description: "첫날 전체 자본으로 한 번에 산다.", params: [] },
    dca: {
      label: "적립식 매수",
      description: "정해진 금액을 일정 간격마다 산다.",
      params: [
        { name: "amount", label: "회당 매수 금액(원)", type: "int", suggested: 100000 },
        { name: "interval_days", label: "매수빈도(일수, 1이면 매일)", type: "int", suggested: 1 },
      ],
    },
    dca_ma: {
      label: "적립식 매수 + 이동평균선 조건",
      description: "이동평균선 아래일 때와 위일 때 매수금액·매수빈도를 각각 따로 정한다. 한쪽만 채워도 되고 둘 다 채워도 된다.",
      params: [
        { name: "ma_window", label: "이동평균 기간(거래일)", type: "int", suggested: 60 },
        { name: "below_amount", label: "이동평균선 아래일 때 매수금액(원) — 비워 두면 이 구간엔 안 삼", type: "int", optional: true, pair: "below", suggested: 100000 },
        { name: "below_interval_days", label: "이동평균선 아래일 때 매수빈도(일수)", type: "int", optional: true, pair: "below", suggested: 1 },
        { name: "above_amount", label: "이동평균선 위일 때 매수금액(원) — 비워 두면 이 구간엔 안 삼", type: "int", optional: true, pair: "above" },
        { name: "above_interval_days", label: "이동평균선 위일 때 매수빈도(일수)", type: "int", optional: true, pair: "above" },
      ],
    },
    drop_based: {
      label: "등락률 기준 비중 조절 매수",
      description: "최근 평균 주가 대비 등락률 구간마다 매수 금액을 다르게 정한다. 하락 구간뿐 아니라 상승 구간도 넣을 수 있다.",
      params: [
        { name: "interval_days", label: "평가 빈도(거래일, 1이면 매일)", type: "int", suggested: 1 },
        { name: "lookback_days", label: "평가 기준일(몇일전 시세대비, 1이면 전일 대비)", type: "int", suggested: 1 },
        {
          name: "tiers",
          label: "등락률 구간별 매수 금액(등락률%, 금액)",
          type: "tiers",
          suggested: [[-5, 120000], [-10, 150000]],
        },
      ],
    },
  };

  var MAX_STRATEGIES = 5;

  // 백엔드 src/auto_trading/optimize.py의 STRATEGY_SEARCH_SCHEMAS와 같은
  // 내용을 화면에서 쓰기 위해 그대로 옮겨 적었다. 후보값 후보를 하나씩
  // 넣지 않아도 되지만, 그 후보값 목록 자체는 화면에 미리 채워져 있고
  // 사람이 바꿀 수 있다. 등락률 기준 비중 조절 매수는 여기서는 구간을
  // 하나로 단순화한다(여러 구간은 전략 비교 탭에서 직접 설정한다).
  //
  // 익절·손절 후보는 매수 방식마다 따로 받는다(2026-09-21에 검색 전체에
  // 공통으로 걸던 값에서 바꿨다. 매수 방식마다 어울리는 익절·손절 폭이
  // 다를 수 있다는 지적을 받았다). 그래서 EXIT_PARAMS를 각 전략의 params
  // 끝에 붙인다.
  var EXIT_PARAMS = [
    { name: "take_profit_pct", label: "익절선 후보(매수평균가 대비%, 쉼표로 구분) — 비워 두면 안 씀", type: "percent_optional", optional: true },
    { name: "stop_loss_pct", label: "손절선 후보(매수평균가 대비%, 쉼표로 구분) — 비워 두면 안 씀", type: "percent_optional", optional: true },
  ];
  var STRATEGY_SEARCH_SCHEMAS = {
    lump_sum: { label: "일회 매수", params: [].concat(EXIT_PARAMS) },
    dca: {
      label: "적립식 매수",
      params: [
        { name: "amount", label: "회당 매수 금액 후보(원, 쉼표로 구분)", type: "int_list", suggested: "50000, 100000, 200000" },
        { name: "interval_days", label: "매수빈도 후보(일수, 쉼표로 구분)", type: "int_list", suggested: "1, 5, 10" },
      ].concat(EXIT_PARAMS),
    },
    dca_ma: {
      label: "적립식 매수 + 이동평균선 조건",
      params: [
        { name: "ma_window", label: "이동평균 기간 후보(거래일, 쉼표로 구분)", type: "int_list", suggested: "20, 60, 120" },
        { name: "below_amount", label: "이동평균선 아래일 때 매수금액 후보(원, 쉼표로 구분) — 비워 두면 이 구간엔 안 삼", type: "int_list", optional: true, pair: "below", suggested: "50000, 100000, 200000" },
        { name: "below_interval_days", label: "이동평균선 아래일 때 매수빈도 후보(일수, 쉼표로 구분)", type: "int_list", optional: true, pair: "below", suggested: "1, 5, 10" },
        { name: "above_amount", label: "이동평균선 위일 때 매수금액 후보(원, 쉼표로 구분) — 비워 두면 이 구간엔 안 삼", type: "int_list", optional: true, pair: "above", suggested: "" },
        { name: "above_interval_days", label: "이동평균선 위일 때 매수빈도 후보(일수, 쉼표로 구분)", type: "int_list", optional: true, pair: "above", suggested: "" },
      ].concat(EXIT_PARAMS),
    },
    drop_based: {
      label: "등락률 기준 비중 조절 매수(구간 하나로 단순화)",
      params: [
        { name: "interval_days", label: "평가 빈도 후보(거래일, 쉼표로 구분)", type: "int_list", suggested: "1, 5, 10" },
        { name: "lookback_days", label: "평가 기준일 후보(몇일전 시세대비, 쉼표로 구분)", type: "int_list", suggested: "1, 5, 10" },
        { name: "threshold_pct", label: "등락률 임계값 후보(%, 쉼표로 구분)", type: "float_list", suggested: "-3, -5, -10" },
        { name: "amount", label: "그 구간 매수 금액 후보(원, 쉼표로 구분)", type: "int_list", suggested: "100000, 200000, 300000" },
      ].concat(EXIT_PARAMS),
    },
  };
  var OPT_MAX_COMBINATIONS = 200;

  // ── 공통 유틸 ──────────────────────────────────────────
  // dca_ma의 "이동평균선 아래일 때 매수금액/매수빈도"처럼 짝을 이루는
  // 선택값은 둘 다 채우거나 둘 다 비워야 한다. schema.params에서 같은
  // pair 이름을 가진 항목끼리 묶어서 확인한다. 문제가 없으면 null을
  // 돌려준다. contextLabel은 오류 문구 앞에 붙일 "몇 번째 전략(이름)"
  // 같은 설명이다.
  function checkParamPairs(schema, cfg, contextLabel) {
    var pairs = {};
    schema.params.forEach(function (p) {
      if (!p.pair) return;
      pairs[p.pair] = pairs[p.pair] || [];
      pairs[p.pair].push(p);
    });
    var keys = Object.keys(pairs);
    for (var i = 0; i < keys.length; i++) {
      var members = pairs[keys[i]];
      var filled = members.map(function (p) { return cfg[p.name] !== undefined && cfg[p.name] !== ""; });
      var anyFilled = filled.some(Boolean);
      var allFilled = filled.every(Boolean);
      if (anyFilled && !allFilled) {
        return contextLabel + "의 '" + keys[i] + "' 쪽 값은 전부 채우거나 전부 비워야 합니다.";
      }
    }
    return null;
  }

  function fmtNumber(n) {
    return Math.round(n).toLocaleString("ko-KR");
  }
  function fmtDateTimeKST(isoUtc) {
    try {
      return new Date(isoUtc).toLocaleString("ko-KR", { hour12: false });
    } catch (e) {
      return isoUtc;
    }
  }
  function setStatus(el, state, message) {
    el.dataset.state = state;
    el.textContent = message;
  }
  function todayStr() {
    return new Date().toISOString().slice(0, 10);
  }
  function yearsAgoStr(years) {
    var d = new Date();
    d.setFullYear(d.getFullYear() - years);
    return d.toISOString().slice(0, 10);
  }
  function notifyIfPermitted(title, body) {
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(title, { body: body });
      }
    } catch (e) {
      // 알림을 못 띄워도 화면 문구로 이미 안내하므로 무시한다
    }
  }

  // GitHub Actions 실행이 끝나도 화면에 알림이 없어서 '조회하기'를 눌러
  // 봐야만 아는지 안 아는지 알 수 있었다. 실행 상태를 직접 물어봐서,
  // 끝나면 자동으로 알려준다. 시세 수집의 daily.csv, 전략 비교의 결과
  // JSON 둘 다 쓸 수 있게 워크플로 파일 이름을 인자로 받는다(README
  // 참고). 파일의 마지막 수정 시각만 보면 안 된다. 이미 최신이라 새로
  // 받을 게 없는 날은 수정 시각이 안 바뀌는데, 그걸 "아직 안 끝났다"로
  // 잘못 읽게 된다.
  function pollWorkflowRun(workflowFile, statusEl, previousRunId, attempt, setTimer, onDone) {
    var maxAttempts = 60; // 10초 간격으로 최대 10분
    fetch(URLS.checkRun + "?workflow=" + encodeURIComponent(workflowFile))
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (runs) {
        var latest = runs && runs[0];
        var isNewRun = latest && latest.id !== previousRunId;

        if (!isNewRun || latest.status !== "completed") {
          if (attempt >= maxAttempts) {
            setStatus(statusEl, "err", "실행 확인이 오래 걸립니다. 잠시 뒤 새로고침해 보세요.");
            setTimer(null);
            return;
          }
          setTimer(setTimeout(function () {
            pollWorkflowRun(workflowFile, statusEl, previousRunId, attempt + 1, setTimer, onDone);
          }, 10000));
          return;
        }

        setTimer(null);
        onDone(latest);
      })
      .catch(function (err) {
        if (attempt >= maxAttempts) {
          setStatus(statusEl, "err", "진행 확인 중 오류가 반복됩니다: " + err.message);
          setTimer(null);
          return;
        }
        setTimer(setTimeout(function () {
          pollWorkflowRun(workflowFile, statusEl, previousRunId, attempt + 1, setTimer, onDone);
        }, 10000));
      });
  }

  // ── 1. 시세 수집 ───────────────────────────────────────
  var pricesForm = document.getElementById("form-prices");
  var pricesStatus = document.getElementById("prices-status");
  var priceCollectionPollTimer = null;
  function setPriceCollectionPollTimer(t) { priceCollectionPollTimer = t; }

  pricesForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var symbols = document
      .getElementById("prices-symbols")
      .value.split(",")
      .map(function (s) { return s.trim().toUpperCase(); })
      .filter(Boolean)
      .join(",");
    var fullRefresh = document.getElementById("prices-full-refresh").checked;

    if (!symbols) {
      setStatus(pricesStatus, "err", "종목을 하나 이상 입력하세요.");
      return;
    }

    if (priceCollectionPollTimer) {
      clearTimeout(priceCollectionPollTimer);
      priceCollectionPollTimer = null;
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }

    setStatus(pricesStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.checkRun + "?workflow=update-prices.yml")
      .then(function (res) { return res.ok ? res.json() : []; })
      .catch(function () { return []; })
      .then(function (beforeRuns) {
        var previousRunId = beforeRuns && beforeRuns[0] ? beforeRuns[0].id : null;
        return fetch(URLS.updatePrices, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbols: symbols, full_refresh: fullRefresh }),
        }).then(function (res) {
          if (!res.ok) throw new Error("응답 코드 " + res.status);
          setStatus(pricesStatus, "", "요청을 보냈습니다. 완료되면 자동으로 알려 드립니다...");
          priceCollectionPollTimer = setTimeout(function () {
            pollWorkflowRun("update-prices.yml", pricesStatus, previousRunId, 1, setPriceCollectionPollTimer, function (latest) {
              if (latest.conclusion === "success") {
                setStatus(pricesStatus, "ok", "완료됐습니다(" + symbols + "). 아래 수집 결과 조회에 자동으로 반영했습니다.");
                notifyIfPermitted("시세 수집 완료", symbols + " 수집이 끝났습니다.");
                refreshPricesListBtn.click();
              } else {
                setStatus(pricesStatus, "err", "수집이 실패로 끝났습니다(" + latest.conclusion + "). GitHub Actions 로그를 확인해야 합니다.");
                notifyIfPermitted("시세 수집 실패", symbols + " 수집이 실패했습니다.");
              }
            });
          }, 5000);
        });
      })
      .catch(function (err) {
        setStatus(pricesStatus, "err", "요청을 보내지 못했습니다: " + err.message);
      });
  });

  // ── 2. 수집 결과 조회 ──────────────────────────────────
  var refreshPricesListBtn = document.getElementById("refresh-prices-list");
  var pricesListStatus = document.getElementById("prices-list-status");
  var pricesTableBody = document.querySelector("#prices-table tbody");
  var priceDetailEl = document.getElementById("price-detail");

  // 달러/원화 전환 그래프에 쓸 환율(KRW=X)은 update-prices.yml이 다른
  // 종목과 같은 방식으로 같이 받아 둔다. 사람이 고른 종목이 아니라서
  // 목록 표에는 안 보여준다.
  var FX_SYMBOL = "KRW=X";

  // 종목별 file_id를 기억해 둔다(2026-09-21에 fxFileId 하나만 있던 것을
  // 일반화했다). 전에는 '목록 새로고침'을 먼저 눌러야만 채워졌고, 그러면
  // 원화 전환 버튼과 유사 과거 불러오기가 그 버튼을 먼저 누르지 않으면
  // 못 쓰는 구조였다. 필요한 시점에 아직 못 찾은 종목이면 그때 목록을
  // 직접 다시 받아 온다.
  var priceFileIdBySymbol = {};

  function fetchPriceList() {
    return fetch(URLS.listPrices)
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (allRows) {
        (allRows || []).forEach(function (r) { priceFileIdBySymbol[r.symbol] = r.file_id || null; });
        return allRows || [];
      });
  }

  // 이미 목록을 한 번이라도 받아서 이 종목이 있는지 없는지 아는 상태면
  // 그 값을 그대로 쓰고, 모르면 목록을 새로 받아 온다.
  function fetchPriceFileId(symbol) {
    if (Object.prototype.hasOwnProperty.call(priceFileIdBySymbol, symbol)) {
      return Promise.resolve(priceFileIdBySymbol[symbol]);
    }
    return fetchPriceList().then(function () { return priceFileIdBySymbol[symbol] || null; });
  }

  refreshPricesListBtn.addEventListener("click", function () {
    setStatus(pricesListStatus, "", "목록을 불러오는 중입니다...");
    priceDetailEl.innerHTML = "";
    fetchPriceList()
      .then(function (allRows) {
        pricesTableBody.innerHTML = "";
        var rows = allRows.filter(function (r) { return r.symbol !== FX_SYMBOL; });
        if (!rows || rows.length === 0) {
          setStatus(pricesListStatus, "ok", "아직 수집한 종목이 없습니다.");
          return;
        }
        rows.forEach(function (row) {
          var tr = document.createElement("tr");

          var tdSymbol = document.createElement("td");
          tdSymbol.textContent = row.symbol;
          tr.appendChild(tdSymbol);

          var tdState = document.createElement("td");
          tdState.textContent = row["수집됨"] ? "수집됨" : "아직 없음";
          tr.appendChild(tdState);

          var tdTime = document.createElement("td");
          tdTime.textContent = row.modified_time ? fmtDateTimeKST(row.modified_time) : "-";
          tr.appendChild(tdTime);

          var tdAction = document.createElement("td");
          if (row.file_id) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = "내용 보기";
            btn.addEventListener("click", function () { loadPriceDetail(row.symbol, row.file_id); });
            tdAction.appendChild(btn);
          }
          tr.appendChild(tdAction);

          pricesTableBody.appendChild(tr);
        });
        setStatus(pricesListStatus, "ok", rows.length + "개 종목 폴더를 찾았습니다.");
      })
      .catch(function (err) {
        setStatus(pricesListStatus, "err", "목록을 불러오지 못했습니다: " + err.message);
      });
  });

  // 새로고침을 안 눌러도 화면을 열면 바로 목록이 보이게 한다.
  refreshPricesListBtn.click();

  function parsePriceRows(text) {
    var lines = text.split(/\r?\n/).filter(function (line) { return line.trim().length > 0; });
    if (lines.length <= 1) return [];
    var header = lines[0].split(",");
    var dateIdx = header.indexOf("trade_date");
    var closeIdx = header.indexOf("close");
    if (dateIdx < 0 || closeIdx < 0) return [];
    var rows = [];
    for (var i = 1; i < lines.length; i++) {
      var cols = lines[i].split(",");
      var close = parseFloat(cols[closeIdx]);
      if (cols[dateIdx] && !isNaN(close)) rows.push({ date: cols[dateIdx], close: close });
    }
    rows.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    return rows;
  }

  function summarizeRows(rows) {
    if (rows.length === 0) return { rows: 0, startDate: null, endDate: null, lastClose: null };
    return {
      rows: rows.length,
      startDate: rows[0].date,
      endDate: rows[rows.length - 1].date,
      lastClose: rows[rows.length - 1].close,
    };
  }

  // ── 종가 그래프(반응형, 달러/원화 전환) ────────────────
  // rows와 fxRows는 둘 다 날짜 오름차순으로 정렬돼 있다(parsePriceRows가
  // 정렬해 둔다). 환율 날짜가 종목 거래일과 정확히 안 맞을 수 있어서
  // (증시 휴장일이 서로 다르다), 그날 이전의 가장 최근 환율을 그대로
  // 쓴다(forward-fill). 두 배열을 한 번씩만 훑으면 되므로 O(n+m)이다.
  function alignFxToRows(rows, fxRows) {
    var result = [];
    var fi = 0;
    var lastRate = null;
    for (var i = 0; i < rows.length; i++) {
      while (fi < fxRows.length && fxRows[fi].date <= rows[i].date) {
        lastRate = fxRows[fi].close;
        fi++;
      }
      result.push(lastRate);
    }
    return result;
  }

  // 원화 버튼을 눌러도 반응이 없다는 지적을 받았다(2026-09-21). 원인은
  // 환율(KRW=X)을 아직 한 번도 못 받아 온 상태에서는 버튼을 disabled로
  // 막아 둔 것이었다. 누가 봐도 "눌렀는데 반응이 없다"로 보인다. 그때도
  // 버튼은 항상 눌리게 하고, 누른 시점에 환율을 직접 받아와서 있으면
  // 보여주고 없으면 그 이유를 화면에 글로 적는다.
  function buildPriceChart(rows, symbol) {
    var currency = "usd";
    var fxState = "idle"; // idle | loading | loaded | error
    var fxAligned = null;
    var fxErrorMessage = "";

    var wrap = document.createElement("div");

    var toggle = document.createElement("div");
    toggle.className = "row";
    var usdBtn = document.createElement("button");
    usdBtn.type = "button";
    usdBtn.textContent = "달러(USD)";
    var krwBtn = document.createElement("button");
    krwBtn.type = "button";
    krwBtn.textContent = "원화(KRW)";
    toggle.appendChild(usdBtn);
    toggle.appendChild(krwBtn);
    wrap.appendChild(toggle);

    var fxNote = document.createElement("p");
    fxNote.className = "hint";
    wrap.appendChild(fxNote);

    var chartHost = document.createElement("div");
    wrap.appendChild(chartHost);

    function render() {
      usdBtn.setAttribute("aria-pressed", currency === "usd" ? "true" : "false");
      krwBtn.setAttribute("aria-pressed", currency === "krw" ? "true" : "false");
      chartHost.innerHTML = "";
      chartHost.appendChild(drawPriceLineChart(rows, currency === "krw" ? fxAligned : null, symbol, currency));
      if (currency !== "krw") {
        fxNote.textContent = "";
      } else if (fxState === "loading") {
        fxNote.textContent = "환율을 불러오는 중입니다...";
      } else if (fxState === "error") {
        fxNote.textContent = fxErrorMessage;
      } else {
        fxNote.textContent = "";
      }
    }

    usdBtn.addEventListener("click", function () {
      currency = "usd";
      render();
    });

    krwBtn.addEventListener("click", function () {
      currency = "krw";
      if (fxState === "loaded" || fxState === "loading") {
        render();
        return;
      }
      fxState = "loading";
      render();
      fetchPriceFileId(FX_SYMBOL)
        .then(function (fileId) {
          if (!fileId) {
            throw new Error("환율 자료가 아직 없습니다. 시세 수집을 한 번 더 실행하면 그때부터 원화로도 볼 수 있습니다.");
          }
          return fetch(URLS.getPrice + "?id=" + encodeURIComponent(fileId)).then(function (res) {
            if (!res.ok) throw new Error("응답 코드 " + res.status);
            return res.text();
          });
        })
        .then(function (text) {
          var fxRows = parsePriceRows(text);
          if (fxRows.length === 0) throw new Error("환율 자료를 받았지만 내용이 비어 있습니다.");
          fxAligned = alignFxToRows(rows, fxRows);
          fxState = "loaded";
          render();
        })
        .catch(function (err) {
          fxState = "error";
          fxErrorMessage = err.message;
          render();
        });
    });

    render();
    return wrap;
  }

  function drawPriceLineChart(rows, fxAligned, symbol, currency) {
    var width = 360, height = 160, padding = { top: 10, right: 10, bottom: 20, left: 54 };
    var plotW = width - padding.left - padding.right;
    var plotH = height - padding.top - padding.bottom;

    var values = rows.map(function (r, i) {
      return fxAligned && fxAligned[i] !== null ? r.close * fxAligned[i] : r.close;
    });
    var minV = Math.min.apply(null, values);
    var maxV = Math.max.apply(null, values);
    if (minV === maxV) { minV -= 1; maxV += 1; }

    function xAt(i) { return padding.left + (rows.length <= 1 ? 0 : (i / (rows.length - 1)) * plotW); }
    function yAt(v) { return padding.top + plotH - ((v - minV) / (maxV - minV)) * plotH; }

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", "100%");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", symbol + " 종가 추이(" + (currency === "krw" ? "원화" : "달러") + ")");

    [0, 0.5, 1].forEach(function (t) {
      var v = minV + (maxV - minV) * t;
      var y = yAt(v);
      var line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", padding.left);
      line.setAttribute("x2", width - padding.right);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("stroke", "currentColor");
      line.setAttribute("stroke-opacity", "0.15");
      svg.appendChild(line);

      var label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", padding.left - 6);
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "10");
      label.setAttribute("fill", "currentColor");
      label.setAttribute("opacity", "0.6");
      label.textContent = fmtNumber(v) + (currency === "krw" ? "원" : "달러");
      svg.appendChild(label);
    });

    var xTickCount = Math.min(5, rows.length);
    for (var ti = 0; ti < xTickCount; ti++) {
      var idx = xTickCount <= 1 ? 0 : Math.round((ti / (xTickCount - 1)) * (rows.length - 1));
      var xLabel = document.createElementNS(svgNS, "text");
      xLabel.setAttribute("x", xAt(idx));
      xLabel.setAttribute("y", height - 6);
      xLabel.setAttribute("text-anchor", ti === 0 ? "start" : ti === xTickCount - 1 ? "end" : "middle");
      xLabel.setAttribute("font-size", "10");
      xLabel.setAttribute("fill", "currentColor");
      xLabel.setAttribute("opacity", "0.6");
      xLabel.textContent = rows[idx].date;
      svg.appendChild(xLabel);
    }

    var d = values
      .map(function (v, i) { return (i === 0 ? "M" : "L") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1); })
      .join(" ");
    var path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#2f6f65");
    path.setAttribute("stroke-width", "2");
    svg.appendChild(path);

    var wrap = document.createElement("div");
    wrap.appendChild(svg);
    return wrap;
  }

  // ── 최근 흐름과 가장 비슷했던 과거 구간 찾기 ────────────
  // 시작일을 0%로 맞춘 누적 수익률 곡선끼리 비교한다. 곡선 모양이
  // 비슷할수록(RMSE가 작을수록) "비슷한 구간"으로 본다. 미래를
  // 맞히는 것이 아니라 과거에 모양이 비슷했던 때를 찾는 것뿐이다.
  var SIMILAR_WINDOWS = [
    { label: "최근 2주", days: 10 },
    { label: "최근 한 달", days: 21 },
    { label: "최근 두 달", days: 42 },
  ];
  // 종목별로 마지막에 찾은 유사 구간 결과를 담아 둔다. 전략 비교·최적
  // 조건 찾기 탭의 "유사 과거 불러오기"가 이것을 읽어서 조회기간을
  // 채운다. 구조: { SYMBOL: { 10: [순위1,2,3], 21: [...], 42: [...] } }.
  var SIMILAR_MATCHES_BY_SYMBOL = {};

  function cumulativeReturnCurve(closes) {
    var base = closes[0];
    return closes.map(function (c) { return (c / base - 1) * 100; });
  }

  function curveDistance(a, b) {
    var sum = 0;
    for (var i = 0; i < a.length; i++) {
      var d = a[i] - b[i];
      sum += d * d;
    }
    return Math.sqrt(sum / a.length);
  }

  // 과거 구간은 이미 지난 일이라 그 뒤에 값이 어떻게 움직였는지도 이미
  // 알 수 있다. 찾은 구간과 같은 길이만큼 뒤로 이어진 실제 수익률을
  // 같이 보여준다(처음에는 두 배 길이로 잘못 만들었다가 같은 길이로
  // 고쳤다). 그 구간을 찾는 데 쓴 자리와 그 뒤 구간이 겹치지 않게,
  // 같은 길이만큼 더 들어갈 자리가 있는 과거만 후보로 본다.
  //
  // **1위만 찾지 않고 3위까지 남긴다**(2026-09-21에 더함). 1위만 보면
  // "2위였으면 어땠나"를 알 수 없다. 화면은 1위를 기본으로 보여주고,
  // 2·3위는 체크박스를 눌러야 그래프에 겹쳐 그린다.
  function findSimilarPastRanked(rows, windowDays, topN) {
    if (rows.length < windowDays * 2) return [];
    var recentRows = rows.slice(rows.length - windowDays);
    var recentShapeCurve = cumulativeReturnCurve(recentRows.map(function (r) { return r.close; }));

    var searchEnd = rows.length - windowDays * 2;
    var candidates = [];
    for (var start = 0; start <= searchEnd; start++) {
      var candidate = rows.slice(start, start + windowDays);
      var shapeCurve = cumulativeReturnCurve(candidate.map(function (r) { return r.close; }));
      candidates.push({ dist: curveDistance(recentShapeCurve, shapeCurve), rows: candidate, start: start });
    }
    candidates.sort(function (a, b) { return a.dist - b.dist; });

    var todayClose = recentRows[recentRows.length - 1].close;
    var recentCurveForChart = recentRows.map(function (r) { return (r.close / todayClose - 1) * 100; });

    return candidates.slice(0, topN).map(function (best, rankIdx) {
      var matchRows = best.rows;
      var first = matchRows[0];
      var last = matchRows[matchRows.length - 1];
      var followRows = rows.slice(best.start + windowDays, best.start + windowDays * 2);
      var followEnd = followRows.length ? followRows[followRows.length - 1] : null;

      // 그래프용 곡선. 과거 구간은 그 구간의 마지막 날(그래프의 경계
      // 지점)을 0%로 맞춰서, 앞의 실제 구간과 뒤의 실제 구간을 하나로
      // 잇는다. 지금 구간은 오늘 종가를 0%로 맞춘다. 두 곡선을 같은
      // 방식으로 맞췄기 때문에, 과거 구간의 뒤쪽 절반을 그대로 지금
      // 구간 뒤에 이어 붙이면 "같은 흐름이 반복된다면"을 그릴 수 있다.
      var boundaryClose = last.close;
      var matchCurveFull = matchRows.concat(followRows).map(function (r) {
        return (r.close / boundaryClose - 1) * 100;
      });

      return {
        rank: rankIdx + 1,
        startDate: first.date,
        endDate: last.date,
        returnPct: (last.close / first.close - 1) * 100,
        followDays: windowDays,
        followEndDate: followEnd ? followEnd.date : null,
        followReturnPct: followEnd ? (followEnd.close / last.close - 1) * 100 : null,
        matchCurveFull: matchCurveFull,
        recentCurveForChart: recentCurveForChart,
        // 후보가 총 몇 개 중에서 이 순위였는지. 단 한 번의 사례를 마치
        // 통계처럼 부풀리지 않으려고, "유사 과거 불러오기"로 결과를 볼 때
        // 이 값을 같이 보여준다.
        totalCandidates: candidates.length,
      };
    });
  }

  function computeSimilarMatchesFromRows(rows) {
    var bySymbol = {};
    SIMILAR_WINDOWS.forEach(function (w) {
      var matches = findSimilarPastRanked(rows, w.days, 3);
      if (matches.length > 0) bySymbol[w.days] = matches;
    });
    return bySymbol;
  }

  // 종목의 유사 구간을 필요할 때 바로 계산한다(2026-09-21에 추가). 전에는
  // DATA 수집 탭에서 '내용 보기'를 먼저 눌러야만 SIMILAR_MATCHES_BY_SYMBOL가
  // 채워져서, 전략 비교·최적 조건 찾기의 '유사 과거 불러오기'가 그 순서를
  // 강제했다. 이미 계산해 둔 값이 있으면 그대로 쓰고, 없으면 시세를 직접
  // 받아서 그 자리에서 계산한다.
  function ensureSimilarMatches(symbol) {
    if (SIMILAR_MATCHES_BY_SYMBOL[symbol]) return Promise.resolve(SIMILAR_MATCHES_BY_SYMBOL[symbol]);
    return fetchPriceFileId(symbol)
      .then(function (fileId) {
        if (!fileId) throw new Error(symbol + "은(는) 아직 수집한 시세가 없습니다. 먼저 시세 수집을 하세요.");
        return fetch(URLS.getPrice + "?id=" + encodeURIComponent(fileId)).then(function (res) {
          if (!res.ok) throw new Error("응답 코드 " + res.status);
          return res.text();
        });
      })
      .then(function (text) {
        var rows = parsePriceRows(text);
        var bySymbol = computeSimilarMatchesFromRows(rows);
        SIMILAR_MATCHES_BY_SYMBOL[symbol] = bySymbol;
        return bySymbol;
      });
  }

  var ANALOG_NOW_COLOR = "#b5502e";
  var ANALOG_PROJECTED_COLOR = "#9b968a"; // 예상선(점선)은 순위와 상관없이 전부 이 회색을 쓴다
  var ANALOG_RANK_COLORS = ["#4a6fa5", "#8a5a9e", "#3d8f8a"]; // 1·2·3순위 과거 구간 색

  // matches: findSimilarPastRanked가 돌려준 배열(최대 3개). visibleRanks:
  // 지금 체크돼서 그래프에 그려야 하는 순위 목록(예: [1] 또는 [1,2]).
  // "지금 흐름"은 순위와 상관없이 하나(오늘까지 실제로 움직인 값)뿐이라,
  // 실선은 오늘에서 반드시 끊고 그 뒤는 과거 패턴을 그대로 옮겨 그린
  // 참고용 점선으로만 잇는다(2026-09-21에 다시 확인. 미래는 모르는데
  // 실선처럼 보이면 마치 예측인 것처럼 읽힌다는 지적을 받았다).
  function buildAnalogChart(matches, windowDays, visibleRanks) {
    var width = 360, height = 150, padding = { top: 8, right: 8, bottom: 20, left: 34 };
    var plotW = width - padding.left - padding.right;
    var plotH = height - padding.top - padding.bottom;
    var totalPoints = windowDays * 2;
    var recentCurveForChart = matches[0].recentCurveForChart;
    var visible = matches.filter(function (m) { return visibleRanks.indexOf(m.rank) !== -1; });

    var allValues = recentCurveForChart.concat([0]);
    visible.forEach(function (m) { allValues = allValues.concat(m.matchCurveFull); });
    var minV = Math.min.apply(null, allValues);
    var maxV = Math.max.apply(null, allValues);
    if (minV === maxV) { minV -= 1; maxV += 1; }

    function xAt(i) { return padding.left + (i / (totalPoints - 1)) * plotW; }
    function yAt(v) { return padding.top + plotH - ((v - minV) / (maxV - minV)) * plotH; }
    function pathFor(points, startIndex) {
      return points
        .map(function (v, i) { return (i === 0 ? "M" : "L") + xAt(startIndex + i).toFixed(1) + "," + yAt(v).toFixed(1); })
        .join(" ");
    }

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", "100%");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "과거 비슷한 구간과 지금 흐름, 예상 흐름 비교");

    var zero = document.createElementNS(svgNS, "line");
    zero.setAttribute("x1", padding.left);
    zero.setAttribute("x2", width - padding.right);
    zero.setAttribute("y1", yAt(0));
    zero.setAttribute("y2", yAt(0));
    zero.setAttribute("stroke", "currentColor");
    zero.setAttribute("stroke-opacity", "0.15");
    svg.appendChild(zero);

    var boundaryX = xAt(windowDays - 0.5);
    var boundary = document.createElementNS(svgNS, "line");
    boundary.setAttribute("x1", boundaryX);
    boundary.setAttribute("x2", boundaryX);
    boundary.setAttribute("y1", padding.top);
    boundary.setAttribute("y2", height - padding.bottom);
    boundary.setAttribute("stroke", "currentColor");
    boundary.setAttribute("stroke-opacity", "0.35");
    boundary.setAttribute("stroke-dasharray", "2,2");
    svg.appendChild(boundary);

    var todayLabel = document.createElementNS(svgNS, "text");
    todayLabel.setAttribute("x", boundaryX);
    todayLabel.setAttribute("y", padding.top + 9);
    todayLabel.setAttribute("text-anchor", "middle");
    todayLabel.setAttribute("font-size", "9");
    todayLabel.setAttribute("font-weight", "bold");
    todayLabel.setAttribute("fill", "currentColor");
    todayLabel.setAttribute("opacity", "0.7");
    todayLabel.textContent = "오늘";
    svg.appendChild(todayLabel);

    // x축에는 실제 날짜 대신 구간 시작으로부터 며칠째인지를 적는다.
    // 과거 구간마다 실제 달력 날짜가 다르고(2위·3위가 서로 다른 해일
    // 수도 있다), "지금" 흐름과 겹쳐 그리려면 어차피 날짜가 아니라
    // 일수로 자리를 맞춰야 하기 때문이다. 실제 달력 날짜는 본문
    // 글줄에 따로 적는다.
    [0, windowDays - 1, windowDays, totalPoints - 1].forEach(function (i, idx) {
      var label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", xAt(i));
      label.setAttribute("y", height - 6);
      label.setAttribute("text-anchor", idx === 0 ? "start" : idx === 3 ? "end" : "middle");
      label.setAttribute("font-size", "9");
      label.setAttribute("fill", "currentColor");
      label.setAttribute("opacity", "0.55");
      label.textContent = (i - (windowDays - 1)) <= 0 ? (i - (windowDays - 1)) + "일" : "+" + (i - (windowDays - 1)) + "일";
      svg.appendChild(label);
    });

    visible.forEach(function (m) {
      var color = ANALOG_RANK_COLORS[(m.rank - 1) % ANALOG_RANK_COLORS.length];
      var pastPath = document.createElementNS(svgNS, "path");
      pastPath.setAttribute("d", pathFor(m.matchCurveFull.slice(0, windowDays), 0));
      pastPath.setAttribute("fill", "none");
      pastPath.setAttribute("stroke", color);
      pastPath.setAttribute("stroke-width", "2");
      svg.appendChild(pastPath);

      var projPath = document.createElementNS(svgNS, "path");
      projPath.setAttribute("d", pathFor(m.matchCurveFull.slice(windowDays), windowDays));
      projPath.setAttribute("fill", "none");
      projPath.setAttribute("stroke", ANALOG_PROJECTED_COLOR);
      projPath.setAttribute("stroke-width", "1.6");
      projPath.setAttribute("stroke-dasharray", "3,3");
      svg.appendChild(projPath);
    });

    // "지금 흐름"은 오늘까지만 실선으로 긋는다. 그 뒤(미래)는 아무
    // 선도 안 그린다 — 위에서 그린 회색 점선이 "과거 패턴을 옮겨 본
    // 참고용 예상"이고, 실제로 있었던 일이 아니다.
    var recentPath = document.createElementNS(svgNS, "path");
    recentPath.setAttribute("d", pathFor(recentCurveForChart, 0));
    recentPath.setAttribute("fill", "none");
    recentPath.setAttribute("stroke", ANALOG_NOW_COLOR);
    recentPath.setAttribute("stroke-width", "2.4");
    svg.appendChild(recentPath);

    var todayDot = document.createElementNS(svgNS, "circle");
    todayDot.setAttribute("cx", xAt(windowDays - 1));
    todayDot.setAttribute("cy", yAt(recentCurveForChart[recentCurveForChart.length - 1]));
    todayDot.setAttribute("r", 2.6);
    todayDot.setAttribute("fill", ANALOG_NOW_COLOR);
    svg.appendChild(todayDot);

    var wrap = document.createElement("div");
    wrap.appendChild(svg);

    var legend = document.createElement("div");
    legend.className = "chart-legend";
    var legendItems = [{ color: ANALOG_NOW_COLOR, label: "지금 흐름(오늘까지 실제 값)" }];
    visible.forEach(function (m) {
      legendItems.push({
        color: ANALOG_RANK_COLORS[(m.rank - 1) % ANALOG_RANK_COLORS.length],
        label: m.rank + "순위 과거 구간(" + m.startDate + " ~ " + m.endDate + ")",
      });
    });
    legendItems.push({ color: ANALOG_PROJECTED_COLOR, label: "점선: 과거 패턴을 지금 시점에 옮겨 본 참고용 예상(실제 아님)" });
    legendItems.forEach(function (item) {
      var span = document.createElement("span");
      var i = document.createElement("i");
      i.style.background = item.color;
      span.appendChild(i);
      span.appendChild(document.createTextNode(item.label));
      legend.appendChild(span);
    });
    wrap.appendChild(legend);

    return wrap;
  }

  function loadPriceDetail(symbol, fileId) {
    priceDetailEl.innerHTML = "";
    setStatus(pricesListStatus, "", symbol + " 내용을 불러오는 중입니다...");
    fetch(URLS.getPrice + "?id=" + encodeURIComponent(fileId))
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.text();
      })
      .then(function (text) {
        var rows = parsePriceRows(text);
        var summary = summarizeRows(rows);
        priceDetailEl.innerHTML = "";

        var p = document.createElement("p");
        p.className = "desc";
        if (summary.rows === 0) {
          p.textContent = symbol + ": 파일은 있지만 거래일 자료가 없습니다.";
          priceDetailEl.appendChild(p);
          setStatus(pricesListStatus, "ok", symbol + " 내용을 불러왔습니다.");
          return;
        }
        p.textContent =
          symbol + ": 거래일 " + summary.rows + "개, " +
          summary.startDate + " ~ " + summary.endDate +
          ", 마지막 종가 " + summary.lastClose;
        priceDetailEl.appendChild(p);

        priceDetailEl.appendChild(buildPriceChart(rows, symbol));

        var simIntro = document.createElement("p");
        simIntro.className = "desc";
        simIntro.textContent =
          "값이 움직인 모양이 최근 흐름과 가장 비슷했던 과거 구간입니다. 1순위를 기본으로 보여주고, " +
          "2·3순위는 체크하면 같은 그래프에 겹쳐 그립니다. 과거 구간이라 그 뒤에 값이 어떻게 움직였는지도 " +
          "이미 알 수 있어서, 같은 길이만큼 이어진 실제 수익률을 같이 보여줍니다. 그래프에서 지금 흐름은 " +
          "오늘까지만 실선으로 긋고 그 뒤는 아무것도 그리지 않습니다. 회색 점선은 과거 흐름을 지금 시점에 " +
          "그대로 옮겨 본 참고용일 뿐, 실제로 일어난 일이 아닙니다.";
        priceDetailEl.appendChild(simIntro);

        var similarBySymbol = computeSimilarMatchesFromRows(rows);
        SIMILAR_MATCHES_BY_SYMBOL[symbol] = similarBySymbol;

        SIMILAR_WINDOWS.forEach(function (w) {
          var matches = similarBySymbol[w.days];
          if (!matches) {
            var noneLine = document.createElement("p");
            noneLine.className = "desc";
            noneLine.textContent = w.label + "(" + w.days + "거래일): 비교할 과거 데이터가 부족합니다.";
            priceDetailEl.appendChild(noneLine);
            return;
          }

          var box = document.createElement("div");
          box.className = "similar-box";

          matches.forEach(function (m) {
            var line = document.createElement("p");
            line.className = "desc";
            line.textContent =
              w.label + "(" + w.days + "거래일) " + m.rank + "순위로 비슷했던 구간: " +
              m.startDate + " ~ " + m.endDate +
              " (그 구간 수익률 " + m.returnPct.toFixed(1) + "%). " +
              "그 뒤 " + m.followDays + "거래일(" + m.endDate + " ~ " + m.followEndDate + ") 동안 수익률 " +
              (m.followReturnPct !== null ? m.followReturnPct.toFixed(1) + "%" : "자료 부족");
            box.appendChild(line);
          });

          var visibleRanks = [1];
          var chartHost = document.createElement("div");
          function rerenderChart() {
            chartHost.innerHTML = "";
            chartHost.appendChild(buildAnalogChart(matches, w.days, visibleRanks));
          }

          if (matches.length > 1) {
            var toggles = document.createElement("div");
            toggles.className = "row";
            matches.slice(1).forEach(function (m) {
              var label = document.createElement("label");
              label.className = "row";
              var checkbox = document.createElement("input");
              checkbox.type = "checkbox";
              checkbox.addEventListener("change", function () {
                if (checkbox.checked) {
                  if (visibleRanks.indexOf(m.rank) === -1) visibleRanks.push(m.rank);
                } else {
                  visibleRanks = visibleRanks.filter(function (r) { return r !== m.rank; });
                }
                rerenderChart();
              });
              label.appendChild(checkbox);
              label.appendChild(document.createTextNode(
                " " + m.rank + "순위도 그래프에 표시(" + m.startDate + " ~ " + m.endDate + ")"
              ));
              toggles.appendChild(label);
            });
            box.appendChild(toggles);
          }

          box.appendChild(chartHost);
          rerenderChart();
          priceDetailEl.appendChild(box);
        });

        setStatus(pricesListStatus, "ok", symbol + " 내용을 불러왔습니다.");
      })
      .catch(function (err) {
        setStatus(pricesListStatus, "err", symbol + " 내용을 불러오지 못했습니다: " + err.message);
      });
  }

  // ── 3. 전략 비교 입력 ──────────────────────────────────
  var strategyListEl = document.getElementById("strategy-list");
  var addStrategyBtn = document.getElementById("add-strategy");
  var strategyBlocks = []; // { id, el, type }
  var nextBlockId = 1;

  function renderParamField(param, blockId) {
    var wrap = document.createElement("label");
    wrap.textContent = param.label;
    var fieldId = "p-" + blockId + "-" + param.name;

    if (param.type === "choice") {
      var select = document.createElement("select");
      select.id = fieldId;
      param.options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (opt.value === param.suggested) o.selected = true;
        select.appendChild(o);
      });
      wrap.appendChild(select);
      return wrap;
    }

    if (param.type === "tiers") {
      var tiersWrap = document.createElement("div");
      tiersWrap.id = fieldId;
      tiersWrap.className = "tiers-field";
      var rowsHost = document.createElement("div");
      tiersWrap.appendChild(rowsHost);

      function addTierRow(threshold, amount) {
        var row = document.createElement("div");
        row.className = "tier-row";
        var t = document.createElement("input");
        t.type = "number";
        t.step = "0.1";
        t.placeholder = "등락률 %(예: -5 또는 +5)";
        t.value = threshold;
        var a = document.createElement("input");
        a.type = "number";
        a.placeholder = "매수 금액(원)";
        a.value = amount;
        var rm = document.createElement("button");
        rm.type = "button";
        rm.textContent = "삭제";
        rm.addEventListener("click", function () { row.remove(); });
        row.appendChild(t);
        row.appendChild(a);
        row.appendChild(rm);
        rowsHost.appendChild(row);
      }

      (param.suggested || []).forEach(function (pair) { addTierRow(pair[0], pair[1]); });

      var addRowBtn = document.createElement("button");
      addRowBtn.type = "button";
      addRowBtn.textContent = "+ 구간 추가";
      addRowBtn.addEventListener("click", function () { addTierRow("", ""); });
      tiersWrap.appendChild(addRowBtn);
      wrap.appendChild(tiersWrap);
      return wrap;
    }

    var input = document.createElement("input");
    input.type = "number";
    input.id = fieldId;
    input.value = param.suggested !== undefined ? param.suggested : "";
    wrap.appendChild(input);
    return wrap;
  }

  function addStrategyBlock() {
    if (strategyBlocks.length >= MAX_STRATEGIES) return;
    var blockId = nextBlockId++;
    var el = document.createElement("div");
    el.className = "strategy-block";

    var head = document.createElement("div");
    head.className = "row-head";

    var typeSelect = document.createElement("select");
    Object.keys(STRATEGY_SCHEMAS).forEach(function (key) {
      var o = document.createElement("option");
      o.value = key;
      o.textContent = STRATEGY_SCHEMAS[key].label;
      typeSelect.appendChild(o);
    });

    var labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.placeholder = "표에 표시할 이름(선택)";

    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-strategy";
    removeBtn.textContent = "삭제";
    removeBtn.addEventListener("click", function () {
      el.remove();
      strategyBlocks = strategyBlocks.filter(function (b) { return b.id !== blockId; });
    });

    head.appendChild(typeSelect);
    head.appendChild(labelInput);
    head.appendChild(removeBtn);
    el.appendChild(head);

    var descEl = document.createElement("p");
    descEl.className = "hint";
    el.appendChild(descEl);

    var paramsHost = document.createElement("div");
    paramsHost.className = "param-grid";
    el.appendChild(paramsHost);

    var tpLabel = document.createElement("label");
    tpLabel.textContent = "익절선(매수평균가 대비%) — 비워 두면 안 씀";
    var tpInput = document.createElement("input");
    tpInput.type = "number";
    tpInput.step = "0.1";
    tpLabel.appendChild(tpInput);
    el.appendChild(tpLabel);

    var slLabel = document.createElement("label");
    slLabel.textContent = "손절선(매수평균가 대비%) — 비워 두면 안 씀";
    var slInput = document.createElement("input");
    slInput.type = "number";
    slInput.step = "0.1";
    slLabel.appendChild(slInput);
    el.appendChild(slLabel);

    function renderParams() {
      paramsHost.innerHTML = "";
      var schema = STRATEGY_SCHEMAS[typeSelect.value];
      descEl.textContent = schema.description;
      schema.params.forEach(function (param) {
        paramsHost.appendChild(renderParamField(param, blockId));
      });
    }
    typeSelect.addEventListener("change", renderParams);
    renderParams();

    strategyListEl.appendChild(el);
    strategyBlocks.push({
      id: blockId,
      el: el,
      typeSelect: typeSelect,
      labelInput: labelInput,
      tpInput: tpInput,
      slInput: slInput,
      paramsHost: paramsHost,
    });
    addStrategyBtn.disabled = strategyBlocks.length >= MAX_STRATEGIES;
  }

  addStrategyBtn.addEventListener("click", addStrategyBlock);
  // 시작할 때 두 개를 미리 만들어 둔다(빈 화면보다 낫다). 종류는 사용자가 바꿀 수 있다.
  addStrategyBlock();
  addStrategyBlock();
  if (strategyBlocks[1]) strategyBlocks[1].typeSelect.value = "dca";
  if (strategyBlocks[1]) strategyBlocks[1].typeSelect.dispatchEvent(new Event("change"));

  document.getElementById("bt-start").value = yearsAgoStr(5);
  document.getElementById("bt-end").value = todayStr();

  function collectStrategyConfig(block) {
    var key = block.typeSelect.value;
    var schema = STRATEGY_SCHEMAS[key];
    var config = { key: key };
    var label = block.labelInput.value.trim();
    if (label) config.label = label;

    schema.params.forEach(function (param) {
      var fieldId = "p-" + block.id + "-" + param.name;
      if (param.type === "tiers") {
        var host = document.getElementById(fieldId);
        var tiers = [];
        host.querySelectorAll(".tier-row").forEach(function (row) {
          var inputs = row.querySelectorAll("input");
          var threshold = parseFloat(inputs[0].value);
          var amount = parseFloat(inputs[1].value);
          if (!isNaN(threshold) && !isNaN(amount)) tiers.push([threshold, amount]);
        });
        config.tiers = tiers;
        return;
      }
      var el = document.getElementById(fieldId);
      if (!el) return;
      if (param.type === "int") {
        var n = parseInt(el.value, 10);
        if (!isNaN(n)) config[param.name] = n;
      } else {
        config[param.name] = el.value;
      }
    });

    var tp = parseFloat(block.tpInput.value);
    if (!isNaN(tp)) config.take_profit_pct = tp / 100;

    var sl = parseFloat(block.slInput.value);
    if (!isNaN(sl)) config.stop_loss_pct = sl / 100;

    return config;
  }

  var backtestForm = document.getElementById("form-backtest");
  var btStatus = document.getElementById("bt-status");
  var backtestPollTimer = null;
  function setBacktestPollTimer(t) { backtestPollTimer = t; }

  backtestForm.addEventListener("submit", function (event) {
    event.preventDefault();

    var capital = parseFloat(document.getElementById("bt-capital").value);
    var symbols = document
      .getElementById("bt-symbols")
      .value.split(",")
      .map(function (s) { return s.trim().toUpperCase(); })
      .filter(Boolean)
      .join(",");
    var start = document.getElementById("bt-start").value;
    var end = document.getElementById("bt-end").value;

    if (!symbols || !start || !end || !capital) {
      setStatus(btStatus, "err", "총자본·종목·조회기간을 모두 입력하세요.");
      return;
    }
    if (strategyBlocks.length === 0) {
      setStatus(btStatus, "err", "전략을 하나 이상 추가하세요.");
      return;
    }

    var strategies = strategyBlocks.map(collectStrategyConfig);
    for (var i = 0; i < strategies.length; i++) {
      var cfg = strategies[i];
      var schema = STRATEGY_SCHEMAS[cfg.key];
      for (var j = 0; j < schema.params.length; j++) {
        var p = schema.params[j];
        if (p.type === "tiers") {
          if (!cfg.tiers || cfg.tiers.length === 0) {
            setStatus(btStatus, "err", (i + 1) + "번째 전략(" + schema.label + ")의 등락률 구간을 입력하세요.");
            return;
          }
        } else if (p.optional) {
          continue; // 선택값. 짝이 맞는지는 아래에서 따로 본다.
        } else if (cfg[p.name] === undefined || cfg[p.name] === "") {
          setStatus(btStatus, "err", (i + 1) + "번째 전략(" + schema.label + ")의 '" + p.label + "'을(를) 입력하세요.");
          return;
        }
      }
      var pairError = checkParamPairs(schema, cfg, (i + 1) + "번째 전략(" + schema.label + ")");
      if (pairError) {
        setStatus(btStatus, "err", pairError);
        return;
      }
      if (cfg.key === "dca_ma" && cfg.below_amount === undefined && cfg.above_amount === undefined) {
        setStatus(btStatus, "err", (i + 1) + "번째 전략(" + schema.label + ")은 이동평균선 아래·위 중 최소 한쪽은 매수금액과 매수빈도를 채워야 합니다.");
        return;
      }
    }

    if (backtestPollTimer) {
      clearTimeout(backtestPollTimer);
      backtestPollTimer = null;
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }

    setStatus(btStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.checkRun + "?workflow=run-backtest.yml")
      .then(function (res) { return res.ok ? res.json() : []; })
      .catch(function () { return []; })
      .then(function (beforeRuns) {
        var previousRunId = beforeRuns && beforeRuns[0] ? beforeRuns[0].id : null;
        return fetch(URLS.runBacktest, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbols: symbols,
            strategies: JSON.stringify(strategies),
            capital: capital,
            start: start,
            end: end,
            upload: true,
          }),
        }).then(function (res) {
          if (!res.ok) throw new Error("응답 코드 " + res.status);
          setStatus(btStatus, "", "요청을 보냈습니다. 완료되면 자동으로 알려 드립니다...");
          backtestPollTimer = setTimeout(function () {
            pollWorkflowRun("run-backtest.yml", btStatus, previousRunId, 1, setBacktestPollTimer, function (latest) {
              if (latest.conclusion === "success") {
                setStatus(btStatus, "ok", "완료됐습니다(" + symbols + "). 비교 결과 탭에 자동으로 반영했습니다.");
                notifyIfPermitted("전략 비교 완료", symbols + " 비교 계산이 끝났습니다.");
                refreshBtn.click();
              } else {
                setStatus(btStatus, "err", "계산이 실패로 끝났습니다(" + latest.conclusion + "). GitHub Actions 로그를 확인해야 합니다.");
                notifyIfPermitted("전략 비교 실패", symbols + " 비교 계산이 실패했습니다.");
              }
            });
          }, 5000);
        });
      })
      .catch(function (err) {
        setStatus(btStatus, "err", "요청을 보내지 못했습니다: " + err.message);
      });
  });

  // ── 4. 결과 조회 ───────────────────────────────────────
  var refreshBtn = document.getElementById("refresh-results");
  var resultSelect = document.getElementById("result-select");
  var resultsStatus = document.getElementById("results-status");
  var resultView = document.getElementById("result-view");
  var deleteResultBtn = document.getElementById("delete-result");

  // 지금 보고 있는 결과의 file_id와, 그 결과가 만든 엑셀들의 file_id를
  // 기억해 둔다. '지우기'가 이 둘을 한꺼번에 지운다(2026-09-21에 추가).
  var currentResultId = null;
  var currentResultExcelFiles = []; // [{file_id, 이름}]

  function openFile(fileId, name) {
    if (!fileId) return;
    window.open(URLS.getFile + "?id=" + encodeURIComponent(fileId) + "&name=" + encodeURIComponent(name || "result.xlsx"), "_blank");
  }

  refreshBtn.addEventListener("click", function () {
    setStatus(resultsStatus, "", "목록을 불러오는 중입니다...");
    fetch(URLS.listResults)
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (files) {
        resultSelect.innerHTML = "";
        if (!files || files.length === 0) {
          var o = document.createElement("option");
          o.textContent = "아직 결과가 없습니다";
          resultSelect.appendChild(o);
          setStatus(resultsStatus, "ok", "결과가 아직 없습니다.");
          return;
        }
        files.forEach(function (f) {
          var o = document.createElement("option");
          o.value = f.id;
          o.textContent = f.name + " (" + fmtDateTimeKST(f.modifiedTime) + ")";
          resultSelect.appendChild(o);
        });
        setStatus(resultsStatus, "ok", files.length + "건을 찾았습니다.");
        loadResult(files[0].id);
      })
      .catch(function (err) {
        setStatus(resultsStatus, "err", "목록을 불러오지 못했습니다: " + err.message);
      });
  });

  resultSelect.addEventListener("change", function () {
    if (resultSelect.value) loadResult(resultSelect.value);
  });

  function loadResult(id) {
    resultView.innerHTML = "";
    deleteResultBtn.disabled = true;
    currentResultId = null;
    currentResultExcelFiles = [];
    setStatus(resultsStatus, "", "결과를 불러오는 중입니다...");
    fetch(URLS.getResult + "?id=" + encodeURIComponent(id))
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (data) {
        setStatus(resultsStatus, "ok", "생성 시각(KST): " + (data["생성시각_KST"] || "알 수 없음"));
        currentResultId = id;
        renderResult(data);
        deleteResultBtn.disabled = false;
      })
      .catch(function (err) {
        setStatus(resultsStatus, "err", "결과를 불러오지 못했습니다: " + err.message);
      });
  }

  deleteResultBtn.addEventListener("click", function () {
    if (!currentResultId) return;
    var label = resultSelect.options[resultSelect.selectedIndex]
      ? resultSelect.options[resultSelect.selectedIndex].textContent
      : "이 결과";
    var fileIds = [currentResultId].concat(currentResultExcelFiles.map(function (f) { return f.file_id; }));
    var confirmMsg =
      label + "를 지웁니다.\n\n" +
      "결과 파일 1개" + (currentResultExcelFiles.length ? "와 엑셀 " + currentResultExcelFiles.length + "개" : "") +
      "가 구글 드라이브에서 함께 지워지고, 되돌릴 수 없습니다.\n계속할까요?";
    if (!window.confirm(confirmMsg)) return;

    setStatus(resultsStatus, "", "지우는 중입니다...");
    deleteResultBtn.disabled = true;
    fetch(URLS.deleteResult, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_ids: fileIds }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (result) {
        setStatus(resultsStatus, "ok", "지웠습니다(" + result["성공"] + "/" + result["요청"] + "건). 목록을 새로고침합니다.");
        resultView.innerHTML = "";
        currentResultId = null;
        currentResultExcelFiles = [];
        refreshBtn.click();
      })
      .catch(function (err) {
        setStatus(resultsStatus, "err", "지우지 못했습니다: " + err.message);
        deleteResultBtn.disabled = false;
      });
  });

  function fmtCell(v, suffix) {
    if (v === null || v === undefined || v === "") return "-";
    return v + (suffix || "");
  }

  function renderResult(data) {
    var summary = data["요약"] || [];
    var series = data["시계열"] || {};

    // 지우기 버튼이 이 결과의 file_id와 함께 지울 엑셀 목록을 여기서
    // 모은다. 종목·전략 조합별 엑셀(row["엑셀"])과 전체 비교 엑셀
    // (data["비교엑셀"])이 있다(2026-09-21에 추가. find_best_strategy.py
    // 결과에는 비교엑셀만 있고 조합별 엑셀은 없다. 조합이 너무 많아서다).
    currentResultExcelFiles = [];
    summary.forEach(function (row) {
      if (row["엑셀"] && row["엑셀"].file_id) currentResultExcelFiles.push(row["엑셀"]);
    });
    if (data["비교엑셀"] && data["비교엑셀"].file_id) currentResultExcelFiles.push(data["비교엑셀"]);

    if (data["비교엑셀"] && data["비교엑셀"].file_id) {
      var wholeBtn = document.createElement("button");
      wholeBtn.type = "button";
      wholeBtn.textContent = "전체 비교 엑셀 보기";
      wholeBtn.addEventListener("click", function () {
        openFile(data["비교엑셀"].file_id, data["비교엑셀"]["이름"]);
      });
      resultView.appendChild(wholeBtn);
    }

    // Walk-forward 결과는 폴드별 학습·검증 성과를 담고 있어서 모양이
    // 완전히 다르다. "폴드별_결과" 키로 구분해서 전용 화면을 그린다
    // (2026-09-22에 추가). 그 외(전략 비교, 최적 조건 찾기)는 기존 표
    // 그대로 그린다.
    if (data["폴드별_결과"]) {
      renderWalkforwardResult(data);
      return;
    }

    // 전략 이름을 코드에 가까운 문자열 하나로 보여주면 읽기 어렵다는
    // 지적을 받아서, 매수방식·매수금액·매수빈도·이동평균조건·등락구간·
    // 익절선·손절선을 각각 칸으로 나눴다(summarize_result의
    // describe_strategy가 만든 값). 칸이 많아 폭이 좁은 화면에서는
    // 가로로 스크롤하게 wrap을 둔다.
    var wrap = document.createElement("div");
    wrap.className = "table-scroll";

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>종목</th><th>매수방식</th><th>매수금액</th><th>매수빈도</th><th>이동평균조건</th>" +
      "<th>등락구간</th><th>익절선</th><th>손절선</th><th>총투자금</th><th>실현손익</th>" +
      "<th>평가손익</th><th>합계</th><th>수익률</th><th>최대낙폭</th><th>엑셀</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    summary.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + row.symbol + "</td>" +
        "<td>" + fmtCell(row["매수방식"]) + "</td>" +
        "<td>" + (row["매수금액"] !== null && row["매수금액"] !== undefined ? fmtNumber(row["매수금액"]) : "-") + "</td>" +
        "<td>" + fmtCell(row["매수빈도"], "일") + "</td>" +
        "<td>" + fmtCell(row["이동평균조건"]) + "</td>" +
        "<td>" + fmtCell(row["등락구간"]) + "</td>" +
        "<td>" + fmtCell(row["익절선"]) + "</td>" +
        "<td>" + fmtCell(row["손절선"]) + "</td>" +
        "<td>" + fmtNumber(row["총투자금"]) + "</td>" +
        "<td>" + fmtNumber(row["실현손익"]) + "</td>" +
        "<td>" + fmtNumber(row["평가손익"]) + "</td>" +
        "<td>" + fmtNumber(row["합계"]) + "</td>" +
        "<td>" + row["수익률"] + "%</td>" +
        "<td>" + fmtCell(row["최대낙폭"], "%") + "</td>" +
        "<td></td>";
      if (row["엑셀"] && row["엑셀"].file_id) {
        var excelBtn = document.createElement("button");
        excelBtn.type = "button";
        excelBtn.textContent = "보기";
        excelBtn.addEventListener("click", function () {
          openFile(row["엑셀"].file_id, row["엑셀"]["이름"]);
        });
        tr.lastElementChild.appendChild(excelBtn);
      } else {
        tr.lastElementChild.textContent = "-";
      }
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    resultView.appendChild(wrap);

    var chart = buildChart(series);
    if (chart) resultView.appendChild(chart);

    var similarSelection = matchingSimilarSelection(data);
    if (similarSelection) resultView.appendChild(buildSimilarFollowupNote(similarSelection));
  }

  // Walk-forward 결과 전용 화면. 폴드마다 학습기간에서 고른 조건과 그
  // 조건을 검증기간에 그대로 적용한 성과를 나란히 보여주고, 검증기간들을
  // 이어 붙인 전체 OOS 성과, 그리고 기존 방식(전체 기간 최적화)과의
  // 비교를 같이 보여준다(2026-09-22).
  function renderWalkforwardResult(data) {
    var config = data["워크포워드_설정"] || {};
    var folds = data["폴드별_결과"] || [];
    var combined = data["전체_OOS_성과"] || {};
    var baseline = data["비교_전체기간_최적화"] || [];

    var intro = document.createElement("p");
    intro.className = "desc";
    intro.textContent =
      "학습 " + fmtCell(config["학습기간_년"]) + "년 · 검증 " + fmtCell(config["검증기간_년"]) +
      "년 · 이동 " + fmtCell(config["이동간격_년"]) + "년으로 폴드 " + folds.length + "개를 검증했습니다. " +
      "'검증' 칸은 그 폴드의 학습기간에서 고른 조건을 검증기간에 손대지 않고 그대로 적용한 결과입니다. " +
      "검증기간의 데이터는 조건을 고르는 데 전혀 쓰지 않았습니다.";
    resultView.appendChild(intro);

    var wrap = document.createElement("div");
    wrap.className = "table-scroll";
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>폴드</th><th>검증기간</th><th>선정조건</th>" +
      "<th>학습 수익률</th><th>검증 수익률</th><th>검증 CAGR</th><th>검증 최대낙폭</th>" +
      "<th>검증 회복일수</th><th>검증 Calmar</th><th>검증 거래횟수</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    folds.forEach(function (fold) {
      var train = fold["학습기간_성과"] || {};
      var test = fold["검증기간_성과"] || {};
      var recoveryText = test["최대낙폭회복일수"] === null || test["최대낙폭회복일수"] === undefined
        ? "회복 못 함"
        : test["최대낙폭회복일수"] + "일";
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + fold["폴드"] + "</td>" +
        "<td>" + fold["검증기간"]["시작"] + " ~ " + fold["검증기간"]["종료"] + "</td>" +
        "<td>" + fold["선정조건"]["설명"] + "</td>" +
        "<td>" + fmtCell(train["누적수익률"], "%") + "</td>" +
        "<td>" + fmtCell(test["누적수익률"], "%") + "</td>" +
        "<td>" + fmtCell(test["CAGR"], "%") + "</td>" +
        "<td>" + fmtCell(test["최대낙폭"], "%") + "</td>" +
        "<td>" + recoveryText + "</td>" +
        "<td>" + fmtCell(test["Calmar"]) + "</td>" +
        "<td>" + fmtCell(test["거래횟수"]) + "</td>";
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    resultView.appendChild(wrap);

    var combinedBox = document.createElement("div");
    combinedBox.className = "similar-box";
    var combinedTitle = document.createElement("p");
    combinedTitle.className = "desc";
    var combinedStrong = document.createElement("strong");
    combinedStrong.textContent = "전체 OOS 성과(검증기간을 자본으로 이어 붙인 것)";
    combinedTitle.appendChild(combinedStrong);
    combinedBox.appendChild(combinedTitle);
    var combinedRecovery = combined["최대낙폭회복일수"] === null || combined["최대낙폭회복일수"] === undefined
      ? "회복 못 함"
      : combined["최대낙폭회복일수"] + "일";
    var combinedLine = document.createElement("p");
    combinedLine.className = "desc";
    combinedLine.textContent =
      "누적수익률 " + fmtCell(combined["누적수익률"], "%") + ", CAGR " + fmtCell(combined["CAGR"], "%") +
      ", 최대낙폭 " + fmtCell(combined["최대낙폭"], "%") + ", 회복일수 " + combinedRecovery +
      ", Calmar " + fmtCell(combined["Calmar"]) + ", 거래횟수 " + fmtCell(combined["거래횟수"]);
    combinedBox.appendChild(combinedLine);
    resultView.appendChild(combinedBox);

    if (baseline.length > 0) {
      var baseBox = document.createElement("div");
      baseBox.className = "similar-box";
      var baseTitle = document.createElement("p");
      baseTitle.className = "desc";
      var baseStrong = document.createElement("strong");
      baseStrong.textContent = "비교: 기존 방식(전체 기간에서 한 번에 고른 1위)";
      baseTitle.appendChild(baseStrong);
      baseBox.appendChild(baseTitle);
      var best = baseline[0];
      var baseLine = document.createElement("p");
      baseLine.className = "desc";
      baseLine.textContent =
        best["strategy_name"] + " — 전체 기간 수익률 " + best["수익률"] + "%, 최대낙폭 " +
        best["최대낙폭"] + "%. 이 조건이 위 폴드들의 '선정조건' 칸에도 반복해서 나오는지 " +
        "직접 견줘 보세요. 전체 기간 1위와 자주 다른 조건이 뽑혔다면, 전체 기간 1위는 " +
        "그 기간에만 맞았던 조건(과최적화)일 수 있습니다.";
      baseBox.appendChild(baseLine);
      resultView.appendChild(baseBox);
    }
  }

  var PALETTE = ["#2f6f65", "#b5502e", "#4a6fa5", "#8a5a9e", "#c98f1c", "#5a8f4a", "#a5455a", "#3d8f8a"];

  function buildChart(series) {
    var keys = Object.keys(series);
    if (keys.length === 0) return null;

    var width = 640, height = 280, padding = { top: 10, right: 10, bottom: 24, left: 60 };
    var plotW = width - padding.left - padding.right;
    var plotH = height - padding.top - padding.bottom;

    var allValues = [];
    var maxLen = 0;
    keys.forEach(function (k) {
      series[k].forEach(function (p) { allValues.push(p.total_value); });
      maxLen = Math.max(maxLen, series[k].length);
    });
    var minV = Math.min.apply(null, allValues);
    var maxV = Math.max.apply(null, allValues);
    if (minV === maxV) { minV -= 1; maxV += 1; }

    function xAt(i, len) { return padding.left + (len <= 1 ? 0 : (i / (len - 1)) * plotW); }
    function yAt(v) { return padding.top + plotH - ((v - minV) / (maxV - minV)) * plotH; }

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", "100%");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "전략별 총자산 추이");

    // y축 가이드라인 3개
    [0, 0.5, 1].forEach(function (t) {
      var v = minV + (maxV - minV) * t;
      var y = yAt(v);
      var line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", padding.left);
      line.setAttribute("x2", width - padding.right);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("stroke", "currentColor");
      line.setAttribute("stroke-opacity", "0.15");
      svg.appendChild(line);

      var label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", padding.left - 6);
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "10");
      label.setAttribute("fill", "currentColor");
      label.setAttribute("opacity", "0.6");
      label.textContent = fmtNumber(v);
      svg.appendChild(label);
    });

    // 거래일 몇 군데를 골라 x축에 날짜를 적는다(처음, 끝, 그 사이 고르게).
    var longestKey = keys.reduce(function (a, b) { return series[a].length >= series[b].length ? a : b; });
    var longestPoints = series[longestKey];
    var xTickCount = Math.min(5, longestPoints.length);
    for (var ti = 0; ti < xTickCount; ti++) {
      var idx2 = xTickCount <= 1 ? 0 : Math.round((ti / (xTickCount - 1)) * (longestPoints.length - 1));
      var xTickText = document.createElementNS(svgNS, "text");
      xTickText.setAttribute("x", xAt(idx2, longestPoints.length));
      xTickText.setAttribute("y", height - 6);
      xTickText.setAttribute("text-anchor", ti === 0 ? "start" : ti === xTickCount - 1 ? "end" : "middle");
      xTickText.setAttribute("font-size", "10");
      xTickText.setAttribute("fill", "currentColor");
      xTickText.setAttribute("opacity", "0.6");
      xTickText.textContent = longestPoints[idx2].trade_date;
      svg.appendChild(xTickText);
    }

    var buyColor = "#2f6f65";
    var takeProfitColor = "#c98f1c";
    var stopLossColor = "#b5502e";

    keys.forEach(function (k, idx) {
      var points = series[k];
      var color = PALETTE[idx % PALETTE.length];
      var d = points
        .map(function (p, i) { return (i === 0 ? "M" : "L") + xAt(i, points.length).toFixed(1) + "," + yAt(p.total_value).toFixed(1); })
        .join(" ");
      var path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "2");
      svg.appendChild(path);

      // 그날 매수·익절 매도·손절 매도가 있었으면 점으로 표시한다.
      points.forEach(function (p, i) {
        var markerColor = null;
        if (p.sell_type === "익절") markerColor = takeProfitColor;
        else if (p.sell_type === "손절") markerColor = stopLossColor;
        else if (p.buy_amount) markerColor = buyColor;
        if (!markerColor) return;
        var dot = document.createElementNS(svgNS, "circle");
        dot.setAttribute("cx", xAt(i, points.length).toFixed(1));
        dot.setAttribute("cy", yAt(p.total_value).toFixed(1));
        dot.setAttribute("r", p.sell_type ? 3 : 2.3);
        dot.setAttribute("fill", markerColor);
        dot.setAttribute("stroke", "var(--surface)");
        dot.setAttribute("stroke-width", "0.7");
        var title = document.createElementNS(svgNS, "title");
        title.textContent = p.trade_date + " " + (p.sell_type ? p.sell_type + " 매도" : "매수 " + fmtNumber(p.buy_amount) + "원");
        dot.appendChild(title);
        svg.appendChild(dot);
      });
    });

    var wrap = document.createElement("div");
    wrap.appendChild(svg);

    var legend = document.createElement("div");
    legend.className = "chart-legend";
    keys.forEach(function (k, idx) {
      var item = document.createElement("span");
      var swatch = document.createElement("i");
      swatch.style.background = PALETTE[idx % PALETTE.length];
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(k + " 총자산"));
      legend.appendChild(item);
    });
    [
      { color: buyColor, label: "매수 시점" },
      { color: takeProfitColor, label: "익절 매도" },
      { color: stopLossColor, label: "손절 매도" },
    ].forEach(function (item) {
      var span = document.createElement("span");
      var i = document.createElement("i");
      i.style.background = item.color;
      i.style.borderRadius = "50%";
      span.appendChild(i);
      span.appendChild(document.createTextNode(item.label));
      legend.appendChild(span);
    });
    wrap.appendChild(legend);

    return wrap;
  }

  // ── 5. 최적 조건 찾기 ──────────────────────────────────
  var optimizeListEl = document.getElementById("optimize-strategy-list");
  var optComboStatus = document.getElementById("opt-combo-status");
  var optSubmitBtn = document.getElementById("opt-submit");
  var optBlocks = {}; // key -> { checkbox, fields, paramsHost }

  function parseIntListText(text) {
    return text
      .split(",")
      .map(function (s) { return parseInt(s.trim(), 10); })
      .filter(function (n) { return !isNaN(n); });
  }
  function parseFloatListText(text) {
    return text
      .split(",")
      .map(function (s) { return parseFloat(s.trim()); })
      .filter(function (n) { return !isNaN(n); });
  }
  // 익절·손절 후보는 사람에게는 %로 보여주고 보내기 직전에 비율로 바꾼다
  // (다른 화면의 익절·손절 입력칸과 같은 방식).
  function parsePercentListText(text) {
    return parseFloatListText(text).map(function (n) { return n / 100; });
  }

  function collectOptimizeStrategy(key) {
    var schema = STRATEGY_SEARCH_SCHEMAS[key];
    var block = optBlocks[key];
    var values = {};
    var count = 1;
    for (var i = 0; i < schema.params.length; i++) {
      var param = schema.params[i];
      var field = block.fields[param.name];
      var list;
      if (param.type === "choice_multi") {
        list = field.checkboxes.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
      } else if (param.type === "int_list") {
        list = parseIntListText(field.input.value);
      } else if (param.type === "percent_optional") {
        list = parsePercentListText(field.input.value);
      } else {
        list = parseFloatListText(field.input.value);
      }

      // 선택값(익절·손절, dca_ma의 아래/위 매수금액·매수빈도)은 비워
      // 두면 그 조건 없이 계산하는 조합 하나로 보고, 조합 수도 늘리지
      // 않는다. 다른 변수는 비우면 오류다.
      if (param.optional) {
        if (list.length > 0) {
          values[param.name] = list;
          count *= list.length;
        }
        continue;
      }
      if (list.length === 0) {
        return { error: schema.label + "의 '" + param.label + "'에 후보값을 하나 이상 넣으세요." };
      }
      values[param.name] = list;
      count *= list.length;
    }

    var pairError = checkParamPairs(schema, values, schema.label);
    if (pairError) return { error: pairError };
    if (key === "dca_ma" && values.below_amount === undefined && values.above_amount === undefined) {
      return { error: schema.label + "은 이동평균선 아래·위 중 최소 한쪽은 매수금액과 매수빈도를 채워야 합니다." };
    }

    return { values: values, count: count };
  }

  function updateComboCount() {
    var total = 0;
    var firstError = null;
    Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
      var block = optBlocks[key];
      if (!block.checkbox.checked) return;
      var result = collectOptimizeStrategy(key);
      if (result.error) {
        if (!firstError) firstError = result.error;
        return;
      }
      total += result.count;
    });

    if (firstError) {
      setStatus(optComboStatus, "err", firstError);
      optSubmitBtn.disabled = true;
      return null;
    }
    if (total === 0) {
      setStatus(optComboStatus, "err", "찾아볼 매수 방식을 하나 이상 선택하세요.");
      optSubmitBtn.disabled = true;
      return null;
    }
    if (total > OPT_MAX_COMBINATIONS) {
      setStatus(optComboStatus, "err", "예상 조합 " + total + "개로 최대 " + OPT_MAX_COMBINATIONS + "개를 넘습니다. 후보값 개수를 줄이세요.");
      optSubmitBtn.disabled = true;
      return null;
    }
    setStatus(optComboStatus, "ok", "예상 조합 " + total + "개를 계산합니다.");
    optSubmitBtn.disabled = false;
    return total;
  }

  function renderOptimizeBlock(key, schema) {
    var wrap = document.createElement("div");
    wrap.className = "strategy-block";

    var head = document.createElement("label");
    head.className = "row-head";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    head.appendChild(checkbox);
    head.appendChild(document.createTextNode(" " + schema.label));
    wrap.appendChild(head);

    var paramsHost = document.createElement("div");
    paramsHost.className = "param-grid";
    wrap.appendChild(paramsHost);

    var fields = {};
    schema.params.forEach(function (param) {
      var fieldWrap = document.createElement("label");
      fieldWrap.textContent = param.label;

      if (param.type === "choice_multi") {
        var optsWrap = document.createElement("div");
        var checkEls = [];
        param.options.forEach(function (opt) {
          var optLabel = document.createElement("label");
          optLabel.className = "row";
          var optCheck = document.createElement("input");
          optCheck.type = "checkbox";
          optCheck.checked = true;
          optCheck.value = opt.value;
          optCheck.addEventListener("change", updateComboCount);
          optLabel.appendChild(optCheck);
          optLabel.appendChild(document.createTextNode(" " + opt.label));
          optsWrap.appendChild(optLabel);
          checkEls.push(optCheck);
        });
        fieldWrap.appendChild(optsWrap);
        fields[param.name] = { type: "choice_multi", checkboxes: checkEls };
      } else {
        var input = document.createElement("input");
        input.type = "text";
        input.value = param.suggested || "";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.addEventListener("input", updateComboCount);
        fieldWrap.appendChild(input);
        fields[param.name] = { type: param.type, input: input };
      }
      paramsHost.appendChild(fieldWrap);
    });

    optimizeListEl.appendChild(wrap);
    optBlocks[key] = { checkbox: checkbox, fields: fields, paramsHost: paramsHost };

    checkbox.addEventListener("change", function () {
      paramsHost.style.display = checkbox.checked ? "" : "none";
      updateComboCount();
    });
  }

  Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
    renderOptimizeBlock(key, STRATEGY_SEARCH_SCHEMAS[key]);
  });
  updateComboCount();

  document.getElementById("opt-start").value = yearsAgoStr(5);
  document.getElementById("opt-end").value = todayStr();

  var optimizeForm = document.getElementById("form-optimize");
  var optStatus = document.getElementById("opt-status");
  var optimizePollTimer = null;
  function setOptimizePollTimer(t) { optimizePollTimer = t; }

  optimizeForm.addEventListener("submit", function (event) {
    event.preventDefault();

    var capital = parseFloat(document.getElementById("opt-capital").value);
    var symbol = document.getElementById("opt-symbol").value.trim().toUpperCase();
    var start = document.getElementById("opt-start").value;
    var end = document.getElementById("opt-end").value;

    if (!symbol || !start || !end || !capital) {
      setStatus(optStatus, "err", "총자본·종목·조회기간을 모두 입력하세요.");
      return;
    }

    var total = updateComboCount();
    if (total === null) {
      setStatus(optStatus, "err", "위 후보값을 먼저 바로잡으세요.");
      return;
    }

    var search = {};
    Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
      var block = optBlocks[key];
      if (!block.checkbox.checked) return;
      var result = collectOptimizeStrategy(key);
      search[key] = result.values;
    });

    var body = {
      symbol: symbol,
      capital: capital,
      start: start,
      end: end,
      search: JSON.stringify(search),
      upload: true,
    };

    if (optimizePollTimer) {
      clearTimeout(optimizePollTimer);
      optimizePollTimer = null;
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }

    setStatus(optStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.checkRun + "?workflow=find-best-strategy.yml")
      .then(function (res) { return res.ok ? res.json() : []; })
      .catch(function () { return []; })
      .then(function (beforeRuns) {
        var previousRunId = beforeRuns && beforeRuns[0] ? beforeRuns[0].id : null;
        return fetch(URLS.findBest, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }).then(function (res) {
          if (!res.ok) throw new Error("응답 코드 " + res.status);
          setStatus(optStatus, "", "요청을 보냈습니다(조합 " + total + "개). 완료되면 자동으로 알려 드립니다...");
          optimizePollTimer = setTimeout(function () {
            pollWorkflowRun("find-best-strategy.yml", optStatus, previousRunId, 1, setOptimizePollTimer, function (latest) {
              if (latest.conclusion === "success") {
                setStatus(optStatus, "ok", "완료됐습니다(" + symbol + "). 비교 결과 탭에 자동으로 반영했습니다.");
                notifyIfPermitted("최적 조건 찾기 완료", symbol + " 계산이 끝났습니다.");
                refreshBtn.click();
              } else {
                setStatus(optStatus, "err", "계산이 실패로 끝났습니다(" + latest.conclusion + "). GitHub Actions 로그를 확인해야 합니다.");
                notifyIfPermitted("최적 조건 찾기 실패", symbol + " 계산이 실패했습니다.");
              }
            });
          }, 5000);
        });
      })
      .catch(function (err) {
        setStatus(optStatus, "err", "요청을 보내지 못했습니다: " + err.message);
      });
  });

  // ── 이전 최적 조건 찾기 불러오기 (2026-09-21에 추가) ────
  // 매번 후보값을 처음부터 다시 채우는 게 번거롭다는 지적을 받았다.
  // find_best_strategy.py가 결과 파일에 "검색조건"(요청 그대로),
  // "자본금", "조회기간", "종목"을 같이 남겨 두므로 새 서버 작업 없이
  // 그 파일만 읽으면 요청값을 그대로 되살릴 수 있다. 화면을 열면 가장
  // 최근 실행값을 기본값으로 자동으로 채우고, 드롭박스에서 다른 과거
  // 실행을 고르면 그 값으로 다시 채울 수 있다.
  var optPrevSelect = document.getElementById("opt-load-prev");
  var optPrevHint = document.getElementById("opt-prev-hint");
  var optPrevResultsCache = {}; // file id -> 이미 받은 결과 내용(다시 안 받는다)

  function fillListInput(input, list) {
    input.value = (list || []).join(", ");
  }
  // 익절선·손절선 후보는 비율(0.1)로 저장돼 있고 입력칸에는 %(10)로
  // 보여준다. 요청을 보낼 때 하는 변환(parsePercentListText)의 반대다.
  function fillPercentListInput(input, list) {
    input.value = (list || []).map(function (v) { return v * 100; }).join(", ");
  }

  function applyOptimizeRequestToForm(data) {
    var symbols = data["종목"] || [];
    if (symbols[0]) document.getElementById("opt-symbol").value = symbols[0];
    if (data["자본금"] !== undefined && data["자본금"] !== null) {
      document.getElementById("opt-capital").value = data["자본금"];
    }
    var period = data["조회기간"] || {};
    if (period["시작"]) document.getElementById("opt-start").value = period["시작"];
    if (period["종료"]) document.getElementById("opt-end").value = period["종료"];

    var search = data["검색조건"] || {};
    Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
      var block = optBlocks[key];
      var schema = STRATEGY_SEARCH_SCHEMAS[key];
      var values = search[key];
      block.checkbox.checked = Boolean(values);
      block.paramsHost.style.display = values ? "" : "none";
      if (!values) return;
      schema.params.forEach(function (param) {
        var field = block.fields[param.name];
        if (!field) return;
        var list = values[param.name];
        if (param.type === "choice_multi") {
          field.checkboxes.forEach(function (c) { c.checked = list ? list.indexOf(c.value) !== -1 : false; });
        } else if (param.type === "percent_optional") {
          fillPercentListInput(field.input, list);
        } else {
          fillListInput(field.input, list);
        }
      });
    });
    updateComboCount();
  }

  // 파일 이름 "최적화_20260921_140501_SPY.json"에서 종목만 뽑아 드롭박스
  // 문구에 쓴다. 내용을 다 받지 않아도 목록을 채울 수 있어서 가볍다.
  function symbolFromOptimizeFilename(name) {
    var m = /^최적화_\d{8}_\d{6}_(.+)\.json$/.exec(name || "");
    return m ? m[1] : name;
  }

  function loadOptimizeResultById(id) {
    if (optPrevResultsCache[id]) return Promise.resolve(optPrevResultsCache[id]);
    return fetch(URLS.getResult + "?id=" + encodeURIComponent(id))
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (data) {
        optPrevResultsCache[id] = data;
        return data;
      });
  }

  fetch(URLS.listResults)
    .then(function (res) { return res.ok ? res.json() : []; })
    .catch(function () { return []; })
    .then(function (files) {
      return (files || []).filter(function (f) { return f.name && f.name.indexOf("최적화_") === 0; });
    })
    .then(function (files) {
      optPrevSelect.innerHTML = "";
      if (files.length === 0) {
        optPrevSelect.innerHTML = "<option value=''>지난 실행 기록이 없습니다(아래 기본값을 그대로 둡니다)</option>";
        return;
      }
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "직접 고르기(지금은 가장 최근 값이 채워져 있습니다)";
      optPrevSelect.appendChild(placeholder);
      files.forEach(function (f) {
        var opt = document.createElement("option");
        opt.value = f.id;
        opt.textContent = symbolFromOptimizeFilename(f.name) + " (" + fmtDateTimeKST(f.modifiedTime) + ")";
        optPrevSelect.appendChild(opt);
      });

      var latest = files[0];
      loadOptimizeResultById(latest.id)
        .then(function (data) {
          applyOptimizeRequestToForm(data);
          optPrevSelect.value = latest.id;
          optPrevHint.textContent =
            "가장 최근 실행값(" + symbolFromOptimizeFilename(latest.name) + ", " +
            fmtDateTimeKST(latest.modifiedTime) + ")을 기본값으로 채웠습니다.";
        })
        .catch(function () {
          // 기본값을 자동으로 못 채워도 화면 전체를 막지 않는다. 처음
          // 미리 채워 둔 시작값이 그대로 남는다.
        });
    });

  optPrevSelect.addEventListener("change", function () {
    var id = optPrevSelect.value;
    if (!id) return;
    optPrevHint.textContent = "불러오는 중입니다...";
    loadOptimizeResultById(id)
      .then(function (data) {
        applyOptimizeRequestToForm(data);
        optPrevHint.textContent = "선택한 실행값을 불러왔습니다.";
      })
      .catch(function (err) {
        optPrevHint.textContent = "불러오지 못했습니다: " + err.message;
      });
  });

  // ── 유사 과거 불러오기 (전략 비교 · 최적 조건 찾기 공용) ──
  // 유사 구간(1순위)을 조회 시작일·종료일에 그대로 채워 넣는다. 전에는
  // DATA 수집 탭에서 '내용 보기'를 먼저 눌러야만 계산돼 있었는데,
  // 그 순서를 강제하는 게 불편하다는 지적을 받았다(2026-09-21). 이미
  // 계산해 둔 값이 있으면 그대로 쓰고, 없으면 이 자리에서 바로 시세를
  // 받아 계산한다(ensureSimilarMatches).
  //
  // 이렇게 채운 기간으로 비교·최적화를 실행하고 결과를 보면, 그 구간이
  // 끝난 뒤 실제로 어떻게 됐는지(과거 기록)를 같이 보여준다. 단 한 번의
  // 사례라 통계라고 부를 수는 없어서, 전체 후보 중 몇 번째로 비슷했는지도
  // 같이 적어 과장하지 않는다.
  var lastSimilarSelection = null;

  function wireSimilarLoader(buttonId, pickerId, selectId, symbolInputId, startInputId, endInputId) {
    var btn = document.getElementById(buttonId);
    var picker = document.getElementById(pickerId);
    var select = document.getElementById(selectId);

    function currentSymbol() {
      return document.getElementById(symbolInputId).value.split(",")[0].trim().toUpperCase();
    }

    function fillOptions(bySymbol) {
      select.innerHTML = "<option value=''>기간을 고르세요</option>";
      SIMILAR_WINDOWS.forEach(function (w) {
        var matches = bySymbol[w.days];
        if (!matches || matches.length === 0) return;
        var best = matches[0];
        var opt = document.createElement("option");
        opt.value = String(w.days);
        opt.textContent = w.label + "(" + w.days + "거래일, " + best.startDate + " ~ " + best.endDate + ")";
        select.appendChild(opt);
      });
    }

    btn.addEventListener("click", function () {
      var symbol = currentSymbol();
      picker.hidden = false;
      if (!symbol) {
        select.innerHTML = "<option value=''>종목을 먼저 입력하세요</option>";
        return;
      }
      if (SIMILAR_MATCHES_BY_SYMBOL[symbol]) {
        fillOptions(SIMILAR_MATCHES_BY_SYMBOL[symbol]);
        return;
      }
      select.innerHTML = "<option value=''>" + symbol + " 값 움직임을 불러와 유사 구간을 찾는 중입니다...</option>";
      ensureSimilarMatches(symbol)
        .then(function (bySymbol) {
          if (currentSymbol() !== symbol) return; // 그사이 종목을 바꿨으면 이 결과는 버린다
          fillOptions(bySymbol);
        })
        .catch(function (err) {
          if (currentSymbol() !== symbol) return;
          select.innerHTML = "<option value=''>" + err.message + "</option>";
        });
    });

    select.addEventListener("change", function () {
      var days = parseInt(select.value, 10);
      if (isNaN(days)) return;
      var symbol = currentSymbol();
      var matches = SIMILAR_MATCHES_BY_SYMBOL[symbol] && SIMILAR_MATCHES_BY_SYMBOL[symbol][days];
      if (!matches || matches.length === 0) return;
      var best = matches[0];
      document.getElementById(startInputId).value = best.startDate;
      document.getElementById(endInputId).value = best.endDate;
      lastSimilarSelection = { symbol: symbol, windowDays: days, match: best };
    });
  }

  wireSimilarLoader("bt-load-similar", "bt-similar-picker", "bt-similar-select", "bt-symbols", "bt-start", "bt-end");
  wireSimilarLoader("opt-load-similar", "opt-similar-picker", "opt-similar-select", "opt-symbol", "opt-start", "opt-end");

  // 결과에 표시된 종목·조회기간이 마지막으로 "유사 과거 불러오기"로
  // 고른 것과 정확히 같을 때만 이후 예측을 붙인다. 사람이 기간을 손으로
  // 바꿔서 계산했으면 그 유사 구간과 더는 상관없는 결과다.
  function matchingSimilarSelection(data) {
    if (!lastSimilarSelection) return null;
    var symbols = data["종목"] || [];
    if (symbols.length !== 1 || symbols[0] !== lastSimilarSelection.symbol) return null;
    var period = data["조회기간"] || {};
    var m = lastSimilarSelection.match;
    if (period["시작"] !== m.startDate || period["종료"] !== m.endDate) return null;
    return lastSimilarSelection;
  }

  function buildSimilarFollowupNote(selection) {
    var m = selection.match;
    var box = document.createElement("div");
    box.className = "similar-box";

    var title = document.createElement("p");
    title.className = "desc";
    var strong = document.createElement("strong");
    strong.textContent = "이후 예측(과거 기록 기준)";
    title.appendChild(strong);
    box.appendChild(title);

    var p1 = document.createElement("p");
    p1.className = "desc";
    p1.textContent =
      "이 조회기간은 '유사 과거 불러오기'로 고른 " + selection.windowDays + "거래일 유사 구간 1순위입니다. " +
      "같은 길이의 과거 후보 " + m.totalCandidates + "개 중 값 움직임 모양이 가장 비슷한 구간이었습니다.";
    box.appendChild(p1);

    var p2 = document.createElement("p");
    p2.className = "desc";
    if (m.followReturnPct !== null) {
      p2.textContent =
        "실제로 이 구간이 끝난 뒤 " + m.followDays + "거래일(" + m.endDate + " ~ " + m.followEndDate + ") 동안 " +
        "수익률은 " + m.followReturnPct.toFixed(1) + "%였습니다. 이 값은 한 번의 사례일 뿐 여러 번 반복해서 " +
        "확인한 통계가 아니므로, 앞으로도 이렇게 된다는 뜻은 아닙니다.";
    } else {
      p2.textContent = "그 뒤 구간 자료가 부족해서 실제로 어떻게 됐는지 확인할 수 없습니다.";
    }
    box.appendChild(p2);

    return box;
  }

  // ── AI 분석용 프롬프트 작성 (최적 조건 찾기) ────────────
  // 여기서 Claude를 직접 부르지는 않는다. 사람이 이 프롬프트를 복사해서
  // Claude(구글 드라이브를 읽을 수 있는 대화)에 붙여넣으면, 그 종목의
  // 과거 시세와 이미 쌓인 백테스트 결과를 보고 어떤 변수 후보값이
  // 그럴듯한지 판단을 돕는다. 판단은 사람이 그 대화에서 받고, 받은
  // 값을 이 화면의 후보값 칸에 직접 옮겨 적는다.
  var optAiPromptBtn = document.getElementById("opt-ai-prompt");
  var optAiPromptBox = document.getElementById("opt-ai-prompt-box");
  var optAiPromptText = document.getElementById("opt-ai-prompt-text");
  var optAiPromptCopyBtn = document.getElementById("opt-ai-prompt-copy");
  var optAiPromptStatus = document.getElementById("opt-ai-prompt-status");

  function buildAiPrompt() {
    var symbol = document.getElementById("opt-symbol").value.trim().toUpperCase();
    var capital = document.getElementById("opt-capital").value;
    var start = document.getElementById("opt-start").value;
    var end = document.getElementById("opt-end").value;

    var lines = [];
    lines.push("구글 드라이브의 Auto_Trading 폴더에서 아래 자료를 찾아 보고 판단해 주세요.");
    lines.push("- 이 종목의 과거 시세: 01_시세원본/" + (symbol || "<종목>") + "/daily.csv");
    lines.push("- 이 종목의 지난 백테스트 결과(엑셀): 02_백테스트결과/" + (symbol || "<종목>") + "/ 폴더");
    lines.push("- 여러 종목을 견준 참고 자료(엑셀): 02_백테스트결과/종목비교결과/ 폴더");
    lines.push("");
    lines.push("조건:");
    lines.push("- 종목: " + (symbol || "(비워짐, 먼저 채워 주세요)"));
    lines.push("- 총자본: " + (capital ? Number(capital).toLocaleString("ko-KR") + "원" : "(비워짐, 먼저 채워 주세요)"));
    lines.push("- 조회기간: " + (start || "(비워짐)") + " ~ " + (end || "(비워짐)"));
    if (
      lastSimilarSelection &&
      lastSimilarSelection.symbol === symbol &&
      lastSimilarSelection.match.startDate === start &&
      lastSimilarSelection.match.endDate === end
    ) {
      lines.push(
        "- 참고: 이 조회기간은 '유사 과거 불러오기'로 고른 " + lastSimilarSelection.windowDays +
        "거래일 유사 구간 1순위입니다."
      );
    }
    lines.push("");
    lines.push("요청:");
    lines.push("위 자료를 참고해서, 아래 네 가지 매수 방식마다 어떤 변수 후보값을 시험해 보면");
    lines.push("좋을지 추천해 주세요. 과거 시세의 어떤 점 때문에 그 값을 골랐는지 근거도 같이");
    lines.push("적어 주세요. 후보를 곱한 전체 조합 수가 200개를 넘지 않게 해 주세요.");
    lines.push("");
    lines.push("1) 일회 매수: 후보값이 없는 방식입니다(그대로 둡니다).");
    lines.push("2) 적립식 매수: amount(회당 매수 금액, 원), interval_days(매수빈도, 일수)");
    lines.push("3) 적립식 매수 + 이동평균선 조건: ma_window(이동평균 기간, 거래일),");
    lines.push("   below_amount/below_interval_days(이동평균선 아래일 때 매수금액·매수빈도),");
    lines.push("   above_amount/above_interval_days(위일 때 매수금액·매수빈도). 아래·위 중");
    lines.push("   한쪽만 후보를 줘도 됩니다.");
    lines.push("4) 등락률 기준 비중 조절 매수(구간 하나로 단순화): interval_days(평가 빈도,");
    lines.push("   거래일), lookback_days(평가 기준일, 몇일전 시세대비), threshold_pct(등락률");
    lines.push("   임계값, %), amount(그 구간 매수 금액, 원)");
    lines.push("");
    lines.push("네 방식 모두 익절선(take_profit_pct)·손절선(stop_loss_pct) 후보도 매수평균가");
    lines.push("대비 비율로 줄 수 있습니다(예: 0.1 = 10%). 비워 두면 그 조건 없이 계산합니다.");
    lines.push("");
    lines.push("답은 이 화면의 '찾아볼 매수 방식과 변수 후보' 칸에 그대로 옮겨 적을 수 있게,");
    lines.push("후보값을 매수 방식별로 쉼표로 구분한 목록 형태로 알려주세요.");

    return lines.join("\n");
  }

  optAiPromptBtn.addEventListener("click", function () {
    optAiPromptText.value = buildAiPrompt();
    optAiPromptBox.hidden = false;
    setStatus(optAiPromptStatus, "", "");
  });

  optAiPromptCopyBtn.addEventListener("click", function () {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(optAiPromptText.value).then(
        function () { setStatus(optAiPromptStatus, "ok", "복사했습니다."); },
        function () { setStatus(optAiPromptStatus, "err", "복사에 실패했습니다. 아래 칸에서 직접 선택해서 복사하세요."); }
      );
      return;
    }
    optAiPromptText.select();
    try {
      document.execCommand("copy");
      setStatus(optAiPromptStatus, "ok", "복사했습니다.");
    } catch (e) {
      setStatus(optAiPromptStatus, "err", "복사에 실패했습니다. 아래 칸에서 직접 선택해서 복사하세요.");
    }
  });

  // ── 6. Walk-forward 검증 ───────────────────────────────
  // "찾아볼 매수 방식과 변수 후보" 입력 칸은 최적 조건 찾기(5번)와
  // 똑같은 모양(STRATEGY_SEARCH_SCHEMAS)을 쓴다. 다만 이 화면의 다른
  // 기능을 건드리지 않으려고 5번의 코드를 고쳐서 같이 쓰지 않고,
  // 여기서 따로 만든다(2026-09-22).
  var wfListEl = document.getElementById("walkforward-strategy-list");
  var wfComboStatus = document.getElementById("wf-combo-status");
  var wfSubmitBtn = document.getElementById("wf-submit");
  var wfBlocks = {}; // key -> { checkbox, fields, paramsHost }

  function collectWalkforwardStrategy(key) {
    var schema = STRATEGY_SEARCH_SCHEMAS[key];
    var block = wfBlocks[key];
    var values = {};
    var count = 1;
    for (var i = 0; i < schema.params.length; i++) {
      var param = schema.params[i];
      var field = block.fields[param.name];
      var list;
      if (param.type === "choice_multi") {
        list = field.checkboxes.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
      } else if (param.type === "int_list") {
        list = parseIntListText(field.input.value);
      } else if (param.type === "percent_optional") {
        list = parsePercentListText(field.input.value);
      } else {
        list = parseFloatListText(field.input.value);
      }

      if (param.optional) {
        if (list.length > 0) {
          values[param.name] = list;
          count *= list.length;
        }
        continue;
      }
      if (list.length === 0) {
        return { error: schema.label + "의 '" + param.label + "'에 후보값을 하나 이상 넣으세요." };
      }
      values[param.name] = list;
      count *= list.length;
    }

    var pairError = checkParamPairs(schema, values, schema.label);
    if (pairError) return { error: pairError };
    if (key === "dca_ma" && values.below_amount === undefined && values.above_amount === undefined) {
      return { error: schema.label + "은 이동평균선 아래·위 중 최소 한쪽은 매수금액과 매수빈도를 채워야 합니다." };
    }

    return { values: values, count: count };
  }

  function updateWalkforwardComboCount() {
    var total = 0;
    var firstError = null;
    Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
      var block = wfBlocks[key];
      if (!block.checkbox.checked) return;
      var result = collectWalkforwardStrategy(key);
      if (result.error) {
        if (!firstError) firstError = result.error;
        return;
      }
      total += result.count;
    });

    if (firstError) {
      setStatus(wfComboStatus, "err", firstError);
      wfSubmitBtn.disabled = true;
      return null;
    }
    if (total === 0) {
      setStatus(wfComboStatus, "err", "찾아볼 매수 방식을 하나 이상 선택하세요.");
      wfSubmitBtn.disabled = true;
      return null;
    }
    if (total > OPT_MAX_COMBINATIONS) {
      setStatus(wfComboStatus, "err", "폴드마다 " + total + "개 조합을 계산합니다(최대 " + OPT_MAX_COMBINATIONS + "개). 후보값 개수를 줄이세요.");
      wfSubmitBtn.disabled = true;
      return null;
    }
    setStatus(wfComboStatus, "ok", "폴드마다 " + total + "개 조합을 계산합니다(폴드 수는 요청 뒤에 정해집니다).");
    wfSubmitBtn.disabled = false;
    return total;
  }

  function renderWalkforwardBlock(key, schema) {
    var wrap = document.createElement("div");
    wrap.className = "strategy-block";

    var head = document.createElement("label");
    head.className = "row-head";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    head.appendChild(checkbox);
    head.appendChild(document.createTextNode(" " + schema.label));
    wrap.appendChild(head);

    var paramsHost = document.createElement("div");
    paramsHost.className = "param-grid";
    wrap.appendChild(paramsHost);

    var fields = {};
    schema.params.forEach(function (param) {
      var fieldWrap = document.createElement("label");
      fieldWrap.textContent = param.label;

      if (param.type === "choice_multi") {
        var optsWrap = document.createElement("div");
        var checkEls = [];
        param.options.forEach(function (opt) {
          var optLabel = document.createElement("label");
          optLabel.className = "row";
          var optCheck = document.createElement("input");
          optCheck.type = "checkbox";
          optCheck.checked = true;
          optCheck.value = opt.value;
          optCheck.addEventListener("change", updateWalkforwardComboCount);
          optLabel.appendChild(optCheck);
          optLabel.appendChild(document.createTextNode(" " + opt.label));
          optsWrap.appendChild(optLabel);
          checkEls.push(optCheck);
        });
        fieldWrap.appendChild(optsWrap);
        fields[param.name] = { type: "choice_multi", checkboxes: checkEls };
      } else {
        var input = document.createElement("input");
        input.type = "text";
        input.value = param.suggested || "";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.addEventListener("input", updateWalkforwardComboCount);
        fieldWrap.appendChild(input);
        fields[param.name] = { type: param.type, input: input };
      }
      paramsHost.appendChild(fieldWrap);
    });

    wfListEl.appendChild(wrap);
    wfBlocks[key] = { checkbox: checkbox, fields: fields, paramsHost: paramsHost };

    checkbox.addEventListener("change", function () {
      paramsHost.style.display = checkbox.checked ? "" : "none";
      updateWalkforwardComboCount();
    });
  }

  Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
    renderWalkforwardBlock(key, STRATEGY_SEARCH_SCHEMAS[key]);
  });
  updateWalkforwardComboCount();

  var walkforwardForm = document.getElementById("form-walkforward");
  var wfStatus = document.getElementById("wf-status");
  var walkforwardPollTimer = null;
  function setWalkforwardPollTimer(t) { walkforwardPollTimer = t; }

  walkforwardForm.addEventListener("submit", function (event) {
    event.preventDefault();

    var capital = parseFloat(document.getElementById("wf-capital").value);
    var symbol = document.getElementById("wf-symbol").value.trim().toUpperCase();
    var start = document.getElementById("wf-start").value;
    var end = document.getElementById("wf-end").value;
    var inSampleYears = parseFloat(document.getElementById("wf-in-sample-years").value);
    var outSampleYears = parseFloat(document.getElementById("wf-out-sample-years").value);
    var stepYears = parseFloat(document.getElementById("wf-step-years").value);

    if (!symbol || !capital) {
      setStatus(wfStatus, "err", "총자본·종목을 입력하세요.");
      return;
    }
    if (!inSampleYears || !outSampleYears || !stepYears) {
      setStatus(wfStatus, "err", "학습기간·검증기간·이동 간격을 모두 입력하세요.");
      return;
    }

    var total = updateWalkforwardComboCount();
    if (total === null) {
      setStatus(wfStatus, "err", "위 후보값을 먼저 바로잡으세요.");
      return;
    }

    var search = {};
    Object.keys(STRATEGY_SEARCH_SCHEMAS).forEach(function (key) {
      var block = wfBlocks[key];
      if (!block.checkbox.checked) return;
      var result = collectWalkforwardStrategy(key);
      search[key] = result.values;
    });

    var body = {
      symbol: symbol,
      capital: capital,
      search: JSON.stringify(search),
      start: start,
      end: end,
      in_sample_years: inSampleYears,
      out_sample_years: outSampleYears,
      step_years: stepYears,
      upload: true,
    };

    if (walkforwardPollTimer) {
      clearTimeout(walkforwardPollTimer);
      walkforwardPollTimer = null;
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }

    setStatus(wfStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.checkRun + "?workflow=run-walkforward.yml")
      .then(function (res) { return res.ok ? res.json() : []; })
      .catch(function () { return []; })
      .then(function (beforeRuns) {
        var previousRunId = beforeRuns && beforeRuns[0] ? beforeRuns[0].id : null;
        return fetch(URLS.runWalkforward, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }).then(function (res) {
          if (!res.ok) throw new Error("응답 코드 " + res.status);
          setStatus(wfStatus, "", "요청을 보냈습니다(폴드마다 조합 " + total + "개). 완료되면 자동으로 알려 드립니다...");
          walkforwardPollTimer = setTimeout(function () {
            pollWorkflowRun("run-walkforward.yml", wfStatus, previousRunId, 1, setWalkforwardPollTimer, function (latest) {
              if (latest.conclusion === "success") {
                setStatus(wfStatus, "ok", "완료됐습니다(" + symbol + "). 비교 결과 탭에 자동으로 반영했습니다.");
                notifyIfPermitted("Walk-forward 검증 완료", symbol + " 검증이 끝났습니다.");
                refreshBtn.click();
              } else {
                setStatus(wfStatus, "err", "계산이 실패로 끝났습니다(" + latest.conclusion + "). GitHub Actions 로그를 확인해야 합니다.");
                notifyIfPermitted("Walk-forward 검증 실패", symbol + " 검증이 실패했습니다.");
              }
            });
          }, 5000);
        });
      })
      .catch(function (err) {
        setStatus(wfStatus, "err", "요청을 보내지 못했습니다: " + err.message);
      });
  });
})();
