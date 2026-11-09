document.addEventListener('DOMContentLoaded', function () {
    var toggleBtn = document.getElementById('cbSidebarToggle');
    var sidebar = document.getElementById('cbSidebar');

    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener('click', function () {
            sidebar.classList.toggle('collapsed');
        });
    }

    var themeToggle = document.querySelector('[data-theme-toggle]');
    var storedTheme = localStorage.getItem('classbuddy-theme') || 'light';
    document.body.setAttribute('data-theme', storedTheme);

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            var currentTheme = document.body.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.body.setAttribute('data-theme', currentTheme);
            localStorage.setItem('classbuddy-theme', currentTheme);
            var icon = themeToggle.querySelector('i');
            if (icon) {
                icon.className = currentTheme === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
            }
        });
    }

    var alerts = document.querySelectorAll('.alert');
    alerts.forEach(function (alertEl) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alertEl);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 5000);
    });
});
