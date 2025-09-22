// Flatpickr initialization for event date input
// This file should be included after flatpickr is loaded

document.addEventListener('DOMContentLoaded', function() {
    if (window.flatpickr) {
        flatpickr('#event_date_input', {
            dateFormat: 'Y-m-d',
            allowInput: true,
        });
    }
});
