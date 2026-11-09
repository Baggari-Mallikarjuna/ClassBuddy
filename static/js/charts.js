const CB_CHART_COLORS = ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#0ea5e9', '#ec4899'];

function emptyChart(canvas, message, subMessage) {
    if (!canvas) {
        return;
    }

    const parent = canvas.parentElement;
    if (!parent) {
        return;
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'cb-empty-state compact';
    wrapper.innerHTML = '<div class="cb-empty-icon"><i class="bi bi-bar-chart"></i></div><h6>' + message + '</h6><p>' + subMessage + '</p>';
    parent.innerHTML = '';
    parent.appendChild(wrapper);
}

function renderSubjectPieChart(canvasId, subjectData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        return;
    }

    if (!subjectData || subjectData.length === 0) {
        emptyChart(canvas, 'No attendance data available yet', 'Mark attendance to start seeing your analytics.');
        return;
    }

    new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: subjectData.map(function (item) { return item.subject; }),
            datasets: [{
                data: subjectData.map(function (item) { return item.percentage; }),
                backgroundColor: CB_CHART_COLORS
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom' } }
        }
    });
}

function renderMonthlyLineChart(canvasId, monthlyData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        return;
    }

    if (!monthlyData || monthlyData.length === 0) {
        emptyChart(canvas, 'No monthly trend yet', 'Your attendance trend will appear here as you log classes.');
        return;
    }

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: monthlyData.map(function (item) { return item.month; }),
            datasets: [{
                label: 'Attendance %',
                data: monthlyData.map(function (item) { return item.percentage; }),
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37, 99, 235, 0.12)',
                fill: true,
                tension: 0.3,
                pointRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true, max: 100 } }
        }
    });
}

function renderComparisonBarChart(canvasId, subjectData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        return;
    }

    if (!subjectData || subjectData.length === 0) {
        emptyChart(canvas, 'No subject comparison available', 'Track more attendance to unlock comparisons.');
        return;
    }

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: subjectData.map(function (item) { return item.subject; }),
            datasets: [{
                label: 'Attendance %',
                data: subjectData.map(function (item) { return item.percentage; }),
                backgroundColor: CB_CHART_COLORS,
                borderRadius: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true, max: 100 } }
        }
    });
}

function renderPresentAbsentChart(canvasId, presentClasses, absentClasses) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        return;
    }

    const total = (presentClasses || 0) + (absentClasses || 0);
    if (total === 0) {
        emptyChart(canvas, 'No attendance data yet', 'Mark attendance to start seeing your analytics.');
        return;
    }

    new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: ['Present', 'Absent'],
            datasets: [{
                data: [presentClasses, absentClasses],
                backgroundColor: ['#10b981', '#ef4444']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom' } }
        }
    });
}

function loadDashboardChart() {
    const canvas = document.getElementById('subjectChart');
    if (!canvas) {
        return;
    }

    fetch('/api/analytics/data')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            renderSubjectPieChart('subjectChart', data.subject_wise);
        })
        .catch(function () {
            emptyChart(canvas, 'No attendance data yet', 'Mark attendance to start seeing your analytics.');
        });
}

function loadAnalyticsPage() {
    fetch('/api/analytics/data')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            const overall = document.getElementById('overallSummaryValue');
            if (overall) overall.textContent = (data.overall_percentage || 0) + '%';

            const present = document.getElementById('presentSummaryValue');
            if (present) present.textContent = data.present_classes || 0;

            const absent = document.getElementById('absentSummaryValue');
            if (absent) absent.textContent = data.absent_classes || 0;

            const risk = document.getElementById('riskSummaryValue');
            if (risk) risk.textContent = data.subjects_at_risk || 0;

            renderSubjectPieChart('analyticsSubjectChart', data.subject_wise);
            renderMonthlyLineChart('analyticsMonthlyChart', data.monthly);
            renderPresentAbsentChart('analyticsPresentAbsentChart', data.present_classes, data.absent_classes);
            renderComparisonBarChart('analyticsComparisonChart', data.subject_wise);

            const tableBody = document.querySelector('#subjectBreakdownTable tbody');
            if (tableBody) {
                tableBody.innerHTML = '';
                if (!data.subject_wise || data.subject_wise.length === 0) {
                    tableBody.innerHTML = '<tr><td colspan="4">No attendance data available yet</td></tr>';
                    return;
                }
                data.subject_wise.forEach(function (item) {
                    const row = document.createElement('tr');
                    row.innerHTML = '<td>' + item.subject + '</td>' +
                        '<td>' + Math.round((item.percentage / 100) * item.total) + '</td>' +
                        '<td>' + item.total + '</td>' +
                        '<td>' + item.percentage + '%</td>';
                    tableBody.appendChild(row);
                });
            }
        })
        .catch(function () {
            const overall = document.getElementById('overallSummaryValue');
            if (overall) overall.textContent = '0%';
        });
}

document.addEventListener('DOMContentLoaded', loadDashboardChart);
