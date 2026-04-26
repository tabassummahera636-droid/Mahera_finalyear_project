@'

\# RouteNova - Bangalore Traffic AI/ML Project



RouteNova is an AI/ML-based smart traffic decision-support platform for Bangalore.  

It combines live traffic, weather, route analytics, and an AI assistant to help users make better travel decisions.



\## Features



\- Live traffic insights and congestion hotspots

\- Route planning with ETA and delay comparison

\- Weather impact awareness for travel

\- AI assistant with quick actions:

&nbsp; - Best route now

&nbsp; - Traffic near me

&nbsp; - Best travel time today

&nbsp; - Weather impact

\- Incident and mobility snapshot dashboard

\- FastAPI backend + React (Vite) frontend



\## Tech Stack



\- \*\*Frontend:\*\* React, Vite

\- \*\*Backend:\*\* FastAPI, Uvicorn

\- \*\*Languages:\*\* Python, JavaScript

\- \*\*APIs:\*\* TomTom, Geoapify, OpenWeather (and related map/traffic providers)

\- \*\*AI/ML:\*\* AI-driven insights and ML-based traffic support modules



\## Project Structure



```text

Bangalore\_traffic\_AIML\_Project/

└── Bangalore\_traffic\_AIML\_Project/

&nbsp;   └── Mahera\_Shaistha\_project/

&nbsp;       └── Bangalore\_traffic/

&nbsp;           ├── backend/

&nbsp;           ├── frontend/

&nbsp;           ├── data/

&nbsp;           ├── ML/

&nbsp;           └── models/

Local Setup

1\) Backend

cd "Bangalore\_traffic\_AIML\_Project/Bangalore\_traffic\_AIML\_Project/Mahera\_Shaistha\_project/Bangalore\_traffic/backend"

.\\.venv313\\Scripts\\Activate.ps1

python -m pip install -r requirements.txt

uvicorn app:app --reload --host 127.0.0.1 --port 8000

2\) Frontend

cd "Bangalore\_traffic\_AIML\_Project/Bangalore\_traffic\_AIML\_Project/Mahera\_Shaistha\_project/Bangalore\_traffic/frontend"

npm install

npm run dev

Run URLs

Frontend: http://localhost:5173

Backend API Docs: http://127.0.0.1:8000/docs

Notes

The project is configured to run with backend virtual environment .venv313.

If OpenAI key/package is not configured, chatbot fallback responses are used.

Keep .venv, .venv313, node\_modules, and build/cache folders out of Git tracking.

Team

Mahera Tabassum - Co-Founder / AI, ML \& Frontend

Shaistha Fathima - Co-Founder / Data \& Backend



