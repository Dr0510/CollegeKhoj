/**
 * CollegeKhoj Admin Dashboard v2
 * Handles all dashboard interactivity: stats, charts, import monitoring,
 * recommendation testing, activity timeline, exam type tabs.
 */
(function() {
    'use strict';

    // ── State ──────────────────────────────────────────────────────────────
    let currentExamType = 'ALL';
    let refreshInterval = null;

    // ── DOM Cache ──────────────────────────────────────────────────────────
    const $ = function(id) { return document.getElementById(id); };

    // ── Init ───────────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function() {
        loadDashboardData();
        setupExamTabs();
        setupRecommendationForm();
        setupImportPolling();
    });

    // ── Load Dashboard Data ────────────────────────────────────────────────
    function loadDashboardData() {
        var url = '/admin/api/dashboard-full';
        if (currentExamType !== 'ALL') {
            url += '?exam_type=' + encodeURIComponent(currentExamType);
        }

        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (!d.ok) { console.error('Dashboard API error'); return; }
                renderStats(d.stats);
                renderCollegeOverview(d.college_overview);
                renderImportMonitoring(d.import_monitoring);
                renderTimeline(d.timeline);
                renderCharts(d.charts);
            })
            .catch(function(e) { console.error('Dashboard fetch error:', e); });
    }

    // ── Exam Type Tabs ─────────────────────────────────────────────────────
    function setupExamTabs() {
        var tabs = document.querySelectorAll('.dash-exam-tab');
        tabs.forEach(function(tab) {
            tab.addEventListener('click', function(e) {
                e.preventDefault();
                var examType = this.getAttribute('data-exam');
                tabs.forEach(function(t) { t.classList.remove('active'); });
                this.classList.add('active');
                currentExamType = examType;
                loadDashboardData();
            });
        });
    }

    // ── Render Stats Cards ─────────────────────────────────────────────────
    function renderStats(stats) {
        if (!stats) return;
        setText('dash-total-colleges', formatNum(stats.total_colleges));
        setText('dash-total-branches', formatNum(stats.total_branches));
        setText('dash-total-records', formatNum(stats.total_cutoff_records));
        setText('dash-total-users', formatNum(stats.total_users));
        setText('dash-pending-approvals', formatNum(stats.pending_approvals));
        setText('dash-failed-imports', formatNum(stats.failed_imports));

        // Color-code pending approvals
        var pendingEl = $('dash-pending-approvals');
        if (pendingEl) {
            pendingEl.style.color = stats.pending_approvals > 0 ? 'var(--color-warning)' : 'var(--color-success)';
        }
        var failedEl = $('dash-failed-imports');
        if (failedEl) {
            failedEl.style.color = stats.failed_imports > 0 ? 'var(--color-error)' : 'var(--color-success)';
        }
    }

    // ── Render College Overview ───────────────────────────────────────────
    function renderCollegeOverview(overview) {
        if (!overview) return;
        setText('dash-ov-total', formatNum(overview.total_colleges));
        setText('dash-ov-branches', formatNum(overview.total_branches));

        // Top locations
        var locationList = $('dash-top-locations');
        if (locationList && overview.top_locations) {
            locationList.innerHTML = '';
            overview.top_locations.slice(0, 5).forEach(function(loc) {
                var li = document.createElement('li');
                li.innerHTML = '<span class="dash-loc-name">' + escHtml(loc.location) + '</span> <span class="dash-loc-count">' + loc.count + '</span>';
                locationList.appendChild(li);
            });
        }

        // Recently added
        var recentList = $('dash-recent-colleges');
        if (recentList && overview.recently_added) {
            recentList.innerHTML = '';
            overview.recently_added.forEach(function(c) {
                var tr = document.createElement('tr');
                tr.innerHTML = '<td>' + escHtml(c.college) + '</td>' +
                    '<td>' + escHtml(c.branch) + '</td>' +
                    '<td>' + escHtml(c.location) + '</td>' +
                    '<td><span class="badge badge-info">NIRF ' + (c.nirf_rank || '—') + '</span></td>';
                recentList.appendChild(tr);
            });
        }
    }

    // ── Render Import Monitoring ──────────────────────────────────────────
    function renderImportMonitoring(monitor) {
        if (!monitor) return;
        var tableBody = $('dash-import-table-body');
        if (!tableBody) return;

        tableBody.innerHTML = '';
        if (!monitor.recent_jobs || monitor.recent_jobs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary);padding:24px;">No import jobs yet</td></tr>';
            return;
        }

        var activeIds = monitor.active_job_ids || [];

        monitor.recent_jobs.forEach(function(job) {
            var isActive = activeIds.indexOf(job.id) !== -1;
            var statusClass = job.status === 'COMPLETED' ? 'badge-success' :
                              job.status === 'FAILED' ? 'badge-danger' :
                              job.status === 'PROCESSING' ? 'badge-warning' :
                              'badge-info';
            var statusIcon = isActive ? '<i class="fas fa-spinner fa-spin"></i> ' : '';
            var progressBar = job.total_pages > 0 ?
                '<div style="display:flex;align-items:center;gap:6px;"><div style="flex:1;height:5px;background:var(--bg-primary);border-radius:3px;overflow:hidden;"><div style="height:100%;width:' + job.progress_pct + '%;background:var(--color-primary-gradient);border-radius:3px;transition:width 0.3s;"></div></div><span style="font-size:0.75rem;min-width:32px;">' + job.progress_pct + '%</span></div>' :
                '<span style="color:var(--text-tertiary);font-size:0.8rem;">—</span>';

            var tr = document.createElement('tr');
            tr.innerHTML = '<td><span title="' + escHtml(job.filename || '') + '">' + truncate(job.filename, 25) + '</span></td>' +
                '<td>' + progressBar + '</td>' +
                '<td><strong>' + (job.rows_imported || 0) + '</strong></td>' +
                '<td>' + (job.rows_failed || 0) + '</td>' +
                '<td><span class="badge ' + statusClass + '">' + statusIcon + job.status + '</span></td>' +
                '<td><a href="/admin/bulk-imports/' + job.id + '" class="btn btn-outline btn-sm"><i class="fas fa-eye"></i></a></td>';
            tableBody.appendChild(tr);
        });

        // Store active IDs for polling
        window.__dashActiveJobs = activeIds;
    }

    // ── Render Timeline ──────────────────────────────────────────────────
    function renderTimeline(entries) {
        var container = $('dash-timeline-list');
        if (!container) return;
        container.innerHTML = '';

        if (!entries || entries.length === 0) {
            container.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);padding:24px;"><i class="fas fa-clock" style="font-size:1.5rem;display:block;margin-bottom:8px;"></i>No recent activity</div>';
            return;
        }

        entries.forEach(function(entry) {
            var icon = getActionIcon(entry.action);
            var actionLabel = getActionLabel(entry.action);
            var resourceLabel = entry.resource_type ? (entry.resource_type + (entry.resource_id ? ' #' + entry.resource_id : '')) : '';
            var timeAgo = getTimeAgo(entry.timestamp);

            var div = document.createElement('div');
            div.className = 'dash-timeline-item';
            div.innerHTML = '<div class="dash-timeline-icon"><i class="fas ' + icon + '"></i></div>' +
                '<div class="dash-timeline-content">' +
                '<div class="dash-timeline-header"><strong>' + escHtml(entry.user || 'System') + '</strong> ' + actionLabel + '</div>' +
                (resourceLabel ? '<div class="dash-timeline-resource">' + escHtml(resourceLabel) + '</div>' : '') +
                '<div class="dash-timeline-time">' + timeAgo + '</div>' +
                '</div>';
            container.appendChild(div);
        });
    }

    // ── Render Charts ────────────────────────────────────────────────────
    function renderCharts(charts) {
        if (!charts) return;
        renderRecordsByYear(charts.records_by_year);
        renderBranchPopularity(charts.branch_popularity);
        renderImportSuccessRate(charts.import_success_rate);
        renderCollegeDistribution(charts.college_distribution);
    }

    function renderRecordsByYear(data) {
        var canvas = $('chart-records-year');
        if (!canvas || !data || data.length === 0) return;
        var ctx = canvas.getContext('2d');
        destroyChart('records-year');

        var labels = data.map(function(d) { return d.year; });
        var values = data.map(function(d) { return d.count; });

        window.__dashCharts = window.__dashCharts || {};
        window.__dashCharts['records-year'] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Cutoff Records',
                    data: values,
                    backgroundColor: 'rgba(79, 142, 247, 0.6)',
                    borderColor: 'rgba(79, 142, 247, 1)',
                    borderWidth: 1,
                    borderRadius: 4,
                }]
            },
            options: chartDefaults('Cutoff Records by Year')
        });
    }

    function renderBranchPopularity(data) {
        var canvas = $('chart-branch-pop');
        if (!canvas || !data || data.length === 0) return;
        var ctx = canvas.getContext('2d');
        destroyChart('branch-pop');

        var labels = data.map(function(d) { return truncate(d.branch, 20); });
        var values = data.map(function(d) { return d.avg_cutoff; });

        window.__dashCharts = window.__dashCharts || {};
        window.__dashCharts['branch-pop'] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Avg Cutoff',
                    data: values,
                    backgroundColor: 'rgba(99, 102, 241, 0.6)',
                    borderColor: 'rgba(99, 102, 241, 1)',
                    borderWidth: 1,
                    borderRadius: 4,
                }]
            },
            options: chartDefaults('Branch Popularity (Avg Cutoff)')
        });
    }

    function renderImportSuccessRate(data) {
        var canvas = $('chart-import-rate');
        if (!canvas || !data) return;
        var ctx = canvas.getContext('2d');
        destroyChart('import-rate');

        var labels = [];
        var values = [];
        var colors = [];

        var colorMap = {
            'COMPLETED': 'rgba(34, 197, 94, 0.8)',
            'FAILED': 'rgba(239, 68, 68, 0.8)',
            'CANCELLED': 'rgba(245, 158, 11, 0.8)',
            'PROCESSING': 'rgba(99, 102, 241, 0.8)',
            'PENDING': 'rgba(148, 163, 184, 0.8)',
        };

        for (var key in data) {
            labels.push(key);
            values.push(data[key]);
            colors.push(colorMap[key] || 'rgba(148, 163, 184, 0.8)');
        }

        if (labels.length === 0) return;

        window.__dashCharts = window.__dashCharts || {};
        window.__dashCharts['import-rate'] = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 2,
                    borderColor: 'var(--bg-card)',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { position: 'bottom', labels: { color: 'var(--text-secondary)', font: { size: 11 } } },
                    title: { display: true, text: 'Import Success Rate', color: 'var(--text-primary)', font: { size: 14, weight: '600' } }
                }
            }
        });
    }

    function renderCollegeDistribution(data) {
        var canvas = $('chart-college-dist');
        if (!canvas || !data || data.length === 0) return;
        var ctx = canvas.getContext('2d');
        destroyChart('college-dist');

        var colors = [
            'rgba(79, 142, 247, 0.8)', 'rgba(99, 102, 241, 0.8)',
            'rgba(139, 92, 246, 0.8)', 'rgba(34, 197, 94, 0.8)',
            'rgba(245, 158, 11, 0.8)', 'rgba(239, 68, 68, 0.8)',
            'rgba(6, 182, 212, 0.8)', 'rgba(236, 72, 153, 0.8)',
        ];

        var labels = data.map(function(d) { return d.location; });
        var values = data.map(function(d) { return d.count; });

        window.__dashCharts = window.__dashCharts || {};
        window.__dashCharts['college-dist'] = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors.slice(0, labels.length),
                    borderWidth: 2,
                    borderColor: 'var(--bg-card)',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { position: 'bottom', labels: { color: 'var(--text-secondary)', font: { size: 11 } } },
                    title: { display: true, text: 'College Distribution by Location', color: 'var(--text-primary)', font: { size: 14, weight: '600' } }
                }
            }
        });
    }

    function chartDefaults(title) {
        return {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { display: false },
                title: { display: true, text: title, color: 'var(--text-primary)', font: { size: 14, weight: '600' } }
            },
            scales: {
                y: { beginAtZero: true, ticks: { color: 'var(--text-tertiary)', font: { size: 10 } }, grid: { color: 'var(--border-light)' } },
                x: { ticks: { color: 'var(--text-tertiary)', font: { size: 10 } }, grid: { display: false } }
            }
        };
    }

    // ── Recommendation Testing ────────────────────────────────────────────
    function setupRecommendationForm() {
        var form = $('dash-rec-form');
        if (!form) return;

        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var btn = $('dash-rec-btn');
            var results = $('dash-rec-results');
            var loading = $('dash-rec-loading');

            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';
            if (results) results.style.display = 'none';
            if (loading) loading.style.display = 'block';

            var data = {
                percentile: parseFloat($('dash-rec-percentile').value) || 0,
                category: $('dash-rec-category').value,
                gender: $('dash-rec-gender').value,
                branch: $('dash-rec-branch').value,
                district: $('dash-rec-district').value,
            };

            fetch('/admin/api/dashboard/recommendation-test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            })
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (loading) loading.style.display = 'none';
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-magic"></i> Generate Recommendations';

                if (d.ok) {
                    renderRecommendations(d);
                } else {
                    if (results) {
                        results.style.display = 'block';
                        results.innerHTML = '<div class="alert alert-error">' + escHtml(d.error || 'Failed to generate recommendations') + '</div>';
                    }
                }
            })
            .catch(function() {
                if (loading) loading.style.display = 'none';
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-magic"></i> Generate Recommendations';
                if (results) {
                    results.style.display = 'block';
                    results.innerHTML = '<div class="alert alert-error">Network error. Please try again.</div>';
                }
            });
        });
    }

    function renderRecommendations(d) {
        var container = $('dash-rec-results');
        if (!container) return;
        container.style.display = 'block';

        var html = '';
        if (d.summary) {
            html += '<div class="dash-rec-summary">' +
                '<span class="dash-rec-tag safe">Safe: ' + d.summary.safe_count + '</span>' +
                '<span class="dash-rec-tag moderate">Moderate: ' + d.summary.moderate_count + '</span>' +
                '<span class="dash-rec-tag dream">Dream: ' + d.summary.dream_count + '</span>' +
                (d.summary.latest_year ? '<span class="dash-rec-tag" style="background:var(--color-primary-muted);color:var(--color-primary);">Data: ' + d.summary.latest_year + '</span>' : '') +
                '</div>';
        }

        var tiers = [
            { key: 'safe', label: 'Safe Colleges', icon: 'fa-shield-halved', color: 'var(--color-safe)' },
            { key: 'moderate', label: 'Moderate Colleges', icon: 'fa-chart-simple', color: 'var(--color-moderate)' },
            { key: 'dream', label: 'Dream Colleges', icon: 'fa-star', color: 'var(--color-dream)' },
        ];

        tiers.forEach(function(tier) {
            var items = d[tier.key] || [];
            html += '<div class="dash-rec-tier">' +
                '<h4 style="color:' + tier.color + ';"><i class="fas ' + tier.icon + '"></i> ' + tier.label + ' (' + items.length + ')</h4>';

            if (items.length === 0) {
                html += '<p style="color:var(--text-tertiary);font-size:0.85rem;padding:8px 0;">No colleges in this tier</p>';
            } else {
                html += '<div class="table-wrap"><table><thead><tr><th>College</th><th>Branch</th><th>Cutoff</th><th>Location</th><th>NIRF Rank</th></tr></thead><tbody>';
                items.forEach(function(c) {
                    html += '<tr><td>' + escHtml(c.college_name) + '</td><td>' + escHtml(c.branch) + '</td><td><strong>' + c.cutoff + '</strong></td><td>' + escHtml(c.location || '—') + '</td><td>' + (c.nirf_rank || '—') + '</td></tr>';
                });
                html += '</tbody></table></div>';
            }
            html += '</div>';
        });

        container.innerHTML = html;
    }

    // ── Import Polling ────────────────────────────────────────────────────
    function setupImportPolling() {
        // Auto-refresh import monitoring every 10s ONLY when there are active jobs.
        // Avoids hammering the server when nothing is processing.
        setInterval(function() {
            var active = window.__dashActiveJobs || [];
            if (active.length > 0) {
                var url = '/admin/api/dashboard-full';
                if (currentExamType !== 'ALL') {
                    url += '?exam_type=' + encodeURIComponent(currentExamType);
                }
                fetch(url)
                    .then(function(r) { return r.json(); })
                    .then(function(d) {
                        if (d.ok) {
                            renderImportMonitoring(d.import_monitoring);
                            window.__dashActiveJobs = d.import_monitoring.active_job_ids || [];
                        }
                    })
                    .catch(function() {});
            }
        }, 10000);
    }

    // ── Utility Functions ─────────────────────────────────────────────────
    function setText(id, val) {
        var el = $(id);
        if (el) el.textContent = val;
    }

    function formatNum(n) {
        if (n === null || n === undefined) return '0';
        return n.toLocaleString('en-IN');
    }

    function escHtml(str) {
        if (!str) return '';
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function truncate(str, len) {
        if (!str) return '';
        return str.length > len ? str.substring(0, len) + '…' : str;
    }

    function getTimeAgo(iso) {
        if (!iso) return '';
        var now = new Date();
        var then = new Date(iso);
        var diff = Math.floor((now - then) / 1000);
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return then.toLocaleDateString();
    }

    function getActionIcon(action) {
        var map = {
            'login': 'fa-right-to-bracket',
            'logout': 'fa-right-from-bracket',
            'upload_preview': 'fa-file-upload',
            'import_commit': 'fa-database',
            'bulk_upload': 'fa-cloud-upload-alt',
            'bulk_start': 'fa-play',
            'bulk_cancel': 'fa-stop',
            'approve_import': 'fa-check-circle',
            'reject_import': 'fa-times-circle',
            'bulk_approve': 'fa-check-double',
            'backup': 'fa-cloud-arrow-up',
            'restore': 'fa-cloud-arrow-down',
            'delete': 'fa-trash',
            'edit': 'fa-pen',
            'toggle_admin': 'fa-user-shield',
            'change_password': 'fa-key',
            'college_upload_commit': 'fa-building',
        };
        return map[action] || 'fa-circle';
    }

    function getActionLabel(action) {
        var map = {
            'login': 'logged in',
            'logout': 'logged out',
            'upload_preview': 'uploaded PDF',
            'import_commit': 'committed import',
            'bulk_upload': 'uploaded bulk PDF',
            'bulk_start': 'started import',
            'bulk_cancel': 'cancelled import',
            'approve_import': 'approved import',
            'reject_import': 'rejected import',
            'bulk_approve': 'bulk approved imports',
            'backup': 'created backup',
            'restore': 'restored backup',
            'delete': 'deleted',
            'edit': 'edited',
            'toggle_admin': 'toggled admin role',
            'change_password': 'changed password',
            'college_upload_commit': 'uploaded college data',
        };
        return map[action] || action;
    }

    function destroyChart(key) {
        window.__dashCharts = window.__dashCharts || {};
        if (window.__dashCharts[key]) {
            window.__dashCharts[key].destroy();
            delete window.__dashCharts[key];
        }
    }

})();