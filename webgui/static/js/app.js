// GitHub OSINT Monitor Web GUI

const API_BASE = window.location.origin;

// DOM Elements
const reposGrid = document.getElementById('reposGrid');
const runAllBtn = document.getElementById('runAllBtn');
const fetchAllReposBtn = document.getElementById('fetchAllReposBtn');
const loginBtn = document.getElementById('loginBtn');
const loginModal = document.getElementById('loginModal');
const fetchReposModal = document.getElementById('fetchReposModal');
const tokenInput = document.getElementById('tokenInput');
const closeBtn = document.querySelectorAll('.close');
const repoList = document.getElementById('repoList');

let selectedRepos = new Set();
let allUserRepos = [];

// Modal handlers
loginBtn.addEventListener('click', () => {
    loginModal.style.display = 'flex';
});

fetchAllReposBtn.addEventListener('click', async () => {
    const statusRes = await fetch(`${API_BASE}/api/status`);
    const status = await statusRes.json();
    if (!status.token_loaded && !status.saved_token_loaded) {
        alert('Please login first to fetch your repositories.');
        loginModal.style.display = 'flex';
        return;
    }
    fetchReposModal.style.display = 'flex';
    await loadUserRepos();
});

closeBtn.forEach(btn => {
    btn.addEventListener('click', () => {
        loginModal.style.display = 'none';
        fetchReposModal.style.display = 'none';
    });
});

window.addEventListener('click', (e) => {
    if (e.target === loginModal) {
        loginModal.style.display = 'none';
    }
    if (e.target === fetchReposModal) {
        fetchReposModal.style.display = 'none';
    }
});

// Login/Logout
async function submitLogin() {
    const token = tokenInput.value.trim();
    if (!token) {
        alert('Please enter a token');
        return;
    }
    const res = await fetch(`${API_BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    });
    if (res.ok) {
        loginModal.style.display = 'none';
        tokenInput.value = '';
        alert('Token saved successfully');
    } else {
        alert('Failed to save token');
    }
}

async function logout() {
    const res = await fetch(`${API_BASE}/api/logout`, { method: 'POST' });
    if (res.ok) {
        alert('Token removed');
    }
}

// Run single repo
async function runRepo(repo) {
    const repoId = repo.replace('/', '-');
    const statusEl = document.getElementById(`status-${repoId}`);
    const eventsEl = document.getElementById(`events-${repoId}`);
    
    statusEl.textContent = 'Running...';
    statusEl.className = 'repo-status status-running';
    eventsEl.innerHTML = '<div class="no-data">Checking...</div>';

    try {
        const res = await fetch(`${API_BASE}/api/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo }),
        });
        const data = await res.json();
        
        if (data.error) {
            statusEl.textContent = 'Error';
            statusEl.className = 'repo-status status-error';
            eventsEl.innerHTML = `<div class="event-item" style="color: var(--danger)">${data.error}</div>`;
            return;
        }

        // Update metrics
        if (data.metrics) {
            document.getElementById(`stars-${repoId}`).textContent = data.metrics.stars ?? '-';
            document.getElementById(`forks-${repoId}`).textContent = data.metrics.forks ?? '-';
            document.getElementById(`watchers-${repoId}`).textContent = data.metrics.watchers ?? '-';
            document.getElementById(`issues-${repoId}`).textContent = data.metrics.open_issues ?? '-';
        }

        // Update events
        if (data.is_first_run) {
            statusEl.textContent = 'Baseline';
            eventsEl.innerHTML = `<div class="event-item">Baseline established: ⭐ ${data.baseline.stars} | 🍴 ${data.baseline.forks}</div>`;
        } else if (data.events && data.events.length > 0) {
            statusEl.textContent = `${data.events_count} new`;
            statusEl.className = 'repo-status status-success';
            eventsEl.innerHTML = data.events.map(e => `<div class="event-item">${e}</div>`).join('');
        } else {
            statusEl.textContent = 'No changes';
            statusEl.className = 'repo-status status-idle';
            eventsEl.innerHTML = '<div class="no-data">No changes detected</div>';
        }

        // Show secrets if any
        if (data.secrets_count > 0) {
            const secretHtml = data.secrets.map(s => 
                `<div class="secret-item">⚠️ ${s}</div>`
            ).join('');
            eventsEl.innerHTML += `<div class="secret-alert"><div class="secret-alert-title">${data.secrets_count} Possible Secret(s) Found</div>${secretHtml}</div>`;
        }
    } catch (err) {
        statusEl.textContent = 'Error';
        statusEl.className = 'repo-status status-error';
        eventsEl.innerHTML = `<div class="event-item" style="color: var(--danger)">${err.message}</div>`;
    }
}

// Run all repos
runAllBtn.addEventListener('click', async () => {
    runAllBtn.disabled = true;
    runAllBtn.textContent = 'Running...';
    
    try {
        const res = await fetch(`${API_BASE}/api/run-all`, { method: 'POST' });
        const data = await res.json();
        
        // Refresh all repo cards
        for (const result of data) {
            const repo = result.repo;
            const repoId = repo.replace('/', '-');
            const statusEl = document.getElementById(`status-${repoId}`);
            const eventsEl = document.getElementById(`events-${repoId}`);
            
            if (result.error) {
                statusEl.textContent = 'Error';
                statusEl.className = 'repo-status status-error';
                eventsEl.innerHTML = `<div class="event-item" style="color: var(--danger)">${result.error}</div>`;
                continue;
            }

            if (result.metrics) {
                document.getElementById(`stars-${repoId}`).textContent = result.metrics.stars ?? '-';
                document.getElementById(`forks-${repoId}`).textContent = result.metrics.forks ?? '-';
                document.getElementById(`watchers-${repoId}`).textContent = result.metrics.watchers ?? '-';
                document.getElementById(`issues-${repoId}`).textContent = result.metrics.open_issues ?? '-';
            }

            if (result.is_first_run) {
                statusEl.textContent = 'Baseline';
                eventsEl.innerHTML = `<div class="event-item">Baseline established</div>`;
            } else if (result.events && result.events.length > 0) {
                statusEl.textContent = `${result.events_count} new`;
                statusEl.className = 'repo-status status-success';
                eventsEl.innerHTML = result.events.map(e => `<div class="event-item">${e}</div>`).join('');
            } else {
                statusEl.textContent = 'No changes';
                statusEl.className = 'repo-status status-idle';
                eventsEl.innerHTML = '<div class="no-data">No changes detected</div>';
            }

            if (result.secrets_count > 0) {
                const secretHtml = result.secrets.map(s => 
                    `<div class="secret-item">⚠️ ${s}</div>`
                ).join('');
                eventsEl.innerHTML += `<div class="secret-alert"><div class="secret-alert-title">${result.secrets_count} Possible Secret(s) Found</div>${secretHtml}</div>`;
            }
        }
    } catch (err) {
        alert('Failed to run all repos: ' + err.message);
    } finally {
        runAllBtn.disabled = false;
        runAllBtn.textContent = 'Run All';
    }
});

// Auto-refresh status on load
async function loadStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();
        loginBtn.textContent = data.token_loaded ? 'Logout' : 'Login';
    } catch (err) {
        console.error('Failed to load status:', err);
    }
}

// Fetch all user repos
async function loadUserRepos() {
    repoList.innerHTML = '<div class="no-data">Loading...</div>';
    selectedRepos.clear();
    try {
        const res = await fetch(`${API_BASE}/api/user/repos`);
        const data = await res.json();
        if (data.error) {
            repoList.innerHTML = `<div class="no-data" style="color: var(--danger)">${data.error}</div>`;
            return;
        }
        allUserRepos = data;
        if (data.length === 0) {
            repoList.innerHTML = '<div class="no-data">No repositories found.</div>';
            return;
        }
        repoList.innerHTML = data.map((repo, idx) => `
            <div class="repo-list-item" data-index="${idx}" onclick="toggleRepo(${idx})">
                <input type="checkbox" id="repo-check-${idx}" />
                <div class="repo-list-info">
                    <div class="repo-list-name">${repo.name} <span class="repo-list-private ${repo.private ? 'private' : 'public'}">${repo.private ? 'Private' : 'Public'}</span></div>
                    <div class="repo-list-meta">Updated: ${new Date(repo.updated_at).toLocaleString()}</div>
                </div>
            </div>
        `).join('');
    } catch (err) {
        repoList.innerHTML = `<div class="no-data" style="color: var(--danger)">Failed to load repos: ${err.message}</div>`;
    }
}

function toggleRepo(idx) {
    const item = document.querySelector(`.repo-list-item[data-index="${idx}"]`);
    const checkbox = document.getElementById(`repo-check-${idx}`);
    if (selectedRepos.has(idx)) {
        selectedRepos.delete(idx);
        item.classList.remove('selected');
        checkbox.checked = false;
    } else {
        selectedRepos.add(idx);
        item.classList.add('selected');
        checkbox.checked = true;
    }
}

async function addSelectedRepos() {
    const reposToAdd = Array.from(selectedRepos).map(idx => allUserRepos[idx].name);
    if (reposToAdd.length === 0) {
        alert('Please select at least one repository.');
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/api/repos/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repos: reposToAdd }),
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Added ${data.added.length} repo(s). Total monitored: ${data.total}`);
            fetchReposModal.style.display = 'none';
            location.reload();
        } else {
            alert('Failed to add repos: ' + data.error);
        }
    } catch (err) {
        alert('Failed to add repos: ' + err.message);
    }
}

function closeFetchModal() {
    fetchReposModal.style.display = 'none';
}

loadStatus();
