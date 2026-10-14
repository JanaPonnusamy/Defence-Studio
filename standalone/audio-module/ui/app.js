const API_BASE = window.location.origin.includes("5173") ? "http://127.0.0.1:8000" : "";

async function api(path, opts = {}) {
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error?.message || res.statusText);
  }
  return res.json();
}

// --- Tabs ---
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("panel-" + btn.dataset.tab).classList.add("active");
  });
});

async function checkHealth() {
  try {
    await api("/health");
    document.getElementById("apiStatus").textContent = "API connected";
  } catch (e) {
    document.getElementById("apiStatus").textContent = "API unreachable: " + e.message;
  }
}
checkHealth();

// --- Reference Library ---
async function loadReferences() {
  const refs = await api("/references");
  const tbody = document.querySelector("#referencesTable tbody");
  tbody.innerHTML = "";
  refs.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.title || ""}</td><td>${r.status}</td><td>${r.duration ? r.duration.toFixed(2) + "s" : "-"}</td>
      <td>
        <button data-id="${r.reference_id}" class="processBtn">Process</button>
        <button data-id="${r.reference_id}" class="removeBtn">Remove</button>
        <code>${r.reference_id}</code>
      </td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll(".processBtn").forEach(b => b.addEventListener("click", async () => {
    await api(`/references/${b.dataset.id}/process`, { method: "POST" });
    alert("Processing job submitted. Check Processing Queue tab.");
  }));
  tbody.querySelectorAll(".removeBtn").forEach(b => b.addEventListener("click", async () => {
    await api(`/references/${b.dataset.id}`, { method: "DELETE" });
    loadReferences();
  }));
}
document.getElementById("refreshReferences").addEventListener("click", loadReferences);

document.getElementById("addReferenceForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const res = await fetch(API_BASE + "/references", { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert("Error: " + (body.error?.message || res.statusText));
    return;
  }
  e.target.reset();
  loadReferences();
});

// --- Processing Queue / Job Status ---
async function loadJobs() {
  const jobs = await api("/jobs");
  const tbody = document.querySelector("#jobsTable tbody");
  tbody.innerHTML = "";
  jobs.forEach(j => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${j.job_id.slice(0, 8)}</code></td><td>${j.job_type}</td><td>${j.status}</td>
      <td>${j.stage || ""}</td><td>${(j.progress * 100).toFixed(0)}%</td>
      <td>${j.result_id ? j.result_id.slice(0, 8) : ""}</td><td>${j.error_message || ""}</td>`;
    tbody.appendChild(tr);
  });
}
document.getElementById("refreshJobs").addEventListener("click", loadJobs);
setInterval(() => { if (document.getElementById("panel-queue").classList.contains("active")) loadJobs(); }, 2000);

// --- Workspace ---
document.getElementById("loadWorkspace").addEventListener("click", async () => {
  const refId = document.getElementById("workspaceRefId").value.trim();
  if (!refId) return;
  const ref = await api(`/references/${refId}`);

  if (ref.audio_id) {
    const audioMeta = await api(`/audio/${ref.audio_id}`);
    document.getElementById("player").src = API_BASE + audioMeta.stream_url;
    if (audioMeta.waveform_url) {
      const wf = await api(audioMeta.waveform_url);
      drawWaveform(wf);
    }
  }
  if (ref.transcript_id) {
    const t = await api(`/transcripts/${ref.transcript_id}`);
    document.getElementById("transcriptView").textContent = t.raw_text;
  }
  if (ref.analysis_id) {
    const a = await api(`/analysis/${ref.analysis_id}`);
    document.getElementById("analysisView").textContent = JSON.stringify(a, null, 2);
  }
});

function drawWaveform(wf) {
  const canvas = document.getElementById("waveformCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#0a0f18";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const peaks = wf.peaks || [];
  if (!peaks.length) return;
  const mid = canvas.height / 2;
  const scaleX = canvas.width / peaks.length;
  ctx.strokeStyle = "#5ec8d8";
  ctx.beginPath();
  peaks.forEach((p, i) => {
    const x = i * scaleX;
    const yMin = mid - p[0] * mid;
    const yMax = mid - p[1] * mid;
    ctx.moveTo(x, yMin);
    ctx.lineTo(x, yMax);
  });
  ctx.stroke();
}

// --- Performance Profiles ---
async function loadProfiles() {
  const profiles = await api("/profiles");
  const tbody = document.querySelector("#profilesTable tbody");
  tbody.innerHTML = "";
  profiles.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.profile_name}</td><td>${p.tempo}</td><td>${p.pause_style}</td><td>${p.energy}</td><td>${p.emphasis}</td><td>${p.pitch_style}</td><td><code>${p.profile_id}</code></td>`;
    tbody.appendChild(tr);
  });
}
document.getElementById("refreshProfiles").addEventListener("click", loadProfiles);

document.getElementById("buildProfileForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const body = {
    profile_name: form.get("profile_name"),
    analysis_ids: form.get("analysis_ids").split(",").map(s => s.trim()).filter(Boolean),
  };
  const job = await api("/profiles/build", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  alert("Profile build job submitted: " + job.job_id);
});

document.getElementById("buildPlanForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const body = { profile_id: form.get("profile_id"), script: form.get("script"), language: "en" };
  const plan = await api("/profiles/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  document.getElementById("planView").textContent = JSON.stringify(plan, null, 2);
});

// --- Audio Creator ---
async function loadProviders() {
  const data = await api("/tts/providers");
  const select = document.getElementById("providerSelect");
  select.innerHTML = "";
  data.providers.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.provider;
    opt.textContent = `${p.provider}${p.is_mock ? " (MOCK)" : ""}`;
    select.appendChild(opt);
  });
}
loadProviders();

document.getElementById("ttsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const body = {
    text: form.get("text"), voice_id: form.get("voice_id") || null,
    provider: form.get("provider"), performance_plan_id: form.get("performance_plan_id") || null,
  };
  const job = await api("/tts/synthesize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  document.getElementById("ttsJobStatus").textContent = "Job submitted: " + job.job_id;
  pollTtsJob(job.job_id);
});

async function pollTtsJob(jobId) {
  const poll = async () => {
    const job = await api(`/jobs/${jobId}`);
    document.getElementById("ttsJobStatus").textContent = `Job ${jobId.slice(0,8)}: ${job.status} (${job.stage || ""})`;
    if (job.status === "SUCCEEDED") {
      const gen = await api(`/generated/${job.result_id}`);
      document.getElementById("ttsResult").innerHTML = `
        <p>Provider: <b>${gen.provider}</b> ${gen.provider_metadata?.is_mock ? "(MOCK)" : ""}</p>
        <p>Duration: ${gen.duration?.toFixed(2)}s | Validation: ${gen.validation_status}</p>
        <audio controls src="${API_BASE}${gen.stream_url}"></audio>`;
    } else if (job.status === "FAILED") {
      document.getElementById("ttsResult").textContent = "Failed: " + job.error_message;
    } else {
      setTimeout(poll, 1000);
    }
  };
  poll();
}

loadReferences();
loadJobs();
loadProfiles();
