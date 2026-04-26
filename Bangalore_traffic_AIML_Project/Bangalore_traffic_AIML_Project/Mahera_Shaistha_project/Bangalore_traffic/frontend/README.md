# Bangalore Traffic Frontend (React + Vite)

## Start

```bash
cd frontend
npm install
npm run dev
```

## Backend URL

Default backend URL is `http://127.0.0.1:8000`.

Set a custom URL if needed:

```bash
# PowerShell
$env:VITE_API_BASE_URL='http://127.0.0.1:8000'
```

## Features

- Bangalore-focused source/destination autocomplete
- Route alternatives with traffic-aware scoring
- Live weather and flow metrics
- Live incident count for Bangalore region
- Nearby POI discovery (hospital/police/fuel)
