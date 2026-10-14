/* Ticket archive search UI – talks to Meilisearch through Caddy (/meili) and
 * reads full tickets from the file archive (/files/<number>/ticket.json). */
(function () {
  "use strict";

  var CFG = window.ZAMMAD_ARCHIVE || {};
  var FACETS = [
    ["topics", "Thema"], ["group", "Gruppe"], ["state", "Status"],
    ["year", "Jahr"], ["tags", "Tags"], ["customer", "Kunde (Pseudonym)"]
  ];
  var PER_PAGE = 20;
  var PRE = "\u0001", POST = "\u0002";

  var state = { q: "", sort: "", page: 1, filters: {}, ticket: null };
  var $ = function (id) { return document.getElementById(id); };
  var searchSeq = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function hl(s) {
    return esc(s).split(PRE).join("<mark>").split(POST).join("</mark>");
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleDateString("de-DE", { year: "numeric", month: "2-digit", day: "2-digit" });
  }
  function fmtSize(n) {
    if (!n) return "";
    return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  }
  function quote(v) {
    return '"' + String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }
  function fileUrl(id, path) {
    return CFG.filesPath + "/" + encodeURIComponent(id) + "/" +
      String(path).split("/").map(encodeURIComponent).join("/");
  }

  // ---- URL state (shareable links) -------------------------------------------
  function writeHash() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.sort) p.set("sort", state.sort);
    if (state.page > 1) p.set("page", state.page);
    Object.keys(state.filters).forEach(function (k) {
      state.filters[k].forEach(function (v) { p.append("f." + k, v); });
    });
    if (state.ticket) p.set("ticket", state.ticket);
    var h = p.toString();
    history.replaceState(null, "", h ? "#" + h : location.pathname);
  }
  function readHash() {
    var p = new URLSearchParams(location.hash.slice(1));
    state.q = p.get("q") || "";
    state.sort = p.get("sort") || "";
    state.page = parseInt(p.get("page") || "1", 10) || 1;
    state.ticket = p.get("ticket");
    state.filters = {};
    p.forEach(function (v, k) {
      if (k.indexOf("f.") === 0) (state.filters[k.slice(2)] = state.filters[k.slice(2)] || []).push(v);
    });
    $("q").value = state.q;
    $("sort").value = state.sort;
  }

  // ---- search ---------------------------------------------------------------
  function api(path, body) {
    return fetch(CFG.meiliPath + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + CFG.apiKey },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error("HTTP " + r.status + ": " + t.slice(0, 200)); });
      return r.json();
    });
  }

  function buildFilter() {
    return Object.keys(state.filters).filter(function (k) { return state.filters[k].length; })
      .map(function (k) {
        return state.filters[k].map(function (v) {
          return k + " = " + (k === "year" ? Number(v) : quote(v));
        });
      });
  }

  function search() {
    var seq = ++searchSeq;
    writeHash();
    $("status").textContent = "Suche …";
    var body = {
      q: state.q,
      page: state.page,
      hitsPerPage: PER_PAGE,
      facets: FACETS.map(function (f) { return f[0]; }),
      filter: buildFilter(),
      attributesToRetrieve: ["id", "number", "title", "state", "group", "topics", "tags", "customer",
        "created_at", "article_count", "attachment_names"],
      attributesToCrop: ["content:36"],
      attributesToHighlight: ["title", "content", "attachment_names"],
      highlightPreTag: PRE,
      highlightPostTag: POST,
      cropMarker: " … "
    };
    if (state.sort) body.sort = [state.sort];
    api("/indexes/" + encodeURIComponent(CFG.index) + "/search", body).then(function (res) {
      if (seq !== searchSeq) return;
      renderHits(res);
      renderFacets(res.facetDistribution || {});
    }).catch(function (err) {
      if (seq !== searchSeq) return;
      $("status").textContent = "Fehler bei der Suche: " + err.message;
      $("hits").innerHTML = "";
    });
  }

  function renderHits(res) {
    var total = res.totalHits || 0;
    $("status").textContent = total === 0 ? "Keine Tickets gefunden." :
      total + " Ticket" + (total === 1 ? "" : "s") + " (" + res.processingTimeMs + " ms)";
    $("hits").innerHTML = (res.hits || []).map(function (h) {
      var f = h._formatted || h;
      var snippet = f.content ? '<p class="snippet">' + hl(f.content) + "</p>" : "";
      var atts = (f.attachment_names || []).filter(function (n) { return n.indexOf(PRE) >= 0; });
      if (atts.length) snippet += '<p class="snippet">📎 ' + atts.map(hl).join(", ") + "</p>";
      return '<li class="hit' + (h.id === state.ticket ? " active" : "") + '" tabindex="0" data-id="' + esc(h.id) + '">' +
        "<h2>" + hl(f.title || "(ohne Titel)") + "</h2>" +
        '<div class="meta"><span>#' + esc(h.number) + "</span>" +
        (h.state ? '<span class="badge">' + esc(h.state) + "</span>" : "") +
        (h.group ? "<span>" + esc(h.group) + "</span>" : "") +
        "<span>" + fmtDate(h.created_at) + "</span>" +
        "<span>" + esc(h.article_count) + " Artikel</span>" +
        (h.topics || []).map(function (t) { return '<span class="badge">' + esc(t) + "</span>"; }).join("") +
        "</div>" + snippet + "</li>";
    }).join("");
    var pages = Math.max(1, res.totalPages || 1);
    $("page").textContent = "Seite " + state.page + " / " + pages;
    $("prev").disabled = state.page <= 1;
    $("next").disabled = state.page >= pages;
  }

  function renderFacets(dist) {
    var active = [];
    Object.keys(state.filters).forEach(function (k) {
      state.filters[k].forEach(function (v) {
        active.push('<button type="button" class="chip" data-facet="' + esc(k) + '" data-value="' + esc(v) +
          '">' + esc(v) + " ✕</button>");
      });
    });
    $("active-filters").innerHTML = active.join("");
    $("facet-list").innerHTML = FACETS.map(function (f) {
      var key = f[0], values = dist[key] || {};
      var selected = state.filters[key] || [];
      var names = Object.keys(values);
      selected.forEach(function (v) { if (names.indexOf(v) < 0) names.push(v); });
      if (!names.length) return "";
      names.sort(function (a, b) {
        return key === "year" ? b - a : (values[b] || 0) - (values[a] || 0) || a.localeCompare(b);
      });
      return '<div class="facet"><h3>' + esc(f[1]) + "</h3>" + names.slice(0, 15).map(function (v) {
        return '<label><input type="checkbox" data-facet="' + esc(key) + '" data-value="' + esc(v) + '"' +
          (selected.indexOf(v) >= 0 ? " checked" : "") + '><span class="name">' + esc(v) +
          '</span><span class="count">' + (values[v] || 0) + "</span></label>";
      }).join("") + "</div>";
    }).join("");
  }

  function toggleFilter(key, value, on) {
    var list = state.filters[key] = state.filters[key] || [];
    var i = list.indexOf(value);
    if (on && i < 0) list.push(value);
    if (!on && i >= 0) list.splice(i, 1);
    if (!list.length) delete state.filters[key];
    state.page = 1;
    search();
  }

  // ---- detail view ----------------------------------------------------------
  function openTicket(id) {
    state.ticket = id;
    writeHash();
    Array.prototype.forEach.call(document.querySelectorAll(".hit"), function (el) {
      el.classList.toggle("active", el.getAttribute("data-id") === id);
    });
    $("detail").hidden = false;
    document.querySelector(".layout").classList.add("with-detail");
    $("detail-body").innerHTML = "<p>Lade …</p>";
    fetch(fileUrl(id, "ticket.json")).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      if (state.ticket === id) renderTicket(id, data);
    }).catch(function (err) {
      $("detail-body").innerHTML = "<p>Ticket konnte nicht geladen werden: " + esc(err.message) + "</p>";
    });
  }

  function closeTicket() {
    state.ticket = null;
    writeHash();
    $("detail").hidden = true;
    document.querySelector(".layout").classList.remove("with-detail");
    Array.prototype.forEach.call(document.querySelectorAll(".hit.active"), function (el) {
      el.classList.remove("active");
    });
  }

  function renderTicket(id, data) {
    var t = data.ticket || {}, arch = data.archive || {};
    var rows = [
      ["Nummer", esc(t.number)], ["Status", esc(t.state)], ["Priorität", esc(t.priority)],
      ["Gruppe", esc(t.group)], ["Bearbeiter", esc(t.owner)],
      ["Kunde", t.customer ? '<button type="button" class="linkbtn" data-facet="customer" data-value="' +
        esc(t.customer) + '">' + esc(t.customer) + "</button>" : ""],
      ["Organisation", esc(t.organization)],
      ["Erstellt", fmtDate(t.created_at)], ["Geschlossen", fmtDate(t.close_at)],
      ["Tags", esc((t.tags || []).join(", "))], ["Themen", esc((arch.matched_queries || []).join(", "))],
      ["Archiviert", fmtDate(arch.exported_at)]
    ].filter(function (r) { return r[1]; });
    var html = "<h2>" + esc(t.title) + "</h2><dl>" + rows.map(function (r) {
      return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>";
    }).join("") + "</dl>";
    if (arch.zammad_url) {
      html += '<p class="meta"><a href="' + esc(arch.zammad_url) + '" target="_blank" rel="noopener noreferrer">' +
        "In Zammad öffnen</a> (nur solange es dort noch existiert)</p>";
    }
    html += (data.articles || []).map(function (a) {
      var atts = (a.attachments || []).filter(function (x) { return x.path; });
      return '<section class="article' + (a.internal ? " internal" : "") + '"><header>' +
        "<span>" + fmtDate(a.created_at) + "</span><span>" + esc(a.sender) + " · " + esc(a.type) + "</span>" +
        (a.internal ? "<span>intern</span>" : "") +
        (a.from ? "<span>von " + esc(a.from) + "</span>" : "") +
        (a.to ? "<span>an " + esc(a.to) + "</span>" : "") + "</header>" +
        (a.subject ? '<div class="subject">' + esc(a.subject) + "</div>" : "") +
        "<pre>" + esc(a.body) + "</pre>" +
        (atts.length ? '<ul class="attachments">' + atts.map(function (x) {
          return '<li><a href="' + esc(fileUrl(id, x.path)) + '" download>' + esc(x.filename) + "</a> " +
            '<span class="meta">' + fmtSize(x.size) + "</span></li>";
        }).join("") + "</ul>" : "") + "</section>";
    }).join("");
    $("detail-body").innerHTML = html;
    $("detail").scrollTop = 0;
  }

  // ---- events ---------------------------------------------------------------
  var debounce;
  $("q").addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(function () { state.q = $("q").value.trim(); state.page = 1; search(); }, 200);
  });
  $("search-form").addEventListener("submit", function (e) {
    e.preventDefault();
    state.q = $("q").value.trim(); state.page = 1; search();
  });
  $("sort").addEventListener("change", function () { state.sort = $("sort").value; state.page = 1; search(); });
  $("prev").addEventListener("click", function () { state.page--; search(); window.scrollTo(0, 0); });
  $("next").addEventListener("click", function () { state.page++; search(); window.scrollTo(0, 0); });
  $("facet-list").addEventListener("change", function (e) {
    var el = e.target;
    if (el.getAttribute("data-facet")) toggleFilter(el.getAttribute("data-facet"), el.getAttribute("data-value"), el.checked);
  });
  document.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-facet]");
    if (chip && chip.tagName === "BUTTON") {
      var key = chip.getAttribute("data-facet"), val = chip.getAttribute("data-value");
      var on = !(state.filters[key] || []).includes(val);
      toggleFilter(key, val, on);
      return;
    }
    var hit = e.target.closest(".hit");
    if (hit) openTicket(hit.getAttribute("data-id"));
  });
  $("hits").addEventListener("keydown", function (e) {
    var hit = e.target.closest(".hit");
    if (hit && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openTicket(hit.getAttribute("data-id")); }
  });
  $("close-detail").addEventListener("click", closeTicket);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && state.ticket) closeTicket(); });

  if (!CFG.apiKey) {
    $("status").textContent = "config.js fehlt oder ist unvollständig – `zammad-archive meili-setup` ausführen.";
    return;
  }
  readHash();
  search();
  if (state.ticket) openTicket(state.ticket);
})();
