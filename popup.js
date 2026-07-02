// ════════════════════════════════════════════
// popup.js - Upwork JobSearch v1.0 production build
// License auth + prompt/selector distribution
// API key sent directly (not routed through our server)
// ════════════════════════════════════════════

const SERVER_URL    = "https://upwork.doublemoon.biz";
const CACHE_KEY     = "ujs_server_config";
const CACHE_TTL_SEC = 3600;  // 1 hour

const $ = id => document.getElementById(id);

// ── State management ──────────────────────────────
const STATE = {
    STARTING:    'starting',
    AUTH:        'auth',
    NO_LICENSE:  'no_license',
    READY:       'ready',
    NO_PAGE:     'no_page',
    RUNNING:     'running',
    DONE:        'done',
    ERROR:       'error',
};

let currentState   = STATE.STARTING;
let loadedSettings = null;
let serverConfig   = null;
let startTime      = null;

// ════════════════════════════════════════════
// Initialization (on extension startup)
// ════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", async () => {
    setState(STATE.STARTING);

    try {
        // 1. Load server config from cache
        serverConfig = await loadServerConfig();

        if (!serverConfig) {
            // 2. No/invalid cache -> prompt for JSON file selection
            //    (a license key is required)
            setState(STATE.NO_LICENSE);
            return;
        }

        // 3. Server config available -> check the current page
        await checkPage();

    } catch(err) {
        setState(STATE.ERROR, { message: "Startup error: " + err.message });
    }
});

// ════════════════════════════════════════════
// Fetch server config (cache + server)
// ════════════════════════════════════════════
async function loadServerConfig() {
    // Load from cache
    const cached = await chrome.storage.local.get(CACHE_KEY);
    const data   = cached[CACHE_KEY];

    if (data && data.cached_at) {
        const ageSec = (Date.now() - data.cached_at) / 1000;
        if (ageSec < CACHE_TTL_SEC) {
            // Cache is still valid
            return data;
        }
    }

    return null;  // Invalid or expired
}

async function fetchServerConfig(licenseKey) {
    /**
     * Fetch configuration from the server
     * 1. Send the license key to /license/validate
     * 2. Receive the prompt, selectors, exclude list, and AI settings
     * 3. Cache the result in chrome.storage.local
     */
    const res = await fetch(SERVER_URL + "/license/validate", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ license_key: licenseKey }),
    });

    const data = await res.json();

    if (res.status === 403 || data.status !== "valid") {
        throw new Error(data.message || "License validation failed");
    }

    // Save to cache
    const cacheData = {
        ...data,
        license_key: licenseKey,
        cached_at:   Date.now(),
    };
    await chrome.storage.local.set({ [CACHE_KEY]: cacheData });

    return cacheData;
}

// ════════════════════════════════════════════
// Page check
// ════════════════════════════════════════════
async function checkPage() {
    try {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        const tab  = tabs[0];

        if (!tab || !tab.url) {
            setState(STATE.NO_PAGE);
            return;
        }

        if (!tab.url.includes("upwork.com")) {
            setState(STATE.NO_PAGE);
            return;
        }

        // Check the job count on the Upwork page (using server-distributed selectors)
        try {
            const selectorConfig = serverConfig.config.selectors.config;
            const result = await chrome.tabs.sendMessage(
                tab.id,
                { action: "extractJobCount", selectorConfig }
            );
            setState(STATE.READY, { jobCount: result.job_count });
        } catch(e) {
            setState(STATE.READY, { jobCount: null });
        }

    } catch(err) {
        setState(STATE.ERROR, { message: err.message });
    }
}

// ════════════════════════════════════════════
// Main button click
// ════════════════════════════════════════════
$("main-btn").addEventListener("click", async () => {

    // No license -> prompt to select the JSON file
    if (currentState === STATE.NO_LICENSE) {
        await selectSettingsAndAuth();
        return;
    }

    if (currentState === STATE.DONE) {
        await checkPage();
        return;
    }

    if (currentState !== STATE.READY) return;

    await runProcess();
});

// ════════════════════════════════════════════
// Select JSON file -> license authentication
// ════════════════════════════════════════════
async function selectSettingsAndAuth() {
    try {
        setState(STATE.AUTH, { message: "Please select your JSON file..." });

        // File selection
        let settings;
        try {
            settings = await selectSettingsFile();
        } catch(e) {
            if (e.name === 'AbortError') {
                setState(STATE.NO_LICENSE);
                return;
            }
            throw e;
        }

        loadedSettings = settings;

        // Check the license key
        const licenseKey = settings.license_key;
        if (!licenseKey) {
            setState(STATE.ERROR, {
                message: "No license key was found in the JSON file. "
                       + "Please enter your license key in the Settings sheet in Excel."
            });
            return;
        }

        setState(STATE.AUTH, { message: "Validating your license..." });

        // Authenticate with the server
        try {
            serverConfig = await fetchServerConfig(licenseKey);
        } catch(e) {
            setState(STATE.ERROR, { message: e.message });
            return;
        }

        // Authenticated -> check the current page
        await checkPage();

    } catch(err) {
        setState(STATE.ERROR, { message: err.message });
    }
}

// ════════════════════════════════════════════
// Main process
// ════════════════════════════════════════════
async function runProcess() {
    setState(STATE.RUNNING);
    startTime = Date.now();

    try {
        // ── STEP 1: Check the settings file ──
        if (!loadedSettings) {
            setStatusText("📂 Please select your Excel JSON file...");

            try {
                loadedSettings = await selectSettingsFile();
            } catch(e) {
                if (e.name === 'AbortError') {
                    setState(STATE.READY, { jobCount: null });
                    return;
                }
                setState(STATE.ERROR, { message: 'Failed to read JSON file: ' + e.message });
                return;
            }
        }

        const apiKey = (loadedSettings.api_key || "").trim();
        if (!apiKey) {
            setState(STATE.ERROR, {
                message: 'No AI API key is set. Please enter it in the Settings sheet in Excel.'
            });
            return;
        }

        setProgress(10);

        // ── STEP 2: Fetch data from Upwork ──
        setStatusText("📥 Fetching data from Upwork...");
        setProgress(20);

        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        const tab  = tabs[0];

        const selectorConfig = serverConfig.config.selectors.config;
        const excludeSkills  = serverConfig.config.exclude_skills || [];

        let jobsResult;
        try {
            jobsResult = await chrome.tabs.sendMessage(tab.id, {
                action:         "extractJobs",
                selectorConfig: selectorConfig,
                excludeSkills:  excludeSkills,
            });
        } catch(e) {
            setState(STATE.ERROR, {
                message: 'Failed to fetch data from Upwork. Please reload the page and try again.'
            });
            return;
        }

        if (!jobsResult || !jobsResult.success || jobsResult.job_count === 0) {
            setState(STATE.ERROR, {
                message: 'No jobs were found. Please open an Upwork job search results page.'
            });
            return;
        }

        setProgress(40);
        setStatusText(`✅ Found ${jobsResult.job_count} jobs. Preparing AI evaluation...`);
        await sleep(200);

        // ── STEP 3: Call the Gemini API directly (not routed through our server) ──
        setProgress(50);
        setStatusText("🤖 Gemini AI is evaluating the jobs...");

        const aiResult = await callGeminiDirect(apiKey, jobsResult.jobs, loadedSettings);

        setProgress(85);

        if (!aiResult.success) {
            setState(STATE.ERROR, { message: aiResult.message });
            return;
        }

        setStatusText("📊 Generating the CSV file...");
        await sleep(200);

        // ── STEP 4: Generate and download the CSV ──
        const threshold  = parseInt(loadedSettings.score_threshold) || 75;
        const scoredJobs = aiResult.scored_jobs;
        const csvContent = generateCSV(scoredJobs);
        downloadCSV(csvContent);

        setProgress(100);
        await sleep(200);

        // Done
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        const matched = scoredJobs.filter(j => j.score >= threshold).length;

        setState(STATE.DONE, {
            total:     jobsResult.job_count,
            evaluated: scoredJobs.length,
            matched:   matched,
            elapsed:   elapsed,
        });

        // Clear the settings from memory (re-select next time)
        loadedSettings = null;

    } catch(err) {
        setState(STATE.ERROR, { message: 'Unexpected error: ' + err.message });
    }
}

// ════════════════════════════════════════════
// Call the Gemini API directly
// ════════════════════════════════════════════
async function callGeminiDirect(apiKey, jobs, settings) {
    const profile  = settings.profile  || {};
    const ai       = serverConfig.config.ai_settings;
    const template = serverConfig.config.prompt.template;
    const maxJobs  = parseInt(ai.max_jobs_per_evaluate) || 20;
    const model    = ai.default_model || "gemini-2.5-flash";

    const targetJobs = jobs.slice(0, maxJobs);

    // Build the prompt
    const prompt = buildPrompt(template, profile, targetJobs);

    // Call the Gemini API directly
    const url = `${ai.gemini_api_base}/${model}:generateContent?key=${apiKey}`;
    const payload = {
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: {
            temperature:      parseFloat(ai.temperature) || 0.3,
            maxOutputTokens:  parseInt(ai.max_output_tokens) || 4096,
            responseMimeType: ai.response_mime_type || "application/json",
        },
    };

    let res;
    try {
        res = await fetch(url, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify(payload),
        });
    } catch(e) {
        return { success: false, message: "Failed to connect to the Gemini API" };
    }

    if (!res.ok) {
        const errText = await res.text().catch(() => "");
        let errMsg = `Gemini API error (HTTP ${res.status})`;
        if (res.status === 400 && errText.includes("API key")) {
            errMsg = "Your API key appears to be invalid. Please check it in the Settings sheet in Excel.";
        } else if (res.status === 429) {
            errMsg = "The Gemini API rate limit has been reached. Please wait a moment and try again.";
        }
        return { success: false, message: errMsg };
    }

    const data = await res.json();

    let rawText;
    try {
        rawText = data.candidates[0].content.parts[0].text;
    } catch(e) {
        return { success: false, message: "The Gemini API response format was unexpected" };
    }

    // Extract the JSON payload
    let aiResult;
    try {
        const clean = rawText.trim();
        const m = clean.match(/(\{[\s\S]*\})/);
        const jsonStr = m ? m[1] : clean;
        aiResult = JSON.parse(jsonStr);
    } catch(e) {
        return { success: false, message: "Failed to parse the AI response as JSON" };
    }

    // Merge job data with AI scores
    const scoredJobs = [];
    const results = aiResult.results || [];
    for (const r of results) {
        const idx = r.index || 0;
        if (idx < targetJobs.length) {
            const job = targetJobs[idx];
            scoredJobs.push({
                title:          job.title,
                url:            job.url,
                budget:         job.budget,
                posted:         job.posted,
                skills:         job.skills,
                score:          r.score || 0,
                reason:         r.reason || "",
                recommendation: r.recommendation || "",
            });
        }
    }

    // Sort by score descending
    scoredJobs.sort((a, b) => b.score - a.score);

    return { success: true, scored_jobs: scoredJobs };
}

// ════════════════════════════════════════════
// Build the prompt
// ════════════════════════════════════════════
function buildPrompt(template, profile, jobs) {
    const skills    = profile.skills            || "(not set)";
    const category  = profile.category          || "(not set)";
    const minRate   = profile.min_rate          || "(not set)";
    const excludeKw = profile.exclude_keywords  || "";
    const preferKw  = profile.prefer_keywords   || "";
    const aiRequest = profile.ai_request        || "";

    const excludeLine   = excludeKw  ? `\nKeywords to avoid: ${excludeKw}` : "";
    const preferLine    = preferKw   ? `\nPreferred keywords: ${preferKw}`  : "";
    const aiRequestLine = aiRequest  ? `\nUser's request to the AI: ${aiRequest}` : "";

    // Build the job list
    let jobsText = "";
    jobs.forEach((job, i) => {
        const skillsStr = Array.isArray(job.skills) ? job.skills.join(", ") : (job.skills || "Unknown");
        const description = (job.description || "No description").substring(0, 300);
        jobsText += `
[Job ${i+1}]
Title: ${job.title}
Budget / Rate: ${job.budget || "Unknown"}
Posted: ${job.posted || "Unknown"}
Skills: ${skillsStr}
Description: ${description}
---`;
    });

    // Replace placeholders in the template
    return template
        .replace(/\{skills\}/g, skills)
        .replace(/\{category\}/g, category)
        .replace(/\{min_rate\}/g, minRate)
        .replace(/\{exclude_line\}/g, excludeLine)
        .replace(/\{prefer_line\}/g, preferLine)
        .replace(/\{ai_request_line\}/g, aiRequestLine)
        .replace(/\{jobs_text\}/g, jobsText);
}

// ════════════════════════════════════════════
// Select the settings file
// ════════════════════════════════════════════
async function selectSettingsFile() {
    const [fileHandle] = await window.showOpenFilePicker({
        types: [{
            description: 'JSON settings file',
            accept: { 'application/json': ['.json'] },
        }],
        startIn:  'downloads',
        multiple: false,
    });

    const file = await fileHandle.getFile();
    const text = await file.text();
    const clean = text.startsWith('\uFEFF') ? text.slice(1) : text;
    return JSON.parse(clean);
}

// ════════════════════════════════════════════
// Generate the CSV
// ════════════════════════════════════════════
function generateCSV(jobs) {
    const headers = [
        'Score', 'Recommendation', 'Job Title', 'Budget / Rate',
        'Posted', 'Skills', 'AI Comment', 'URL'
    ];

    const rows = [headers];
    for (const job of jobs) {
        const skillsStr = Array.isArray(job.skills) ? job.skills.join(' / ') : (job.skills || '');
        rows.push([
            job.score          || 0,
            job.recommendation || '',
            job.title          || '',
            job.budget         || '',
            job.posted         || '',
            skillsStr,
            job.reason         || '',
            job.url            || '',
        ]);
    }

    const bom = '\uFEFF';
    return bom + rows.map(row =>
        row.map(cell => `"${String(cell).replace(/"/g, '""')}"`)
            .join(',')
    ).join('\n');
}

function downloadCSV(content) {
    try {
        const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
        const url  = URL.createObjectURL(blob);
        chrome.downloads.download({
            url:      url,
            filename: 'upwork_result.csv',
            saveAs:   false,
        }, () => URL.revokeObjectURL(url));
    } catch(e) {
        console.error('CSV download error:', e);
    }
}

// ════════════════════════════════════════════
// State management
// ════════════════════════════════════════════
function setState(state, data = {}) {
    currentState = state;

    const progressWrap = $("progress-wrap");
    const resultDiv    = $("result-summary");
    const btn          = $("main-btn");
    const footerLic    = $("footer-lic");

    progressWrap.style.display = "none";
    resultDiv.style.display    = "none";
    btn.disabled  = false;
    btn.className = "btn btn-primary";

    // Update the license status shown in the footer
    if (serverConfig && serverConfig.license) {
        const lic = serverConfig.license;
        footerLic.className = "footer-lic";
        footerLic.textContent = `✅ License valid (${lic.days_left} days left)`;
    } else if (state === STATE.NO_LICENSE || state === STATE.STARTING) {
        footerLic.className = "footer-lic";
        footerLic.textContent = "⏳ Awaiting authentication";
    }

    switch(state) {

        case STATE.STARTING:
            setStatusClass("s-info");
            setStatusContent("🔵", "Starting...");
            btn.disabled  = true;
            btn.className = "btn btn-disabled";
            setBtnLabel("⏳", "Starting...");
            break;

        case STATE.AUTH:
            setStatusClass("s-working");
            setStatusContent("spinner", data.message || "Authenticating...");
            btn.disabled  = true;
            btn.className = "btn btn-disabled";
            setBtnLabel("⏳", "Authenticating...");
            break;

        case STATE.NO_LICENSE:
            setStatusClass("s-warning");
            setStatusContent("📂",
                "This looks like your first time using the extension. Please select your Excel settings file (settings.json) to validate your license.");
            setBtnLabel("📂", "Select Excel JSON File");
            break;

        case STATE.READY: {
            const count = data.jobCount;
            const msg   = count != null
                ? `Upwork page detected (${count} jobs shown).\nClick the button to start the AI evaluation.`
                : "Upwork page detected.\nClick the button to start the AI evaluation.";
            setStatusClass("s-info");
            setStatusContent("🟢", msg);
            setBtnLabel("▶", "Run AI Evaluation & Save CSV");
            break;
        }

        case STATE.NO_PAGE:
            setStatusClass("s-warning");
            setStatusContent("⚠️",
                "Please open an Upwork job search results page.\nhttps://www.upwork.com/nx/find-work/");
            btn.disabled  = true;
            btn.className = "btn btn-disabled";
            setBtnLabel("⚠️", "Please Open an Upwork Page");
            break;

        case STATE.RUNNING:
            setStatusClass("s-working");
            setStatusContent("spinner", "Processing...");
            progressWrap.style.display = "block";
            setProgress(0);
            btn.disabled  = true;
            btn.className = "btn btn-disabled";
            setBtnLabel("⏳", "Processing (please wait)...");
            break;

        case STATE.DONE:
            setStatusClass("s-done");
            setStatusContent("✅", "Done! The CSV file has been saved.\nClick the \"Import CSV\" button in Excel to view the results.");
            resultDiv.style.display    = "block";
            $("r-total").textContent     = data.total     + "";
            $("r-evaluated").textContent = data.evaluated + "";
            $("r-matched").textContent   = data.matched   + "";
            $("r-time").textContent      = data.elapsed   + " sec";
            setBtnLabel("🔄", "Run Again");
            break;

        case STATE.ERROR:
            setStatusClass("s-error");
            setStatusContent("❌", data.message || "An error occurred");
            setBtnLabel("🔄", "Try Again");
            break;
    }
}

function setStatusClass(cls) {
    $("status").className = "status " + cls;
}

function setStatusContent(icon, text) {
    const el = $("status");
    const lines = text.replace(/\n/g, '<br>');
    if (icon === "spinner") {
        el.innerHTML = `<div class="spinner"></div><span>${lines}</span>`;
    } else {
        el.innerHTML = `<span class="status-icon">${icon}</span><span>${lines}</span>`;
    }
}

function setStatusText(text) {
    const span = $("status").querySelector("span:last-child");
    if (span) span.innerHTML = text.replace(/\n/g, '<br>');
}

function setBtnLabel(icon, text) {
    $("btn-icon").textContent = icon;
    $("btn-text").textContent = text;
}

function setProgress(pct) {
    $("progress-fill").style.width = pct + "%";
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
