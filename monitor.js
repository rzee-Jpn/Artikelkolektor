import fs from "fs";

const targets = JSON.parse(fs.readFileSync("targets.json", "utf8"));

async function check(url) {
  const res = await fetch(url, { redirect: "follow" });
  const headers = res.headers;

  const status = res.status;
  const redirects = res.redirected ? 1 : 0;
  const lastModified = headers.get("last-modified");
  const length = Number(headers.get("content-length") || 0);

  let score = 1.0;
  let signals = [];

  if (status !== 200) {
    score -= 0.5;
    signals.push("http_error");
  }

  if (redirects > 0) {
    score -= 0.1;
    signals.push("redirect");
  }

  if (!lastModified) {
    score -= 0.1;
    signals.push("no_last_modified");
  }

  if (length === 0) {
    score -= 0.2;
    signals.push("unknown_size");
  }

  return {
    url,
    status,
    score: Math.max(score, 0),
    signals
  };
}

(async () => {
  const results = [];
  for (const t of targets) {
    try {
      const r = await check(t.url);
      results.push({ name: t.name, ...r });
    } catch {
      results.push({
        name: t.name,
        url: t.url,
        status: "error",
        score: 0,
        signals: ["unreachable"]
      });
    }
  }

  fs.writeFileSync("health.json", JSON.stringify({
    generated_at: new Date().toISOString(),
    results
  }, null, 2));
})();