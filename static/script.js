const newsContainer = document.getElementById("newsContainer");
const loading = document.getElementById("loading");
const errorBox = document.getElementById("error");
const pageTitle = document.getElementById("pageTitle");
const lastUpdated = document.getElementById("lastUpdated");
const refreshBtn = document.getElementById("refreshBtn");
const searchInput = document.getElementById("searchInput");

const AUTO_REFRESH_MS = 5 * 60 * 1000; // refresh every 5 minutes

const PLACEHOLDER =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400">' +
        '<rect width="100%" height="100%" fill="#e5e7eb"/>' +
        '<text x="50%" y="50%" font-size="28" font-family="Arial" fill="#6b7280" ' +
        'text-anchor="middle" dominant-baseline="middle">No Image</text></svg>'
    );

// What is currently shown (so refresh / auto-refresh repeat the same view)
let current = { type: "category", value: "general" };
let requestCounter = 0; // ignore out-of-date responses

/* ---------- helpers ---------- */

function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[c]);
}

// Only allow http(s) links and same-site paths (blocks javascript: URLs).
function safeUrl(url) {
    try {
        const u = new URL(url, window.location.origin);
        return ["http:", "https:"].includes(u.protocol) ? u.href : "#";
    } catch {
        return "#";
    }
}

function titleFor(category) {
    const names = { general: "Latest News", ours: "Our News" };
    return "📰 " + (names[category] || category.charAt(0).toUpperCase() + category.slice(1));
}

function setLoading(on) {
    loading.style.display = on ? "block" : "none";
    refreshBtn.classList.toggle("spin", on);
}

function showError(message) {
    errorBox.innerText = "❌ " + message;
    errorBox.style.display = "block";
}

function hideError() {
    errorBox.style.display = "none";
}

async function getJson(url) {
    const response = await fetch(url);
    let data = {};
    try {
        data = await response.json();
    } catch {
        /* non-JSON error page */
    }
    if (!response.ok) {
        throw new Error(data.message || "Request failed (" + response.status + ")");
    }
    return data;
}

/* ---------- loading data ---------- */

async function load(view) {
    current = view;
    const myRequest = ++requestCounter;

    newsContainer.innerHTML = "";
    hideError();
    setLoading(true);

    let ownUrl, externalUrl;
    if (view.type === "search") {
        pageTitle.innerText = "🔍 Search Results: " + view.value;
        ownUrl = "/api/my-news?q=" + encodeURIComponent(view.value);
        externalUrl = "/search?q=" + encodeURIComponent(view.value);
    } else {
        pageTitle.innerText = titleFor(view.value);
        ownUrl = "/api/my-news?category=" + encodeURIComponent(view.value);
        // "Our News" tab shows only our own uploaded stories.
        externalUrl = view.value === "ours" ? null : "/news?category=" + encodeURIComponent(view.value);
    }

    const [ownResult, extResult] = await Promise.allSettled([
        getJson(ownUrl),
        externalUrl ? getJson(externalUrl) : Promise.resolve({ articles: [] }),
    ]);

    if (myRequest !== requestCounter) return; // a newer click already took over
    setLoading(false);

    const own = ownResult.status === "fulfilled" ? ownResult.value.articles || [] : [];
    const external = extResult.status === "fulfilled" ? extResult.value.articles || [] : [];

    if (ownResult.status === "rejected" && extResult.status === "rejected") {
        showError(extResult.reason.message);
        return;
    }

    displayNews([...own, ...external]);

    if (extResult.status === "rejected") {
        const note = document.createElement("div");
        note.className = "notice";
        note.textContent = "Headlines from NewsAPI are unavailable right now: " + extResult.reason.message;
        newsContainer.prepend(note);
    }

    lastUpdated.textContent = "Updated " + new Date().toLocaleTimeString();
}

function loadCategory(category) {
    searchInput.value = "";
    document.querySelectorAll("#categoryNav button").forEach((b) => {
        b.classList.toggle("active", b.dataset.category === category);
    });
    load({ type: "category", value: category });
}

function searchNews() {
    const keyword = searchInput.value.trim();
    if (!keyword) {
        showError("Please enter something to search.");
        return;
    }
    document.querySelectorAll("#categoryNav button").forEach((b) => b.classList.remove("active"));
    load({ type: "search", value: keyword });
}

/* ---------- rendering ---------- */

function displayNews(articles) {
    newsContainer.innerHTML = "";

    if (!articles || articles.length === 0) {
        newsContainer.innerHTML = "<h2>No news found.</h2>";
        return;
    }

    articles.forEach((article) => {
        const isOwn = article.own === true;
        const card = document.createElement("div");
        card.className = "news-card" + (isOwn ? " own" : "");

        const image = safeUrl(article.urlToImage || "") !== "#" && article.urlToImage ? safeUrl(article.urlToImage) : PLACEHOLDER;
        const title = article.title || "No title";
        const description = article.description || "No description available.";
        const source = article.source?.name || "Unknown source";
        const date = article.publishedAt ? new Date(article.publishedAt).toLocaleString() : "Unknown";
        const link = safeUrl(article.url);

        card.innerHTML = `
            <img src="${esc(image)}" alt="News image" loading="lazy" referrerpolicy="no-referrer">
            <div class="news-content">
                ${isOwn ? '<span class="own-badge">⭐ Our News</span>' : ""}
                <h2>${esc(title)}</h2>
                <p>${esc(description)}</p>
                <div class="source">
                    📢 ${esc(source)}<br>
                    🕒 ${esc(date)}
                </div>
                <a href="${esc(link)}" ${isOwn ? "" : 'target="_blank" rel="noopener noreferrer"'} class="read-more">
                    Read More →
                </a>
            </div>`;

        card.querySelector("img").addEventListener("error", function () {
            this.src = PLACEHOLDER;
        });

        newsContainer.appendChild(card);
    });
}

/* ---------- events ---------- */

document.querySelectorAll("#categoryNav button").forEach((btn) => {
    btn.addEventListener("click", () => loadCategory(btn.dataset.category));
});

document.getElementById("searchBtn").addEventListener("click", searchNews);

searchInput.addEventListener("keypress", (event) => {
    if (event.key === "Enter") searchNews();
});

refreshBtn.addEventListener("click", () => load(current));

/* DARK MODE (remembered between visits) */

if (localStorage.getItem("dark") === "1") document.body.classList.add("dark");

document.getElementById("darkModeBtn").addEventListener("click", () => {
    document.body.classList.toggle("dark");
    localStorage.setItem("dark", document.body.classList.contains("dark") ? "1" : "0");
});

/* LOAD NEWS WHEN WEBSITE OPENS + AUTO REFRESH */

loadCategory("general");

setInterval(() => {
    if (!document.hidden) load(current);
}, AUTO_REFRESH_MS);
