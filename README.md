SkillSphere: Intelligent Team Finder for Hackathons & Projects

SkillSphere is a dynamic web application designed to intelligently connect individuals for hackathons, academic projects, and competitions. It moves beyond simple listings by using a recommendation engine to match users based on their skills, location, and even complementary abilities, ensuring the formation of well-rounded and effective teams.
✨ Key Features

    🤝 Intelligent Matchmaking: A powerful recommendation engine suggests relevant projects and potential teammates. The algorithm considers:

        Required Skills: Matching your profile skills to a project's needs.

        Geographic Location: Prioritizing local collaborators for in-person events.

        Complementary Abilities: Suggesting frontend-heavy teams to backend developers, and vice-versa.

    👤 Advanced User Profiles: Users can build comprehensive profiles showcasing their name, bio, education, and links to GitHub/LinkedIn.

    🛠️ Skill Verification & Leveling:

        Add a wide range of technical and design skills to your profile.

        Verify skills by completing projects, taking skill-specific quizzes, or uploading certificates.

        Level up your skills through successful project completions and quiz performance.

    🏆 Gamified Leaderboard: A competitive leaderboard ranks users based on their total skill levels and verifications across different categories (Frontend, Backend, DevOps, etc.).

    💬 Real-Time Team Chat: Once a team is formed, members gain access to a private, real-time chat room (powered by WebSockets) for seamless communication.

    🤖 AI-Powered Assistant: A helpful chatbot, integrated with the Google Gemini API, provides instant answers to user questions about the platform.

    📣 Post Creation & Management: Users can create detailed posts to recruit team members, specifying required skills, team size, event details, and more.

💻 Tech Stack

    Backend: Python, Flask, Flask-SocketIO, SQLAlchemy ORM

    Frontend: HTML5, Tailwind CSS, JavaScript

    Database: SQLite (for development), easily configurable for PostgreSQL or MySQL.

    AI Integration: Google Generative AI (Gemini)

    Deployment: Designed for serverless deployment (e.g., Firebase Cloud Functions, Google Cloud Run).

🚀 Getting Started

Follow these instructions to set up and run the project locally.
Prerequisites

    Python 3.8+

    Node.js and npm (for Firebase CLI, if deploying)

    A Google Gemini API Key

Installation & Setup

    Clone the Repository

    git clone [https://github.com/your-username/skillsphere.git](https://github.com/your-username/skillsphere.git)
    cd skillsphere

    Create a Virtual Environment

    # For Windows
    python -m venv venv
    venv\Scripts\activate

    # For macOS/Linux
    python3 -m venv venv
    source venv/bin/activate

    Install Dependencies

    pip install -r requirements.txt

    Set Up Environment Variables
    Create a .env file in the root directory and add your secret keys.

    GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
    SECRET_KEY="a-very-random-secret-key-for-flask"
    DATABASE_URL="sqlite:///app.db"

    Initialize the Database
    If you have Flask-Migrate set up, run the migrations. Otherwise, you can initialize it from a Python shell.

    # With Flask-Migrate
    flask db init
    flask db migrate -m "Initial migration"
    flask db upgrade

    Run the Application

    flask run

    The application should now be running at http://127.0.0.1:5000.

🤝 Contributing

Contributions are welcome! If you have suggestions or want to improve the code, please feel free to:

    Fork the repository.

    Create a new branch (git checkout -b feature/AmazingFeature).

    Commit your changes (git commit -m 'Add some AmazingFeature').

    Push to the branch (git push origin feature/AmazingFeature).

    Open a Pull Request.


