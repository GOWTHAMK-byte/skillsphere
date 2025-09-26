document.addEventListener('DOMContentLoaded', function() {
    // Find the scrape button on the page
    const scrapeBtn = document.getElementById('scrape-btn');

    // If the button doesn't exist (e.g., for non-admin users), stop the script.
    if (!scrapeBtn) {
        return;
    }

    // Get references to the text and spinner elements within the button
    const btnText = document.getElementById('btn-text');
    const loadingSpinner = document.getElementById('loading-spinner');

    // Add a click event listener to the button
    scrapeBtn.addEventListener('click', async function() {
        // --- 1. Provide visual feedback that the process has started ---
        btnText.textContent = 'Scraping...'; // Update button text
        loadingSpinner.style.display = 'inline-block'; // Show the spinner
        scrapeBtn.disabled = true; // Disable the button to prevent multiple clicks

        try {
            // --- 2. Make the API call to the backend ---
            const response = await fetch('/scrape_hackathons', {
                method: 'POST',
                headers: {
                    // Although not strictly necessary for this endpoint,
                    // it's good practice to include headers.
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
            });

            // --- 3. Handle the server's response ---
            const result = await response.json();

            if (response.ok && result.success) {
                // If successful, show the success message
                alert(result.message);
                // Reload the page to display the newly scraped hackathons
                window.location.reload();
            } else {
                // If there's an error, show the error message from the server
                throw new Error(result.error || 'An unknown error occurred.');
            }
        } catch (error) {
            // --- 4. Handle any network or unexpected errors ---
            console.error('Scraping failed:', error);
            alert('Scraping failed: ' + error.message);

            // --- 5. Reset the button state on failure ---
            btnText.textContent = 'Scrape New Hackathons';
            loadingSpinner.style.display = 'none';
            scrapeBtn.disabled = false;
        }
        // Note: We don't need a `finally` block here because a successful
        // scrape reloads the page, which automatically resets the button.
    });
});
