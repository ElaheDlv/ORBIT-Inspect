const CSV_PATH = "../automated_experiments/plots_grid_1056_analysis_cleaned/summary_current_grid.csv";

const fallbackRows = [];
const SPEEDS = [0.5, 1.0, 1.5, 2.0];
const RESOLUTIONS = ["320x180", "640x360", "1280x720"];
const PRBS = [10, 15, 25, 35, 45, 55, 65, 75, 85, 90];
const CPUS = [1, 2, 3, 4, 5, 6, 7, 8];
const CONVEYOR_PART_LEFT_PX = 54;
const CONVEYOR_END_X_PX = 780;
const SORT_DISTANCE_M = 1.0;

const state = {
  rows: [],
  selected: { speed: 1.0, resolution: "640x360", prb: 35, cpu: 4 },
  live: {
    speed: 1.0,
    resolution: "640x360",
    prb: 35,
    cpu: 4,
  },
  replayTimer: null,
  livePollTimer: null,
  liveBackend: false,
};

const $ = (id) => document.getElementById(id);

function parseCsv(text) {
  const rows = [];
  let field = "";
  let row = [];
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (quoted && ch === '"' && next === '"') {
      field += '"';
      i += 1;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (!quoted && ch === ",") {
      row.push(field);
      field = "";
    } else if (!quoted && (ch === "\n" || ch === "\r")) {
      if (ch === "\r" && next === "\n") i += 1;
      row.push(field);
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  const [header, ...data] = rows;
  return data.map((values) => Object.fromEntries(header.map((key, index) => [key, values[index] ?? ""])));
}

function numeric(row, key, fallback = 0) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : fallback;
}

function shortPath(path) {
  if (!path) return "--";
  const parts = String(path).split("/");
  const releaseIndex = parts.lastIndexOf("ORBIT-Inspect");
  if (releaseIndex >= 0) return parts.slice(releaseIndex).join("/");
  const testbedIndex = parts.lastIndexOf("orbit_inspect_testbed");
  if (testbedIndex >= 0) return parts.slice(testbedIndex).join("/");
  return parts.slice(-5).join("/");
}

function normalizeRow(row) {
  return {
    runId: row.run_id || "synthetic",
    speed: numeric(row, "run_speed"),
    resolution: row.resolution,
    prb: numeric(row, "slice_ul_pct"),
    cpu: numeric(row, "edge_cpus"),
    correctOnTime: numeric(row, "correct_on_time_rate"),
    correctRate: numeric(row, "correct_rate"),
    lateRate: numeric(row, "late_rate"),
    meanRoundtrip: numeric(row, "mean_roundtrip_ms"),
    p95Roundtrip: numeric(row, "p95_roundtrip_ms"),
    meanInference: numeric(row, "mean_inference_ms"),
    p95Inference: numeric(row, "p95_inference_ms"),
    resourceCost: numeric(row, "resource_cost"),
    goodput: numeric(row, "production_goodput"),
    feasible: String(row.feasible).toLowerCase() === "true" || numeric(row, "correct_on_time_rate") >= 0.6,
  };
}

function buildFallbackRows() {
  if (fallbackRows.length) return fallbackRows;
  for (const speed of SPEEDS) {
    for (const resolution of RESOLUTIONS) {
      const pixels = resolution === "320x180" ? 0.7 : resolution === "640x360" ? 1.0 : 1.85;
      for (const prb of PRBS) {
        for (const cpu of CPUS) {
          const upload = (540 * pixels * 35) / prb;
          const inference = (230 * pixels) / Math.sqrt(cpu);
          const roundtrip = upload + inference + 38;
          const budget = 960 / speed;
          const cot = Math.max(0, Math.min(0.96, 0.72 - Math.max(0, roundtrip - budget) / 650 + cpu * 0.012 + prb * 0.001));
          fallbackRows.push({
            runId: "fallback",
            speed,
            resolution,
            prb,
            cpu,
            correctOnTime: cot,
            correctRate: Math.min(0.96, cot + 0.16),
            lateRate: Math.max(0, 1 - cot - 0.18),
            meanRoundtrip: roundtrip * 0.9,
            p95Roundtrip: roundtrip,
            meanInference: inference,
            p95Inference: inference * 1.22,
            resourceCost: prb * cpu,
            goodput: cot * speed * 60,
            feasible: cot >= 0.6,
          });
        }
      }
    }
  }
  return fallbackRows;
}

function nearest(values, target) {
  return values.reduce((best, value) => Math.abs(value - target) < Math.abs(best - target) ? value : best, values[0]);
}

function currentRow() {
  const exact = state.rows.find((row) =>
    row.speed === state.selected.speed &&
    row.resolution === state.selected.resolution &&
    row.prb === state.selected.prb &&
    row.cpu === state.selected.cpu
  );
  if (exact) return exact;

  const speeds = unique("speed");
  const prbs = unique("prb");
  const cpus = unique("cpu");
  state.selected.speed = nearest(speeds, state.selected.speed);
  state.selected.prb = nearest(prbs, state.selected.prb);
  state.selected.cpu = nearest(cpus, state.selected.cpu);
  return currentRow();
}

function unique(key) {
  return [...new Set(state.rows.map((row) => row[key]))].sort((a, b) => {
    if (typeof a === "string") return RESOLUTIONS.indexOf(a) - RESOLUTIONS.indexOf(b);
    return a - b;
  });
}

function recommend() {
  const candidates = state.rows.filter((row) => row.speed === state.selected.speed);
  const feasible = candidates.filter((row) => row.correctOnTime >= 0.6);
  const pool = feasible.length ? feasible : candidates;
  return [...pool].sort((a, b) =>
    (b.goodput - a.goodput) ||
    (b.correctOnTime - a.correctOnTime) ||
    (a.resourceCost - b.resourceCost) ||
    (a.p95Roundtrip - b.p95Roundtrip)
  )[0];
}

function setSelection(row) {
  state.selected = {
    speed: row.speed,
    resolution: row.resolution,
    prb: row.prb,
    cpu: row.cpu,
  };
  syncControls();
  syncLiveInputsFromMeasuredKnobs();
  render();
}

function syncControls() {
  const speeds = unique("speed");
  const prbs = unique("prb");
  const cpus = unique("cpu");
  $("speed").value = speeds.indexOf(state.selected.speed);
  $("prb").value = prbs.indexOf(state.selected.prb);
  $("cpu").value = cpus.indexOf(state.selected.cpu);
  $("speedOut").textContent = `${state.selected.speed.toFixed(1)} m/s`;
  $("prbOut").textContent = `${state.selected.prb.toFixed(0)}%`;
  $("cpuOut").textContent = `${state.selected.cpu.toFixed(0)} cores`;
  $("ranState").textContent = `UL ${state.selected.prb.toFixed(0)}%`;
  $("edgeState").textContent = `${state.selected.cpu.toFixed(0)} cores`;
  document.querySelectorAll("#resolutionGroup button").forEach((button) => {
    button.setAttribute("aria-checked", String(button.dataset.value === state.selected.resolution));
  });
}

function syncLiveInputsFromMeasuredKnobs() {
  state.live.speed = state.selected.speed;
  state.live.resolution = state.selected.resolution;
  state.live.prb = state.selected.prb;
  state.live.cpu = state.selected.cpu;
  $("liveSpeed").value = state.live.speed;
  $("liveResolution").value = state.live.resolution;
  $("livePrb").value = state.live.prb;
  $("liveCpu").value = state.live.cpu;
}

function readLiveInputs() {
  state.live = {
    speed: Number($("liveSpeed").value),
    resolution: $("liveResolution").value.trim().toLowerCase(),
    prb: Number($("livePrb").value),
    cpu: Number($("liveCpu").value),
  };
  return state.live;
}

function formatPct(value) {
  return `${Math.round(value * 100)}%`;
}

function formatMs(value) {
  return `${Math.round(value)} ms`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function deadlineBudgetMs(speed) {
  return (SORT_DISTANCE_M / Math.max(Number(speed) || 1, 0.1)) * 1000;
}

function deadlinePositionPx(speed) {
  return Math.max(210, 680 - speed * 120);
}

function render() {
  const row = currentRow();
  const rec = recommend();
  syncControls();

  $("statusTitle").textContent = `${row.resolution}, ${row.prb.toFixed(0)}% UL, ${row.cpu.toFixed(0)} CPU`;
  $("cotRate").textContent = formatPct(row.correctOnTime);
  $("goodput").textContent = row.goodput.toFixed(1);
  $("p95Latency").textContent = formatMs(row.p95Roundtrip);
  $("resourceCost").textContent = row.resourceCost.toFixed(0);
  $("uploadLatency").textContent = `mean UL ${formatMs(Math.max(0, row.meanRoundtrip - row.meanInference - 35))}`;
  $("inferenceLatency").textContent = `mean edge ${formatMs(row.meanInference)}`;

  const deadlinePx = deadlinePositionPx(row.speed);
  const budgetMs = deadlineBudgetMs(row.speed);
  const deadlineMet = row.p95Roundtrip <= budgetMs;

  const badge = $("statusBadge");
  badge.className = "badge";
  if (!deadlineMet) {
    badge.textContent = "p95 late";
    badge.classList.add("badge--fail");
  } else if (row.correctOnTime >= 0.7) {
    badge.textContent = "stable";
  } else if (row.correctOnTime >= 0.45) {
    badge.textContent = "marginal";
    badge.classList.add("badge--warn");
  } else {
    badge.textContent = "low COT";
    badge.classList.add("badge--fail");
  }

  const upload = Math.max(20, row.meanRoundtrip - row.meanInference - 35);
  const edge = Math.max(20, row.meanInference);
  const feedback = 35;
  const total = upload + edge + feedback;
  $("uploadBar").style.width = `${(upload / total) * 100}%`;
  $("edgeBar").style.width = `${(edge / total) * 100}%`;
  $("feedbackBar").style.width = `${(feedback / total) * 100}%`;

  const latencyRatio = row.p95Roundtrip / budgetMs;
  const partX = clamp(
    (deadlinePx - CONVEYOR_PART_LEFT_PX) * latencyRatio,
    0,
    CONVEYOR_END_X_PX - CONVEYOR_PART_LEFT_PX
  );
  $("deadlineMarker").style.left = `min(82%, ${deadlinePx}px)`;
  $("part").style.transform = `translateX(${partX}px)`;
  $("deadlineStatus").textContent = `p95 ${formatMs(row.p95Roundtrip)} / deadline ${formatMs(budgetMs)}`;
  $("deadlineStatus").className = `deadline-status${deadlineMet ? "" : " deadline-status--late"}`;

  $("decisionText").textContent = deadlineMet
    ? "The selected measured point keeps p95 latency before the physical sorting deadline."
    : "This point is useful as a failure case: p95 latency exceeds the physical sorting deadline.";

  $("recResolution").textContent = rec.resolution;
  $("recPrb").textContent = `${rec.prb.toFixed(0)}%`;
  $("recCpu").textContent = `${rec.cpu.toFixed(0)} cores`;
  $("recOutcome").textContent = `${formatPct(rec.correctOnTime)} COT, ${rec.goodput.toFixed(1)} parts/min`;

  drawHeatmap(row);
  drawTimeline();
}

function drawHeatmap(selected) {
  const canvas = $("heatmap");
  const ctx = canvas.getContext("2d");
  const metricMap = {
    correct_on_time_rate: "correctOnTime",
    production_goodput: "goodput",
    p95_roundtrip_ms: "p95Roundtrip",
    resource_cost: "resourceCost",
  };
  const metric = metricMap[$("heatMetric").value];
  const rows = state.rows.filter((row) => row.speed === state.selected.speed && row.resolution === state.selected.resolution);
  const prbs = unique("prb");
  const cpus = unique("cpu");
  const values = rows.map((row) => row[metric]).filter(Number.isFinite);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "13px sans-serif";
  ctx.fillStyle = "#617069";
  ctx.fillText(`Speed ${state.selected.speed.toFixed(1)} m/s, ${state.selected.resolution}`, 24, 28);

  const left = 58;
  const top = 52;
  const cellW = 58;
  const cellH = 34;
  for (let y = 0; y < cpus.length; y += 1) {
    ctx.fillStyle = "#617069";
    ctx.fillText(`${cpus[y]} CPU`, 10, top + y * cellH + 22);
    for (let x = 0; x < prbs.length; x += 1) {
      const row = rows.find((item) => item.prb === prbs[x] && item.cpu === cpus[y]);
      const value = row ? row[metric] : NaN;
      const t = Number.isFinite(value) && max > min ? (value - min) / (max - min) : 0;
      const color = metric === "p95Roundtrip" || metric === "resourceCost"
        ? ramp(1 - t)
        : ramp(t);
      ctx.fillStyle = color;
      ctx.fillRect(left + x * cellW, top + y * cellH, cellW - 4, cellH - 4);
      if (row && row.prb === selected.prb && row.cpu === selected.cpu) {
        ctx.strokeStyle = "#17201c";
        ctx.lineWidth = 3;
        ctx.strokeRect(left + x * cellW + 1, top + y * cellH + 1, cellW - 6, cellH - 6);
      }
    }
  }
  ctx.fillStyle = "#617069";
  prbs.forEach((prb, index) => ctx.fillText(`${prb}%`, left + index * cellW + 8, top + cpus.length * cellH + 18));
}

function ramp(t) {
  const clamped = Math.max(0, Math.min(1, t));
  const low = [179, 58, 58];
  const mid = [220, 165, 64];
  const high = [31, 122, 77];
  const from = clamped < 0.5 ? low : mid;
  const to = clamped < 0.5 ? mid : high;
  const local = clamped < 0.5 ? clamped * 2 : (clamped - 0.5) * 2;
  const rgb = from.map((value, index) => Math.round(value + (to[index] - value) * local));
  return `rgb(${rgb.join(",")})`;
}

function drawTimeline() {
  const canvas = $("timeline");
  const ctx = canvas.getContext("2d");
  const speeds = [0.5, 1.0, 1.5, 2.0, 1.0];
  const points = speeds.map((speed) => {
    const pool = state.rows.filter((row) => row.speed === speed);
    return [...pool].sort((a, b) => b.goodput - a.goodput)[0];
  });
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#d7ddd6";
  ctx.lineWidth = 1;
  for (let y = 40; y <= 240; y += 50) {
    ctx.beginPath();
    ctx.moveTo(48, y);
    ctx.lineTo(690, y);
    ctx.stroke();
  }
  const maxGoodput = Math.max(...points.map((row) => row.goodput), 1);
  ctx.beginPath();
  points.forEach((row, index) => {
    const x = 70 + index * 145;
    const y = 250 - (row.goodput / maxGoodput) * 180;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#286ea8";
  ctx.lineWidth = 3;
  ctx.stroke();
  points.forEach((row, index) => {
    const x = 70 + index * 145;
    const y = 250 - (row.goodput / maxGoodput) * 180;
    ctx.fillStyle = "#286ea8";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#17201c";
    ctx.font = "12px sans-serif";
    ctx.fillText(`${row.goodput.toFixed(1)}`, x - 14, y - 12);
    ctx.fillStyle = "#617069";
    ctx.fillText(`${speeds[index].toFixed(1)} m/s`, x - 20, 276);
  });
}

function attachEvents() {
  const resolutionGroup = $("resolutionGroup");
  RESOLUTIONS.forEach((resolution) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.value = resolution;
    button.setAttribute("role", "radio");
    button.textContent = resolution;
    button.addEventListener("click", () => {
      state.selected.resolution = resolution;
      syncLiveInputsFromMeasuredKnobs();
      maybeRecover();
    });
    resolutionGroup.append(button);
  });

  $("speed").addEventListener("input", (event) => {
    state.selected.speed = unique("speed")[Number(event.target.value)];
    syncLiveInputsFromMeasuredKnobs();
    maybeRecover();
  });
  $("prb").addEventListener("input", (event) => {
    state.selected.prb = unique("prb")[Number(event.target.value)];
    syncLiveInputsFromMeasuredKnobs();
    render();
  });
  $("cpu").addEventListener("input", (event) => {
    state.selected.cpu = unique("cpu")[Number(event.target.value)];
    syncLiveInputsFromMeasuredKnobs();
    render();
  });
  $("heatMetric").addEventListener("change", render);
  $("recoverBtn").addEventListener("click", () => setSelection(recommend()));
  $("applyLive").addEventListener("click", applyLiveControl);
  document.querySelectorAll(".scenario-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const scenario = button.dataset.scenario;
      if (scenario === "nominal") state.selected = { speed: 1.0, resolution: "640x360", prb: 55, cpu: 4 };
      if (scenario === "stress") state.selected = { speed: 2.0, resolution: "1280x720", prb: 15, cpu: 1 };
      if (scenario === "scarce") state.selected = { speed: 1.5, resolution: "640x360", prb: 10, cpu: 2 };
      syncLiveInputsFromMeasuredKnobs();
      render();
    });
  });
  $("playReplay").addEventListener("click", playReplay);
}

function maybeRecover() {
  if ($("autoRecover").checked) setSelection(recommend());
  else render();
}

function playReplay() {
  const sequence = [0.5, 1.0, 1.5, 2.0, 1.0];
  let index = 0;
  clearInterval(state.replayTimer);
  state.replayTimer = setInterval(() => {
    state.selected.speed = sequence[index % sequence.length];
    setSelection(recommend());
    index += 1;
    if (index >= sequence.length) clearInterval(state.replayTimer);
  }, 900);
}

async function refreshLiveStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.liveBackend = true;
    const mode = data.state && data.state.apply_real ? "real actuators" : "dry-run actuators";
    $("liveStatus").textContent = `backend online, ${mode}`;
    renderLiveRun(data.state);
  } catch (error) {
    state.liveBackend = false;
    $("liveStatus").textContent = "backend offline";
    $("liveRunStatus").textContent = "backend offline";
    $("liveIsaacProgress").textContent = "backend offline";
  }
}

function renderLiveRun(liveState) {
  if (!liveState) return;
  $("liveRunStatus").textContent = liveState.run_status || "idle";

  const isaac = liveState.isaac_status || {};
  if (isaac.phase && isaac.phase !== "waiting_for_isaac_status") {
    const spawned = Number.isFinite(Number(isaac.spawned)) ? Number(isaac.spawned) : null;
    const planned = Number.isFinite(Number(isaac.planned)) ? Number(isaac.planned) : null;
    const completed = Number.isFinite(Number(isaac.completed)) ? Number(isaac.completed) : null;
    const active = Number.isFinite(Number(isaac.active)) ? Number(isaac.active) : null;
    const replays = Number.isFinite(Number(isaac.replays)) ? Number(isaac.replays) : null;
    const progress = spawned != null && planned != null && planned > 0
      ? `${spawned}/${planned} spawned`
      : "no spawn plan yet";
    const completedText = completed == null ? "" : `, ${completed} completed`;
    const activeText = active == null ? "" : `, ${active} active`;
    const replayText = replays ? `, ${replays} replays` : "";
    $("liveIsaacProgress").textContent = `${isaac.phase}: ${progress}${completedText}${activeText}${replayText}`;
  } else if (isaac.phase === "waiting_for_isaac_status") {
    $("liveIsaacProgress").textContent = "no Isaac heartbeat yet";
  } else {
    $("liveIsaacProgress").textContent = "waiting for Isaac";
  }

  const results = liveState.actuator_results || [];
  const ros = results.find((item) => item.name === "ros_control");
  if (ros && ros.acks && ros.acks.length) {
    const seen = new Set();
    const uniqueAcks = ros.acks.filter((ack) => {
      const key = `${ack.component}:${ack.status}:${ack.run_request || ""}:${ack.resolution || ""}:${ack.speed || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    $("liveAckSummary").textContent = uniqueAcks
      .map((ack) => {
        const run = ack.run_request ? `, run ${ack.run_request}` : "";
        return `${ack.component}: ${ack.status}${run}`;
      })
      .join(", ");
  } else if (ros) {
    if (ros.status === "no_subscribers") {
      $("liveAckSummary").textContent = "no /orbit_inspect/control subscribers";
    } else if (ros.status === "published_no_ack") {
      $("liveAckSummary").textContent = "subscriber seen, no Isaac/factory ack";
    } else {
      $("liveAckSummary").textContent = ros.status;
    }
  } else {
    $("liveAckSummary").textContent = "none yet";
  }

  if (results.length) {
    $("liveActuatorSummary").textContent = results
      .filter((item) => item.name !== "ros_control")
      .map((item) => {
        if (item.name === "edge_cpu") return `CPU ${item.status}: ${item.detail || "--"}`;
        if (item.name === "ul_prb") return `PRB ${item.status}: ${item.detail || "--"}`;
        return `${item.name} ${item.status}`;
      })
      .join(" | ") || "ROS only";
  } else {
    $("liveActuatorSummary").textContent = "none yet";
  }

  const measurement = liveState.online_measurement || {};
  const resultPath = measurement.path || liveState.latency_csv_path || "";
  $("liveResultPath").textContent = resultPath ? shortPath(resultPath) : "not created yet";

  if (measurement.status === "not_configured") {
    $("liveMeasurement").textContent = "set ORBIT_LIVE_LATENCY_CSV to show online metrics";
  } else if (measurement.status === "waiting_for_file") {
    $("liveMeasurement").textContent = "waiting for latency CSV";
  } else if (measurement.status === "waiting_for_rows") {
    $("liveMeasurement").textContent = "running, waiting for new rows";
  } else if (measurement.status === "waiting_for_matching_rows") {
    $("liveMeasurement").textContent =
      `rows arriving, waiting for this run (${measurement.candidate_rows || 0} unmatched)`;
  } else if (measurement.status === "stale_measurement_schema") {
    $("liveMeasurement").textContent = "restart ORBIT-Inspect factory relay for live measurement fields";
  } else if (measurement.status === "actuator_error") {
    $("liveMeasurement").textContent = measurement.detail || "live actuator error";
  } else if (measurement.status === "run_already_running") {
    $("liveMeasurement").textContent = "Isaac already running; wait, then apply again";
  } else if (measurement.rows) {
    const cot = measurement.correct_on_time_rate == null
      ? "--"
      : `${Math.round(measurement.correct_on_time_rate * 100)}%`;
    const p95 = measurement.p95_roundtrip_ms == null
      ? "--"
      : `${Math.round(measurement.p95_roundtrip_ms)} ms`;
    const removed = measurement.replay_rows_removed
      ? `, ${measurement.replay_rows_removed} replay/problem removed`
      : "";
    const cotCount = measurement.correct_on_time_count == null ? "--" : measurement.correct_on_time_count;
    const cotDenominator = measurement.correct_on_time_denominator == null
      ? measurement.rows
      : measurement.correct_on_time_denominator;
    const correctCount = measurement.correct_count == null ? 0 : measurement.correct_count;
    const lateCount = measurement.late_count == null ? 0 : measurement.late_count;
    const unknownCount = measurement.unknown_count == null ? 0 : measurement.unknown_count;
    const noAckCount = measurement.no_ack_count == null ? 0 : measurement.no_ack_count;
    $("liveMeasurement").textContent =
      `${measurement.status}: ${measurement.rows} valid rows${removed}, COT ${cot} (${cotCount}/${cotDenominator}), p95 ${p95}; correct ${correctCount}, late ${lateCount}, unknown ${unknownCount}, no_ack ${noAckCount}`;
  } else {
    $("liveMeasurement").textContent = "waiting for run";
  }

  if (measurement.rows) {
    const sentResolutions = measurement.observed_sent_resolutions && measurement.observed_sent_resolutions.length
      ? measurement.observed_sent_resolutions
      : (measurement.observed_resolutions || []);
    const resolution = sentResolutions.join(", ") || "--";
    const controlResolution = (measurement.observed_control_resolutions || []).join(", ") || "--";
    const controlSpeed = (measurement.observed_control_speeds || []).join(", ") || "--";
    const controlPrb = (measurement.observed_control_prbs || []).join(", ") || "--";
    const controlCpu = (measurement.observed_control_cpus || []).join(", ") || "--";
    const factoryPids = (measurement.observed_factory_pids || []).join(", ") || "--";
    const edgeCpus = (measurement.observed_edge_cpus || []).join(", ") || "--";
    const edgeCapacity = (measurement.observed_edge_compute_capacity || []).join(", ") || "--";
    $("liveObservedConfig").textContent =
      `sent ${resolution}; control ${controlSpeed} m/s, ${controlResolution}, PRB ${controlPrb}, CPU ${controlCpu}; factory pid ${factoryPids}; edge_cpus ${edgeCpus}, capacity ${edgeCapacity}`;
  } else if (measurement.status === "run_already_running") {
    $("liveObservedConfig").textContent = "not a clean new run";
  } else if (measurement.status === "waiting_for_matching_rows" || measurement.status === "stale_measurement_schema") {
    const sentResolutions = measurement.observed_sent_resolutions && measurement.observed_sent_resolutions.length
      ? measurement.observed_sent_resolutions
      : (measurement.observed_resolutions || []);
    const resolution = sentResolutions.join(", ") || "--";
    const controlResolution = (measurement.observed_control_resolutions || []).join(", ") || "--";
    const controlSpeed = (measurement.observed_control_speeds || []).join(", ") || "--";
    const controlPrb = (measurement.observed_control_prbs || []).join(", ") || "--";
    const controlCpu = (measurement.observed_control_cpus || []).join(", ") || "--";
    $("liveObservedConfig").textContent =
      `unmatched rows: sent ${resolution}; control ${controlSpeed} m/s, ${controlResolution}, PRB ${controlPrb}, CPU ${controlCpu}`;
  } else {
    $("liveObservedConfig").textContent = "waiting for rows";
  }
}

function startLivePolling() {
  clearInterval(state.livePollTimer);
  state.livePollTimer = setInterval(refreshLiveStatus, 2000);
}

async function applyLiveControl() {
  const live = readLiveInputs();
  const payload = {
    speed: live.speed,
    resolution: live.resolution,
    prb: live.prb,
    cpu: live.cpu,
    start_run: true,
  };
  $("liveStatus").textContent = "applying controls";
  try {
    const response = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    state.liveBackend = true;
    renderLiveRun(data.state);
    const results = data.state.actuator_results || [];
    const failed = results.filter((item) => item.status === "error");
    const dryRun = results.filter((item) => item.status === "dry_run");
    if (failed.length) {
      $("liveStatus").textContent = `error: ${failed[0].name}`;
    } else if (results.some((item) => item.status === "no_subscribers")) {
      $("liveStatus").textContent = "Isaac/factory subscriber not visible";
    } else if (results.some((item) => item.status === "partial_ack")) {
      $("liveStatus").textContent = "live command partially acknowledged";
    } else if (dryRun.length) {
      $("liveStatus").textContent = "applied ROS, dry-run system actuators";
    } else {
      $("liveStatus").textContent = "live controls sent, run pending";
    }
    startLivePolling();
  } catch (error) {
    $("liveStatus").textContent = `apply failed: ${error.message}`;
  }
}

async function init() {
  attachEvents();
  try {
    const response = await fetch(CSV_PATH);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const csv = await response.text();
    state.rows = parseCsv(csv).map(normalizeRow).filter((row) =>
      row.resolution && row.speed && row.prb && row.cpu
    );
    $("liveModeLabel").textContent = "Measured replay";
  } catch (error) {
    state.rows = buildFallbackRows();
    $("liveModeLabel").textContent = "Fallback";
    console.warn("Using fallback model because measured CSV could not be loaded.", error);
  }
  $("pointCount").textContent = state.rows.length.toLocaleString();
  refreshLiveStatus();
  startLivePolling();
  render();
}

init();
