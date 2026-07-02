// ════════════════════════════════════════════
// content_script.js v1.0 production build
// Extracts job data from Upwork using server-distributed selectors
// ════════════════════════════════════════════

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

    if (message.action === "extractJobCount") {
        // Just check the number of jobs
        const sel = message.selectorConfig?.title_selector
                    || 'h3.job-tile-title a, h2.job-tile-title a';
        const links = document.querySelectorAll(sel);
        sendResponse({ job_count: links.length });
    }

    if (message.action === "extractJobs") {
        const result = extractJobs(message.selectorConfig, message.excludeSkills);
        sendResponse(result);
    }

    return true;
});


// ════════════════════════════════════════════
// Extract job data (using server-distributed selectors)
// ════════════════════════════════════════════
function extractJobs(selectorConfig, excludeSkills) {

    // Fallback values (used when no server config was received)
    const config = selectorConfig || {
        title_selector:   'h3.job-tile-title a, h2.job-tile-title a',
        section_selector: 'section[data-ev-label-prefix], section.job-tile',
        budget_keywords:  ['Fixed', 'Hourly', '$', 'Budget'],
        posted_keywords:  ['Posted', 'ago', 'hours', 'days', 'minutes'],
        skill_class_includes: ['token', 'skill'],
        url_base:         'https://www.upwork.com',
        max_jobs:         20,
        description_min_length: 30,
        description_max_length: 300,
    };

    const excludeSet = new Set(excludeSkills || []);

    const jobs  = [];
    const debug = [];

    const titleLinks = document.querySelectorAll(config.title_selector);
    debug.push(`Title links found: ${titleLinks.length}`);

    titleLinks.forEach((link, index) => {
        if (index >= config.max_jobs) return;

        // Identify the section element
        let section = null;
        const sectionSelectors = config.section_selector.split(',').map(s => s.trim());
        for (const sel of sectionSelectors) {
            section = link.closest(sel);
            if (section) break;
        }
        if (!section) {
            section = link.closest('section');
        }

        const title = cleanText(link.innerText);
        const rawUrl = link.getAttribute('href') || '';
        const url = rawUrl.startsWith('http')
                    ? rawUrl
                    : (config.url_base || 'https://www.upwork.com') + rawUrl;

        if (!title) return;

        let budget = '', posted = '', description = '', skills = [], clientInfo = '';

        if (section) {
            // Look for the budget / hourly rate
            const budgetKeywords = config.budget_keywords || [];
            const spans = section.querySelectorAll('span, small, li');
            for (const el of spans) {
                const t = cleanText(el.innerText);
                if (t.length < 80 && budgetKeywords.some(k => t.includes(k))) {
                    budget = t;
                    break;
                }
            }

            // Look for the posted date/time
            const postedKeywords = config.posted_keywords || [];
            for (const el of section.querySelectorAll('small, span')) {
                const t = cleanText(el.innerText);
                if (postedKeywords.some(k => t.includes(k))) {
                    posted = t.substring(0, 60);
                    break;
                }
            }

            // Look for the description text
            const minLen = config.description_min_length || 30;
            const maxLen = config.description_max_length || 300;
            for (const p of section.querySelectorAll('p')) {
                const t = cleanText(p.innerText);
                if (t.length > minLen) {
                    description = t.substring(0, maxLen);
                    break;
                }
            }

            // Look for skill tags (applying the exclude list)
            const skillIncludes = config.skill_class_includes || ['token', 'skill'];
            const skillSelector = skillIncludes
                .map(s => `[class*="${s}"]`)
                .join(', ');
            const tokens = section.querySelectorAll(skillSelector);
            skills = Array.from(tokens)
                .map(el => cleanText(el.innerText))
                .filter(s => s &&
                             s.length > 1 &&
                             s.length < 40 &&
                             !excludeSet.has(s))
                .slice(0, 8);

            // Client info
            const clientEl = section.querySelector(
                '[data-test="client-country"], [data-test="payment-verified"]'
            );
            if (clientEl) clientInfo = cleanText(clientEl.innerText);
        }

        jobs.push({
            title, url, budget, posted, description, skills,
            client_info: clientInfo
        });
    });

    debug.push(`Jobs extracted: ${jobs.length}`);

    return {
        success:    jobs.length > 0,
        job_count:  jobs.length,
        jobs,
        debug,
        page_url:   window.location.href,
        page_title: document.title,
    };
}


function cleanText(text) {
    if (!text) return '';
    return text.replace(/\s+/g, ' ').trim();
}
