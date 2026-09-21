(function () {
  "use strict";

  var N8N_BASE = "https://sondullab.app.n8n.cloud/webhook";
  var URLS = {
    updatePrices: N8N_BASE + "/auto-trading-update-prices",
    listPrices: N8N_BASE + "/auto-trading-list-prices",
    getPrice: N8N_BASE + "/auto-trading-get-price",
    runBacktest: N8N_BASE + "/auto-trading-run-backtest",
    listResults: N8N_BASE + "/auto-trading-list-results",
    getResult: N8N_BASE + "/auto-trading-get-result",
  };

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
        { name: "interval_days", label: "매수 간격(거래일, 1이면 매일)", type: "int", suggested: 1 },
      ],
    },
    dca_ma: {
      label: "적립식 매수 + 이동평균선 조건",
      description: "정해진 날이 와도 이동평균선 조건을 만족해야 산다.",
      params: [
        { name: "amount", label: "회당 매수 금액(원)", type: "int", suggested: 100000 },
        { name: "interval_days", label: "매수 간격(거래일, 1이면 매일)", type: "int", suggested: 1 },
        { name: "ma_window", label: "이동평균 기간(거래일)", type: "int", suggested: 60 },
        {
          name: "buy_when",
          label: "조건",
          type: "choice",
          options: [
            { value: "below", label: "이동평균선 아래일 때만" },
            { value: "above", label: "이동평균선 위일 때만" },
          ],
          suggested: "below",
        },
      ],
    },
    drop_based: {
      label: "등락률 기준 비중 조절 매수",
      description: "최근 평균 주가 대비 등락률 구간마다 매수 금액을 다르게 정한다. 하락 구간뿐 아니라 상승 구간도 넣을 수 있다.",
      params: [
        { name: "interval_days", label: "판단 간격(거래일, 1이면 매일)", type: "int", suggested: 1 },
        { name: "lookback_days", label: "등락률 기준 기간(거래일, 1이면 전일 대비)", type: "int", suggested: 1 },
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

  // ── 공통 유틸 ──────────────────────────────────────────
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

  // ── 1. 시세 수집 ───────────────────────────────────────
  var pricesForm = document.getElementById("form-prices");
  var pricesStatus = document.getElementById("prices-status");

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

    setStatus(pricesStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.updatePrices, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: symbols, full_refresh: fullRefresh }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        setStatus(pricesStatus, "ok", "요청을 보냈습니다. GitHub Actions에서 진행됩니다.");
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

  refreshPricesListBtn.addEventListener("click", function () {
    setStatus(pricesListStatus, "", "목록을 불러오는 중입니다...");
    priceDetailEl.innerHTML = "";
    fetch(URLS.listPrices)
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (rows) {
        pricesTableBody.innerHTML = "";
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

  function parseDailyCsv(text) {
    var lines = text.split(/\r?\n/).filter(function (line) { return line.trim().length > 0; });
    if (lines.length <= 1) return { rows: 0, startDate: null, endDate: null, lastClose: null };
    var header = lines[0].split(",");
    var dateIdx = header.indexOf("trade_date");
    var closeIdx = header.indexOf("close");
    var first = lines[1].split(",");
    var last = lines[lines.length - 1].split(",");
    return {
      rows: lines.length - 1,
      startDate: dateIdx >= 0 ? first[dateIdx] : null,
      endDate: dateIdx >= 0 ? last[dateIdx] : null,
      lastClose: closeIdx >= 0 ? last[closeIdx] : null,
    };
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
        var summary = parseDailyCsv(text);
        var p = document.createElement("p");
        p.className = "desc";
        if (summary.rows === 0) {
          p.textContent = symbol + ": 파일은 있지만 거래일 자료가 없습니다.";
        } else {
          p.textContent =
            symbol + ": 거래일 " + summary.rows + "개, " +
            (summary.startDate || "?") + " ~ " + (summary.endDate || "?") +
            (summary.lastClose ? ", 마지막 종가 " + summary.lastClose : "");
        }
        priceDetailEl.innerHTML = "";
        priceDetailEl.appendChild(p);
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
    tpLabel.textContent = "목표 수익률(익절, %) — 비워 두면 안 씀";
    var tpInput = document.createElement("input");
    tpInput.type = "number";
    tpInput.step = "0.1";
    tpLabel.appendChild(tpInput);
    el.appendChild(tpLabel);

    var slLabel = document.createElement("label");
    slLabel.textContent = "손실 한도(손절, %) — 비워 두면 안 씀";
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
        } else if (cfg[p.name] === undefined || cfg[p.name] === "") {
          setStatus(btStatus, "err", (i + 1) + "번째 전략(" + schema.label + ")의 '" + p.label + "'을(를) 입력하세요.");
          return;
        }
      }
    }

    setStatus(btStatus, "", "요청을 보내는 중입니다...");
    fetch(URLS.runBacktest, {
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
    })
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        setStatus(btStatus, "ok", "요청을 보냈습니다. 아래 3번에서 잠시 뒤 새로고침해 확인하세요.");
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
    setStatus(resultsStatus, "", "결과를 불러오는 중입니다...");
    fetch(URLS.getResult + "?id=" + encodeURIComponent(id))
      .then(function (res) {
        if (!res.ok) throw new Error("응답 코드 " + res.status);
        return res.json();
      })
      .then(function (data) {
        setStatus(resultsStatus, "ok", "생성 시각(KST): " + (data["생성시각_KST"] || "알 수 없음"));
        renderResult(data);
      })
      .catch(function (err) {
        setStatus(resultsStatus, "err", "결과를 불러오지 못했습니다: " + err.message);
      });
  }

  function renderResult(data) {
    var summary = data["요약"] || [];
    var series = data["시계열"] || {};

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    thead.innerHTML =
      "<tr><th>종목</th><th>전략</th><th>총투자금</th><th>실현손익</th><th>평가손익</th><th>합계</th><th>수익률</th></tr>";
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    summary.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + row.symbol + "</td>" +
        "<td>" + row.strategy_name + "</td>" +
        "<td>" + fmtNumber(row["총투자금"]) + "</td>" +
        "<td>" + fmtNumber(row["실현손익"]) + "</td>" +
        "<td>" + fmtNumber(row["평가손익"]) + "</td>" +
        "<td>" + fmtNumber(row["합계"]) + "</td>" +
        "<td>" + row["수익률"] + "%</td>";
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    resultView.appendChild(table);

    var chart = buildChart(series);
    if (chart) resultView.appendChild(chart);
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
    wrap.appendChild(legend);

    return wrap;
  }
})();
