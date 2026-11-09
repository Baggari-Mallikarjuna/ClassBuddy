const REMINDER_POLL_INTERVAL_MS = 60000;
const notifiedClasses = new Set();

function requestNotificationAccess() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function showClassReminder(classInfo) {
    const notificationKey = classInfo.subject + classInfo.start_time;
    if (notifiedClasses.has(notificationKey)) {
        return;
    }

    const title = classInfo.subject + ' starts in ' + classInfo.minutes_until + ' minutes';
    const body = 'Room ' + classInfo.room + ' • ' + classInfo.faculty;

    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body: body, icon: '/static/images/bell.png' });
    }

    notifiedClasses.add(notificationKey);
}

function checkUpcomingClasses() {
    fetch('/api/reminders/upcoming')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            data.upcoming.forEach(showClassReminder);
        })
        .catch(function (error) {
            console.error('Reminder check failed:', error);
        });
}

document.addEventListener('DOMContentLoaded', function () {
    requestNotificationAccess();
    checkUpcomingClasses();
    setInterval(checkUpcomingClasses, REMINDER_POLL_INTERVAL_MS);
});
