fetch("health.json")
  .then(r => r.json())
  .then(data => {
    document.getElementById("time").textContent =
      "Last update: " + data.generated_at;

    const root = document.getElementById("cards");

    data.results.forEach(r => {
      const el = document.createElement("div");
      el.className = "card";

      el.innerHTML = `
        <strong>${r.name}</strong><br>
        <small>${r.url}</small><br><br>

        <div class="score ${r.citation_risk}">
          ${(r.health_score * 100).toFixed(0)}%
        </div>

        Volatility: ${r.volatility}<br>
        Citation Risk: <b class="${r.citation_risk}">${r.citation_risk}</b><br>
        Action: ${r.recommended_action}<br>
        Signals: ${r.signals.join(", ") || "none"}
      `;
      root.appendChild(el);
    });
  });

Citation Risk:
<span class="badge ${r.citation_risk}">
  ${r.citation_risk}
</span>