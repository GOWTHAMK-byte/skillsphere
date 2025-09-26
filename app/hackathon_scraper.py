import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sqlalchemy as sa

# Import your db instance and the HackathonPost model from your app
from app import db
from app.models import HackathonPost


class HackathonScraper:
    """
    Scrapes hackathon data and provides a method to save it to the database.
    """
    BASE_URL = "https://www.knowafest.com/explore/category/Hackathons_in_Chennai_2025"

    def fetch_hackathons(self):
        """
        Fetches a list of hackathons from the target URL.
        Returns a list of dictionaries, where each dictionary represents a hackathon.
        """
        try:
            response = requests.get(self.BASE_URL, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            hackathons = []
            table = soup.find("table")
            if not table:
                return []

            # Iterate over table rows, skipping the header
            for tr in table.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 4:
                    hackathon_data = {
                        "start_date": tds[0],
                        "fest_name": tds[1],
                        "fest_type": tds[2],
                        "college_city": tds[3],
                    }
                    hackathons.append(hackathon_data)
            return hackathons
        except requests.RequestException as e:
            print(f"Error fetching hackathons: {e}")
            return []

    def create_posts_from_hackathons(self):
        print("Starting hackathon scrape and save process...")
        hackathons_data = self.fetch_hackathons()
        if not hackathons_data:
            print("No hackathon data fetched. Aborting.")
            return 0

        new_posts_count = 0
        for item in hackathons_data:
            title = item['fest_name']

            # Check if a post with this title already exists to avoid duplicates
            existing_post = db.session.scalar(
                sa.select(HackathonPost).where(HackathonPost.title == title)
            )

            if not existing_post:
                # Format a helpful description from the scraped data
                description = (
                    f"Type: {item['fest_type']}. "
                    f"Venue: {item['college_city']}. "
                    f"Event Date: {item['start_date']}."
                )

                # Create a new HackathonPost object
                new_post = HackathonPost(
                    title=title,
                    description=description
                )
                db.session.add(new_post)
                new_posts_count += 1
                print(f"Adding new hackathon: {title}")

        if new_posts_count > 0:
            db.session.commit()
            print(f"Successfully added {new_posts_count} new hackathons.")
        else:
            print("No new hackathons to add.")

        return new_posts_count