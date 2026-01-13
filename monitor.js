import fs from "fs";
import crypto from "crypto";

const targets = JSON.parse(fs.readFileSync("targets.json", "utf8"));
const prev = fs.existsSync("health.json")
  ? JSON.parse(fs.readFileSync("health.json", "utf8"))
  : { results: [] };

const prevMap = Object.fromEntries(
  prev.results.map(r => [r.url, r])
);

function hash(v) {
  return crypto.createHash("sha256").update(String(v)).digest("hex");
}

async function inspect(target) {
  const start = Date.now();
  const res = await fetch(target.url, { redirect: "follow" });
  const time = Date.now() - start;

  const len = Number(res.headers.get("content-length") || 0);
  const lm = res.headers.get("last-modified");
  const etag = res.headers.get("etag");
  const title = res.headers.get("content-type") || "";

  const prevData = prevMap[target.url] || {};

  let signals = [];
  let score = 1.0;
  let changed = false;

  if (res.status !== 200) {
    score -= 0.5;
    signals.push("http_error");
  }

  if (prevData.content_length && Math.abs(len - prevData.content_length) / prevData.content_length > 0.3) {
    score -= 0.2;
    signals.push("content_shift");
    changed = true;
  }

  if (etag && prevData.etag && etag !== prevData.etag) {
    signals.push("etag_changed");
    changed = true;
  }

  if (!lm) {
    score -= 0.1;
    signals.push("no_last_modified");
  }

  const changeCount = (prevData.change_count_12m || 0) + (changed ? 1 : 0);
  const volatility =
    changeCount > 6 ? "volatile" :
    changeCount > 2 ? "moderate" : "stable";

  const citationRisk =
    score < 0.4 ? "high" :
    score < 0.7 ? "medium" : "low";

  return {
    name: target.name,
    url: target.url,
    observed_at: new Date().toISOString(),
    http_status: res.status,
    response_time_ms: time,
    content_length: len,
    etag,
    title_hash: hash(title),
    health_score: Number(score.toFixed(2)),
    volatility,
    change_count_12m: changeCount,
    last_change_detected: changed
      ? new Date().toISOString().split("T")[0]
      : prevData.last_change_detected || null,
    citation_risk: citationRisk,
    recommended_action:
      citationRisk === "low"
        ? "safe_to_cite"
        : citationRisk === "medium"
        ? "archive_before_citing"
        : "avoid_as_primary_source",
    signals
  };
}

(async () => {
  const results = [];
  for (const t of targets) {
    try {
      results.push(await inspect(t));
    } catch {
      results.push({
        name: t.name,
        url: t.url,
        observed_at: new Date().toISOString(),
        health_score: 0,
        volatility: "unknown",
        citation_risk: "high",
        recommended_action: "avoid",
        signals: ["unreachable"]
      });
    }
  }

  fs.writeFileSync(
    "health.json",
    JSON.stringify({ generated_at: new Date().toISOString(), results }, null, 2)
  );
})();