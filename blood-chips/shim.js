/* 静态只读模式：把页面里的 /api/* 请求改到 GitHub 上的静态快照；
   需要后端的写操作/查询直接回一条人话错误，页面顶部横幅会显示出来。 */
(function () {
  var DATA_BASE = "https://raw.githubusercontent.com/brauniole-sketch/hermes-monitors/data-bc/";
  var MAP = {
    "/api/data": "bc_data.json",
    "/api/deep": "bc_deep.json",
    "/api/discount": "bc_discount.json",
    "/api/brief": "bc_brief.json",
    "/api/status": "bc_status.json"
  };
  var MSG = "静态只读模式：这个功能（地址查询 / 算带血筹码 / 重新采集 / 观察池增删）需要本机后端，GitHub Pages 上没有服务器。数据每 30 分钟自动更新一次。";
  var origFetch = window.fetch;
  function errResponse(msg) {
    return new Response(JSON.stringify({ error: msg }), {
      status: 503,
      headers: { "content-type": "application/json" }
    });
  }
  window.fetch = function (input, opts) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url;
    try { path = new URL(url, location.href).pathname; } catch (e) {}
    var method = (opts && opts.method) ? String(opts.method).toUpperCase() : "GET";
    if (Object.prototype.hasOwnProperty.call(MAP, path)) {
      if (method !== "GET") { return Promise.resolve(errResponse(MSG)); }
      return origFetch(DATA_BASE + MAP[path], { cache: "no-store" }).then(function (r) {
        if (!r.ok) { throw new Error("静态数据读取失败（HTTP " + r.status + "）"); }
        return r.text().then(function (t) {
          return new Response(t, {
            status: 200,
            headers: { "content-type": "application/json; charset=utf-8" }
          });
        });
      });
    }
    if (path.indexOf("/api/") === 0) { return Promise.resolve(errResponse(MSG)); }
    return origFetch.apply(window, arguments);
  };
})();
