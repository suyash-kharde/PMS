(function () {
  if (window.__gwShim) return;
  window.__gwShim = true;
  var P = "__PREFIX__";
  var loc = window.location;
  var slice = Array.prototype.slice;

  // Per-app storage namespace: apps share one origin, so keys like "user" would collide.
  if (__ISOLATE__) {
    var nsStorage = function (real, pre) {
      var keys = function () {
        var out = [];
        for (var i = 0; i < real.length; i++) {
          var k = real.key(i);
          if (k !== null && k.indexOf(pre) === 0) out.push(k.slice(pre.length));
        }
        return out;
      };
      var api = {
        getItem: function (k) { return real.getItem(pre + k); },
        setItem: function (k, v) { real.setItem(pre + k, v); },
        removeItem: function (k) { real.removeItem(pre + k); },
        key: function (i) { var ks = keys(); return i < ks.length ? ks[i] : null; },
        clear: function () { keys().forEach(function (k) { real.removeItem(pre + k); }); }
      };
      return new Proxy(api, {
        get: function (t, p) {
          if (p === "length") return keys().length;
          if (typeof p === "string" && !(p in api)) { var v = real.getItem(pre + p); return v === null ? undefined : v; }
          return api[p];
        },
        set: function (t, p, v) { real.setItem(pre + String(p), String(v)); return true; },
        has: function (t, p) { return p in api || (typeof p === "string" && real.getItem(pre + p) !== null); },
        deleteProperty: function (t, p) { real.removeItem(pre + String(p)); return true; },
        ownKeys: function () { return keys(); },
        getOwnPropertyDescriptor: function (t, p) {
          var v = typeof p === "string" ? real.getItem(pre + p) : null;
          return v === null ? undefined : { value: v, enumerable: true, configurable: true, writable: true };
        }
      });
    };
    ["localStorage", "sessionStorage"].forEach(function (name) {
      try {
        var proxy = nsStorage(window[name], P + "::");
        Object.defineProperty(window, name, { get: function () { return proxy; }, configurable: true });
      } catch (e) {}
    });
  }

  // Add the app prefix to root-relative and same-host absolute URLs.
  function fix(u) {
    if (typeof u !== "string") return u;
    if (u.charAt(0) === "/" && u.charAt(1) !== "/") {
      if (u === P || u.indexOf(P + "/") === 0 || u.indexOf(P + "?") === 0 || u.indexOf(P + "#") === 0) return u;
      return P + u;
    }
    if (/^(?:https?|wss?):\/\//i.test(u)) {
      try {
        var x = new URL(u);
        if (x.host === loc.host && x.pathname !== P && x.pathname.indexOf(P + "/") !== 0) {
          x.pathname = P + x.pathname;
          return x.href;
        }
      } catch (e) {}
    }
    return u;
  }

  // element URL attributes resolve against <base href="/">, where excess "../" clamps to the root
  function fixAttr(u) {
    if (typeof u === "string") {
      var m = /^(?:\.\.\/)+([^.\/].*)$/.exec(u);
      if (m) return P + "/" + m[1];
    }
    return fix(u);
  }

  // fetch
  var of = window.fetch;
  if (of) {
    window.fetch = function (input, init) {
      try {
        if (typeof input === "string") input = fix(input);
        else if (typeof URL !== "undefined" && input instanceof URL) input = fix(input.href);
        else if (typeof Request !== "undefined" && input instanceof Request) {
          var nu = fix(input.url);
          if (nu !== input.url) input = new Request(nu, input);
        }
      } catch (e) {}
      return of.call(this, input, init);
    };
  }

  // XMLHttpRequest
  var xo = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) {
    var a = slice.call(arguments);
    a[1] = fix(String(u));
    return xo.apply(this, a);
  };

  // WebSocket, EventSource, Worker, SharedWorker
  function wrapCtor(name) {
    var O = window[name];
    if (!O) return;
    function W(u) {
      var a = slice.call(arguments);
      a[0] = fix(String(u));
      if (name === "WebSocket" && a[0].charAt(0) === "/") {
        a[0] = (loc.protocol === "https:" ? "wss://" : "ws://") + loc.host + a[0];
      }
      return new (Function.prototype.bind.apply(O, [null].concat(a)))();
    }
    W.prototype = O.prototype;
    for (var k in O) { try { W[k] = O[k]; } catch (e) {} }
    window[name] = W;
  }
  ["WebSocket", "EventSource", "Worker", "SharedWorker"].forEach(wrapCtor);

  // sendBeacon, window.open
  if (navigator.sendBeacon) {
    var sb = navigator.sendBeacon;
    navigator.sendBeacon = function (u, d) { return sb.call(navigator, fix(String(u)), d); };
  }
  var wo = window.open;
  window.open = function (u) {
    var a = slice.call(arguments);
    if (u != null) a[0] = fix(String(u));
    return wo.apply(window, a);
  };

  // element URL properties: img.src = "/x.png"
  [
    ["HTMLImageElement", "src"], ["HTMLScriptElement", "src"], ["HTMLLinkElement", "href"],
    ["HTMLAnchorElement", "href"], ["HTMLIFrameElement", "src"], ["HTMLSourceElement", "src"],
    ["HTMLMediaElement", "src"], ["HTMLFormElement", "action"], ["HTMLEmbedElement", "src"]
  ].forEach(function (p) {
    var C = window[p[0]];
    if (!C) return;
    var d = Object.getOwnPropertyDescriptor(C.prototype, p[1]);
    if (!d || !d.set) return;
    Object.defineProperty(C.prototype, p[1], {
      get: d.get,
      set: function (v) { d.set.call(this, fixAttr(String(v))); },
      configurable: true,
      enumerable: d.enumerable
    });
  });

  // el.setAttribute("src", "/x.png")
  var URLATTR = { src: 1, href: 1, action: 1, poster: 1, formaction: 1, "data-src": 1 };
  var sa = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) {
    if (typeof n === "string" && URLATTR[n.toLowerCase()] === 1 && typeof v === "string") v = fixAttr(v);
    return sa.call(this, n, v);
  };

  // nodes inserted via innerHTML etc. (best effort: the browser may already have started loading)
  var attrs = ["src", "href", "action", "poster", "formaction", "data-src"];
  function fixEl(el) {
    for (var i = 0; i < attrs.length; i++) {
      var v = el.getAttribute(attrs[i]);
      if (v && (v.charAt(0) === "/" || v.indexOf("../") === 0)) {
        var nv = fixAttr(v);
        if (nv !== v) sa.call(el, attrs[i], nv);
      }
    }
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        fixEl(n);
        if (n.querySelectorAll) n.querySelectorAll("[src],[href],[action],[poster],[formaction],[data-src]").forEach(fixEl);
      });
    });
  }).observe(document, { childList: true, subtree: true });
})();
