const PLAYLIST_KEY = "output/user_result.m3u";
const REPORT_KEY = "output/report.json";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function authorized(request, env) {
  const supplied = new URL(request.url).searchParams.get("token");
  return Boolean(env.PLAYLIST_TOKEN && supplied && supplied === env.PLAYLIST_TOKEN);
}

function objectHeaders(object, contentType) {
  const headers = new Headers({
    "Content-Type": contentType,
    "Cache-Control": "private, max-age=300",
    "X-Content-Type-Options": "nosniff",
  });
  if (object.httpEtag) headers.set("ETag", object.httpEtag);
  if (object.uploaded) headers.set("Last-Modified", object.uploaded.toUTCString());
  return headers;
}

async function objectResponse(request, env, key, contentType) {
  const object = await env.IPTV_BUCKET.get(key);
  if (!object) return json({ error: "not_ready" }, 404);
  if (request.headers.get("If-None-Match") === object.httpEtag) {
    return new Response(null, { status: 304, headers: objectHeaders(object, contentType) });
  }
  return new Response(request.method === "HEAD" ? null : object.body, {
    headers: objectHeaders(object, contentType),
  });
}

async function health(env) {
  const report = await env.IPTV_BUCKET.get(REPORT_KEY);
  if (!report) return json({ ready: false }, 503);
  let parsed = {};
  try {
    parsed = await report.json();
  } catch (_) {
    return json({ ready: false, report_valid: false }, 503);
  }
  const validation = parsed.validation || {};
  return json({
    ready: parsed.published === true,
    updated_at: parsed.generated_at || null,
    channel_count: validation.channel_count || 0,
    coverage: validation.coverage || 0,
  }, parsed.published === true ? 200 : 503);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ error: "method_not_allowed" }, 405);
    }
    try {
      if (path === "/health") return health(env);
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      if (path === "/m3u") {
        return objectResponse(request, env, PLAYLIST_KEY, "application/vnd.apple.mpegurl; charset=utf-8");
      }
      if (path === "/report") {
        return objectResponse(request, env, REPORT_KEY, "application/json; charset=utf-8");
      }
      return json({ error: "not_found" }, 404);
    } catch (_) {
      return json({ error: "storage_unavailable" }, 503);
    }
  },
};
