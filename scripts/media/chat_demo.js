// A scripted agent run, played through the real chat code (createAssistantMessageContainer and
// handleResearchEvent in app.js), so the companion's powers can be checked and recorded without a
// model or the internet. Load it into a demo Xod page, then: await XodChatDemo.run({ approval: true })
window.XodChatDemo = (() => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function snapshot() {
    const c = document.createElement("canvas");
    c.width = 640; c.height = 360;
    const g = c.getContext("2d");
    const grad = g.createLinearGradient(0, 0, 640, 360);
    grad.addColorStop(0, "#1b2140"); grad.addColorStop(1, "#3a2d5c");
    g.fillStyle = grad; g.fillRect(0, 0, 640, 360);
    for (let i = 0; i < 120; i++) { g.fillStyle = `rgba(255,250,240,${Math.random() * 0.8})`; g.fillRect(Math.random() * 640, Math.random() * 360, 1.5, 1.5); }
    g.fillStyle = "#fffaf0"; g.font = "600 34px Georgia"; g.fillText("Telescope news", 40, 90);
    g.fillStyle = "rgba(255,250,240,0.6)"; g.font = "18px sans-serif";
    ["A new image of a far-off galaxy cluster", "Water found in a young star's disc", "Next observing cycle announced"].forEach((t, i) => g.fillText("• " + t, 44, 150 + i * 40));
    return c.toDataURL("image/png");
  }

  const ANSWER = `Here's what happened with the telescope this week:

- **A deep look at a galaxy cluster.** The new image shows thousands of galaxies, some bent into arcs by the cluster's gravity.
- **Water around a young star.** Astronomers spotted water vapour in the disc where planets are forming.
- **The next observing cycle is set.** Hundreds of programmes were picked, from exoplanet weather to the earliest galaxies.

Want me to turn this into a short report with sources?`;

  async function run({ question = "What's new with the space telescope this week?", approval = false, error = false, speed = 1 } = {}) {
    const w = (ms) => wait(ms / speed);
    document.querySelectorAll(".fixed.inset-0").forEach((e) => { if (e.textContent.includes("Pick Xod")) e.remove(); });
    const welcome = document.getElementById("welcomeHero");
    if (welcome) welcome.style.display = "none";
    appendUserMessage(question);
    isStreaming = true;
    const b = createAssistantMessageContainer();
    const ev = (e) => handleResearchEvent(e, b);
    try {
      await w(700);
      ev({ type: "status", content: "Thinking…" });
      await w(1000);
      ev({ type: "tool_call", name: "web_search", args: { query: "space telescope news this week" }, id: "d1" });
      await w(1900);
      ev({ type: "tool_result", name: "web_search", summary: "8 results", data: {}, id: "d1" });
      await w(600);
      ev({ type: "tool_call", name: "browser_navigate", args: { url: "https://example.org/telescope-news" }, id: "d2" });
      await w(1700);
      ev({ type: "tool_result", name: "browser_navigate", summary: "Read the page", data: { screenshot_uri: snapshot(), url: "https://example.org/telescope-news", title: "Telescope news" }, id: "d2" });
      await w(2400);
      if (approval) {
        ev({ type: "tool_call", name: "mcp__notion__create_page", args: { title: "Telescope notes" }, id: "d3" });
        await w(900);
        ev({ type: "tool_result", name: "mcp__notion__create_page", summary: "Waiting for your OK", id: "d3",
             data: { needs_approval: true, approval_id: `demo-${Date.now()}`, tool: "mcp__notion__create_page", message: "Xod wants to create a Notion page", args: { title: "Telescope notes" } } });
        await w(3200);
      }
      if (error) {
        ev({ type: "error", content: "The page took too long to answer." });
      } else {
        for (let i = 0; i < ANSWER.length;) {
          const n = 2 + Math.floor(Math.random() * 5);
          ev({ type: "content", content: ANSWER.slice(i, i + n) });
          i += n;
          await w(28);
        }
      }
      await w(300);
    } finally {
      isStreaming = false;
      ev({ type: "done" });
    }
    return b;
  }
  return { run };
})();
